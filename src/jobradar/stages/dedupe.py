"""Stage 1a - cross-source dedupe (pure code).

The same job often arrives twice: from the company's career board (with full
description) and from a LinkedIn alert email (title only). Rule: same
normalized company + very similar title (+ compatible location) = duplicate,
and the copy WITH a description wins.

Two copies are the same job only when they appeared around the same time: some
pair of their dates (posted_at, first_seen_at) is within filters.max_age_days.
So a late listing of a job (techmap a few days after the career board) is a
duplicate, while a posting the company bumped again a week later is a new
appearance.

Today: string similarity. Upgrade seam: embeddings (docs/ROADMAP.md).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from jobradar.models import Status
from jobradar.textutil import age_days

TITLE_SIMILARITY = 0.9
_HEBREW = re.compile(r"[֐-׿]")
_REPLACEABLE = {Status.NEW.value, Status.TRIAGE_PENDING.value, Status.LIGHT_MATCH.value,
                Status.FILTERED_OUT.value, Status.REJECTED_TRIAGE.value}


def _host(url: str) -> str:
    return urlparse(url or "").netloc.lower().removeprefix("www.").removeprefix("il.")


def _loc_compatible(a: str, b: str) -> bool:
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return True
    if _HEBREW.search(a) or _HEBREW.search(b):  # "נתניה, Israel" vs "Netanya": different scripts, can't compare
        return True
    ta = {t for t in a.replace(",", " ").replace("-", " ").split() if len(t) > 2}
    tb = {t for t in b.replace(",", " ").replace("-", " ").split() if len(t) > 2}
    return bool(ta & tb) or "remote" in a or "remote" in b


def same_appearance(a, b, max_age_days) -> bool:
    """True when two copies appeared within max_age_days of each other (no window configured = always)."""
    if not max_age_days:
        return True
    da = [x for x in (age_days(a["posted_at"]), age_days(a["first_seen_at"])) if x is not None]
    db = [x for x in (age_days(b["posted_at"]), age_days(b["first_seen_at"])) if x is not None]
    if not da or not db:
        return True
    return min(abs(x - y) for x in da for y in db) <= float(max_age_days)


class DedupeStage:
    name = "dedupe"

    def run(self, ctx) -> dict:
        store = ctx.store
        dupes = 0
        window = (ctx.config.filters or {}).get("max_age_days")
        for job in store.jobs_by_status(Status.NEW):
            has_desc = len(job["description"] or "") > 200
            for other in store.jobs_same_company(job["company_norm"], job["id"]):
                # Within one career board, two postings with the same title are usually
                # real separate openings. Within an aggregator (techmap lists the same job
                # from LinkedIn and from Comeet) the link host differs - that is a duplicate.
                if not same_appearance(job, other, window):
                    continue
                if other["source"] == job["source"] and _host(other["url"]) == _host(job["url"]):
                    continue
                if SequenceMatcher(None, job["title_norm"], other["title_norm"]).ratio() < TITLE_SIMILARITY:
                    continue
                if not _loc_compatible(job["location"], other["location"]):
                    continue
                other_has_desc = len(other["description"] or "") > 200
                if has_desc and not other_has_desc and other["status"] in _REPLACEABLE:
                    store.set_status(other["id"], Status.DUPLICATE, f"replaced by #{job['id']}",
                                     duplicate_of=job["id"])
                else:
                    store.set_status(job["id"], Status.DUPLICATE, f"same as #{other['id']}",
                                     duplicate_of=other["id"])
                dupes += 1
                break
        store.commit()
        return {"duplicates": dupes}
