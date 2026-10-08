"""Greenhouse public Job Board API.
Docs: https://developers.greenhouse.io/job-board.html
companies.yaml:  {name: Acme, ats: greenhouse, slug: acme}
"""

from __future__ import annotations

from jobradar import http
from jobradar.models import JobPosting
from jobradar.sources import register
from jobradar.textutil import html_to_text, to_iso


@register("greenhouse")
class GreenhouseSource:
    URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def __init__(self, company: dict, config=None):
        self.company = company.get("name") or company["slug"]
        self.slug = company["slug"]
        self.name = f"greenhouse:{self.slug}"

    def fetch(self) -> list[JobPosting]:
        return self.parse(http.get_json(self.URL.format(slug=self.slug)), self.company)

    @staticmethod
    def parse(data: dict, company: str) -> list[JobPosting]:
        out = []
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            depts = ", ".join(d.get("name", "") for d in j.get("departments") or [] if d.get("name"))
            out.append(JobPosting(
                source="greenhouse",
                native_id=str(j["id"]),
                company=j.get("company_name") or company,
                title=j.get("title", "").strip(),
                url=j.get("absolute_url", ""),
                location=loc,
                workplace=_workplace_from_text(loc),
                department=depts,
                description=html_to_text(j.get("content")),
                posted_at=to_iso(j.get("first_published") or j.get("updated_at")),
            ))
        return out


def _workplace_from_text(s: str) -> str:
    s = (s or "").lower()
    if "remote" in s:
        return "remote"
    if "hybrid" in s:
        return "hybrid"
    return ""
