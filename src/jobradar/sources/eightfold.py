"""Eightfold career sites (the public JSON the careers page itself calls).
Used by Microsoft and Qualcomm, among others.

companies.yaml:
    - name: Microsoft
      ats: eightfold
      host: apply.careers.microsoft.com
      domain: microsoft.com
      country: Israel          # optional, default Israel

The search is sorted newest first and read a few pages deep (`max_pages`,
default 5): a daily radar only needs what is new. Microsoft answers 429 to
quick bursts, so requests are spaced out. Each posting's details (with the
description) are fetched once, only for postings the radar has not stored yet
and that are not already older than filters.max_age_days.
"""

from __future__ import annotations

import time
from urllib.parse import urlencode

from jobradar import http
from jobradar.models import JobPosting
from jobradar.sources import register
from jobradar.textutil import age_days, html_to_text, to_iso

PAGE = 10      # Eightfold's fixed page size
PAUSE = 2.0    # seconds between requests


@register("eightfold")
class EightfoldSource:
    def __init__(self, company: dict, config=None):
        self.company = company.get("name") or company["domain"]
        self.host = company["host"]
        self.domain = company["domain"]
        self.country = company.get("country") or "Israel"
        self.max_pages = int(company.get("max_pages") or 5)
        self.name = f"eightfold:{self.domain}"
        f = (config.filters if config is not None else {}) or {}
        self.max_age = f.get("max_age_days")
        self._known: dict = {}

    def prepare(self, store) -> None:
        self._known = store.known_jobs("eightfold", self.company)

    def _api(self, path: str, **params) -> dict:
        query = urlencode({"domain": self.domain, **params})
        return http.get_json(f"https://{self.host}/api/pcsx/{path}?{query}").get("data") or {}

    def fetch(self) -> list[JobPosting]:
        positions: list[dict] = []
        for n in range(self.max_pages):
            if n:
                time.sleep(PAUSE)
            data = self._api("search", query="", location=self.country, start=n * PAGE, sort_by="timestamp")
            page = data.get("positions") or []
            positions += page
            if len(page) < PAGE or len(positions) >= int(data.get("count") or 0):
                break
        return [self._posting(p) for p in positions if p.get("id")]

    def _posting(self, p: dict) -> JobPosting:
        nid = str(p["id"])
        known = self._known.get(nid)
        if known is not None and known["description"]:  # as stored, so the content hash stays stable
            return JobPosting(source="eightfold", native_id=nid, company=self.company,
                              title=(p.get("name") or "").strip() or known["title"], url=known["url"],
                              location=known["location"], workplace=known["workplace"],
                              department=known["department"], description=known["description"],
                              posted_at=known["posted_at"])
        age = age_days(to_iso(p.get("postedTs") or p.get("creationTs")))
        if known is not None or (self.max_age and age is not None and age > float(self.max_age)):
            return parse_position(p, self.company, self.host)  # stored without details, or too old: no fetch
        time.sleep(PAUSE)
        details = self._api("position_details", position_id=nid, hl="en")
        return parse_position({**p, **details}, self.company, self.host)


def parse_position(p: dict, company: str, host: str) -> JobPosting:
    """One position from the search or position_details JSON (only details carry jobDescription)."""
    work = (p.get("workLocationOption") or "").lower()
    path = p.get("positionUrl") or f"/careers/job/{p['id']}"
    return JobPosting(
        source="eightfold", native_id=str(p["id"]), company=company,
        title=(p.get("name") or "").strip(),
        url=f"https://{host}{path}",
        location="; ".join(p.get("locations") or []),
        workplace=work if work in ("remote", "hybrid", "onsite") else "",
        department=p.get("department") or "",
        description=html_to_text(p.get("jobDescription")),
        posted_at=to_iso(p.get("postedTs") or p.get("creationTs")),
        extra={"req_id": str(p.get("displayJobId") or "")})
