"""Turn a careers-page URL into a companies.yaml entry (which ATS, which slug).

First looks at the URL itself; if that is the company's own domain, downloads
the page once and looks for embedded ATS links (iframes / scripts / links).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from jobradar import http

# (ats, regex with named group slug [, uid])
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?(?P<slug>[\w-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/(?P<slug>[\w-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=(?P<slug>[\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?P<eu>eu\.)?lever\.co/(?P<slug>[\w.-]+)", re.I)),
    ("lever", re.compile(r"api\.(?P<eu>eu\.)?lever\.co/v0/postings/(?P<slug>[\w.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/(?P<slug>[\w.%-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/(?P<slug>[\w.%-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?:api/v\d/widget/accounts/)?(?P<slug>[\w-]+)", re.I)),
    ("workable", re.compile(r"(?P<slug>[\w-]+)\.workable\.com", re.I)),
    ("comeet", re.compile(r"comeet\.com?/jobs/(?P<slug>[\w.-]+)/(?P<uid>[0-9A-Z]{2}\.[0-9A-Z]{3})", re.I)),
    ("workday", re.compile(r"(?P<slug>[\w-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[\w-]+/)?"
                           r"(?:[a-z]{2}-[a-z]{2}/)?(?P<site>[\w-]+)", re.I)),
]

_IGNORE_SLUGS = {"embed", "api", "v1", "www", "apply", "jobs", "careers", "static", "assets"}


def match_text(text: str) -> dict | None:
    for ats, rx in PATTERNS:
        for m in rx.finditer(text):
            slug = m.group("slug")
            if slug.lower() in _IGNORE_SLUGS:
                continue
            entry = {"ats": ats, "slug": slug}
            gd = m.groupdict()
            if gd.get("eu"):
                entry["region"] = "eu"
            if ats == "comeet":
                entry["uid"] = gd["uid"]
                entry["careers_url"] = "https://www." + m.group(0)
            if ats == "workday":
                entry["host"] = f"{slug}.{gd['wd']}.myworkdayjobs.com"
                entry["site"] = gd["site"]
                entry["careers_url"] = f"https://{entry['host']}/{gd['site']}"
            return entry
    return None


def detect(url: str, fetch_page: bool = True) -> tuple[dict | None, str]:
    """Return (entry_or_None, explanation)."""
    entry = match_text(url)
    if entry:
        return entry, "recognized from the URL"
    if not fetch_page:
        return None, "URL not recognized"
    try:
        page = http.get_text(url)
    except Exception as e:  # noqa: BLE001
        return None, f"could not download page: {e}"
    entry = match_text(page)
    if entry:
        return entry, "found an embedded ATS link inside the page"
    return None, ("no known ATS found (the site may load jobs with JavaScript, or use an ATS "
                  "that is not supported yet - see docs/ROADMAP.md)")


def guess_name(url: str, entry: dict | None) -> str:
    host = urlparse(url if "//" in url else "https://" + url).hostname or ""
    parts = [p for p in host.split(".") if p not in ("www", "careers", "jobs", "com", "co", "io", "il", "ai")]
    if parts and not any(x in host for x in ("greenhouse", "lever", "ashbyhq", "workable", "comeet")):
        return parts[0].capitalize()
    return (entry or {}).get("slug", "Company").replace("-", " ").title()
