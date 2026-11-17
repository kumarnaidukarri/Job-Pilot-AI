"""
AI job scorer — compares each job against the candidate profile in config.yaml.

Public API (used by scheduler.py / description_filler.py):
  load_resume(path=None)       -> resume text (local PDF if present, else MongoDB asset)
  get_resume_text()            -> cached text
  reset_llm_state()
  score_jobs_batch(jobs, batch_size=None) -> list[dict] aligned with `jobs`
        each: {score, reason, exp, skills, gaps, scored_by}; score=0 => every provider failed
  score_job(job)               -> single-job convenience wrapper
"""
import os
import re

import pdfplumber
import yaml

from agent import llm

_resume_text = None


def _config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


# ── Resume ────────────────────────────────────────────────────────────────────

def load_resume(path=None):
    """Prefer the local PDF; fall back to the copy stored in MongoDB (GitHub Actions has no
    resume file — it's deliberately kept out of the repo because it holds contact details)."""
    global _resume_text
    path = path or _config()["resume"]["path"]
    text = ""
    if path and os.path.exists(path):
        with pdfplumber.open(path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    else:
        try:
            from storage.database import get_resume
            text = get_resume().get("text", "")
        except Exception as e:
            print(f"  Resume: could not load from MongoDB ({e})")
    _resume_text = text
    print(f"Resume loaded: {len(text)} characters" + ("" if text else " (none — scoring uses config profile only)"))
    return text


def get_resume_text():
    return _resume_text or ""


def reset_llm_state():
    llm.reset_llm_state()


# ── Prompt ────────────────────────────────────────────────────────────────────

def _profile_block():
    c = _config()
    cand = c.get("candidate", {})
    max_y = int(c["matching"].get("max_experience_years", 4))
    locs = ", ".join(c["search"]["locations"])
    return f"""CANDIDATE: {cand.get('title')} — {cand.get('years_experience')} years experience, India (Hyderabad), immediate joiner.
Core skills: {cand.get('core_skills')}
Also: {cand.get('secondary_skills')}
Domain experience: {cand.get('domains')}
Target roles: {cand.get('target_roles')}
NOT a fit: {cand.get('not_a_fit')}
Preferred locations (in order): {locs}; remote roles hiring in India also OK.

SCORING (integer 1-10) — how likely this candidate gets shortlisted AND it is a role he wants:
9-10: Full-stack/MERN/React/Node role, min experience <= {max_y} yrs, India location or India-eligible remote. Stack overlap is strong.
7-8 : Good fit with some gaps: frontend-React or backend-Node/JS role, TypeScript/Next.js asked, or AI/LLM app dev using JS/Python; experience fits.
5-6 : Partial: generic SDE with unclear stack, Python backend, Angular/Vue frontend, or experience/location unclear.
3-4 : Mostly different primary stack (Java, .NET, PHP, Go, mobile native, data, DevOps) or min experience {max_y + 1}+ yrs.
1-2 : Lead/manager level, non-software, internship/fresher-only, or location outside India and not remote-for-India.
If the description is missing, judge from title + skills and cap the score at 8.
Never invent requirements that aren't in the text."""


_KEY_LINE_RE = re.compile(
    r"(year|yrs|experience|require|must|should|skill|stack|qualif|proficien|knowledge|hands.on|"
    r"react|node|javascript|typescript|next|mongo|postgres|sql|python|java\b|\.net|php|aws|"
    r"location|remote|hybrid|onsite|office|notice|salary|ctc|lpa)", re.IGNORECASE)


def _smart_trim(text, max_chars=1300):
    """Keep the opening + the lines that actually carry requirements, within a budget.
    (The old first-600 + last-200 cut dropped the middle, where stack & years usually live.)"""
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if len(text) <= max_chars:
        return text
    head = text[:350]
    picked, used = [], len(head)
    for line in re.split(r"\n|(?<=[.;•])\s+", text[350:]):
        line = line.strip(" -•*\t")
        if 15 < len(line) < 300 and _KEY_LINE_RE.search(line):
            if used + len(line) + 3 > max_chars:
                break
            picked.append(line)
            used += len(line) + 3
    return head + " ... " + " | ".join(picked)


def _job_text(job):
    extra = []
    if job.get("exp_range"):
        extra.append(f"Experience: {job['exp_range']}")
    if job.get("skills"):
        extra.append(f"Skills: {str(job['skills'])[:250]}")
    if job.get("salary"):
        extra.append(f"Salary: {job['salary']}")
    desc = _smart_trim(job.get("description", ""))
    return ("; ".join(extra) + ("\n" if extra and desc else "") + desc) or "(no description)"


def _build_batch_prompt(jobs):
    blocks = []
    for i, job in enumerate(jobs, 1):
        blocks.append(
            f"[JOB {i}] {job.get('title','')} @ {job.get('company','')} | {job.get('location','')}\n"
            f"{_job_text(job)}"
        )
    return f"""{_profile_block()}

{chr(10).join(blocks)}

Return ONLY a JSON object: {{"results":[{{"id":<job number>,"score":<1-10>,"reason":"<max 15 words>","exp":"<experience asked, e.g. '2-5 yrs' or 'not stated'>","skills":[<up to 4 candidate skills the job asks for>],"gaps":[<up to 3 asked skills he lacks>]}}, ...]}} with exactly {len(jobs)} results."""


def _results_list(obj):
    if isinstance(obj, dict):
        for k in ("results", "jobs", "scores", "data"):
            if isinstance(obj.get(k), list):
                return obj[k]
        return None
    return obj if isinstance(obj, list) else None


def _make_validator(n):
    def ok(obj):
        res = _results_list(obj)
        if not res or len(res) < n:
            return False
        ids = set()
        for r in res:
            if not isinstance(r, dict):
                return False
            try:
                s = int(r.get("score"))
                ids.add(int(r.get("id", 0)))
            except Exception:
                return False
            if not 1 <= s <= 10:
                return False
        return ids >= set(range(1, n + 1))
    return ok


# ── Public scoring API ────────────────────────────────────────────────────────

def adjust_score(job, score):
    """Deterministic guard-rails on top of the LLM (it doesn't reliably obey caps):
      * no real description and no structured skills/exp -> cap at 7 (digest, not instant alert)
      * location outside the preferred list and not remote -> -1
    """
    from agent.notify_email import _location_priority_map, _location_rank
    has_desc = len(str(job.get("description") or "")) >= 200
    has_struct = bool(job.get("skills") or job.get("exp_range"))
    if not has_desc and not has_struct:
        score = min(score, 7)
    pm = _location_priority_map()
    if pm and _location_rank(job.get("location"), pm) >= len(pm):
        score -= 1
    return max(1, min(10, score))


_FAILED = {"score": 0, "reason": "Scoring failed — all providers unavailable", "exp": "", "skills": [], "gaps": [],
           "scored_by": None}


def score_jobs_batch(jobs, batch_size=None):
    if _resume_text is None:
        load_resume()
    batch_size = batch_size or int(_config()["matching"].get("batch_size", 6))
    out = []
    total = (len(jobs) + batch_size - 1) // batch_size
    for bi in range(0, len(jobs), batch_size):
        batch = jobs[bi:bi + batch_size]
        print(f"    Batch {bi // batch_size + 1}/{total} ({len(batch)} jobs)...", end=" ", flush=True)
        obj, provider = llm.complete_json(_build_batch_prompt(batch), validate=_make_validator(len(batch)),
                                          max_tokens=300 + 160 * len(batch), label="score")
        if obj is None:
            print("FAILED (all providers unavailable)")
            out.extend(dict(_FAILED) for _ in batch)
            # every provider is down/exhausted — don't hammer them for the remaining batches
            out.extend(dict(_FAILED) for _ in jobs[bi + batch_size:])
            break
        print(provider)
        by_id = {int(r["id"]): r for r in _results_list(obj)}
        for i in range(1, len(batch) + 1):
            r = by_id[i]
            out.append({
                "score":  adjust_score(batch[i - 1], int(r.get("score"))),
                "raw_score": int(r.get("score")),
                "reason": str(r.get("reason", ""))[:200],
                "exp":    str(r.get("exp", ""))[:60],
                "skills": [str(x)[:40] for x in (r.get("skills") or [])][:4],
                "gaps":   [str(x)[:40] for x in (r.get("gaps") or [])][:3],
                "scored_by": provider,
            })
    return out


def score_job(job):
    return score_jobs_batch([job], batch_size=1)[0]
