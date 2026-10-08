"""Stage 0 - fetch every source and store postings (status NEW)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from jobradar import http
from jobradar.sources import build_sources

log = logging.getLogger(__name__)


class IngestStage:
    name = "ingest"

    def run(self, ctx) -> dict:
        http.configure(ctx.config.get("fetch.user_agent"), ctx.config.get("fetch.timeout_sec"))
        sources, warnings = build_sources(ctx.config)
        for w in warnings:
            log.warning("config: %s", w)
        for s in sources:
            if hasattr(s, "prepare"):
                s.prepare(ctx.store)

        stats = {"sources": len(sources), "sources_failed": 0, "fetched": 0, "new": 0, "failed": []}
        workers = max(1, int(ctx.config.get("fetch.workers", 4)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(s.fetch): s for s in sources}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    postings = fut.result()
                except Exception as e:  # noqa: BLE001 - one broken source must not stop the run
                    stats["sources_failed"] += 1
                    stats["failed"].append(f"{src.name}: {e}")
                    log.warning("source %s failed: %s", src.name, e)
                    continue
                for p in postings:
                    if not p.title:
                        continue
                    _, is_new = ctx.store.upsert_job(p)
                    stats["new"] += int(is_new)
                stats["fetched"] += len(postings)
                if hasattr(src, "after_ingest"):
                    src.after_ingest(ctx.store)
                ctx.store.commit()
                log.info("source %s: %d postings", src.name, len(postings))
        return stats
