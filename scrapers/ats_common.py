"""
Shared helpers for the public ATS (Applicant Tracking System) job-board APIs.

Greenhouse, Lever and Ashby all expose a company's open jobs as public, no-auth JSON —
the same data that renders on the company's own careers page. Companies post there
FIRST; LinkedIn/Naukri copies usually appear hours to days later, so polling these is
how we get to a posting before the crowd.

Each fetch_* function returns a list of normalized dicts:
  {title, location, job_url, description, date_posted (YYYY-MM-DD or ""), remote (bool)}
"""
import html
import re
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}
TIMEOUT = 20

# Words that mark a location as India (or India-eligible remote). Matched case-insensitively
# as substrings of the location string.
INDIA_TERMS = (
    "india", "bengaluru", "bangalore", "hyderabad", "secunderabad", "pune", "chennai",
    "mumbai", "navi mumbai", "thane", "gurgaon", "gurugram", "noida", "delhi", "kolkata",
    "ahmedabad", "kochi", "cochin", "jaipur", "visakhapatnam", "vizag", "chandigarh",
    "coimbatore", "indore", "trivandrum", "thiruvananthapuram", "mysore", "mysuru",
    "vijayawada", "nagpur", "bhubaneswar", "lucknow", "mohali", "vadodara", "surat",
)
_INDIA_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in INDIA_TERMS) + r")\b", re.IGNORECASE)  # \b: "Indiana" is not India


def is_india(*locations) -> bool:
    return any(loc and _INDIA_RE.search(str(loc)) for loc in locations)


_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(s: str) -> str:
    s = html.unescape(str(s or ""))       # Greenhouse double-encodes its HTML
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def _iso_date(value) -> str:
    """Accepts ISO strings or epoch-milliseconds; returns YYYY-MM-DD or ''."""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        return str(value)[:10]
    except Exception:
        return ""


# ── Greenhouse ────────────────────────────────────────────────────────────────

def fetch_greenhouse(slug: str, with_content: bool = False) -> list:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    if with_content:
        url += "?content=true"
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    out = []
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        offices = ", ".join(o.get("name", "") for o in j.get("offices", []) or [])
        out.append({
            "title":       j.get("title", ""),
            "location":    loc or offices,
            "alt_location": offices,
            "job_url":     j.get("absolute_url", ""),
            "description": html_to_text(j.get("content", "")) if with_content else "",
            "date_posted": _iso_date(j.get("first_published") or j.get("updated_at")),
            "remote":      "remote" in (loc or "").lower(),
        })
    return out


# ── Lever ─────────────────────────────────────────────────────────────────────

def fetch_lever(slug: str, with_content: bool = False) -> list:
    r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    data = r.json()
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        cat = j.get("categories") or {}
        all_locs = cat.get("allLocations") or []
        loc = cat.get("location") or ", ".join(all_locs)
        desc = ""
        if with_content:
            parts = [j.get("descriptionPlain", "")]
            for lst in j.get("lists", []) or []:
                parts.append(lst.get("text", ""))
                parts.append(html_to_text(lst.get("content", "")))
            parts.append(j.get("additionalPlain", ""))
            desc = "\n".join(p for p in parts if p)
        out.append({
            "title":       j.get("text", ""),
            "location":    loc,
            "alt_location": ", ".join(all_locs) + " " + str(j.get("country", "")),
            "job_url":     j.get("hostedUrl", ""),
            "description": desc,
            "date_posted": _iso_date(j.get("createdAt")),
            "remote":      (j.get("workplaceType") or "").lower() == "remote",
        })
    return out


# ── Ashby ─────────────────────────────────────────────────────────────────────

def fetch_ashby(slug: str, with_content: bool = False) -> list:
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    out = []
    for j in r.json().get("jobs", []) or []:
        if j.get("isListed") is False:
            continue
        sec = ", ".join(s.get("location", "") for s in j.get("secondaryLocations", []) or [])
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        out.append({
            "title":       j.get("title", ""),
            "location":    j.get("location", ""),
            "alt_location": f"{sec} {country}",
            "job_url":     j.get("jobUrl", ""),
            "description": (j.get("descriptionPlain") or "") if with_content else "",
            "date_posted": _iso_date(j.get("publishedAt")),
            "remote":      bool(j.get("isRemote")),
        })
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}
