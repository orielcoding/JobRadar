"""Stage 3 - deep evaluation: one strong-model call per job.

The prompt (prompts/deep_eval.md, deep-v4) makes the model work in this order:
  1. gate (scope / profession), then must-haves with weight and gap -> capability by a fixed table
  2. desire, red flags
  3. the CV recipe from the master CV, then the screen check on that page -> screen_pass
  4. the recruiter objection (the binding screen constraint), rationales, scores, pitch, verdict
Code (jobradar.scoring) recomputes capability and screen_pass from the model's own
fields (mismatches are recorded) and takes the verdict from the fixed rule.

The notify decision is made here in CODE from the scores (config thresholds),
not by the model - so you can tune it without touching prompts.
"""

from __future__ import annotations

import logging

from jobradar.examples import deep_examples
from jobradar.favorites import Favorites
from jobradar.llm import LLMError, UsageLimitReached
from jobradar.llm.schemas import DEEP_SCHEMA
from jobradar.models import Status
from jobradar.prompts import load_prompt
from jobradar.scoring import (advice_for, capability_from_analysis, screen_from_check,
                              verdict_for)
from jobradar.textutil import truncate

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def decide(result: dict, cfg, favorite: bool = False) -> dict:
    s = result.get("scores") or {}
    cap, des, scr = (int(s.get(k) or 0) for k in ("capability", "desire", "screen_pass"))
    t = dict(cfg.get("thresholds"))
    if favorite:  # ⭐ companies get their own, lower bar (config.yaml › favorites)
        fav = cfg.get("favorites") or {}
        t.update({k: fav[k] for k in ("min_capability", "min_desire", "notify_verdicts") if k in fav})
    notify = (result.get("verdict") in t["notify_verdicts"]
              and cap >= t["min_capability"] and des >= t["min_desire"])
    rank = cap * 2 + des + scr  # for ordering pings; capability weighs double
    if favorite:
        rank += int((cfg.get("favorites") or {}).get("rank_bonus", 0))
    advice = advice_for(result, int(t["cv_gap_flag"]))
    return {"notify": bool(notify), "cv_gap": advice == "tailor", "advice": advice, "rank": rank,
            "favorite": favorite}


def apply_method_checks(result: dict, job_id=None) -> None:
    """Recompute the method's arithmetic (jobradar.scoring) and fix the verdict by rule.
    The model's scores are kept; mismatches are recorded in result['checks']."""
    checks = {"capability": capability_from_analysis(result), "screen_pass": screen_from_check(result)}
    s = result.get("scores") or {}
    for k, v in checks.items():
        if v is not None and v != s.get(k):
            log.info("method check #%s: model %s=%s, recomputed %s", job_id, k, s.get(k), v)
    if "gate" in (result.get("job_analysis") or {}):  # deep-v4 result: verdict by rule
        rule = verdict_for(result)
        if rule != result.get("verdict"):
            checks["verdict_model"] = result.get("verdict")
            result["verdict"] = rule
    result["checks"] = checks


def build_deep_input(cfg, store, job, exclude_example_id=None, favorite: bool = False) -> str:
    desc = truncate(job["description"] or "", int(cfg.get("deep.max_description_chars", 12000)))
    return "\n".join([
        "<candidate_profile>", cfg.read_text("paths.profile").strip() or "(missing)", "</candidate_profile>", "",
        "<candidate_cv>", cfg.read_text("paths.cv").strip() or "(missing)", "</candidate_cv>", "",
        "<candidate_ratings_of_past_jobs>",
        deep_examples(store, cfg, exclude_job_id=exclude_example_id),
        "</candidate_ratings_of_past_jobs>", "",
        "<job_posting>",
        f"title: {job['title']}",
        f"company: {job['company']}",
        f"location: {job['location'] or 'n/a'} {('(' + job['workplace'] + ')') if job['workplace'] else ''}",
        f"department: {job['department'] or 'n/a'}",
        f"candidate_marked_company_as_favorite: {'yes' if favorite else 'no'}",
        "description:",
        desc,
        "</job_posting>",
    ])


def evaluate_job(ctx, job, stage_name: str = "deep", exclude_example_id=None) -> dict:
    """Run one deep evaluation and store it. Raises LLMError / UsageLimitReached."""
    cfg = ctx.config
    favorite = Favorites(cfg).is_favorite(job["company"])
    system, version = load_prompt(cfg, "deep_eval.md")
    # Never show the evaluator the candidate's own rating of the job it is evaluating.
    exclude = exclude_example_id if exclude_example_id is not None else job["id"]
    user = build_deep_input(cfg, ctx.store, job, exclude, favorite)
    res = ctx.backend.complete_json(
        system=system, user=user, schema=DEEP_SCHEMA, model=cfg.get("llm.deep_model"),
        purpose="deep", context={"job": dict(job)},
    )
    result = res.data
    scores = result.setdefault("scores", {})
    for k in ("capability", "desire", "screen_pass"):
        try:
            scores[k] = max(1, min(10, int(scores.get(k, 1))))
        except (TypeError, ValueError):
            scores[k] = 1
    apply_method_checks(result, job["id"])
    result["decision"] = decide(result, cfg, favorite)
    ctx.store.add_evaluation(job["id"], stage_name, result, res.model, version, res.meta, ctx.run_id)
    return result


class DeepEvalStage:
    name = "deep_eval"

    def run(self, ctx) -> dict:
        if ctx.llm_blocked:
            return {"skipped": "llm blocked"}
        store = ctx.store
        limit = int(ctx.overrides.get("max_deep") or ctx.config.get("deep.max_per_run", 12))
        stats = {"evaluated": 0, "notify": 0, "errors": 0}
        for job in store.jobs_by_status(Status.DEEP_PENDING, limit=limit):
            try:
                result = evaluate_job(ctx, job)
            except UsageLimitReached as e:
                log.warning("usage limit reached during deep eval - will continue next run (%s)", e)
                ctx.llm_blocked = True
                break
            except LLMError as e:
                log.error("deep eval failed for #%s: %s", job["id"], e)
                stats["errors"] += 1
                store.bump_attempts([job["id"]])
                if job["attempts"] + 1 >= MAX_ATTEMPTS:
                    store.set_status(job["id"], Status.ERROR, f"deep eval failed: {str(e)[:200]}")
                store.commit()
                continue
            d = result["decision"]
            store.set_status(job["id"], Status.EVALUATED, result.get("verdict"),
                             decision="notify" if d["notify"] else "skip")
            store.commit()
            stats["evaluated"] += 1
            stats["notify"] += int(d["notify"])
        stats["left_pending"] = len(store.jobs_by_status(Status.DEEP_PENDING))
        return stats
