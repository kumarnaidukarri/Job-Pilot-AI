"""
Multi-provider free-LLM client.

One `complete_json(prompt, validate)` call walks the provider chain from config.yaml
(`matching.providers`) until one returns JSON that passes `validate`. Design rules:

  * A provider whose API key env var is missing is skipped (add a key -> it's live).
  * Per-minute rate limit (429 w/o "per day")   -> short wait, retry same provider once.
  * Daily quota / auth / model-retired errors   -> provider put on cooldown, persisted in
    MongoDB (`llm_state`) so the NEXT runs skip it too instead of re-burning calls.
  * Bad/short JSON from a provider              -> that batch moves to the next provider;
    the provider stays enabled for other batches (the old code disabled it for the run).
  * OpenAI-compatible providers (Groq, Cerebras, OpenRouter, Mistral, NVIDIA, Cloudflare,
    local Ollama) share one code path; Gemini/Gemma use the native REST API.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
import yaml
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_run_cooldowns = {}   # name -> until (datetime) — in-memory mirror of llm_state
_usage = {}           # name -> {"calls": n, "ok": n, "fail": n} for this process
_state_loaded = False


class ProviderError(Exception):
    def __init__(self, msg, kind):
        super().__init__(msg)
        self.kind = kind   # "minute" | "daily" | "fatal" | "transient" | "bad_output"


def _config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def _now():
    return datetime.now(timezone.utc)


# ── provider list / cooldown state ────────────────────────────────────────────

def _expand(s: str):
    """Fill {ENV_VAR} placeholders; return None if any referenced var is unset."""
    missing = []

    def rep(m):
        v = os.getenv(m.group(1))
        if not v:
            missing.append(m.group(1))
            return ""
        return v.rstrip("/")
    out = re.sub(r"\{([A-Z0-9_]+)\}", rep, s or "")
    return None if missing else out


def active_providers():
    out = []
    for p in _config()["matching"].get("providers", []):
        key = os.getenv(p["key_env"], "")
        if not key:
            continue
        base = _expand(p.get("base_url", "")) if p.get("base_url") else ""
        if base is None:
            continue
        q = dict(p, api_key=key, base_url=base)
        out.append(q)
    return out


def _load_state():
    global _state_loaded
    if _state_loaded:
        return
    _state_loaded = True
    try:
        from storage.database import get_llm_cooldowns
        for name, until in get_llm_cooldowns().items():
            if until > _now():
                _run_cooldowns[name] = until
    except Exception as e:
        print(f"  [llm] could not load cooldown state: {e}")


def _cooldown(name, hours, why):
    until = _now() + timedelta(hours=hours)
    _run_cooldowns[name] = until
    print(f"  [llm] {name} cooled down for {hours}h — {why[:120]}")
    try:
        from storage.database import set_llm_cooldown
        set_llm_cooldown(name, until, why[:300])
    except Exception:
        pass


def _available(p):
    until = _run_cooldowns.get(p["name"])
    return not until or until <= _now()


def reset_llm_state():
    """Called at the start of each pipeline run (keeps persisted cooldowns)."""
    global _state_loaded
    _run_cooldowns.clear()
    _minute_until.clear()
    _usage.clear()
    _state_loaded = False


def usage_summary():
    return {k: dict(v) for k, v in _usage.items()}


# ── error classification ──────────────────────────────────────────────────────

_DAILY_MARKERS = ("per day", "perday", "tokens per day", "requests per day", "(tpd)", "(rpd)",
                  "daily", "quota exceeded for metric", "exceeded your current quota",
                  "insufficient_quota", "credits", "free tier limit", "free-models-per-day")


def _classify(status, body):
    b = (body or "").lower()
    if status == 429 or "rate limit" in b or "resource_exhausted" in b:
        if any(m in b for m in _DAILY_MARKERS):
            return "daily"
        return "minute"
    if status in (401, 403):
        return "fatal"
    if status == 404 or "model_not_found" in b or "does not exist" in b or "decommissioned" in b:
        return "fatal"
    if status == 402:
        return "daily"
    if status >= 500 or status == 0:
        return "transient"
    return "bad_request"


# ── calls ─────────────────────────────────────────────────────────────────────

def _extract_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except Exception:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s, e = text.find(open_c), text.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except Exception:
                continue
    raise ProviderError("unparseable JSON", "bad_output")


def _call_openai(p, prompt, max_tokens, temperature):
    body = {
        "model": p["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    m = p["model"].lower()
    if "gpt-oss" in m and "groq" in p["base_url"]:
        body["reasoning_effort"] = "low"        # big token saver on reasoning models
    headers = {"Authorization": f"Bearer {p['api_key']}", "Content-Type": "application/json"}
    if "openrouter" in p["base_url"]:
        headers["X-Title"] = "JobPilot"
    url = p["base_url"].rstrip("/") + "/chat/completions"
    for attempt in range(2):
        try:
            r = requests.post(url, json=body, headers=headers, timeout=90)
        except requests.RequestException as e:
            raise ProviderError(str(e), "transient")
        if r.status_code == 400 and attempt == 0 and ("response_format" in r.text or "reasoning" in r.text
                                                     or "json" in r.text.lower()):
            body.pop("response_format", None)   # some models/providers reject JSON mode
            body.pop("reasoning_effort", None)
            continue
        break
    if r.status_code != 200:
        raise ProviderError(f"HTTP {r.status_code}: {r.text[:300]}", _classify(r.status_code, r.text))
    try:
        msg = r.json()["choices"][0]["message"]
        return _extract_json(msg.get("content") or "")
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"bad response shape: {e}", "bad_output")


def _call_gemini(p, prompt, max_tokens, temperature):
    url = GEMINI_URL.format(model=p["model"])
    gen = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if not p["model"].startswith("gemma"):
        gen["responseMimeType"] = "application/json"
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
    try:
        r = requests.post(url, json=payload, headers={"x-goog-api-key": p["api_key"]}, timeout=90)
    except requests.RequestException as e:
        raise ProviderError(str(e), "transient")
    if r.status_code != 200:
        raise ProviderError(f"HTTP {r.status_code}: {r.text[:300]}", _classify(r.status_code, r.text))
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(pt.get("text", "") for pt in parts if not pt.get("thought"))
        return _extract_json(text)
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"bad response shape: {e}", "bad_output")


_minute_until = {}    # name -> until: short per-minute rate-limit pause (this run only)


def _retry_after(err, default=20.0):
    m = re.search(r"(?:try again in|retry in|retryDelay\W+)\s*([\d.]+)\s*(ms|s)?", str(err), re.IGNORECASE)
    if not m:
        return default
    v = float(m.group(1)) / (1000 if (m.group(2) or "").lower() == "ms" else 1)
    return max(2.0, min(60.0, v + 0.5))


def complete_json(prompt, validate=None, max_tokens=2500, temperature=0.1, label="llm"):
    """
    Returns (parsed_json, provider_name) or (None, None) if every provider failed.
    `validate(obj) -> bool` rejects malformed answers (e.g. wrong number of results).

    Per-minute 429s no longer block: the provider is paused for its retry-after and the batch
    goes straight to the next provider. Only when EVERY usable provider is minute-paused do we
    sleep (until the earliest frees up), at most a few rounds.
    """
    _load_state()
    cfg = _config()["matching"]
    cooldown_h = float(cfg.get("quota_cooldown_hours", 6))
    providers = active_providers()
    tried_bad = set()          # providers that gave bad output / errors for THIS prompt

    for _round in range(4):
        waiting = []
        for p in providers:
            name = p["name"]
            if not _available(p) or name in tried_bad:
                continue
            mu = _minute_until.get(name)
            if mu and mu > _now():
                waiting.append(mu)
                continue
            u = _usage.setdefault(name, {"calls": 0, "ok": 0, "fail": 0, "rate_limited": 0})
            u.setdefault("rate_limited", 0)
            for attempt in range(2):
                u["calls"] += 1
                try:
                    fn = _call_gemini if p["kind"] == "gemini" else _call_openai
                    obj = fn(p, prompt, max_tokens, temperature)
                    if validate and not validate(obj):
                        raise ProviderError("output failed validation", "bad_output")
                    u["ok"] += 1
                    return obj, name
                except ProviderError as e:
                    if e.kind == "minute":
                        u["rate_limited"] += 1
                        until = _now() + timedelta(seconds=_retry_after(e))
                        _minute_until[name] = until
                        waiting.append(until)
                        break
                    u["fail"] += 1
                    if e.kind == "transient" and attempt == 0:
                        time.sleep(3)
                        continue
                    if e.kind == "daily":
                        _cooldown(name, cooldown_h, str(e))
                    elif e.kind == "fatal":
                        _cooldown(name, 24, str(e))
                    else:
                        print(f"  [llm] {label}: {name} {e.kind} — {str(e)[:100]}; trying next provider")
                        tried_bad.add(name)
                    break
        if not waiting:
            break
        pause = (min(waiting) - _now()).total_seconds()
        if pause > 0:
            time.sleep(min(pause, 60))
    return None, None
