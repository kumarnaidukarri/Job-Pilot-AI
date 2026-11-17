"""
Company career-page scraper (Greenhouse / Lever / Ashby public JSON APIs).

Polls every company in data/ats_companies.csv (built by tools/discover_ats.py) and keeps
India-located software roles. This is the "get there first" source: companies publish here
before LinkedIn/Naukri mirrors appear, and the apply link goes straight to the company.
"""
import csv
import os
from concurrent.futures import ThreadPoolExecutor

from agent.exp_filter import SOFTWARE_TITLE_RE
from scrapers.ats_common import FETCHERS, is_india
from scrapers.common import job_id_for

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ats_companies.csv")
WORKERS = 64


def _companies():
    if not os.path.exists(CSV_PATH):
        print("  [ATS] data/ats_companies.csv missing — run tools/discover_ats.py")
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("ats") in FETCHERS]


def _one(company):
    ats, slug, name = company["ats"], company["slug"], company["name"]
    try:
        # Lever/Ashby include descriptions in the listing call. Greenhouse doesn't: rather than
        # re-downloading every board with ?content=true each run, the description is fetched
        # per job later (agent/description_filler.py) — only for NEW jobs that pass pre-filters.
        jobs = FETCHERS[ats](slug, with_content=(ats != "greenhouse"))
        keep = [j for j in jobs if is_india(j["location"], j.get("alt_location"))
                and SOFTWARE_TITLE_RE.search(j["title"] or "")]
    except Exception:
        return []
    out = []
    for j in keep:
        if not j.get("job_url"):
            continue
        out.append({
            "job_id":      job_id_for(j["job_url"]),
            "title":       j["title"].strip(),
            "company":     name,
            "location":    j["location"],
            "source":      f"{ats}",
            "ats":         ats,
            "ats_ref":     j.get("ref", ""),
            "job_url":     j["job_url"],
            "description": (j.get("description") or "")[:6000],
            "date_posted": j.get("date_posted", ""),
            "company_website": "",
        })
    return out


def scrape_ats():
    companies = _companies()
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for jobs in ex.map(_one, companies):
            results.extend(jobs)
    print(f"  ATS: {len(results)} India software jobs across {len(companies)} company boards")
    return results
