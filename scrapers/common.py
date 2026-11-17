"""Helpers shared by all scrapers."""
import hashlib
import time

import yaml


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def job_id_for(url: str) -> str:
    return hashlib.md5(str(url).encode()).hexdigest()


def keyword_shard(config=None, slot=None):
    """
    The keywords this run should search. Rotates through search.keywords in chunks of
    scraping.keywords_per_run, one chunk per 30-minute slot — so each run makes few
    requests (no LinkedIn 429s) while every keyword is still covered every few runs.
    """
    config = config or load_config()
    kws = list(config["search"]["keywords"])
    k = int(config["scraping"].get("keywords_per_run", 4))
    if k <= 0 or k >= len(kws):
        return kws
    if slot is None:
        slot = int(time.time() // 1800)
    n_chunks = (len(kws) + k - 1) // k
    i = slot % n_chunks
    return kws[i * k:(i + 1) * k]
