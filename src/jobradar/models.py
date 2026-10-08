"""Core data types shared by every part of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    """Lifecycle of a job. The status column is the contract between stages:
    each stage picks up jobs in one status and moves them to the next."""

    NEW = "new"                          # just ingested
    DUPLICATE = "duplicate"              # same job seen from another source
    FILTERED_OUT = "filtered_out"        # failed a hard (code) filter
    TRIAGE_PENDING = "triage_pending"    # waiting for the cheap LLM pass
    REJECTED_TRIAGE = "rejected_triage"  # cheap LLM said "no"
    LIGHT_MATCH = "light_match"          # passed triage but has no description (e.g. LinkedIn email)
    DEEP_PENDING = "deep_pending"        # waiting for the deep evaluation
    EVALUATED = "evaluated"              # deep evaluation done (see jobs.decision)
    ERROR = "error"                      # gave up after repeated failures


@dataclass
class JobPosting:
    """A job as returned by a Source, normalized to one shape."""

    source: str            # "greenhouse", "lever", "linkedin_email", ...
    native_id: str         # id inside that source
    company: str
    title: str
    url: str
    location: str = ""
    workplace: str = ""    # "remote" | "hybrid" | "onsite" | ""
    department: str = ""
    description: str = ""  # plain text; may be empty (email alerts)
    posted_at: str | None = None  # ISO 8601
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.native_id}"
