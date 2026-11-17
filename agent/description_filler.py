"""
Description filler — fetches missing job descriptions for jobs that already passed the
free pre-filters, right before AI scoring (so the AI judges the real requirements and the
experience regex gets a second chance to reject over-senior postings).

  * LinkedIn: public guest endpoint /jobs-guest/jobs/api/jobPosting/<id> (no login).
    jobspy no longer fetches these up front — that was the main source of 429 blocks.
  * Naukri: skipped — pages are bot-protected, but jobspy already gave us experience range,
    skills and salary, which the scorer uses instead.
  * Foundit: skipped — Akamai returns 403 to every automated fetch (site's explicit control).
  * Anything else: plain GET + largest text block.
"""
import random
import re
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
SKIP_SOURCES = {"naukri", "foundit"}
_LI_ID_RE = re.compile(r"(?:/view/|currentJobId=|-)(\d{8,12})(?:[/?]|$)")


def _clean(text):
    text = re.sub(r"[ \t\xa0]+", " ", text or "")
    return re.sub(r"\n\s*\n+", "\n", text).strip()[:6000]


def _linkedin(url):
    m = _LI_ID_RE.search(url)
    if not m:
        return "", False
    r = requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}",
                     headers=HEADERS, timeout=15)
    if r.status_code == 429:
        return "", True
    if r.status_code != 200:
        return "", False
    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    extra = []
    for li in soup.select(".description__job-criteria-item"):
        h, v = li.select_one("h3"), li.select_one("span")
        if h and v:
            extra.append(f"{h.get_text(strip=True)}: {v.get_text(strip=True)}")
    text = el.get_text("\n", strip=True) if el else ""
    return _clean(text + ("\n" + "; ".join(extra) if extra else "")), False


def _generic(url):
    r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
    if r.status_code != 200:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    el = (soup.select_one("#jobDescriptionText") or soup.select_one("[class*='job-description']")
          or soup.select_one("[class*='description']"))
    if not el:
        best = 0
        for tag in soup.find_all(["section", "article", "div"]):
            n = len(tag.get_text(" ", strip=True))
            if best < n < 8000:
                best, el = n, tag
    return _clean(el.get_text("\n", strip=True)) if el else ""


def fill_descriptions(jobs, limit=40):
    """Mutates jobs in place (adds 'description'); returns the number filled."""
    todo = [j for j in jobs if len(str(j.get("description") or "")) < 80
            and j.get("source") not in SKIP_SOURCES and str(j.get("job_url", "")).startswith("http")][:limit]
    filled, li_blocked = 0, False
    for j in todo:
        is_li = "linkedin.com" in j["job_url"]
        if is_li and li_blocked:
            continue
        try:
            if is_li:
                desc, blocked = _linkedin(j["job_url"])
                if blocked:
                    li_blocked = True
                    print("    [DescFill] LinkedIn rate-limited — remaining LinkedIn jobs scored on title only")
                    continue
            else:
                desc = _generic(j["job_url"])
        except Exception:
            desc = ""
        if len(desc) >= 80:
            j["description"] = desc
            filled += 1
        time.sleep(1.2 + random.random())
    if todo:
        print(f"  [DescFill] filled {filled}/{len(todo)} missing descriptions")
    return filled


# Backwards-compat for old callers (monitor / manual runs)
def run_filler(limit=50, rescore=True, dry_run=False):
    print("[DescFiller] run_filler() is superseded — descriptions are now filled inside the pipeline.")
    return []
