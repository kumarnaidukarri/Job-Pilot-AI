"""
Remote job boards with free, keyless public APIs: RemoteOK, Himalayas, Jobicy, Arbeitnow.

Most remote postings are restricted to US/EU, so only keep jobs that are software roles
AND open to India (location empty/"Worldwide"/"Anywhere"/APAC/Asia/India).
Attribution: each job links back to the source board's own URL, as their terms ask.
"""
import re
from datetime import datetime, timezone

import requests

from agent.exp_filter import SOFTWARE_TITLE_RE
from scrapers.ats_common import UA, html_to_text, is_india
from scrapers.common import job_id_for

_OPEN_RE = re.compile(r"\b(worldwide|anywhere|global|remote|apac|asia|emea\s*&?\s*apac|india)\b", re.IGNORECASE)
_RESTRICT_RE = re.compile(r"\b(us|usa|united states|canada|uk|united kingdom|europe|eu|germany|latam|"
                          r"brazil|mexico|australia|only)\b", re.IGNORECASE)


def _open_to_india(loc) -> bool:
    if isinstance(loc, (list, tuple)):
        if not loc:
            return True
        return any(is_india(x) or _OPEN_RE.search(str(x)) for x in loc)
    loc = str(loc or "").strip()
    if not loc:
        return True
    if is_india(loc):
        return True
    return bool(_OPEN_RE.search(loc)) and not _RESTRICT_RE.search(loc)


def _date(v):
    try:
        if isinstance(v, (int, float)) or str(v).isdigit():
            return datetime.fromtimestamp(int(v), tz=timezone.utc).strftime("%Y-%m-%d")
        return str(v)[:10]
    except Exception:
        return ""


def _job(source, title, company, location, url, desc, date):
    return {
        "job_id": job_id_for(url), "title": (title or "").strip(), "company": (company or "").strip(),
        "location": location if isinstance(location, str) else ", ".join(location or []) or "Remote",
        "source": source, "job_url": url, "description": html_to_text(desc)[:6000],
        "date_posted": _date(date), "company_website": "",
    }


def _remoteok():
    data = requests.get("https://remoteok.com/api", headers=UA, timeout=30).json()
    out = []
    for j in data[1:]:
        if SOFTWARE_TITLE_RE.search(j.get("position", "")) and _open_to_india(j.get("location")):
            out.append(_job("remoteok", j.get("position"), j.get("company"), j.get("location") or "Remote",
                            j.get("url"), j.get("description", ""), j.get("date")))
    return out


def _himalayas():
    out = []
    for offset in (0, 100, 200):
        data = requests.get(f"https://himalayas.app/jobs/api?limit=100&offset={offset}", headers=UA, timeout=30).json()
        for j in data.get("jobs", []):
            if SOFTWARE_TITLE_RE.search(j.get("title", "")) and _open_to_india(j.get("locationRestrictions") or []):
                out.append(_job("himalayas", j.get("title"), j.get("companyName"),
                                j.get("locationRestrictions") or "Worldwide",
                                j.get("guid") or j.get("applicationLink"), j.get("description", ""), j.get("pubDate")))
    return out


def _jobicy():
    out = []
    for tag in ("react", "node", "javascript", "full-stack"):
        data = requests.get(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={tag}", headers=UA, timeout=30).json()
        for j in data.get("jobs", []):
            if SOFTWARE_TITLE_RE.search(j.get("jobTitle", "")) and _open_to_india(j.get("jobGeo")):
                out.append(_job("jobicy", j.get("jobTitle"), j.get("companyName"), j.get("jobGeo") or "Remote",
                                j.get("url"), j.get("jobDescription", ""), j.get("pubDate")))
    return out


def _arbeitnow():
    data = requests.get("https://www.arbeitnow.com/api/job-board-api", headers=UA, timeout=30).json()
    out = []
    for j in data.get("data", []):
        if j.get("remote") and SOFTWARE_TITLE_RE.search(j.get("title", "")) and _open_to_india(j.get("location")):
            out.append(_job("arbeitnow", j.get("title"), j.get("company_name"), "Remote",
                            j.get("url"), j.get("description", ""), j.get("created_at")))
    return out


def scrape_remote_boards():
    results, seen = [], set()
    for name, fn in (("RemoteOK", _remoteok), ("Himalayas", _himalayas), ("Jobicy", _jobicy), ("Arbeitnow", _arbeitnow)):
        try:
            jobs = [j for j in fn() if j["job_url"] and j["job_id"] not in seen]
            seen.update(j["job_id"] for j in jobs)
            results.extend(jobs)
            print(f"  [Remote] {name}: {len(jobs)} India-eligible software jobs")
        except Exception as e:
            print(f"  [Remote] {name}: error — {str(e)[:100]}")
    return results
