"""
Apply kit — for top matches only (score >= alert_threshold), the LLM prepares what Kumar
needs to apply in 2 minutes instead of 15:
  * pitch     — a short first-person note for the recruiter / hiring manager (LinkedIn DM or email)
  * keywords  — JD terms to mirror in the application / resume headline
  * highlight — which of his real experiences/projects to lead with for THIS role
plus zero-cost intel links (no scraping): LinkedIn people-search for the company's
recruiters, and the company's other openings.

Nothing is ever sent automatically — this only prepares material for a human to use.
"""
import urllib.parse

from agent import llm
from agent.resume_matcher import _smart_trim, get_resume_text


def recruiter_search_url(company: str) -> str:
    q = f'"{company}" (recruiter OR "talent acquisition" OR "hiring")'
    return "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote(q)


def company_jobs_url(company: str) -> str:
    return "https://www.linkedin.com/jobs/search/?keywords=" + urllib.parse.quote(company) + "&location=India"


def _prompt(jobs, resume):
    blocks = "\n\n".join(
        f"[JOB {i}] {j.get('title')} @ {j.get('company')} | {j.get('location')}\n"
        f"{_smart_trim(j.get('description') or (str(j.get('skills') or '') + ' ' + str(j.get('exp_range') or '')), 1100)}"
        for i, j in enumerate(jobs, 1))
    return f"""You help a job seeker apply fast. Use ONLY facts from his resume — never invent numbers, employers or skills.

RESUME:
{resume[:2600]}

{blocks}

For each job return:
- "pitch": 45-70 words, first person, to the recruiter/hiring manager. Name the role, connect 2 concrete resume facts to the job's needs, mention he can join immediately, end with a polite ask for a quick chat. No greeting line, no signature, no buzzword soup.
- "keywords": 5 exact terms from the job text worth mirroring in his application.
- "highlight": one short phrase — which experience/project from the resume to lead with.

Return ONLY JSON: {{"results":[{{"id":1,"pitch":"...","keywords":["..."],"highlight":"..."}}, ...]}} with exactly {len(jobs)} results."""


def _valid(n):
    def ok(obj):
        res = obj.get("results") if isinstance(obj, dict) else None
        if not isinstance(res, list) or len(res) < n:
            return False
        ids = {int(r.get("id", 0)) for r in res if isinstance(r, dict) and r.get("pitch")}
        return ids >= set(range(1, n + 1))
    return ok


def build_apply_kits(jobs, batch_size=3):
    """Adds job['apply_kit'] = {pitch, keywords, highlight, recruiters_url, company_jobs_url}."""
    resume = get_resume_text()
    for j in jobs:
        j["apply_kit"] = {"recruiters_url": recruiter_search_url(j.get("company", "")),
                          "company_jobs_url": company_jobs_url(j.get("company", ""))}
    if not resume:
        return jobs
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        obj, provider = llm.complete_json(_prompt(batch, resume), validate=_valid(len(batch)),
                                          max_tokens=350 + 250 * len(batch), temperature=0.4, label="apply-kit")
        if not obj:
            print("    [ApplyKit] all providers busy — kits without pitch this run")
            break
        by_id = {int(r["id"]): r for r in obj["results"]}
        for k, j in enumerate(batch, 1):
            r = by_id.get(k, {})
            j["apply_kit"].update({
                "pitch":     str(r.get("pitch", "")).strip()[:700],
                "keywords":  [str(x)[:40] for x in (r.get("keywords") or [])][:5],
                "highlight": str(r.get("highlight", ""))[:120],
                "by":        provider,
            })
    return jobs
