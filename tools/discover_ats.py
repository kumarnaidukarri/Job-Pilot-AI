"""
One-off / weekly discovery: find which companies on Greenhouse, Lever and Ashby are
currently hiring in India, and save them to data/ats_companies.csv.

Seed list: the MIT-licensed company inventory at github.com/kalil0321/ats-scrapers
(ats-companies/*.csv, ~14k companies). Each company's public board is checked ONCE;
only those with at least one India-located posting are kept, so the 30-minute pipeline
polls a few hundred boards instead of 14k.

Run:  python tools/discover_ats.py            (takes a few minutes, ~40 parallel requests)
"""
import csv
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.ats_common import FETCHERS, is_india

SEED_URL = "https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/{ats}.csv"
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ats_companies.csv")
WORKERS = 40


def load_seed(ats: str) -> list:
    r = requests.get(SEED_URL.format(ats=ats), timeout=60)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    return [(ats, row.get("name", "").strip(), row.get("slug", "").strip()) for row in rows if row.get("slug")]


def load_existing() -> dict:
    if not os.path.exists(OUT_PATH):
        return {}
    with open(OUT_PATH, newline="", encoding="utf-8") as f:
        return {(r["ats"], r["slug"]): r for r in csv.DictReader(f)}


def probe(item):
    ats, name, slug = item
    for attempt in range(2):
        try:
            jobs = FETCHERS[ats](slug)
            india = [j for j in jobs if is_india(j["location"], j.get("alt_location"))]
            return ats, name, slug, len(jobs), len(india)
        except Exception:
            time.sleep(1)
    return ats, name, slug, -1, 0


def main():
    seeds = []
    for ats in FETCHERS:
        try:
            s = load_seed(ats)
            print(f"{ats}: {len(s)} companies in seed list")
            seeds += s
        except Exception as e:
            print(f"{ats}: seed download failed — {e}")

    # Always re-check companies already on our list too (they may be missing from the seed).
    existing = load_existing()
    seen = {(a, s) for a, _, s in seeds}
    for (a, s), row in existing.items():
        if (a, s) not in seen:
            seeds.append((a, row["name"], s))

    keep, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(probe, it) for it in seeds]
        for fut in as_completed(futures):
            ats, name, slug, total, india = fut.result()
            done += 1
            if india > 0:
                keep.append({"ats": ats, "name": name, "slug": slug,
                             "india_jobs": india, "total_jobs": total})
            if done % 1000 == 0:
                print(f"  {done}/{len(seeds)} checked, {len(keep)} hiring in India ({time.time()-t0:.0f}s)")

    keep.sort(key=lambda r: (-r["india_jobs"], r["name"].lower()))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ats", "name", "slug", "india_jobs", "total_jobs"])
        w.writeheader()
        w.writerows(keep)
    print(f"\nSaved {len(keep)} India-hiring companies to {OUT_PATH} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
