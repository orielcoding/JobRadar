"""Runs the configured stages in order, with a lock so two runs never overlap."""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from jobradar.config import Config
from jobradar.llm import get_backend
from jobradar.notifiers import build_notifiers
from jobradar.store import Store
from jobradar.textutil import now_iso

log = logging.getLogger("jobradar")


@dataclass
class Context:
    config: Config
    store: Store
    backend: object = None
    notifiers: list = field(default_factory=list)
    run_id: int | None = None
    dry_run: bool = False
    llm_blocked: bool = False
    overrides: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


def setup_logging(cfg: Config, verbose: bool = False) -> None:
    logs = cfg.path("paths.logs")
    logs.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(logs / "jobradar.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None:  # pythonw (scheduled window) has no console
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.addHandler(sh)


def open_context(cfg: Config, *, backend: str | None = None, dry_run: bool = False,
                 need_llm: bool = True, overrides: dict | None = None) -> Context:
    store = Store(cfg.path("paths.db"))
    ctx = Context(config=cfg, store=store, dry_run=dry_run, overrides=overrides or {})
    if need_llm:
        ctx.backend = get_backend(cfg, backend)
    ctx.notifiers = build_notifiers(cfg, dry_run=dry_run)
    return ctx


class RunLock:
    def __init__(self, path: Path, stale_after_sec: int = 3 * 3600):
        self.path, self.stale = path, stale_after_sec

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime < self.stale:
            raise RuntimeError(f"another run seems active (lock {self.path}); delete it if not")
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def run_pipeline(cfg: Config, stages: list[str] | None = None, *, backend: str | None = None,
                 dry_run: bool = False, overrides: dict | None = None) -> dict:
    from jobradar.stages import STAGES

    names = stages or cfg.get("pipeline.stages")
    unknown = [n for n in names if n not in STAGES]
    if unknown:
        raise ValueError(f"unknown stages {unknown}; available: {list(STAGES)}")
    need_llm = any(n in ("triage", "deep_eval") for n in names)

    with RunLock(cfg.home / "data" / "run.lock"):
        ctx = open_context(cfg, backend=backend, dry_run=dry_run, need_llm=need_llm, overrides=overrides)
        ctx.run_id = ctx.store.start_run()
        ctx.stats["_started"] = now_iso()
        try:
            for name in names:
                t0 = time.time()
                stage = STAGES[name]()
                try:
                    ctx.stats[name] = stage.run(ctx)
                except Exception as e:  # noqa: BLE001 - record and keep going
                    log.exception("stage %s crashed", name)
                    ctx.stats[name] = {"crashed": str(e)}
                log.info("stage %-11s %5.1fs %s", name, time.time() - t0, ctx.stats[name])
            if ctx.llm_blocked:
                ctx.stats["note"] = "usage limit reached; pending jobs continue next run"
            ctx.stats["status_counts"] = ctx.store.counts_by_status()
        finally:
            ctx.store.finish_run(ctx.run_id, ctx.stats)
            ctx.store.close()
    return ctx.stats
