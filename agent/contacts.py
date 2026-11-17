"""
Pull recruiter/HR contact emails out of job descriptions ("send your CV to hr@acme.com").

Shown to Kumar in alerts as a mailto link so HE can send a tailored mail — the agent never
sends anything to recruiters itself (CLAUDE.md security constraint #1).
"""
import re

_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# Addresses that appear in JDs but are not for applications
_JUNK_LOCAL = re.compile(
    r"^(no-?reply|do-?not-?reply|accommodat|privacy|gdpr|dpo|legal|compliance|security|abuse|"
    r"par_leave|leave|benefits|payroll|support|help|press|media|investor|ir|webmaster|admin|"
    r"fraud|scam|ethics|whistle|unsubscribe|marketing|sales|billing|disability|ada)", re.I)
_JUNK_DOMAIN = re.compile(r"(example\.(com|org)|sentry\.io|\.png$|\.jpg$|\.gif$|\.svg$|\.webp$)", re.I)
# Words near the address that mark it as the "apply here" address
_APPLY_CONTEXT = re.compile(r"(cv|resume|résumé|apply|application|send|share|mail|email|reach|contact|"
                            r"interested|profile|hr|recruit|talent|hiring|refer)", re.I)


def extract_emails(text, max_n=2):
    """Return up to max_n plausible application emails, best first."""
    text = str(text or "")
    found = []
    for m in _EMAIL_RE.finditer(text):
        addr = m.group(0).strip(".").lower()
        local, _, domain = addr.partition("@")
        if _JUNK_LOCAL.match(local) or _JUNK_DOMAIN.search(addr) or addr in (a for _, a in found):
            continue
        context = text[max(0, m.start() - 120):m.start()]
        rank = 0 if _APPLY_CONTEXT.search(context) else 1
        found.append((rank, addr))
    found.sort(key=lambda x: x[0])
    return [a for _, a in found[:max_n]]


def hr_email_for(job):
    """Comma-joined string (or None) for the job's description."""
    emails = extract_emails(job.get("description"))
    return ", ".join(emails) or None
