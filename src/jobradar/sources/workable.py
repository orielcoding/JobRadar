"""Workable public widget API (what Workable career pages themselves call).
companies.yaml:  {name: Acme, ats: workable, slug: acme}   (slug = apply.workable.com/<slug>)
"""

from __future__ import annotations

from jobradar import http
from jobradar.models import JobPosting
from jobradar.sources import register
from jobradar.textutil import html_to_text, to_iso


@register("workable")
class WorkableSource:
    URL = "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"

    def __init__(self, company: dict, config=None):
        self.company = company.get("name") or company["slug"]
        self.slug = company["slug"]
        self.name = f"workable:{self.slug}"

    def fetch(self) -> list[JobPosting]:
        return self.parse(http.get_json(self.URL.format(slug=self.slug)), self.company)

    @staticmethod
    def parse(data: dict, company: str) -> list[JobPosting]:
        out = []
        for j in data.get("jobs", []):
            locs = []
            for loc in j.get("locations") or []:
                locs.append(", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x))
            if not locs:
                locs.append(", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x))
            wp = "remote" if j.get("telecommuting") else str(j.get("workplace_type") or "").lower()
            desc_parts = [html_to_text(j.get(k)) for k in ("description", "requirements", "benefits")]
            out.append(JobPosting(
                source="workable",
                native_id=str(j.get("shortcode") or j.get("id") or j.get("url")),
                company=company,
                title=(j.get("title") or "").strip(),
                url=j.get("url") or j.get("shortlink") or j.get("application_url") or "",
                location=" / ".join(x for x in locs if x),
                workplace={"on_site": "onsite", "on-site": "onsite"}.get(wp, wp),
                department=j.get("department") or "",
                description="\n\n".join(x for x in desc_parts if x),
                posted_at=to_iso(j.get("published_on") or j.get("created_at")),
                extra={"employment_type": j.get("employment_type", ""), "experience": j.get("experience", "")},
            ))
        return out
