"""Google Careers (google.com/about/careers): the search results page.

There is no public API. The results page embeds its data as JSON
(`AF_initDataCallback({key: 'ds:1', ...})`), 20 jobs per page, each with the
full description, so one request per page is all it takes. Sorted newest
first and read a few pages deep (`max_pages`, default 10), spaced out.

companies.yaml:
    - name: Google
      ats: google
      country: Israel          # optional, default Israel

If Google changes the page or shows a captcha, the source fails loudly
(`jobradar test-sources --only Google`) instead of returning nothing.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlencode

from jobradar import http
from jobradar.models import JobPosting
from jobradar.sources import register
from jobradar.textutil import html_to_text, to_iso

BASE = "https://www.google.com/about/careers/applications/jobs/results/"
PAGE = 20
PAUSE = 2.0
_DATA = re.compile(r"AF_initDataCallback\(\{key: 'ds:1', hash: '\d+', data:(.*?), sideChannel: \{\}\}\);", re.S)


@register("google")
class GoogleCareersSource:
    def __init__(self, company: dict, config=None):
        self.company = company.get("name") or "Google"
        self.country = company.get("country") or "Israel"
        self.max_pages = int(company.get("max_pages") or 10)
        self.name = f"google:{self.country.lower()}"

    def fetch(self) -> list[JobPosting]:
        out: list[JobPosting] = []
        for page in range(1, self.max_pages + 1):
            if page > 1:
                time.sleep(PAUSE)
            url = BASE + "?" + urlencode({"location": self.country, "sort_by": "date", "page": page})
            jobs, total = parse_page(http.get_text(url), self.company)
            out += jobs
            if len(jobs) < PAGE or page * PAGE >= total:
                break
        return out


def _at(x, *idx):
    for i in idx:
        if not isinstance(x, list) or i >= len(x):
            return None
        x = x[i]
    return x


def parse_page(html: str, company: str = "Google") -> tuple[list[JobPosting], int]:
    """(jobs on this results page, total matching jobs)."""
    m = _DATA.search(html)
    if not m:
        raise RuntimeError("no job data on the page (the format changed, or Google asked for a captcha)")
    data = json.loads(m.group(1))
    out = []
    for j in _at(data, 0) or []:
        if not _at(j, 0) or not _at(j, 1):
            continue
        parts = [("", _at(j, 10, 1)), ("Responsibilities", _at(j, 3, 1)), ("Qualifications", _at(j, 4, 1))]
        description = "\n\n".join(f"{h}\n{html_to_text(t)}".strip() for h, t in parts if t)
        created = _at(j, 12, 0) or _at(j, 14, 0)
        out.append(JobPosting(
            source="google", native_id=str(j[0]), company=_at(j, 7) or company,
            title=j[1].strip(), url=f"{BASE}{j[0]}",
            location="; ".join(loc[0] for loc in _at(j, 9) or [] if _at(loc, 0)),
            description=description,
            posted_at=to_iso(created) if created else None))
    return out, int(_at(data, 2) or 0)
