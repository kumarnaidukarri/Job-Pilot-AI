"""
Indian tech / startup job boards that Google Jobs aggregates but jobspy doesn't cover.

  * Instahyre — public JSON search API (/api/v1/job_search). Gives title, company, city and
    skill tags (no description); `years=` filters by experience server-side.
  * Cutshort  — public SEO listing pages (/jobs/<skill>-jobs-in-<city>); the job list is in the
    page's __NEXT_DATA__ JSON, with description, experience range, salary and posted date.
  * Wellfound — public role/location pages (/role/l/<role>/<city>); Apollo state in
    __NEXT_DATA__ has description, min experience, salary, posted timestamp.

Only logged-out public pages that robots.txt allows; no login, no bypassing of bot protection.
Each run requests a rotating slice of the page list (slot-sharded like the keywords) with a
pause between requests, so the load per site stays at a handful of requests per run.
"""
import json
import re
import time
from datetime import datetime, timezone

import requests

from agent.exp_filter import SOFTWARE_TITLE_RE
from scrapers.ats_common import html_to_text
from scrapers.common import job_id_for, load_config

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9",
}
PAUSE = 1.5
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# config location -> slug used by each site
_CITY = {
    "hyderabad": "hyderabad", "bangalore": "bangalore", "bengaluru": "bangalore", "pune": "pune",
    "chennai": "chennai", "visakhapatnam": "visakhapatnam", "remote": "remote",
}
_CITY_WORDS = {
    "hyderabad": ("hyderabad", "secunderabad"), "bangalore": ("bangalore", "bengaluru"),
    "pune": ("pune",), "chennai": ("chennai",), "visakhapatnam": ("visakhapatnam", "vizag"),
    "remote": ("remote", "work from home", "wfh", "anywhere"),
}


def _cities():
    locs = [str(l).lower() for l in load_config()["search"]["locations"]]
    return [_CITY[l] for l in locs if l in _CITY]


def _wanted_location(text):
    t = str(text or "").lower()
    return any(w in t for c in _cities() for w in _CITY_WORDS.get(c, (c,)))


def _shard(items, per_run):
    """Rotate through `items`, `per_run` at a time, one slice per 30-minute slot."""
    if len(items) <= per_run:
        return items
    n = (len(items) + per_run - 1) // per_run
    i = int(time.time() // 1800) % n
    return items[i * per_run:(i + 1) * per_run]


def _get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=25, **kw)
    time.sleep(PAUSE)
    return r


def _next_data(html):
    m = _NEXT_RE.search(html or "")
    return json.loads(m.group(1)) if m else None


def _job(source, title, company, location, url, desc="", date="", exp_range="", skills=None, salary=""):
    return {
        "job_id": job_id_for(url), "title": (title or "").strip(), "company": (company or "").strip(),
        "location": location, "source": source, "job_url": url,
        "description": html_to_text(desc)[:6000] if desc else "",
        "date_posted": date, "exp_range": exp_range, "skills": skills or None, "salary": salary or None,
        "company_website": "",
    }


# ── Instahyre ────────────────────────────────────────────────────────────────

INSTAHYRE_SKILLS = ["react", "node.js", "mern", "javascript", "full stack", "express", "next.js", "mongodb"]


def scrape_instahyre(pages=2):
    years = int(load_config()["matching"].get("max_experience_years", 4)) - 1   # Kumar ~3 yrs
    out, seen = [], set()
    for skill in _shard(INSTAHYRE_SKILLS, 3):
        for page in range(pages):
            try:
                r = _get("https://www.instahyre.com/api/v1/job_search",
                         params={"skills": skill, "years": years, "limit": 35, "offset": page * 35})
                objs = r.json().get("objects", []) if r.status_code == 200 else []
            except Exception as e:
                print(f"  [Instahyre] {skill}: {e}")
                break
            for o in objs:
                url = o.get("public_url")
                if not url or o["id"] in seen:
                    continue
                seen.add(o["id"])
                if not (SOFTWARE_TITLE_RE.search(o.get("title", "")) and _wanted_location(o.get("locations"))):
                    continue
                out.append(_job("instahyre", o.get("title"), (o.get("employer") or {}).get("company_name"),
                                o.get("locations", ""), url, skills=", ".join(o.get("keywords") or [])[:300],
                                exp_range=f"fits {years} yrs (Instahyre filter)"))
            if len(objs) < 35:
                break
    print(f"  Instahyre: {len(out)} jobs")
    return out


# ── Cutshort ─────────────────────────────────────────────────────────────────

CUTSHORT_SKILLS = ["mern-stack", "reactjs", "nodejs", "javascript"]
_CUTSHORT_CITY = {"bangalore": "bangalore-bengaluru"}


def _cutshort_jobs(html):
    d = _next_data(html)
    if not d:
        return []
    pp = (d.get("props") or {}).get("pageProps") or {}
    for q in (pp.get("dehydratedState") or {}).get("queries") or []:
        try:
            jobs = q["state"]["data"]["data"]["pageData"]["jobs"]
            if isinstance(jobs, list):
                return jobs
        except (KeyError, TypeError):
            continue
    return []


def scrape_cutshort():
    pages = [f"{s}-jobs-in-{_CUTSHORT_CITY.get(c, c)}" for c in _cities() if c not in ("remote", "visakhapatnam") for s in CUTSHORT_SKILLS]
    pages += [f"remote-{s}-jobs" for s in CUTSHORT_SKILLS] if "remote" in _cities() else []
    out, seen = [], set()
    for slug in _shard(pages, 6):
        try:
            r = _get(f"https://cutshort.io/jobs/{slug}")
            jobs = _cutshort_jobs(r.text) if r.status_code == 200 else []
        except Exception as e:
            print(f"  [Cutshort] {slug}: {e}")
            continue
        for j in jobs:
            url = j.get("publicUrl")
            if not url or url in seen or not SOFTWARE_TITLE_RE.search(j.get("headline", "")):
                continue
            seen.add(url)
            exp = j.get("expRange") or {}
            facts = j.get("jobFactSummary") or {}
            locs = ", ".join(j.get("locations") or []) or ("Remote" if "remote" in str(j.get("remoteType")) else "")
            out.append(_job(
                "cutshort", j.get("headline"), (j.get("companyDetails") or {}).get("name") or facts.get("companyName"),
                locs, url, desc=j.get("sanitizedComment") or "", date=str(facts.get("postedDate") or "")[:10],
                exp_range=f"{exp['min']}-{exp.get('max', '')} years" if exp.get("min") is not None else facts.get("experience", ""),
                skills=", ".join(j.get("allSkills") or [])[:300], salary=facts.get("salary") or j.get("salaryRangeText")))
    print(f"  Cutshort: {len(out)} jobs")
    return out


# ── Wellfound ────────────────────────────────────────────────────────────────

WELLFOUND_ROLES = ["full-stack-engineer", "frontend-engineer", "backend-engineer", "software-engineer"]


def _wellfound_jobs(html):
    d = _next_data(html)
    data = (d or {}).get("props", {}).get("pageProps", {}).get("apolloState", {}).get("data", {})
    company_of = {}
    for k, v in data.items():
        if k.startswith("StartupResult"):
            for ref in v.get("highlightedJobListings") or []:
                company_of[ref.get("__ref")] = v.get("name", "")
    return [(v, company_of.get(k, "")) for k, v in data.items() if k.startswith("JobListingSearchResult")]


def scrape_wellfound():
    cities = [("bangalore" if c == "bangalore" else c) for c in _cities() if c not in ("remote", "visakhapatnam")]
    pages = [f"role/l/{r}/{c}" for r in WELLFOUND_ROLES for c in cities]
    if "remote" in _cities():
        pages += [f"role/l/{r}/india" for r in WELLFOUND_ROLES]
    out, seen = [], set()
    for path in _shard(pages, 5):
        try:
            r = _get(f"https://wellfound.com/{path}")
            jobs = _wellfound_jobs(r.text) if r.status_code == 200 else []
            if r.status_code != 200:
                print(f"  [Wellfound] {path}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  [Wellfound] {path}: {e}")
            continue
        for v, company in jobs:
            jid = v.get("id")
            if not jid or jid in seen or not SOFTWARE_TITLE_RE.search(v.get("title", "")):
                continue
            seen.add(jid)
            locs = ", ".join(v.get("locationNames") or []) + (" (remote)" if v.get("remote") else "")
            if not (_wanted_location(locs) or v.get("remote")):
                continue
            ts = v.get("liveStartAt")
            date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d") if ts else ""
            mn, mx = v.get("yearsExperienceMin"), v.get("yearsExperienceMax")
            exp = f"{mn}-{mx} years" if mn is not None and mx else (f"{mn}+ years" if mn is not None else "")
            out.append(_job("wellfound", v.get("title"), company, locs,
                            f"https://wellfound.com/jobs/{jid}-{v.get('slug') or 'job'}",
                            desc=v.get("description") or "", date=date, exp_range=exp,
                            salary=v.get("compensation")))
    print(f"  Wellfound: {len(out)} jobs")
    return out
