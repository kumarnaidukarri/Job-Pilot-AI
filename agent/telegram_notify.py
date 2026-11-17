"""
Telegram alerts + a tiny command interface (free, instant, on the phone).

Setup (one time, ~2 minutes):
  1. In Telegram, message @BotFather -> /newbot -> copy the token -> TELEGRAM_BOT_TOKEN
  2. Send any message to your new bot (Kumar and/or Ashok)
  3. python tools/telegram_setup.py  -> prints the chat ids -> TELEGRAM_CHAT_ID (comma-separated)

Each run:
  * process_commands(): reads messages sent to the bot since last run:
        /applied <id>  /skip <id>  /interview <id>  /offer <id>  /rejected <id>  /stats
    (<id> is the 6-char code shown on every alert) — this is the application tracker.
  * send_job_alerts(jobs): one message per top match, freshest/best first.
No webhook/server needed — commands are picked up on the next 30-minute run.
"""
import html
import os
from datetime import datetime

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def _token():
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_ids():
    return [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]


def enabled():
    return bool(_token() and _chat_ids())


def _call(method, **params):
    r = requests.post(API.format(token=_token(), method=method), json=params, timeout=20)
    return r.json() if r.ok else {"ok": False, "description": r.text[:200]}


def send_text(text, chat_id=None):
    ok = True
    for cid in ([chat_id] if chat_id else _chat_ids()):
        res = _call("sendMessage", chat_id=cid, text=text[:4096], parse_mode="HTML",
                    disable_web_page_preview=True)
        ok &= bool(res.get("ok"))
        if not res.get("ok"):
            print(f"  [Telegram] send failed: {res.get('description', '')[:120]}")
    return ok


def _posted_label(date_posted):
    try:
        d = datetime.strptime(str(date_posted)[:10], "%Y-%m-%d")
        days = (datetime.now() - d).days
        return "today" if days <= 0 else ("yesterday" if days == 1 else f"{days}d ago")
    except Exception:
        return "date n/a"


def format_job(j):
    e = html.escape
    kit = j.get("apply_kit") or {}
    lines = [
        f"<b>{e(str(j.get('relevance_score')))}/10 · {e(j.get('title', ''))}</b>",
        f"🏢 {e(j.get('company', ''))} · 📍 {e(str(j.get('location', ''))[:60])}",
        f"🕒 {_posted_label(j.get('date_posted'))} · {e(str(j.get('source', '')))}"
        + (f" · 💼 {e(str(j.get('experience_required') or j.get('exp_range') or ''))}" if (j.get('experience_required') or j.get('exp_range')) else "")
        + (f" · 💰 {e(j['salary'])}" if j.get("salary") else ""),
        f"✅ {e(j.get('match_reason', ''))}",
    ]
    if j.get("skills_match"):
        lines.append("🧩 Match: " + e(", ".join(j["skills_match"])))
    if j.get("gaps"):
        lines.append("⚠️ Gaps: " + e(", ".join(j["gaps"])))
    if kit.get("highlight"):
        lines.append("🎯 Lead with: " + e(kit["highlight"]))
    if kit.get("keywords"):
        lines.append("🔑 Mirror: " + e(", ".join(kit["keywords"])))
    if kit.get("pitch"):
        lines.append(f"✉️ <i>{e(kit['pitch'])}</i>")
    if j.get("hr_email"):
        lines.append("📧 HR email (from JD): " + e(j["hr_email"]))
    links = [f'<a href="{e(j.get("job_url", ""))}">APPLY</a>']
    if kit.get("recruiters_url"):
        links.append(f'<a href="{e(kit["recruiters_url"])}">Recruiters</a>')
    lines.append(" · ".join(links) + f"  ·  id <code>{e(j.get('short_id') or str(j.get('job_id', ''))[:6])}</code>")
    return "\n".join(lines)


def send_job_alerts(jobs, header=None):
    if not enabled() or not jobs:
        return False
    if header:
        send_text(header)
    ok = True
    for j in jobs:
        ok &= send_text(format_job(j))
    return ok


# ── Command processing (application tracker) ─────────────────────────────────

_STATUS_CMDS = {"/applied": "applied", "/skip": "skipped", "/interview": "interview",
                "/offer": "offer", "/rejected": "rejected"}


def process_commands():
    """Apply any /applied <id> etc. commands sent since the last run. Returns count handled."""
    if not _token():
        return 0
    from storage.database import kv_get, kv_set, find_by_short_id, update_status, status_counts
    offset = kv_get("telegram_offset", 0)
    res = _call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
    if not res.get("ok"):
        return 0
    allowed = set(_chat_ids())
    handled = 0
    for upd in res.get("result", []):
        offset = max(offset, upd["update_id"] + 1)
        msg = upd.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if chat not in allowed or not text.startswith("/"):
            continue
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        if cmd in _STATUS_CMDS and len(parts) > 1:
            replies = []
            for code in parts[1:]:
                job = find_by_short_id(code.strip().lower())
                if job:
                    update_status(job["job_id"], _STATUS_CMDS[cmd])
                    replies.append(f"✔ {html.escape(job['title'][:50])} @ {html.escape(job['company'][:30])} → {_STATUS_CMDS[cmd]}")
                else:
                    replies.append(f"✖ no job with id {html.escape(code)}")
            send_text("\n".join(replies), chat_id=chat)
            handled += 1
        elif cmd == "/stats":
            c = status_counts()
            send_text("📊 <b>Pipeline</b>\n" + "\n".join(f"{k}: {v}" for k, v in sorted(c.items(), key=lambda x: -x[1])),
                      chat_id=chat)
            handled += 1
        elif cmd in ("/start", "/help"):
            send_text("Commands (use the 6-char id from an alert):\n/applied id [id…]\n/skip id\n/interview id\n"
                      "/offer id\n/rejected id\n/stats\nCommands are processed on the next run (≤30 min).",
                      chat_id=chat)
            handled += 1
    kv_set("telegram_offset", offset)
    return handled
