"""Stage 2 - triage: a cheap model screens many jobs per call.

Goal is RECALL (do not lose good jobs), not precision: "maybe" goes forward.
Input per job: title, company, location, first ~700 chars of description.
Output: yes / maybe / no + one-line reason.

Routing after triage:
  yes|maybe + has description  -> DEEP_PENDING
  yes|maybe + no description   -> LIGHT_MATCH (e.g. LinkedIn email; you get a digest ping)
  no                           -> REJECTED_TRIAGE

Swap seam: set triage.enabled=false to send everything to deep eval, or
replace this class with one backed by a local model / embeddings.
"""

from __future__ import annotations

import logging

from jobradar.examples import triage_examples
from jobradar.favorites import Favorites
from jobradar.llm import LLMError, UsageLimitReached
from jobradar.llm.schemas import TRIAGE_SCHEMA
from jobradar.models import Status
from jobradar.prompts import load_prompt
from jobradar.textutil import truncate

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


class TriageStage:
    name = "triage"

    def run(self, ctx) -> dict:
        cfg, store = ctx.config, ctx.store
        min_desc = int(cfg.get("triage.min_description_chars", 200))
        stats = {"calls": 0, "yes": 0, "maybe": 0, "no": 0, "light": 0, "errors": 0, "left_pending": 0}

        if not cfg.get("triage.enabled", True):
            for job in store.jobs_by_status(Status.TRIAGE_PENDING):
                has_desc = len(job["description"] or "") >= min_desc
                store.set_status(job["id"], Status.DEEP_PENDING if has_desc else Status.LIGHT_MATCH, "triage disabled")
            store.commit()
            return {"skipped": True}

        favs = Favorites(cfg)
        if favs and cfg.get("favorites.skip_triage", True):
            for job in store.jobs_by_status(Status.TRIAGE_PENDING):
                if favs.is_favorite(job["company"]):
                    has_desc = len(job["description"] or "") >= min_desc
                    store.set_status(job["id"], Status.DEEP_PENDING if has_desc else Status.LIGHT_MATCH,
                                     "⭐ favorite company - skipped triage", attempts=0)
                    stats["favorites_fast_tracked"] = stats.get("favorites_fast_tracked", 0) + 1
            store.commit()

        if ctx.llm_blocked:
            return {"skipped": "llm blocked"}

        batch = int(cfg.get("triage.batch_size", 25))
        max_calls = int(ctx.overrides.get("max_triage_calls") or cfg.get("triage.max_calls_per_run", 8))
        pending = store.jobs_by_status(Status.TRIAGE_PENDING, limit=batch * max_calls)
        if not pending:
            return stats

        system, version = load_prompt(cfg, "triage.md")
        profile = cfg.read_text("paths.profile")
        examples = triage_examples(store, int(cfg.get("examples.triage_max", 20)))
        n_chars = int(cfg.get("triage.excerpt_chars", 700))
        model = cfg.get("llm.triage_model")

        for i in range(0, len(pending), batch):
            chunk = pending[i:i + batch]
            jobs_ctx = [{"id": j["id"], "title": j["title"], "company": j["company"],
                         "location": j["location"] or "", "workplace": j["workplace"] or "",
                         "excerpt": truncate(j["description"] or "", n_chars),
                         "favorite": favs.is_favorite(j["company"])} for j in chunk]
            user = _triage_input(profile, examples, jobs_ctx)
            try:
                res = ctx.backend.complete_json(system=system, user=user, schema=TRIAGE_SCHEMA,
                                                model=model, purpose="triage", context={"jobs": jobs_ctx})
            except UsageLimitReached as e:
                log.warning("usage limit reached during triage - will continue next run (%s)", e)
                ctx.llm_blocked = True
                break
            except LLMError as e:
                log.error("triage call failed: %s", e)
                stats["errors"] += 1
                store.bump_attempts([j["id"] for j in chunk])
                for j in chunk:
                    if j["attempts"] + 1 >= MAX_ATTEMPTS:
                        store.set_status(j["id"], Status.ERROR, f"triage failed: {str(e)[:200]}")
                store.commit()
                continue
            stats["calls"] += 1

            by_id = {str(r.get("job_id")): r for r in res.data.get("results", []) if isinstance(r, dict)}
            for j in chunk:
                r = by_id.get(str(j["id"]))
                if not r or r.get("verdict") not in ("yes", "maybe", "no"):
                    store.bump_attempts([j["id"]])  # model skipped it - retry next run
                    if j["attempts"] + 1 >= MAX_ATTEMPTS:
                        store.set_status(j["id"], Status.ERROR, "triage: model never returned a verdict")
                    continue
                if r.get("rule") not in (None, "none"):  # triage-v3: a named big-no rule means "no"
                    r["verdict"] = "no"
                    stats.setdefault("rules", {})[r["rule"]] = stats.get("rules", {}).get(r["rule"], 0) + 1
                store.add_evaluation(j["id"], "triage", r, res.model, version, res.meta, ctx.run_id)
                verdict = r["verdict"]
                stats[verdict] += 1
                if verdict == "no":
                    store.set_status(j["id"], Status.REJECTED_TRIAGE, r.get("reason", "")[:300])
                elif len(j["description"] or "") >= min_desc:
                    store.set_status(j["id"], Status.DEEP_PENDING, f"triage {verdict}: {r.get('reason', '')}"[:300],
                                     attempts=0)
                else:
                    store.set_status(j["id"], Status.LIGHT_MATCH, f"triage {verdict}: {r.get('reason', '')}"[:300])
                    stats["light"] += 1
            store.commit()

        stats["left_pending"] = len(store.jobs_by_status(Status.TRIAGE_PENDING))
        return stats


def _triage_input(profile: str, examples: str, jobs: list[dict]) -> str:
    parts = [
        "<candidate_profile>", profile.strip() or "(profile missing)", "</candidate_profile>", "",
        "<candidate_ratings_of_past_jobs>", examples, "</candidate_ratings_of_past_jobs>", "",
        "<jobs>",
    ]
    for j in jobs:
        wp = f" ({j['workplace']})" if j["workplace"] else ""
        parts += [
            f'<job id="{j["id"]}">',
            f"title: {j['title']}",
            f"company: {j['company']}",
            f"location: {j['location'] or 'n/a'}{wp}",
            f"candidate_favorite_company: {'yes' if j.get('favorite') else 'no'}",
            f"description_excerpt: {j['excerpt'] or '[none - from an email alert, judge by title/company]'}",
            "</job>",
        ]
    parts.append("</jobs>")
    return "\n".join(parts)
