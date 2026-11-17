"""
Shared title/experience filters for the whole pipeline (single source of truth —
import from here, never redefine elsewhere). Tuned for a ~3-year JS/full-stack developer.

These run BEFORE any LLM call, so every job they settle is free. Goal: drop the obvious
non-fits (wrong function, wrong stack, far too senior) and let the AI judge the rest.
"""
import re

import yaml

INTERNSHIP_TITLE_RE = re.compile(
    r"\b(intern(ship)?s?|trainee|apprentice(ship)?|fresher|graduate\s+(engineer\s+)?trainee)\b",
    re.IGNORECASE,
)

# Hard-senior titles: never a fit at ~3 yrs.
SENIOR_TITLE_RE = re.compile(
    r"\b(lead|principal|staff|architect|manager|director|head\s+of|vice\s+president|vp|"
    r"chief|cto|founding\s+engineer|sde\s*-?\s*(iii|3)|software\s+engineer\s+(iii|3|iv|4)|"
    r"member\s+of\s+technical\s+staff\s*(iii|3|iv))\b",
    re.IGNORECASE,
)

# Soft-senior: "Senior"/"Sr." — in India often 3–6 yrs, reachable. Sent to the AI only when
# the posting's stated minimum experience is within reach (see scheduler pre-filter).
SOFT_SENIOR_TITLE_RE = re.compile(r"\b(senior|sr\.?)(?=\s|$)", re.IGNORECASE)

# Must look like a software role at all (applied to ATS / remote-board results, which
# return every department: sales, HR, finance, ...).
SOFTWARE_TITLE_RE = re.compile(
    r"(developer|engineer|engg|programmer|\bsde\b|full[\s-]?stack|front[\s-]?end|back[\s-]?end|"
    r"\bweb\b|react|node|mern|mean\b|javascript|typescript|\bjs\b|software|\bai\b|\bllm|"
    r"\bml\b|python|application\s+dev)",
    re.IGNORECASE,
)

# Wrong function or clearly different primary stack — skip without spending an LLM call.
# (Only TITLE-level signals; the AI handles subtler mismatches from the description.)
IRRELEVANT_TITLE_RE = re.compile(
    r"\b("
    # non-engineering functions
    r"sales|account\s+(executive|manager)|business\s+development|marketing|recruiter|"
    r"talent\s+acquisition|\bhr\b|human\s+resources|finance|accountant|legal|counsel|"
    r"customer\s+(support|success)|support\s+engineer|technical\s+support|helpdesk|"
    r"telecaller|bpo|call\s+cent(er|re)|data\s+entry|content\s+writer|designer|"
    # other engineering disciplines / stacks
    r"java(?!\s*script)|spring\s*boot|\.net|dot\s*net|c#|asp\.net|php|laravel|wordpress|"
    r"golang|\bgo\s+(developer|engineer)|ruby|rails|scala|kotlin|swift|ios|android|flutter|"
    r"xamarin|unity|unreal|game\s+(developer|engineer)|c\+\+|embedded|firmware|vlsi|asic|fpga|"
    r"hardware|mechanical|civil|electrical|electronics|chemical|network\s+engineer|"
    r"salesforce|sap\b|servicenow|oracle|mainframe|cobol|sharepoint|dynamics\s*365|"
    r"devops|\bsre\b|site\s+reliability|cloud\s+engineer|platform\s+engineer|"
    r"data\s+(engineer|scientist|analyst)|machine\s+learning\s+engineer|ml\s*ops|"
    r"\bqa\b|quality\s+assurance|test(ing)?\s+engineer|\bsdet\b|automation\s+test|tester|"
    r"security\s+engineer|penetration|soc\s+analyst|database\s+administrator|\bdba\b|"
    r"manufacturing|production\s+engineer|guard"
    r")\b",
    re.IGNORECASE,
)


def _read_max_experience_years(default=4):
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        return int(cfg.get("matching", {}).get("max_experience_years", default))
    except Exception:
        return default


# Reject anything whose MINIMUM requirement is above max_experience_years.
_EXP_FLOOR = _read_max_experience_years() + 1
_YRS = "|".join(str(n) for n in range(_EXP_FLOOR, 21))

# "3 to 6 years", "3-6 yrs" — judged by the LOWER bound.
_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*\\?(?:[-–]|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)

EXP_MIN_RE = re.compile(
    rf"\b({_YRS})\s*\+\s*(?:years?|yrs?)"
    rf"|\b(?:minimum|min\.?|at\s+least)\s+(?:of\s+)?({_YRS})\s+(?:years?|yrs?)"
    rf"|\brequires?\s+({_YRS})\s*\+?\s*(?:years?|yrs?)"
    rf"|\b(?:must\s+have|should\s+have|we\s+need|need\s+to\s+have)\s+({_YRS})\s*\+?\s*(?:years?|yrs?)"
    rf"|\bexperience\s*(?:required)?\s*[:\-]\s*({_YRS})\s*\+?"
    rf"|\b({_YRS})\s+(?:years?|yrs?)\s+(?:of\s+)?(?:relevant|professional|work|industry|hands.on|software|engineering|development)\s+experience"
    rf"|\bexperience\s+of\s+({_YRS})\s*\+?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)

_CEILING_MARKERS = ("up to", "upto", "maximum", "max ", "no more than", "less than",
                    "fewer than", "as many as", "welcome", "open to")


def _is_ceiling(description: str, start: int) -> bool:
    prefix = description[max(0, start - 25): start].lower()
    return any(marker in prefix for marker in _CEILING_MARKERS)


def _find_match(description: str):
    consumed = []
    for m in _RANGE_RE.finditer(description):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi or hi > 30:
            continue
        consumed.append(m.span())
        if _is_ceiling(description, m.start()):
            continue
        if lo >= _EXP_FLOOR:
            return m
    for m in EXP_MIN_RE.finditer(description):
        if any(s <= m.start() < e for s, e in consumed):
            continue
        if _is_ceiling(description, m.start()):
            continue
        return m
    return None


def has_experience_requirement(description: str) -> bool:
    """True if the text states a MINIMUM experience above max_experience_years."""
    if not description or str(description).strip() in ("", "nan", "None"):
        return False
    return _find_match(description) is not None


def find_experience_snippet(description: str):
    if not description or str(description).strip() in ("", "nan", "None"):
        return None
    m = _find_match(description)
    if not m:
        return None
    return description[max(0, m.start() - 15): m.end() + 25].replace("\n", " ").strip()


_MIN_EXP_ANY_RE = re.compile(
    r"\b(\d{1,2})\s*(?:\+|(?:[-–]|to)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)", re.IGNORECASE
)


def min_experience_years(text: str):
    """Best-effort lowest 'N years' / 'N-M yrs' figure in the text, or None if not stated.
    Used to decide whether a 'Senior' title is still within reach."""
    vals = []
    for m in _MIN_EXP_ANY_RE.finditer(str(text or "")):
        n = int(m.group(1))
        if 0 <= n <= 20 and not _is_ceiling(text, m.start()):
            vals.append(n)
    return min(vals) if vals else None


def prefilter(title: str, text: str, require_software_title: bool = False):
    """
    Returns (score, reason, tag) if the job can be settled without AI, else None.
    `text` = description plus any structured exp/skills info the scraper gave us.
    """
    title = title or ""
    if require_software_title and not SOFTWARE_TITLE_RE.search(title):
        return 1, "Not a software role", "irrelevant"
    if INTERNSHIP_TITLE_RE.search(title):
        return 1, "Internship/trainee/fresher role", "internship"
    if SENIOR_TITLE_RE.search(title):
        return 1, "Lead/principal/manager-level title", "senior"
    if IRRELEVANT_TITLE_RE.search(title):
        return 1, "Different function or tech stack", "irrelevant"
    if has_experience_requirement(text):
        return 2, f"Requires {_EXP_FLOOR}+ years experience", f"{_EXP_FLOOR}+ years"
    if SOFT_SENIOR_TITLE_RE.search(title):
        mn = min_experience_years(text)
        if mn is None or mn > _EXP_FLOOR - 1:
            return 2, "Senior title with no reachable experience range stated", "senior"
    return None
