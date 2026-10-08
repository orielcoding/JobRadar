"""Few-shot examples built from YOUR labels (jobradar calibrate / review).

This is how the evaluator learns your judgment without you having to describe
it in prompt jargon: every rating (good / ok / bad) plus the optional one-line
"why" becomes an example shown to the model.

Selection today: most recent labels, balanced by class.
Upgrade seam (docs/ROADMAP.md): pick the examples most SIMILAR to the job
being evaluated (embeddings / keyword overlap) once you have 60+ labels.
"""

from __future__ import annotations

from jobradar.textutil import truncate

_LABEL_TXT = {"good": "GOOD - I would apply", "ok": "OK - borderline", "bad": "BAD - not for me"}


def triage_examples(store, max_n: int) -> str:
    rows = store.labeled_jobs()[:max_n]
    if not rows:
        return "(no examples yet)"
    lines = []
    for r in rows:
        note = f" | why: {r['note']}" if r["note"] else ""
        lines.append(f"- [{r['label'].upper()}] {r['title']} @ {r['company']} ({r['location'] or 'n/a'}){note}")
    return "\n".join(lines)


def deep_examples(store, cfg, exclude_job_id: int | None = None) -> str:
    quota = {"good": cfg.get("examples.deep_max_good", 4),
             "ok": cfg.get("examples.deep_max_ok", 2),
             "bad": cfg.get("examples.deep_max_bad", 4)}
    n_chars = int(cfg.get("examples.excerpt_chars", 500))
    picked: list = []
    # Prefer labels that have a note - the "why" is the most informative part.
    # sorted() is stable, so within each group the newest labels stay first.
    rows = sorted(store.labeled_jobs(), key=lambda r: r["note"] is None)
    for r in rows:
        if r["id"] == exclude_job_id or quota.get(r["label"], 0) <= 0:
            continue
        quota[r["label"]] -= 1
        picked.append(r)
    if not picked:
        return "(no examples yet - rely on the profile)"
    blocks = []
    for r in picked:
        blocks.append(
            f"<example label=\"{r['label']}\">\n"
            f"rating: {_LABEL_TXT[r['label']]}\n"
            f"candidate's reason: {r['note'] or '(none given)'}\n"
            f"job: {r['title']} @ {r['company']} ({r['location'] or 'n/a'})\n"
            f"excerpt: {truncate(r['description'] or '(no description)', n_chars)}\n"
            f"</example>"
        )
    return "\n".join(blocks)
