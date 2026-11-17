"""
JobPilot AI pipeline — one run = scrape -> filter -> score -> apply kit -> alert.

Triggered every 30 min by GitHub Actions (.github/workflows/pipeline.yml), or locally with
`python scheduler.py` (APScheduler loop, e.g. on the spare laptop).
"""
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_log.txt")
LOCK_MINUTES = 40


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def _plog(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _rotate_log():
    try:
        if os.path.exists(_LOG_PATH):
            with open(_LOG_PATH, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > 5000:
                with open(_LOG_PATH, "w", encoding="utf-8") as f:
                    f.writelines(lines[-2000:])
    except Exception:
        pass


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _fresh(job, cutoff):
    dp = str(job.get("date_posted") or "").strip()
    try:
        return datetime.strptime(dp[:10], "%Y-%m-%d") >= cutoff
    except Exception:
        return True   # unknown date -> keep


def _scrape_all(config):
    from concurrent.futures import ThreadPoolExecutor
    from scrapers.ats import scrape_ats
    from scrapers.foundit import scrape_foundit
    from scrapers.india_boards import scrape_cutshort, scrape_instahyre, scrape_wellfound
    from scrapers.jobspy_boards import scrape_jobspy
    from scrapers.remote_boards import scrape_remote_boards
    from scrapers.timesjobs import scrape_timesjobs

    registry = {"jobspy": scrape_jobspy, "ats": scrape_ats, "remote_boards": scrape_remote_boards,
                "foundit": scrape_foundit, "timesjobs": scrape_timesjobs,
                "instahyre": scrape_instahyre, "cutshort": scrape_cutshort, "wellfound": scrape_wellfound}
    enabled = [s for s in config["scraping"].get("sources", registry) if s in registry]
    counts, out = {}, []

    def run(name):
        t = time.time()
        try:
            jobs = registry[name]() or []
        except Exception as e:
            print(f"  {name}: ERROR — {e}")
            jobs = []
        counts[name] = (len(jobs), round(time.time() - t))
        return jobs

    with ThreadPoolExecutor(max_workers=len(enabled)) as ex:
        for jobs in ex.map(run, enabled):
            out.extend(jobs)
    return out, counts


def run_pipeline():
    from agent import llm
    from agent.contacts import hr_email_for
    from agent.exp_filter import has_experience_requirement, prefilter, _EXP_FLOOR
    from agent.resume_matcher import load_resume, reset_llm_state, score_jobs_batch
    from storage.database import (
        check_score_cache, count_pending, existing_job_ids, get_pending_jobs, get_unalerted_matches,
        init_db, insert_job, kv_get, kv_set, mark_notified, save_run, save_score, update_job_fields,
        was_recently_notified,
    )

    t0 = time.time()
    config = load_config()
    m = config["matching"]
    threshold, alert_threshold = int(m["relevance_threshold"]), int(m.get("alert_threshold", 8))
    _rotate_log()
    init_db()

    # ── Run lock: laptop + GitHub Actions must never run concurrently ──────────
    lock = kv_get("run_lock")
    if lock and datetime.fromisoformat(lock) > datetime.now() - timedelta(minutes=LOCK_MINUTES):
        _plog(f"Another run started at {lock} is still active — skipping this run.")
        return
    kv_set("run_lock", datetime.now().isoformat())

    stats = {"started": datetime.now().isoformat(), "host": "github-actions" if os.getenv("GITHUB_ACTIONS") else "local"}
    try:
        _plog("=" * 60)
        _plog("  JobPilot AI — run starting")
        _plog("=" * 60)

        print("\n[1/6] Resume, LLM state, tracker commands")
        load_resume(config["resume"]["path"])
        reset_llm_state()
        try:
            from agent.telegram_notify import process_commands
            n = process_commands()
            if n:
                _plog(f"  Telegram: processed {n} tracker command(s)")
        except Exception as e:
            print(f"  Telegram commands: {e}")

        # ── 2. Scrape ─────────────────────────────────────────────────────────
        print("\n[2/6] Scraping all sources in parallel...")
        all_jobs, counts = _scrape_all(config)
        for name, (n, secs) in counts.items():
            _plog(f"  Source [{name}]: {n} jobs ({secs}s)")
        stats["sources"] = {k: v[0] for k, v in counts.items()}

        seen, unique = set(), []
        for j in all_jobs:
            if not j.get("job_id") or not j.get("title"):
                continue
            key = f"{_norm(j.get('company'))}|{_norm(j.get('title'))}|{_norm(str(j.get('location'))[:25])}"
            if j["job_id"] in seen or key in seen:
                continue
            seen.update((j["job_id"], key))
            unique.append(j)

        known = existing_job_ids([j["job_id"] for j in unique])
        cutoff = datetime.now() - timedelta(days=int(config["scraping"].get("max_age_days", 14)))
        new_jobs = [j for j in unique if j["job_id"] not in known and _fresh(j, cutoff)]
        _plog(f"  Scraped {len(all_jobs)} | unique {len(unique)} | new {len(new_jobs)}")
        stats.update(scraped=len(all_jobs), unique=len(unique), new=len(new_jobs))

        # ── 3. Free pre-filters ───────────────────────────────────────────────
        print(f"\n[3/6] Pre-filtering {len(new_jobs)} new jobs (no AI)...")
        settled = queued = 0
        for j in new_jobs:
            text = " ".join(str(j.get(k) or "") for k in ("exp_range", "description"))
            verdict = prefilter(j.get("title", ""), text, require_software_title=True)
            if verdict:
                j["relevance_score"], j["match_reason"], j["experience_required"] = verdict
                settled += 1
            else:
                j["relevance_score"] = None    # -> pending queue
                queued += 1
            j["hr_email"] = hr_email_for(j)
            insert_job(j)
        _plog(f"  Pre-filter settled {settled}, queued {queued} for AI")
        stats.update(prefiltered=settled, queued=queued)

        # ── 4. Score the queue (new + backlog) ────────────────────────────────
        print("\n[4/6] Scoring pending queue...")
        from agent.description_filler import fill_descriptions
        pending = get_pending_jobs(limit=int(m.get("max_ai_jobs_per_run", 150)),
                                   max_age_days=int(config["scraping"].get("max_age_days", 14)))
        before = {j["job_id"]: j.get("description") for j in pending}
        fill_descriptions(pending, limit=80)
        to_score, cache_hits, late_rejects = [], 0, 0
        for j in pending:
            if j.get("description") != before.get(j["job_id"]):
                j["hr_email"] = hr_email_for(j)
                update_job_fields(j["job_id"], {"description": j["description"], "hr_email": j["hr_email"]})
                if has_experience_requirement(j["description"]):
                    save_score(j["job_id"], {"score": 2, "reason": f"Requires {_EXP_FLOOR}+ years (full description)",
                                             "exp": f"{_EXP_FLOOR}+ years"}, "regex")
                    late_rejects += 1
                    continue
            cached = check_score_cache(j.get("title", ""), j.get("company", ""))
            if cached:
                save_score(j["job_id"], cached, "cache")
                cache_hits += 1
                continue
            to_score.append(j)

        scored = failed = 0
        if to_score:
            print(f"  AI scoring {len(to_score)} jobs...")
            for j, r in zip(to_score, score_jobs_batch(to_score)):
                if r["score"] <= 0:
                    failed += 1       # stays pending; retried next run
                    continue
                save_score(j["job_id"], r, r["scored_by"])
                scored += 1
        pend_left = count_pending()
        _plog(f"  Cache {cache_hits} | late exp-rejects {late_rejects} | AI scored {scored} | "
              f"AI failed {failed} | pending backlog {pend_left}")
        stats.update(cache_hits=cache_hits, ai_scored=scored, ai_failed=failed, pending=pend_left,
                     llm_usage=llm.usage_summary())

        # ── 5. Matches -> apply kits ─────────────────────────────────────────
        print("\n[5/6] Collecting matches...")
        matches = get_unalerted_matches(threshold, since_hours=72)
        fresh_matches, dupes = [], []
        for j in matches:
            (dupes if was_recently_notified(j.get("title", ""), j.get("company", "")) else fresh_matches).append(j)
        if dupes:
            mark_notified([j["job_id"] for j in dupes])   # same posting already sent via another source
        top = [j for j in fresh_matches if int(j.get("relevance_score") or 0) >= alert_threshold]
        _plog(f"  Matches: {len(fresh_matches)} new (>= {threshold}), {len(top)} top (>= {alert_threshold}), "
              f"{len(dupes)} cross-source duplicates suppressed")
        stats.update(matches=len(fresh_matches), top=len(top))

        if top:
            from agent.apply_kit import build_apply_kits
            from agent.notify_email import sort_jobs
            top = sort_jobs(top)[:15]
            build_apply_kits(top)
            for j in top:
                update_job_fields(j["job_id"], {"apply_kit": j["apply_kit"]})
            kits = {j["job_id"]: j["apply_kit"] for j in top}
            for j in fresh_matches:
                if j["job_id"] in kits:
                    j["apply_kit"] = kits[j["job_id"]]

        # ── 6. Notify ─────────────────────────────────────────────────────────
        print("\n[6/6] Notifying...")
        sent_ids = set()
        if fresh_matches:
            from agent import telegram_notify
            from agent.notify_email import send_report, sort_jobs
            if telegram_notify.enabled() and top:
                if telegram_notify.send_job_alerts(
                        sort_jobs(top), header=f"🚀 <b>{len(top)} top match{'es' if len(top) > 1 else ''}</b> — apply early!"):
                    sent_ids.update(j["job_id"] for j in top)
                    _plog(f"  Telegram: sent {len(top)} alerts")
            recipient = os.getenv("NOTIFY_EMAIL") or config.get("notify", {}).get("recipient_email") or None
            stats_line = (f"Run {datetime.now():%d %b %H:%M} · scraped {len(all_jobs)} · new {len(new_jobs)} · "
                          f"AI-scored {scored} · backlog {pend_left}")
            if send_report(fresh_matches, recipient=recipient, tag="[APPLY NOW]", stats_line=stats_line):
                sent_ids.update(j["job_id"] for j in fresh_matches)
                _plog(f"  Email: sent {len(fresh_matches)} matches to NOTIFY_EMAIL")
            else:
                _plog("  Email: not sent")
            if sent_ids:
                mark_notified(list(sent_ids))
        else:
            _plog("  No new matches this run.")
        stats["notified"] = len(sent_ids)

        if not os.getenv("GITHUB_ACTIONS"):
            try:
                from storage.excel_export import export_to_excel
                export_to_excel(config["storage"]["excel_path"], threshold)
            except Exception as e:
                print(f"  Excel export skipped: {e}")
    except Exception as e:
        stats["error"] = f"{type(e).__name__}: {e}"
        _plog(f"  RUN ERROR: {stats['error']}")
        raise
    finally:
        stats["seconds"] = round(time.time() - t0)
        try:
            save_run(stats)
            kv_set("run_lock", "2000-01-01T00:00:00")
        except Exception:
            pass
        _plog(f"  Run finished in {stats['seconds']}s")


def start_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    hours = load_config()["scraping"]["interval_hours"]
    sched = BlockingScheduler()
    sched.add_job(run_pipeline, trigger=IntervalTrigger(hours=hours), id="jobpilot", max_instances=1, coalesce=True)
    print(f"Scheduler running — every {hours} h. Ctrl+C to stop.\n")
    try:
        run_pipeline()
    except Exception as e:
        print(f"First run failed: {e}")
    try:
        sched.start()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    start_scheduler()
