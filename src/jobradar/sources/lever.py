"""Lever public Postings API.
Docs: https://github.com/lever/postings-api
companies.yaml:  {name: Acme, ats: lever, slug: acme}   (add `region: eu` for jobs.eu.lever.co)
"""

from __future__ import annotations

from jobradar import http
from jobradar.models import JobPosting
from jobradar.sources import register
from jobradar.textutil import clean_whitespace, html_to_text, to_iso

_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}


@register("lever")
class LeverSource:
    URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
    URL_EU = "https://api.eu.lever.co/v0/postings/{slug}?mode=json"

    def __init__(self, company: dict, config=None):
        self.company = company.get("name") or company["slug"]
        self.slug = company["slug"]
        self.eu = str(company.get("region", "")).lower() == "eu"
        self.name = f"lever:{self.slug}"

    def fetch(self) -> list[JobPosting]:
        url = (self.URL_EU if self.eu else self.URL).format(slug=self.slug)
        return self.parse(http.get_json(url), self.company)

    @staticmethod
    def parse(data: list, company: str) -> list[JobPosting]:
        out = []
        for p in data or []:
            cats = p.get("categories") or {}
            parts = [p.get("descriptionPlain") or html_to_text(p.get("description"))]
            for lst in p.get("lists") or []:
                parts.append(f"{lst.get('text', '')}:\n{html_to_text(lst.get('content'))}")
            parts.append(p.get("additionalPlain") or html_to_text(p.get("additional")))
            locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
            out.append(JobPosting(
                source="lever",
                native_id=str(p["id"]),
                company=company,
                title=(p.get("text") or "").strip(),
                url=p.get("hostedUrl", ""),
                location=" / ".join(locs),
                workplace=_WORKPLACE.get(str(p.get("workplaceType", "")).lower(), ""),
                department=" / ".join(x for x in (cats.get("department"), cats.get("team")) if x),
                description=clean_whitespace("\n\n".join(x for x in parts if x)),
                posted_at=to_iso(p.get("createdAt")),
                extra={"commitment": cats.get("commitment", "")},
            ))
        return out
