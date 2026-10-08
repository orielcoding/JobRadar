"""Stage 3 - deep evaluation: one strong-model call per job.

The prompt (prompts/deep_eval.md, deep-v5) asks "is this posting worth applying to?":
  1. read the posting: primary activity, firm requirements, seniority
  2. big-no checks (dealbreaker, out family, central blocker skill, prior-role gate, years cap, eligibility)
  3. gap per requirement (none / learnable / risk / cap / blocker), desire
  4. apply_decision (strong_apply / apply / long_shot / big_no) and a one-line reason
  5. the CV recipe and the screen check on that page -> screen_pass, objection, pitch
Code (jobradar.scoring) recomputes the decision from the model's own fields (mismatches
are recorded) and derives a 1-10 fit score for display and ranking. deep-v4 rows
(capability / verdict) stay readable.

The notify decision is made here in CODE from the decision and desire (config
thresholds), not by the model - so you can tune it without touching prompts.
"""

from __future__ import annotations

import logging
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed

from jobradar.examples import deep_examples
from jobradar.favorites import Favorites
from jobradar.llm import LLMError, UsageLimitReached
from jobradar.llm.schemas import DEEP_SCHEMA
from jobradar.models import Status
from jobradar.prompts import load_prompt
from jobradar.scoring import (advice_for, big_no_rule, capability_from_analysis, decision_for,
                              fit_score, is_v5, risk_points_for, screen_from_check, verdict_for)
from jobradar.textutil import truncate

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
_TIER = {"strong_apply": 30, "apply": 20, "long_shot": 10, "big_no": 0}


def decide(result: dict, cfg, favorite: bool = False) -> dict:
    s = result.get("scores") or {}
    cap, des, scr = (int(s.get(k) or 0) for k in ("capability", "desire", "screen_pass"))
    t = dict(cfg.get("thresholds"))
    if favorite:  # ⭐ companies get their own, lower bar (config.yaml › favorites)
        fav = cfg.get("favorites") or {}
        t.update({k: fav[k] for k in ("min_capability", "min_desire", "notify_verdicts") if k in fav})
    v5 = is_v5(result)
    notify = (result.get("verdict") in t["notify_verdicts"] and des >= t["min_desire"]
              and (v5 or cap >= t.get("min_capability", 0)))
    if v5:  # decision tier first, then desire and screen
        rank = _TIER.get(result.get("verdict"), 0) + des + scr
    else:
        rank = cap * 2 + des + scr  # deep-v4: capability weighs double
    if favorite:
        rank += int((cfg.get("favorites") or {}).get("rank_bonus", 0))
    advice = advice_for(result, int(t["cv_gap_flag"]))
    return {"notify": bool(notify), "cv_gap": advice == "tailor", "advice": advice, "rank": rank,
            "favorite": favorite}


def apply_method_checks(result: dict, job_id=None) -> None:
    """Recompute the method's arithmetic (jobradar.scoring) and fix the decision by rule.
    deep-v5: `verdict` = the apply decision, `scores.desire` / `scores.capability` (fit score)
    are filled so older readers keep working. Mismatches are recorded in result['checks']."""
    if is_v5(result):
        scores = result.setdefault("scores", {})
        scores["desire"] = (result.get("desire") or {}).get("score")
        scores["capability"] = fit_score(result)
        checks = {"risk_points": risk_points_for(result), "screen_pass": screen_from_check(result)}
        if checks["risk_points"] != result.get("risk_points"):
            log.info("method check #%s: model risk_points=%s, recomputed %s",
                     job_id, result.get("risk_points"), checks["risk_points"])
        rule = decision_for(result)
        if rule != result.get("apply_decision"):
            checks["decision_model"] = result.get("apply_decision")
            log.info("method check #%s: model decision=%s, rule %s", job_id, result.get("apply_decision"), rule)
        if rule == "big_no" and big_no_rule(result) == "none":
            checks["far_fetched"] = True  # B7: many risks and low desire
        result["apply_decision"] = result["verdict"] = rule
        result["checks"] = checks
        return
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


def prepare_job(ctx, job, exclude_example_id=None) -> dict:
    """Everything one deep call needs, read from the DB (main thread only)."""
    cfg = ctx.config
    favorite = Favorites(cfg).is_favorite(job["company"])
    system, version = load_prompt(cfg, "deep_eval.md")
    # Never show the evaluator the candidate's own rating of the job it is evaluating.
    exclude = exclude_example_id if exclude_example_id is not None else job["id"]
    return {"job": job, "favorite": favorite, "system": system, "version": version,
            "user": build_deep_input(cfg, ctx.store, job, exclude, favorite)}


def call_model(ctx, prep: dict):
    """The model call alone: no DB access, so it can run in a worker thread."""
    return ctx.backend.complete_json(
        system=prep["system"], user=prep["user"], schema=DEEP_SCHEMA, model=ctx.config.get("llm.deep_model"),
        purpose="deep", context={"job": dict(prep["job"])},
    )


def finish_job(ctx, prep: dict, res, stage_name: str = "deep") -> dict:
    """Check the scores, decide, store the evaluation (main thread only)."""
    job, result = prep["job"], res.data
    apply_method_checks(result, job["id"])
    scores = result.setdefault("scores", {})
    for k in ("capability", "desire", "screen_pass"):
        try:
            scores[k] = max(1, min(10, int(scores.get(k, 1))))
        except (TypeError, ValueError):
            scores[k] = 1
    result["decision"] = decide(result, ctx.config, prep["favorite"])
    ctx.store.add_evaluation(job["id"], stage_name, result, res.model, prep["version"], res.meta, ctx.run_id)
    return result


def evaluate_job(ctx, job, stage_name: str = "deep", exclude_example_id=None) -> dict:
    """Run one deep evaluation and store it. Raises LLMError / UsageLimitReached."""
    prep = prepare_job(ctx, job, exclude_example_id)
    return finish_job(ctx, prep, call_model(ctx, prep), stage_name)


class DeepEvalStage:
    name = "deep_eval"

    def run(self, ctx) -> dict:
        if ctx.llm_blocked:
            return {"skipped": "llm blocked"}
        store = ctx.store
        limit = int(ctx.overrides.get("max_deep") or ctx.config.get("deep.max_per_run", 12))
        workers = max(1, int(ctx.config.get("deep.workers", 3)))
        stats = {"evaluated": 0, "notify": 0, "errors": 0}
        # Model calls run in parallel threads; every DB read and write stays in this thread.
        preps = [prepare_job(ctx, job) for job in store.jobs_by_status(Status.DEEP_PENDING, limit=limit)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(call_model, ctx, p): p for p in preps}
            for fut in as_completed(futures):
                job = futures[fut]["job"]
                try:
                    result = finish_job(ctx, futures[fut], fut.result())
                except UsageLimitReached as e:
                    if not ctx.llm_blocked:
                        log.warning("usage limit reached during deep eval - will continue next run (%s)", e)
                    ctx.llm_blocked = True
                    for f in futures:  # calls not started yet stay DEEP_PENDING for the next run
                        f.cancel()
                    continue
                except CancelledError:
                    continue
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
                log.info("deep #%s %s @ %s: %s", job["id"], job["title"], job["company"], result.get("verdict"))
        stats["left_pending"] = len(store.jobs_by_status(Status.DEEP_PENDING))
        return stats
