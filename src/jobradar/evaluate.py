"""Eval harness: how well does the evaluator agree with YOUR labels?

    jobradar eval --limit 20

Re-runs the deep evaluation on jobs you labeled (leaving that job's own label
out of the examples, so the model cannot copy it) and compares:
    label good -> should notify      label bad -> should not notify
Results are stored with the prompt version, so after you edit
prompts/deep_eval.md (and bump its version tag) you can run eval again and
see whether the change helped. This is the safe way to improve prompts.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from jobradar.llm import LLMError, UsageLimitReached
from jobradar.stages.deep_eval import call_model, finish_job, prepare_job

_HIDDEN = ("no", "big_no")


def run_eval(ctx, limit: int = 20, ids: list[int] | None = None) -> dict:
    rows = [r for r in ctx.store.labeled_jobs() if len(r["description"] or "") > 200]
    rows = [r for r in rows if r["id"] in set(ids)] if ids else rows[:limit]
    if not rows:
        print("אין משרות מדורגות עם תיאור. הרץ jobradar calibrate קודם.")
        return {}
    tp = fp = fn = tn = 0
    shown_good = hidden_bad = n_good = n_bad = 0
    disagreements = []
    workers = max(1, int(ctx.config.get("deep.workers", 3)))
    # model calls in parallel; DB reads and writes stay in this thread (see stages/deep_eval.py)
    preps = [prepare_job(ctx, r, exclude_example_id=r["id"]) for r in rows]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(call_model, ctx, p): p for p in preps}
        for fut in as_completed(futures):
            r = futures[fut]["job"]
            try:
                ev = finish_job(ctx, futures[fut], fut.result(), stage_name="eval")
            except UsageLimitReached:
                print("נגמרה מכסת השימוש – עוצר. אפשר להמשיך מאוחר יותר.")
                for f in futures:
                    f.cancel()
                break
            except LLMError as e:
                print(f"#{r['id']}: error {e}")
                continue
            ctx.store.commit()
            notify, shown = ev["decision"]["notify"], ev.get("verdict") not in _HIDDEN
            s = ev["scores"]
            print(f"#{r['id']:<5} label={r['label']:<4} model={'notify' if notify else ('shown' if shown else 'hidden'):<6} "
                  f"F{s['capability']} D{s['desire']} S{s['screen_pass']} {ev.get('verdict'):<12} {r['title'][:45]}")
            if r["label"] == "good":
                n_good += 1
                shown_good += shown
                tp, fn = tp + notify, fn + (not notify)
                if not shown:
                    disagreements.append(("hidden", r, ev))
            elif r["label"] == "bad":
                n_bad += 1
                hidden_bad += not shown
                fp, tn = fp + notify, tn + (not notify)
                if notify:
                    disagreements.append(("false alarm", r, ev))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    print("\n--- סיכום ---")
    print(f"good שהוצגו לך (לא נעלמו): {shown_good}/{n_good}")
    print(f"good עם פינג (recall):      {tp}/{tp + fn}" + (f"  = {recall:.0%}" if recall is not None else ""))
    print(f"bad שהוסתרו:                {hidden_bad}/{n_bad}")
    print(f"פינגים נכונים (precision):  {tp}/{tp + fp}" + (f"  = {precision:.0%}" if precision is not None else ""))
    if disagreements:
        print("\nאי-הסכמות (כאן כדאי להסתכל כדי לשפר את הפרופיל / הפרומפט):")
        for kind, r, ev in disagreements:
            print(f"- [{kind}] #{r['id']} {r['title']} @ {r['company']}")
            print(f"    הסיבה שלך: {r['note'] or '-'}")
            why = ev.get("reason_line") or ev.get("score_rationale", {}).get("capability", "")
            print(f"    המודל: {why[:200]}")
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall,
            "shown_good": shown_good, "n_good": n_good, "hidden_bad": hidden_bad, "n_bad": n_bad}
