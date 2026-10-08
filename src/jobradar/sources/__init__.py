"""Source contract + registry.

A Source is anything with:
    name: str
    fetch() -> list[JobPosting]
and optionally:
    prepare(store)      called once before fetching (in the main thread)
    after_ingest(store) called once after its postings were stored

To add a new ATS: create a module here, decorate the class with
@register("myats"), give it __init__(self, company: dict, config) and fetch().
Then companies.yaml entries with `ats: myats` will use it.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from jobradar.models import JobPosting

log = logging.getLogger(__name__)

SOURCE_TYPES: dict[str, Callable] = {}


class Source(Protocol):
    name: str

    def fetch(self) -> list[JobPosting]: ...


def register(ats: str):
    def deco(cls):
        SOURCE_TYPES[ats] = cls
        return cls
    return deco


def _load_builtin() -> None:
    # Importing registers them.
    from jobradar.sources import ashby, comeet, greenhouse, lever, workable, workday  # noqa: F401


def build_sources(config, only: str | None = None) -> tuple[list, list[str]]:
    """Return (sources, warnings). `only` filters by company name or source name."""
    _load_builtin()
    sources: list = []
    warnings: list[str] = []
    for c in config.companies:
        name = c.get("name") or c.get("slug") or "?"
        if c.get("enabled") is False:
            continue
        if only and only.lower() not in name.lower():
            continue
        ats = (c.get("ats") or "").lower()
        if not ats:
            if c.get("careers_url"):
                warnings.append(f"{name}: no `ats` yet - run `jobradar detect {c['careers_url']}`")
            # name-only entries (e.g. favorites that arrive via alerts) are fine
            continue
        cls = SOURCE_TYPES.get(ats)
        if not cls:
            warnings.append(f"{name}: unknown ats '{ats}' (supported: {', '.join(sorted(SOURCE_TYPES))})")
            continue
        try:
            sources.append(cls(c, config))
        except (KeyError, ValueError) as e:
            warnings.append(f"{name}: bad config ({e})")
    if config.get("techmap.enabled") and (not only or only.lower() in "techmap"):
        from jobradar.sources.techmap import TechmapSource
        try:
            sources.append(TechmapSource(config))
        except ValueError as e:
            warnings.append(f"techmap: {e}")
    if config.get("gmail.enabled") and (not only or only.lower() in "gmail email linkedin"):
        from jobradar.sources.gmail_alerts import GmailAlertsSource
        sources.append(GmailAlertsSource(config))
    return sources, warnings
