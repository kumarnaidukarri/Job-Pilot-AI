# JobPilot AI (Kumar) — Claude Context

## Who / what
Ashok (project owner) runs this for his brother **Kumar Naidu Karri** — Full Stack Developer
(MERN + PostgreSQL, ~3 yrs, React/Redux/Node/Express/MongoDB/PostgreSQL + Python/LLM automation),
Hyderabad, immediate joiner. Resume: `resume/Kumar_Naidu_Karri_Full_Stack_Developer.{html,pdf}`
(gitignored — contains phone/email; the pipeline loads resume text from MongoDB).

Autonomous, zero-cost job-hunting agent: every 30 min it scrapes ~6 source types, filters for
free, AI-scores what's left against Kumar's profile using a chain of free LLMs, builds an
"apply kit" for top matches, and alerts via Telegram (instant) + Gmail (digest + Excel).
It NEVER applies or messages anyone on Kumar's behalf — it prepares; a human submits.

Codebase was cloned from a finance-job pipeline built for someone else; all of that person's
data was removed. Never reintroduce it.

## Architecture
```
scheduler.py  run_pipeline()   (GitHub Actions every 30 min, or `python scheduler.py` on a laptop)
  1 resume (local PDF or MongoDB) · reset LLM state · Telegram tracker commands
  2 scrapers (parallel):
      jobspy_boards.py   LinkedIn, Indeed, Naukri, Glassdoor via python-jobspy (keyword SHARD/run)
      ats.py             Greenhouse/Lever/Ashby public JSON for ~1.2k India-hiring companies
                         (data/ats_companies.csv, rebuilt weekly by tools/discover_ats.py)
      remote_boards.py   RemoteOK, Himalayas, Jobicy, Arbeitnow (India-eligible remote only)
      foundit.py, timesjobs.py
  3 dedup (job_id + company|title|location) · drop known job_ids (1 query) · drop >14 days
    exp_filter.prefilter() → settle obvious non-fits for free (score 1-2); rest inserted PENDING
  4 pending queue (new + backlog, newest first, max_ai_jobs_per_run)
      description_filler.fill_descriptions() — LinkedIn guest API etc., only for survivors
      exp regex again on fetched text · 7-day title+company score cache · batch AI scoring
      AI failure ⇒ job stays pending (needs_score=True) and is retried next run — never lost
  5 unalerted matches (≥ relevance_threshold) minus cross-source dupes already notified
    top (≥ alert_threshold) → apply_kit.build_apply_kits(): pitch, JD keywords, what to lead with,
    recruiter people-search link
  6 Telegram alerts (top) + Gmail HTML digest + Excel (all matches) → mark_notified
```

## Key modules
- `agent/llm.py` — provider chain from `config.yaml matching.providers`. Provider skipped if its
  key env var is unset. 429-per-minute → wait+retry; daily quota / 401 / 404-retired → cooldown
  persisted in Mongo `llm_state` (`quota_cooldown_hours`); bad JSON → next provider for that batch
  only. OpenAI-compatible path (Groq, Cerebras, OpenRouter, Mistral, NVIDIA, Cloudflare, Ollama) +
  native Gemini path. gpt-oss on Groq uses `reasoning_effort: low` (saves tokens).
- `agent/resume_matcher.py` — prompt = compact `candidate:` profile from config (not the full
  resume — free tiers are capped by TOKENS/day) + `_smart_trim()`ed job text (keeps requirement
  lines, not head/tail). Results matched by id; validator enforces all ids, scores 1-10.
- `agent/exp_filter.py` — single source of truth for regex filters. `prefilter()` returns
  (score, reason, tag) or None. Hard-senior titles (lead/principal/staff/manager/SDE-III) rejected;
  "Senior"/"Sr." only kept when a reachable min-experience is stated. `_EXP_FLOOR` =
  `max_experience_years + 1` (currently 5 → "5+ yrs" rejected). IRRELEVANT covers non-eng
  functions AND non-JS primary stacks (Java, .NET, PHP, Go, mobile native, DevOps, data, QA...).
- `agent/apply_kit.py`, `agent/telegram_notify.py` (alerts + `/applied <id>` tracker commands,
  processed each run via getUpdates — no server), `agent/notify_email.py`.
- `storage/database.py` — MongoDB Atlas only (no SQLite, ever). Collections: `jobs`, `companies`,
  `llm_state`, `assets` (resume PDF+text), `runs` (per-run stats), `kv` (run lock, telegram offset).
  `jobs.status`: new → emailed → applied/skipped/interview/offer/rejected.
- `tools/discover_ats.py` (weekly), `tools/upload_resume.py`, `tools/telegram_setup.py`.

## Verified facts (Nov 2026) — re-check before "fixing"
- jobspy 1.4.1: LinkedIn ✔ (429s fast if descriptions fetched in bulk — hence filler), Indeed ✔,
  Naukri ✔ again (no description, but experience_range/skills/salary), Glassdoor ✔ (few),
  Google ✘ (returns nothing — not used).
- Groq free limits are per model: ~1K req/day and **~200K tokens/day** (not 14,400 RPD).
  Active on our key: gpt-oss-120b, qwen/qwen3.8-27b, gpt-oss-20b. llama-3.3-70b NOT available.
- Gemini: 3.5-flash-lite, 3.1-flash-lite, 3.6-flash, gemma-4-31b work; 3.7/3.8-flash and
  gemma-4-26b exist but often 503 (overloaded); 2.5-flash-lite retired (404).
- GitHub Models retired (Jul 2026); Cerebras is trial-credit only.
- Foundit: search API works, job pages 403 (Akamai) — don't try to bypass.
- Scheduled workflows auto-disable after 60 days of no repo activity — pipeline.yml re-enables
  itself each run and discover.yml commits weekly.

## Security constraints (permanent)
1. Never auto-apply or auto-message recruiters — only notifications to Kumar/Ashok.
2. Never commit/push unless Ashok explicitly asks. Never push --force to main.
3. Never print secrets (.env / GitHub secrets: MONGODB_URI, *_API_KEY, GMAIL_APP_PASSWORD, TELEGRAM_*).
4. No phone/email/resume files in tracked files — `resume/*.pdf|html` are gitignored; recipient
   is the `NOTIFY_EMAIL` secret. Actions logs must not print addresses.
5. No LinkedIn login automation / Easy-Apply bots (account-ban risk + ToS).

## Run
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -c "from scheduler import run_pipeline; run_pipeline()"   # one run
.venv/bin/python scheduler.py                                              # loop (laptop)
.venv/bin/python tools/discover_ats.py                                     # refresh companies
.venv/bin/python tools/upload_resume.py resume/<file>.pdf                  # after resume edits
.venv/bin/python tools/telegram_setup.py [--test]
```
GitHub secrets: see `.env.example` (only MONGODB_URI, GROQ/GEMINI keys, GMAIL_* required).
MongoDB Atlas Network Access must allow 0.0.0.0/0 for GitHub Actions runners.
