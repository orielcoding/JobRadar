"""Israeli Tech Map job feed (github.com/mluggy/techmap, ODbL, by Michael Lugassy).

The project publishes ~4,000 current Israeli tech jobs as CSV files per role
category, updated daily. We download the categories you choose from GitHub;
nothing is scraped by JobRadar. The feed has title / company / city / link but
no description, so jobs that are not also found on a watched career board can
only become "light matches" (dedupe merges the rest).

config.yaml:
  techmap:
    enabled: true
    categories: [software, data-science, devops]   # file names under jobs/
"""

from __future__ import annotations

import csv
import io
import re

from jobradar import http
from jobradar.models import JobPosting
from jobradar.textutil import content_hash, to_iso

BASE = "https://raw.githubusercontent.com/mluggy/techmap/main/jobs/{cat}.csv"
CATEGORIES = ["admin", "business", "data-science", "design", "devops", "finance", "frontend", "hardware",
              "hr", "legal", "marketing", "procurement-operations", "product", "project-management", "qa",
              "sales", "security", "software", "support"]
_LI_ID = re.compile(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{6,})")
_COMEET_POS = re.compile(r"comeet\.com/jobs/[^/]+/[0-9A-Z]{2}\.[0-9A-Z]{3}/[^/]+/([0-9A-Z]{2}\.[0-9A-Z]{3})", re.I)


def clean_url(url: str) -> str:
    return re.sub(r"[?&]utm_[^&]+", "", url).rstrip("?&")


class TechmapSource:
    name = "techmap"

    def __init__(self, config):
        cats = config.get("techmap.categories") or []
        unknown = [c for c in cats if c not in CATEGORIES]
        if unknown:
            raise ValueError(f"unknown techmap categories {unknown}; choose from {CATEGORIES}")
        if not cats:
            raise ValueError("techmap.categories is empty")
        self.categories = cats

    def fetch(self) -> list[JobPosting]:
        out: list[JobPosting] = []
        for cat in self.categories:
            out.extend(self.parse(http.get_text(BASE.format(cat=cat)), cat))
        return out

    @staticmethod
    def parse(text: str, category: str) -> list[JobPosting]:
        out = []
        for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
            url = clean_url(r.get("url") or "")
            title = (r.get("title") or "").strip()
            if not url or not title:
                continue
            m = _LI_ID.search(url) or _COMEET_POS.search(url)
            native = m.group(1) if m else content_hash(url)
            city = (r.get("city") or "").strip()
            out.append(JobPosting(
                source="techmap",
                native_id=native,
                company=(r.get("company") or "").strip() or "(unknown)",
                title=title,
                url=url,
                location=f"{city}, Israel" if city else "Israel",
                posted_at=to_iso(r.get("updated")),
                extra={"level": r.get("level", ""), "category": category, "size": r.get("size", ""),
                       "industry": r.get("category", "")},
            ))
        return out
