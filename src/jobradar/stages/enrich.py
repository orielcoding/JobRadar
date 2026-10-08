"""Stage 1c - fetch missing job descriptions from public ATS pages (pure code, no model).

Jobs from the techmap feed carry only title / company / link. When the link is a
public ATS posting, the description can be read from that ATS's public API, so
the job gets a full evaluation instead of ending up as a "light match".
LinkedIn links are never fetched (no scraping LinkedIn).

Supported links: Comeet, Lever, Greenhouse.

Picks up:
  TRIAGE_PENDING without a description -> description added, status unchanged
  LIGHT_MATCH (already passed triage)  -> description added, moved to DEEP_PENDING
Each job is tried once (extra.enrich_tried), so dead links cost one request.
"""

from __future__ import annotations

import logging
import re

from jobradar import http
from jobradar.models import Status
from jobradar.sources.comeet import ComeetSource
from jobradar.sources.greenhouse import GreenhouseSource
from jobradar.sources.lever import LeverSource
from jobradar.textutil import extract_json

log = logging.getLogger(__name__)

_COMEET = re.compile(r"comeet\.com?/jobs/([^/?#]+)/([0-9A-Z]{2}\.[0-9A-Z]{3})/[^/?#]*/([0-9A-Z]{2}\.[0-9A-Z]{3})", re.I)
_LEVER = re.compile(r"jobs\.(eu\.)?lever\.co/([^/?#]+)/([0-9a-f-]{36})", re.I)
_GREENHOUSE = re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([^/?#]+)/jobs/(\d+)", re.I)
_TOKEN = re.compile(r'"token"\s*:\s*"([0-9A-Za-z]{10,})"')

COMEET_API = "https://www.comeet.co/careers-api/2.0/company/{uid}/positions/{pos}?token={token}&details=true"
LEVER_API = "https://api.{eu}lever.co/v0/postings/{slug}/{id}?mode=json"
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}"


def match_url(url: str) -> tuple[str, tuple] | None:
    """Which public ATS a posting link belongs to, and its ids. None = not fetchable."""
    for kind, rx in (("comeet", _COMEET), ("lever", _LEVER), ("greenhouse", _GREENHOUSE)):
        m = rx.search(url or "")
        if m:
            return kind, m.groups()
    return None


def comeet_token(page: str) -> str:
    """The public careers token embedded in a Comeet-hosted page."""
    i = page.find("COMPANY_DATA")
    data = extract_json(page[i:i + 20000]) if i >= 0 else None
    if data and data.get("token"):
        return str(data["token"])
    m = _TOKEN.search(page)
    return m.group(1) if m else ""


def parse_description(kind: str, data) -> str:
    """Description text from one posting's API JSON, reusing the source parsers."""
    if kind == "comeet":
        jobs = ComeetSource.parse([data], "")
    elif kind == "lever":
        jobs = LeverSource.parse([data], "")
    else:
        jobs = GreenhouseSource.parse({"jobs": [data]}, "")
    return jobs[0].description.strip() if jobs else ""


class EnrichStage:
    name = "enrich"

    def __init__(self):
        self._comeet_tokens: dict[str, str] = {}

    def fetch_description(self, url: str) -> str:
        hit = match_url(url)
        if not hit:
            return ""
        kind, ids = hit
        if kind == "comeet":
            _slug, uid, pos = ids
            token = self._comeet_tokens.get(uid)
            if token is None:
                token = comeet_token(http.get_text(url))
                self._comeet_tokens[uid] = token
            if not token:
                return ""
            data = http.get_json(COMEET_API.format(uid=uid, pos=pos, token=token))
        elif kind == "lever":
            eu, slug, pid = ids
            data = http.get_json(LEVER_API.format(eu="eu." if eu else "", slug=slug, id=pid))
        else:
            slug, jid = ids
            data = http.get_json(GREENHOUSE_API.format(slug=slug, id=jid))
        return parse_description(kind, data)

    def run(self, ctx) -> dict:
        store, cfg = ctx.store, ctx.config
        min_desc = int(cfg.get("triage.min_description_chars", 200))
        budget = int(cfg.get("enrich.max_per_run", 80))
        stats = {"tried": 0, "enriched": 0, "to_deep": 0, "failed": 0}
        candidates = store.jobs_by_status(Status.TRIAGE_PENDING) + store.jobs_by_status(Status.LIGHT_MATCH)
        for job in candidates:
            if stats["tried"] >= budget:
                break
            if len(job["description"] or "") >= min_desc or not match_url(job["url"]):
                continue
            if '"enrich_tried": true' in (job["extra"] or ""):
                continue
            stats["tried"] += 1
            try:
                desc = self.fetch_description(job["url"])
            except Exception as e:  # noqa: BLE001 - one dead link must not stop the run
                log.info("enrich #%s failed: %s", job["id"], e)
                desc = ""
            store.merge_extra(job["id"], {"enrich_tried": True})
            if len(desc) < min_desc:
                stats["failed"] += 1
                continue
            store.set_description(job["id"], desc)
            stats["enriched"] += 1
            if job["status"] == Status.LIGHT_MATCH.value:
                store.set_status(job["id"], Status.DEEP_PENDING, "description fetched from ATS")
                stats["to_deep"] += 1
        store.commit()
        return stats
