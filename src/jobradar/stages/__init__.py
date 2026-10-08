"""Pipeline stages.

Contract: a stage is a class with `name` and `run(ctx) -> dict` (stats).
Stages never call each other; they communicate only through job status in
the database. That is what makes them replaceable: a new triage stage only
has to read TRIAGE_PENDING jobs and move them to DEEP_PENDING /
REJECTED_TRIAGE / LIGHT_MATCH like the current one does.

Order comes from config `pipeline.stages`.
"""

from __future__ import annotations

from jobradar.stages.deep_eval import DeepEvalStage
from jobradar.stages.dedupe import DedupeStage
from jobradar.stages.enrich import EnrichStage
from jobradar.stages.hard_filter import HardFilterStage
from jobradar.stages.ingest import IngestStage
from jobradar.stages.notify import NotifyStage
from jobradar.stages.report import ReportStage
from jobradar.stages.triage import TriageStage

STAGES = {
    "ingest": IngestStage,
    "dedupe": DedupeStage,
    "hard_filter": HardFilterStage,
    "enrich": EnrichStage,
    "triage": TriageStage,
    "deep_eval": DeepEvalStage,
    "notify": NotifyStage,
    "report": ReportStage,
}
