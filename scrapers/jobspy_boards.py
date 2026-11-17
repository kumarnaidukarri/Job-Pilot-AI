"""
LinkedIn / Indeed / Naukri / Glassdoor via python-jobspy.

- Searches only this run's keyword SHARD (scrapers.common.keyword_shard) x all locations,
  keeping request volume low enough that LinkedIn doesn't 429 us.
- LinkedIn descriptions are NOT fetched here (that's 1 extra request per job and the main
  429 trigger); agent/description_filler.py fetches them later, only for jobs that survive
  the pre-filters.
- Naukri returns no description but does return experience range, skills and salary — those
  go into dedicated fields that the pre-filter and the AI scorer both use.
"""
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor

from scrapers.common import job_id_for, keyword_shard, load_config

warnings.filterwarnings("ignore")
MAX_WORKERS = 4


def _s(v):
    s = str(v if v is not None else "").strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def _salary(row):
    lo, hi, cur = row.get("min_amount"), row.get("max_amount"), _s(row.get("currency"))
    try:
        lo, hi = float(lo), float(hi)
    except Exception:
        return ""
    if lo != lo or hi != hi:   # NaN
        return ""
    if cur == "INR":
        return f"₹{lo / 1e5:.1f}–{hi / 1e5:.1f} LPA"
    return f"{cur} {lo:,.0f}–{hi:,.0f}"


def scrape_jobspy():
    from jobspy import scrape_jobs

    config   = load_config()
    sites    = config["scraping"].get("jobspy_sites", ["linkedin", "indeed", "naukri"])
    keywords = keyword_shard(config)
    locs     = config["search"]["locations"]
    results  = int(config["search"].get("results_per_keyword", 30))
    hours    = int(config["scraping"].get("hours_old", 24))
    delay    = float(config["scraping"].get("delay_between_requests", 2))
    print(f"  [JobSpy] keywords this run: {keywords}")

    found, seen, lock = [], set(), threading.Lock()
    counts = {}

    def one(combo):
        kw, loc = combo
        remote = loc.lower() == "remote"
        use_sites = [s for s in sites if not (remote and s == "glassdoor")]
        try:
            df = scrape_jobs(
                site_name=use_sites, search_term=kw,
                location="India" if remote else f"{loc}, India",
                is_remote=remote, results_wanted=results, hours_old=hours,
                country_indeed="India", linkedin_fetch_description=False,
                description_format="markdown", verbose=0,
            )
        except Exception as e:
            print(f"    [JobSpy] {kw} | {loc}: {str(e)[:80]}")
            return
        if df is None or df.empty:
            time.sleep(delay)
            return
        for _, row in df.iterrows():
            url = _s(row.get("job_url"))
            title, company = _s(row.get("title")), _s(row.get("company"))
            if not url or not title:
                continue
            key = f"{company.lower()}|{title.lower()}|{_s(row.get('location')).lower()}"
            with lock:
                if url in seen or key in seen:
                    continue
                seen.update((url, key))
                site = _s(row.get("site")) or "jobspy"
                counts[site] = counts.get(site, 0) + 1
                found.append({
                    "job_id":      job_id_for(url),
                    "title":       title,
                    "company":     company,
                    "location":    _s(row.get("location")) or loc,
                    "source":      site,
                    "job_url":     url,
                    "description": _s(row.get("description"))[:6000],
                    "date_posted": _s(row.get("date_posted"))[:10],
                    "company_website": _s(row.get("company_url_direct")) or _s(row.get("company_url")),
                    "exp_range":   _s(row.get("experience_range")),
                    "skills":      _s(row.get("skills")),
                    "salary":      _salary(row),
                })
        time.sleep(delay)

    combos = [(k, l) for k in keywords for l in locs]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(one, combos))
    print(f"  [JobSpy] {len(found)} unique jobs — " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return found
