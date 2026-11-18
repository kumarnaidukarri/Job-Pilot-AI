"""
Once-a-day Telegram brief (first run after 09:00 IST):

  * last 24h: runs, new jobs, matches, top matches
  * top matches not yet acted on (no /applied or /skip) — the "you're leaving these on the table" list
  * follow-ups: jobs marked /applied 5-14 days ago with no update → nudge to message a recruiter
  * tracker totals (applied / interview / offer / rejected)
  * Naukri reminder: recruiters' search ranks recently-updated profiles higher

Nothing is sent to anyone except the configured Telegram chats.
"""
import html
from datetime import datetime, timedelta

BRIEF_HOUR = 9


def _top_open(db, days=3, limit=6):
    since = (datetime.now() - timedelta(days=days)).isoformat()
    return list(db.jobs.find({"relevance_score": {"$gte": 8}, "status": "emailed", "date_scraped": {"$gte": since}},
                             {"_id": 0}).sort([("relevance_score", -1), ("date_scraped", -1)]).limit(limit))


def _followups(db, limit=5):
    now = datetime.now()
    lo, hi = (now - timedelta(days=14)).isoformat(), (now - timedelta(days=5)).isoformat()
    return list(db.jobs.find({"status": "applied", "applied_at": {"$gte": lo, "$lte": hi}}, {"_id": 0})
                .sort("applied_at", 1).limit(limit))


def build_brief(db):
    from agent.apply_kit import recruiter_search_url
    e = html.escape
    now = datetime.now()
    since = (now - timedelta(hours=24)).isoformat()
    runs = list(db.runs.find({"started": {"$gte": since}}, {"_id": 0, "new": 1, "error": 1}))
    new_jobs = sum(int(r.get("new") or 0) for r in runs)
    matches = db.jobs.count_documents({"date_scraped": {"$gte": since}, "relevance_score": {"$gte": 7}})
    top = db.jobs.count_documents({"date_scraped": {"$gte": since}, "relevance_score": {"$gte": 8}})
    errors = sum(1 for r in runs if r.get("error"))

    lines = [f"☀️ <b>Good morning Kumar — JobPilot daily brief</b>",
             f"Last 24h: {len(runs)} runs · {new_jobs} new jobs · <b>{matches}</b> matches · <b>{top}</b> top"
             + (f" · ⚠️ {errors} run errors" if errors else "")]

    open_top = _top_open(db)
    if open_top:
        lines.append("\n🎯 <b>Top matches you haven't acted on</b> (reply /applied id or /skip id):")
        for j in open_top:
            lines.append(f'• {j.get("relevance_score")}/10 <a href="{e(j.get("job_url", ""))}">{e(j.get("title", "")[:45])}</a>'
                         f' — {e(str(j.get("company", ""))[:25])} · <code>{e(j.get("short_id", ""))}</code>')

    fu = _followups(db)
    if fu:
        lines.append("\n📬 <b>Follow up</b> (applied 5+ days ago, no update) — a short note to a recruiter helps:")
        for j in fu:
            days = (now - datetime.fromisoformat(j["applied_at"])).days
            rec = (j.get("apply_kit") or {}).get("recruiters_url") or recruiter_search_url(j.get("company", ""))
            lines.append(f'• {e(j.get("title", "")[:40])} @ {e(str(j.get("company", ""))[:25])} — {days}d · '
                         f'<a href="{e(rec)}">find recruiter</a> · <code>{e(j.get("short_id", ""))}</code>')

    c = {r["_id"]: r["n"] for r in db.jobs.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
    lines.append(f"\n📊 Tracker: applied {c.get('applied', 0)} · interview {c.get('interview', 0)} · "
                 f"offer {c.get('offer', 0)} · rejected {c.get('rejected', 0)}")
    lines.append("💡 Make one small edit to your Naukri profile today — recruiters' search favours recently "
                 "updated profiles.")
    return "\n".join(lines)


def maybe_send_daily_brief():
    """Send at most once per calendar day, on the first run at/after BRIEF_HOUR (local time)."""
    from agent import telegram_notify
    from storage.database import get_db, kv_get, kv_set
    if not telegram_notify.enabled():
        return False
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if now.hour < BRIEF_HOUR or kv_get("daily_brief_date") == today:
        return False
    ok = telegram_notify.send_text(build_brief(get_db()))
    if ok:
        kv_set("daily_brief_date", today)
    return ok
