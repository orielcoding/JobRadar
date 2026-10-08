"""Workday career sites (the public JSON the careers page itself calls).
Used by many large companies (Nvidia, Intel, KLA, Applied Materials, ...).

companies.yaml:
    - name: Nvidia
      ats: workday
      slug: nvidia                                  # Workday tenant
      host: nvidia.wd5.myworkdayjobs.com            # the wdN number differs per company
      site: NVIDIAExternalCareerSite
      country: Israel                               # optional, default Israel

`jobradar detect https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite` fills these in.

The listing has no description, so each posting's page is fetched once, only
for postings the radar has not stored yet and that are not already older than
filters.max_age_days. Known postings keep their stored description.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from jobradar import http
from jobradar.models import JobPosting
from jobradar.sources import register
from jobradar.textutil import html_to_text, to_iso

PAGE = 20          # Workday's maximum page size
MAX_PAGES = 50     # 1,000 postings per company per country is plenty
_HEADERS = {"Accept": "application/json"}


@register("workday")
class WorkdaySource:
    def __init__(self, company: dict, config=None):
        self.company = company.get("name") or company["slug"]
        self.tenant = company["slug"]
        self.host = company["host"]
        self.site = company["site"]
        self.country = company.get("country") or "Israel"
        self.name = f"workday:{self.tenant}"
        self.base = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"
        f = (config.filters if config is not None else {}) or {}
        self.max_age = f.get("max_age_days")
        self._known: dict = {}

    def prepare(self, store) -> None:
        self._known = store.known_jobs("workday", self.company)

    def _list(self, facets: dict, offset: int) -> dict:
        body = {"appliedFacets": facets, "limit": PAGE, "offset": offset, "searchText": ""}
        return json.loads(http.post_json(self.base + "/jobs", body, _HEADERS).decode("utf-8"))

    def fetch(self) -> list[JobPosting]:
        first = self._list({}, 0)
        facets = country_facet(first.get("facets") or [], self.country)
        if not facets:
            raise RuntimeError(f"no '{self.country}' location filter on this Workday site")
        page = self._list(facets, 0)
        total = int(page.get("total") or 0)  # Workday sends the total only on the first page
        postings = list(page.get("jobPostings") or [])
        offset = PAGE
        while offset < total and offset < PAGE * MAX_PAGES:
            postings += self._list(facets, offset).get("jobPostings") or []
            offset += PAGE
        return [self._posting(p) for p in postings if p.get("externalPath")]

    def _posting(self, item: dict) -> JobPosting:
        nid = native_id(item)
        known = self._known.get(nid)
        if known is not None and known["description"]:  # as stored, so the content hash stays stable
            return JobPosting(source="workday", native_id=nid, company=self.company,
                              title=item.get("title", "").strip() or known["title"], url=known["url"],
                              location=known["location"], workplace=known["workplace"],
                              description=known["description"], posted_at=known["posted_at"])
        days = posted_days_ago(item.get("postedOn"))
        if known is not None or (self.max_age and days is not None and days > float(self.max_age)):
            # stored without a page, or too old to matter: no page fetch
            return parse_list_item(item, self.company, self.host, self.site, self.country)
        detail = http.get_json(self.base + item["externalPath"], _HEADERS)
        return parse_detail(detail, item, self.company, self.host, self.site)


def country_facet(facets: list, country: str) -> dict:
    """appliedFacets that restrict the search to one country. Prefers a value named
    exactly like the country (e.g. Locations › "Israel"); otherwise every site
    in that country (e.g. "Israel, Haifa", "Israel, Jerusalem")."""
    c = country.lower()
    exact, partial = {}, {}

    def walk(items, parent):
        for f in items:
            param = f.get("facetParameter") or parent
            for v in f.get("values") or []:
                if "values" in v:          # nested group (locationMainGroup)
                    walk([v], param)
                    continue
                d = (v.get("descriptor") or "").lower()
                if d == c and not exact:
                    exact[param] = [v["id"]]
                elif re.search(rf"\b{re.escape(c)}\b", d):
                    partial.setdefault(param, []).append(v["id"])

    walk(facets, None)
    if exact:
        return exact
    if partial:
        param = max(partial, key=lambda k: len(partial[k]))
        return {param: partial[param]}
    return {}


def native_id(item: dict) -> str:
    return item["externalPath"].rstrip("/").rsplit("/", 1)[-1]


def posted_days_ago(text: str | None) -> int | None:
    """'Posted Today' -> 0, 'Posted Yesterday' -> 1, 'Posted 3 Days Ago' -> 3, 'Posted 30+ Days Ago' -> 30."""
    t = (text or "").lower()
    if "today" in t:
        return 0
    if "yesterday" in t:
        return 1
    m = re.search(r"(\d+)\+?\s*day", t)
    return int(m.group(1)) if m else None


def _url(host: str, site: str, path: str) -> str:
    return f"https://{host}/{site}{path}"


def parse_list_item(item: dict, company: str, host: str, site: str, country: str = "") -> JobPosting:
    days = posted_days_ago(item.get("postedOn"))
    loc = item.get("locationsText", "")
    if re.fullmatch(r"\d+ locations", loc.strip().lower()):  # "3 Locations": the search was already by country
        loc = country
    posted = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat() \
        if days is not None else None
    return JobPosting(
        source="workday", native_id=native_id(item), company=company,
        title=item.get("title", "").strip(), url=_url(host, site, item["externalPath"]),
        location=loc, posted_at=posted)


def parse_detail(data: dict, item: dict, company: str, host: str, site: str) -> JobPosting:
    info = data.get("jobPostingInfo") or {}
    locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
    remote = (info.get("remoteType") or "").lower()
    return JobPosting(
        source="workday", native_id=native_id(item), company=company,
        title=(info.get("title") or item.get("title", "")).strip(),
        url=info.get("externalUrl") or _url(host, site, item["externalPath"]),
        location="; ".join(x for x in locs if x),
        workplace="remote" if "remote" in remote else "hybrid" if "hybrid" in remote else "",
        description=html_to_text(info.get("jobDescription")),
        posted_at=to_iso(info.get("startDate")) or parse_list_item(item, company, host, site).posted_at,
        extra={"req_id": info.get("jobReqId", "")})
