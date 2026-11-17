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


def complete_json(prompt, validate=None, max_tokens=2500, temperature=0.1, label="llm"):
    """
    Returns (parsed_json, provider_name) or (None, None) if every provider failed.
    `validate(obj) -> bool` rejects malformed answers (e.g. wrong number of results).
    """
    _load_state()
    cfg = _config()["matching"]
    cooldown_h = float(cfg.get("quota_cooldown_hours", 6))
    providers = active_providers()

    for p in providers:
        if not _available(p):
            continue
        u = _usage.setdefault(p["name"], {"calls": 0, "ok": 0, "fail": 0})
        for attempt in range(3):
            u["calls"] += 1
            try:
                fn = _call_gemini if p["kind"] == "gemini" else _call_openai
                obj = fn(p, prompt, max_tokens, temperature)
                if validate and not validate(obj):
                    raise ProviderError("output failed validation", "bad_output")
                u["ok"] += 1
                return obj, p["name"]
            except ProviderError as e:
                u["fail"] += 1
                if e.kind == "minute" and attempt < 2:
                    wait = 8 * (attempt + 1)
                    m = re.search(r"try again in ([\d.]+)s", str(e))
                    if m:
                        wait = min(30, float(m.group(1)) + 1)
                    time.sleep(wait)
                    continue
                if e.kind == "transient" and attempt < 1:
                    time.sleep(3)
                    continue
                if e.kind == "daily":
                    _cooldown(p["name"], cooldown_h, str(e))
                elif e.kind == "fatal":
                    _cooldown(p["name"], 24, str(e))
                elif e.kind == "minute":
                    # still rate-limited after retries: rest it for this run only
                    _run_cooldowns[p["name"]] = _now() + timedelta(minutes=10)
                else:
                    print(f"  [llm] {label}: {p['name']} {e.kind} — {str(e)[:100]}; trying next provider")
                break
    return None, None
