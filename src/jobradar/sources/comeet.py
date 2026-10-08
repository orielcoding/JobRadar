"""Comeet (Spark Hire Recruit) Careers API - common in Israel.
Docs: https://developers.comeet.com/reference/careers-api-overview

The API needs the company UID and a public "careers token". The token is not
secret (it is embedded in the company's public careers page), but it is not
shown anywhere obvious. Two ways to configure:

  {name: Acme, ats: comeet, uid: "A1.234", token: "ABCDEF..."}
  {name: Acme, ats: comeet, careers_url: "https://www.comeet.com/jobs/acme/A1.234"}

With only careers_url, the token is read from that page on each run.
(Unverified against every company - check with `jobradar test-sources --only Acme`.)
"""

from __future__ import annotations

import re

from jobradar import http
from jobradar.models import JobPosting
from jobradar.sources import register
from jobradar.textutil import extract_json, html_to_text, to_iso

_UID_RE = re.compile(r'"company_uid"\s*:\s*"([^"]+)"')
_TOKEN_RE = re.compile(r'"token"\s*:\s*"([0-9A-Za-z]{10,})"')
_URL_UID_RE = re.compile(r"comeet\.com?/jobs/[^/]+/([0-9A-Z]{2}\.[0-9A-Z]{3})", re.I)


@register("comeet")
class ComeetSource:
    URL = "https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=true"

    def __init__(self, company: dict, config=None):
        self.company = company.get("name") or company.get("slug") or "?"
        self.uid = company.get("uid") or ""
        self.token = company.get("token") or ""
        self.careers_url = company.get("careers_url") or ""
        if not self.uid and self.careers_url:
            m = _URL_UID_RE.search(self.careers_url)
            self.uid = m.group(1) if m else ""
        if not self.token and not self.careers_url:
            raise ValueError("comeet needs `token` or `careers_url`")
        self.name = f"comeet:{self.uid or self.company}"

    def _bootstrap(self) -> None:
        page = http.get_text(self.careers_url)
        idx = page.find("COMPANY_DATA")
        data = extract_json(page[idx:idx + 20000]) if idx >= 0 else None
        if data:
            self.uid = self.uid or str(data.get("company_uid") or "")
            self.token = str(data.get("token") or "") or self.token
        if self.uid and self.token:
            return
        if not self.uid:
            m = _UID_RE.search(page)
            self.uid = m.group(1) if m else ""
        m = _TOKEN_RE.search(page)
        if m:
            self.token = m.group(1)
        if not (self.uid and self.token):
            raise RuntimeError(f"could not find Comeet uid/token on {self.careers_url}")

    def fetch(self) -> list[JobPosting]:
        if not (self.uid and self.token):
            self._bootstrap()
        data = http.get_json(self.URL.format(uid=self.uid, token=self.token))
        return self.parse(data, self.company)

    @staticmethod
    def parse(data, company: str) -> list[JobPosting]:
        positions = data if isinstance(data, list) else data.get("positions", [])
        out = []
        for p in positions:
            loc = p.get("location") or {}
            loc_str = loc.get("name") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
            details = sorted(p.get("details") or [], key=lambda d: d.get("order", 0))
            desc = "\n\n".join(
                f"{d.get('name', '')}:\n{html_to_text(d.get('value'))}" for d in details if d.get("value")
            )
            wp = str(p.get("workplace_type") or "").lower()
            if loc.get("is_remote"):
                wp = "remote"
            out.append(JobPosting(
                source="comeet",
                native_id=str(p["uid"]),
                company=p.get("company_name") or company,
                title=(p.get("name") or "").strip(),
                url=p.get("url_active_page") or p.get("url_comeet_hosted_page") or "",
                location=loc_str,
                workplace={"on-site": "onsite", "on site": "onsite"}.get(wp, wp),
                department=p.get("department") or "",
                description=desc,
                posted_at=to_iso(p.get("time_updated")),
                extra={"experience_level": p.get("experience_level", ""),
                       "employment_type": p.get("employment_type", "")},
            ))
        return out
