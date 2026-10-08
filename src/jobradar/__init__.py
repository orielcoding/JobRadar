"""JobRadar: a personal, local-first job radar.

Pipeline (each stage reads and writes job *status* in SQLite, so stages are
decoupled and can be swapped, reordered or re-run independently):

    ingest -> dedupe -> hard_filter -> triage (LLM, cheap, batched)
           -> deep_eval (LLM, strong, one job per call) -> notify -> report

See docs/ARCHITECTURE.md for the contracts between the parts.
"""

__version__ = "0.1.0"
