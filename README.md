# JobPilot AI — autonomous job-discovery agent

An AI agent that runs 24/7 at **zero cost**, finds fresh software-engineering openings across job
boards and company career pages, scores each one against a candidate profile with a chain of free
LLMs, and pushes the best matches to the phone within minutes, with a ready-to-send pitch.

```
GitHub Actions (every 30 min) ──► scrape 6 source types in parallel
                                   ├─ LinkedIn · Indeed · Naukri · Glassdoor   (python-jobspy)
                                   ├─ ~1,000 company career pages              (Greenhouse/Lever/Ashby JSON)
                                   ├─ RemoteOK · Himalayas · Jobicy · Arbeitnow (India-eligible remote)
                                   └─ Foundit · TimesJobs
                              ──► dedupe + free regex pre-filters (stack, seniority, experience)
                              ──► batch LLM scoring with a 13-provider free fallback chain
                              ──► apply kit for top matches (pitch, JD keywords, recruiter search)
                              ──► Telegram instant alerts + Gmail digest (HTML + Excel)
MongoDB Atlas: jobs · companies · run stats · LLM quota state · resume asset · tracker status
```

## Why it's built this way
- **Be first:** company career pages (ATS APIs) publish before LinkedIn/Naukri mirrors; a weekly
  job probes ~14k company boards and keeps the ~1,000 currently hiring in India.
- **Free LLMs are capped by tokens/day, not requests:** compact candidate profile instead of the
  whole resume, requirement-aware description trimming, 6 jobs per call, 7-day score cache, and
  `reasoning_effort: low` on reasoning models.
- **Never lose a job to a quota:** if every provider is exhausted, jobs stay in a pending queue
  and are scored on the next run; quota cooldowns persist across runs so dead providers aren't retried.
- **Polite scraping:** keyword sharding across runs, LinkedIn descriptions fetched only for jobs
  that survive the free filters, no login automation, no auto-apply.
- **Application tracker in Telegram:** `/applied <id>`, `/interview <id>`, `/stats`.

## Setup
1. `python -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. Copy `.env.example` → `.env` and fill the required keys (MongoDB Atlas free M0, Groq, Gemini, Gmail app password).
3. `python tools/upload_resume.py path/to/resume.pdf`  (resume lives in MongoDB, never in git)
4. Edit `config.yaml` → `candidate:` profile, keywords, locations.
5. Optional: `python tools/telegram_setup.py` for instant alerts.
6. Add the same values as GitHub Actions secrets; the workflow runs every 30 minutes.
   Or run on any Linux box: `bash deploy/laptop/install.sh`.

Stack: Python · MongoDB Atlas (PyMongo) · Groq / Gemini / OpenAI-compatible LLM APIs · python-jobspy ·
BeautifulSoup · GitHub Actions · Telegram Bot API · openpyxl.
