"""Ashby public job posting API.
Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
companies.yaml:  {name: Acme, ats: ashby, slug: acme}   (slug = jobs.ashbyhq.com/<slug>)
"""

from __future__ import annotations

from jobradar import http
from jobradar.models import JobPosting
from jobradar.sources import register
from jobradar.textutil import html_to_text, to_iso

_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}


@register("ashby")
class AshbySource:
    URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"

    def __init__(self, company: dict, config=None):
        self.company = company.get("name") or company["slug"]
        self.slug = company["slug"]
        self.name = f"ashby:{self.slug}"

    def fetch(self) -> list[JobPosting]:
        return self.parse(http.get_json(self.URL.format(slug=self.slug)), self.company)

    @staticmethod
    def parse(data: dict, company: str) -> list[JobPosting]:
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            locs = [j.get("location") or ""]
            for s in j.get("secondaryLocations") or []:
                locs.append(s.get("location", "") if isinstance(s, dict) else str(s))
            wp = _WORKPLACE.get(str(j.get("workplaceType", "")).lower(), "")
            if not wp and j.get("isRemote"):
                wp = "remote"
            out.append(JobPosting(
                source="ashby",
                native_id=str(j.get("id") or j.get("jobUrl")),
                company=company,
                title=(j.get("title") or "").strip(),
                url=j.get("jobUrl", ""),
                location=" / ".join(x for x in locs if x),
                workplace=wp,
                department=" / ".join(x for x in (j.get("department"), j.get("team")) if x),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                posted_at=to_iso(j.get("publishedAt")),
                extra={"employment_type": j.get("employmentType", "")},
            ))
        return out
