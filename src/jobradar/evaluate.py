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

from jobradar.llm import LLMError, UsageLimitReached
from jobradar.stages.deep_eval import evaluate_job


def run_eval(ctx, limit: int = 20) -> dict:
    rows = [r for r in ctx.store.labeled_jobs() if len(r["description"] or "") > 200][:limit]
    if not rows:
        print("אין משרות מדורגות עם תיאור. הרץ jobradar calibrate קודם.")
        return {}
    tp = fp = fn = tn = 0
    disagreements = []
    for r in rows:
        try:
            ev = evaluate_job(ctx, r, stage_name="eval", exclude_example_id=r["id"])
        except UsageLimitReached:
            print("נגמרה מכסת השימוש – עוצר. אפשר להמשיך מאוחר יותר.")
            break
        except LLMError as e:
            print(f"#{r['id']}: error {e}")
            continue
        ctx.store.commit()
        notify = ev["decision"]["notify"]
        s = ev["scores"]
        line = (f"#{r['id']:<5} label={r['label']:<4} model={'notify' if notify else 'skip':<6} "
                f"C{s['capability']} D{s['desire']} S{s['screen_pass']} {ev.get('verdict'):<7} {r['title'][:45]}")
        print(line)
        if r["label"] == "good":
            tp, fn = tp + notify, fn + (not notify)
            if not notify:
                disagreements.append(("missed", r, ev))
        elif r["label"] == "bad":
            fp, tn = fp + notify, tn + (not notify)
            if notify:
                disagreements.append(("false alarm", r, ev))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    print("\n--- סיכום ---")
    print(f"good שזוהו (recall):    {tp}/{tp + fn}" + (f"  = {recall:.0%}" if recall is not None else ""))
    print(f"פינגים נכונים (precision): {tp}/{tp + fp}" + (f"  = {precision:.0%}" if precision is not None else ""))
    if disagreements:
        print("\nאי-הסכמות (כאן כדאי להסתכל כדי לשפר את הפרופיל / הפרומפט):")
        for kind, r, ev in disagreements:
            print(f"- [{kind}] #{r['id']} {r['title']} @ {r['company']}")
            print(f"    הסיבה שלך: {r['note'] or '-'}")
            print(f"    המודל: {ev.get('score_rationale', {}).get('capability', '')[:160]}")
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall}
