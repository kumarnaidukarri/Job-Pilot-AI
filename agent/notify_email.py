"""
Email digest: HTML body with the top matches (readable on a phone without opening anything)
plus an Excel attachment with every match, its apply kit and recruiter links.
"""
import html
import os
import smtplib
import sys
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import openpyxl
import yaml
from dotenv import load_dotenv
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

GMAIL_ADDRESS  = os.getenv("GMAIL_ADDRESS", "")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587

_LOCATION_ALIASES = {
    "bangalore": ["bangalore", "bengaluru"],
    "delhi":     ["delhi", "new delhi", "gurgaon", "gurugram", "noida"],
    "visakhapatnam": ["visakhapatnam", "vizag"],
    "remote":    ["remote", "work from home", "wfh", "anywhere", "worldwide"],
}


def _location_priority_map() -> dict:
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        pm = {}
        for i, loc in enumerate(cfg.get("search", {}).get("locations", [])):
            for alias in _LOCATION_ALIASES.get(loc.lower(), [loc.lower()]):
                pm[alias] = i
        return pm
    except Exception:
        return {}


def _location_rank(location, pm) -> int:
    loc = str(location or "").lower()
    ranks = [r for name, r in pm.items() if name in loc]
    return min(ranks) if ranks else len(pm)


def _freshness_key(date_posted):
    try:
        return (0, -datetime.strptime(str(date_posted)[:10], "%Y-%m-%d").timestamp())
    except Exception:
        return (1, 0)


def posted_label(date_posted) -> str:
    try:
        days = (datetime.now() - datetime.strptime(str(date_posted)[:10], "%Y-%m-%d")).days
        return "today" if days <= 0 else ("1 day ago" if days == 1 else f"{days} days ago")
    except Exception:
        return "date unknown"


def sort_jobs(jobs):
    """Best score -> preferred location -> freshest (unknown dates last, never dropped)."""
    pm = _location_priority_map()
    return sorted(jobs, key=lambda j: (-int(j.get("relevance_score") or 0),
                                       _location_rank(j.get("location"), pm),
                                       _freshness_key(j.get("date_posted"))))


HEADERS = ["#", "Score", "Job Title", "Company", "Location", "Exp", "Posted", "Source", "Why",
           "Skills match", "Gaps", "Lead with", "Pitch (copy-paste)", "Apply", "Recruiters", "ID", "HR email"]
WIDTHS  = [4, 6, 38, 24, 20, 10, 11, 10, 40, 30, 22, 28, 60, 8, 11, 8, 30]


def _build_sheet(jobs: list, out_path: str):
    jobs = sort_jobs(jobs)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Matches"
    hfill, hfont = PatternFill("solid", fgColor="1F4E79"), Font(bold=True, color="FFFFFF")
    for c, h in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.fill, cell.font = hfill, hfont
        cell.alignment = Alignment(horizontal="center", vertical="center")
    green, yellow, alt = (PatternFill("solid", fgColor=x) for x in ("C6EFCE", "FFEB9C", "EBF3FB"))
    link_font = Font(color="0563C1", underline="single")
    for i, j in enumerate(jobs, 1):
        kit = j.get("apply_kit") or {}
        row = [i, j.get("relevance_score"), j.get("title", ""), j.get("company", ""), j.get("location", ""),
               j.get("experience_required") or j.get("exp_range") or "", posted_label(j.get("date_posted")),
               str(j.get("source", "")), j.get("match_reason", ""), ", ".join(j.get("skills_match") or []),
               ", ".join(j.get("gaps") or []), kit.get("highlight", ""), kit.get("pitch", ""), "", "",
               j.get("short_id") or str(j.get("job_id", ""))[:6], j.get("hr_email") or ""]
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=i + 1, column=c, value=v)
            cell.alignment = Alignment(vertical="top", wrap_text=c in (3, 9, 12, 13))
            if i % 2 == 0:
                cell.fill = alt
        for col, url, label in ((14, j.get("job_url"), "Apply"), (15, kit.get("recruiters_url"), "Recruiters")):
            if url:
                cell = ws.cell(row=i + 1, column=col, value=label)
                cell.hyperlink, cell.font = url, link_font
        score = int(j.get("relevance_score") or 0)
        ws.cell(row=i + 1, column=2).fill = green if score >= 9 else yellow
    for c, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(jobs) + 1}"
    wb.save(out_path)
    return len(jobs)


def _html_body(jobs, total, stats_line):
    e = html.escape
    rows = []
    for j in sort_jobs(jobs)[:15]:
        kit = j.get("apply_kit") or {}
        pitch = f'<div style="color:#555;font-style:italic;margin-top:4px">{e(kit["pitch"])}</div>' if kit.get("pitch") else ""
        rec = f' · <a href="{e(kit["recruiters_url"])}">recruiters</a>' if kit.get("recruiters_url") else ""
        if j.get("hr_email"):
            first = j["hr_email"].split(",")[0].strip()
            rec += f' · 📧 <a href="mailto:{e(first)}">{e(j["hr_email"])}</a>'

        rows.append(
            f'<tr><td style="padding:8px;border-bottom:1px solid #eee;vertical-align:top">'
            f'<b>{e(str(j.get("relevance_score")))}/10</b></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">'
            f'<a href="{e(j.get("job_url", ""))}"><b>{e(j.get("title", ""))}</b></a> — {e(j.get("company", ""))}<br>'
            f'<span style="color:#666">{e(str(j.get("location", ""))[:60])} · {posted_label(j.get("date_posted"))} · '
            f'{e(str(j.get("experience_required") or j.get("exp_range") or ""))} · {e(str(j.get("source", "")))}'
            f' · id {e(j.get("short_id") or "")}</span><br>'
            f'{e(j.get("match_reason", ""))}{rec}{pitch}</td></tr>')
    more = f"<p>+ {total - 15} more in the attached sheet.</p>" if total > 15 else ""
    return f"""<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222">
<p>Hi Kumar — <b>{total}</b> new matching job{'s' if total != 1 else ''} this run. Best and freshest first; apply early.</p>
<table style="border-collapse:collapse;width:100%">{''.join(rows)}</table>{more}
<p style="color:#888;font-size:12px">{e(stats_line)}<br>Track applications: reply to the Telegram bot with /applied &lt;id&gt;.<br>— JobPilot AI</p>
</body></html>"""


def send_report(jobs: list, recipient: str = None, tag: str = "[APPLY NOW]", stats_line: str = "") -> bool:
    if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
        print("  Notify: Gmail credentials not configured — skipping.")
        return False
    # Recipient must be set explicitly (NOTIFY_EMAIL). No "send to the sender account" fallback:
    # that is exactly how a digest once reached the previous owner's inbox.
    to_addr = (recipient or "").strip()
    if not to_addr:
        print("  Notify: NOTIFY_EMAIL not set — skipping email (no implicit recipient).")
        return False
    sheet_path = f"run_jobs_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    n = _build_sheet(jobs, sheet_path)
    top = sort_jobs(jobs)[0] if jobs else {}
    subject = f"JobPilot {tag} {n} new — top: {top.get('title', '')[:40]} @ {top.get('company', '')[:25]}"

    msg = MIMEMultipart("mixed")
    msg["From"], msg["To"], msg["Subject"] = GMAIL_ADDRESS, to_addr, subject
    msg.attach(MIMEText(_html_body(jobs, n, stats_line), "html", "utf-8"))
    with open(sheet_path, "rb") as f:
        part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=sheet_path)
    msg.attach(part)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [a.strip() for a in to_addr.split(",")], msg.as_bytes())
        return True
    except Exception as e:
        print(f"  Notify: Failed to send — {e}")
        return False
    finally:
        try:
            os.remove(sheet_path)
        except Exception:
            pass
