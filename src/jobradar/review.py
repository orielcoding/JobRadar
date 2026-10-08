"""Terminal rating loops - the cheapest way to teach the evaluator your taste.

  jobradar calibrate   rate a sample of fetched jobs (do ~25 once, at setup)
  jobradar review      rate the jobs it pinged you about (2 minutes, weekly)

Each rating = good / ok / bad + an optional one-line "why". The "why" is the
most valuable part: it becomes a few-shot example in every future prompt.
"""

from __future__ import annotations

import random
import textwrap

from jobradar.models import Status
from jobradar.textutil import truncate

_KEYS = {"g": "good", "o": "ok", "b": "bad"}
_HELP = "[g] טובה – הייתי מגיש  [o] גבולית  [b] לא בשבילי  [s] דלג  [q] יציאה"


def _show(job, extra: str = "", desc_chars: int = 1200) -> None:
    print("\n" + "=" * 72)
    print(f"#{job['id']}  {job['title']}  @  {job['company']}")
    print(f"{job['location'] or 'n/a'} {job['workplace'] or ''}  |  {job['url']}")
    if extra:
        print(extra)
    if desc_chars and job["description"]:
        print("-" * 72)
        for para in truncate(job["description"], desc_chars).split("\n"):
            print(textwrap.fill(para, 100) if para else "")
    print("-" * 72)


def _ask(store, job, origin: str) -> bool:
    """Returns False when the user wants to quit."""
    while True:
        ans = input(f"{_HELP}\n> ").strip().lower()
        if ans == "q":
            return False
        if ans == "s" or ans == "":
            return True
        if ans in _KEYS:
            note = input("למה? (משפט אחד, אופציונלי – זה הכי מועיל למודל) > ").strip()
            store.set_label(job["id"], _KEYS[ans], note, origin)
            return True
        print("לא הבנתי.")


def calibration_candidates(store, n: int = 25, seed: int | None = None) -> list:
    """Random unlabeled jobs that passed the hard filters and have a description."""
    rows = store.db.execute(
        """SELECT * FROM jobs WHERE status NOT IN (?, ?) AND length(coalesce(description,'')) > 200
           AND id NOT IN (SELECT job_id FROM labels)""",
        (Status.DUPLICATE.value, Status.FILTERED_OUT.value),
    ).fetchall()
    rng = random.Random(seed)
    return rng.sample(list(rows), min(n, len(rows)))


def review_candidates(store, limit: int = 30) -> list:
    """Unlabeled jobs you were pinged about or that got a deep evaluation, newest first."""
    return store.db.execute(
        """SELECT * FROM jobs WHERE (notified_at IS NOT NULL OR status = ?)
           AND id NOT IN (SELECT job_id FROM labels) ORDER BY id DESC LIMIT ?""",
        (Status.EVALUATED.value, limit),
    ).fetchall()


def job_summary(store, job, desc_chars: int = 800) -> dict:
    """Compact, JSON-friendly view of a job (used by `jobradar list --json`)."""
    ev = store.latest_evaluation(job["id"], "deep") or {}
    return {
        "id": job["id"], "title": job["title"], "company": job["company"],
        "location": job["location"], "workplace": job["workplace"], "url": job["url"],
        "status": job["status"], "notified": job["notified_at"] is not None,
        "scores": ev.get("scores"), "verdict": ev.get("verdict"), "pitch": ev.get("pitch"),
        "description_excerpt": truncate(job["description"] or "", desc_chars),
    }


def calibrate(store, n: int = 25, seed: int | None = None) -> int:
    sample = calibration_candidates(store, n, seed)
    if not sample:
        print("אין משרות לדירוג. הרץ קודם: jobradar fetch")
        return 0
    print(f"דירוג {len(sample)} משרות. דרג לפי תחושת בטן אמיתית – לא לפי מה ש'אמור' להתאים.")
    done = 0
    for i, job in enumerate(sample, 1):
        _show(job, extra=f"({i}/{len(sample)})")
        if not _ask(store, job, "calibrate"):
            break
        done += 1
    return done


def review(store, limit: int = 30) -> int:
    rows = review_candidates(store, limit)
    if not rows:
        print("אין משרות שמחכות לדירוג.")
        return 0
    done = 0
    for job in rows:
        ev = store.latest_evaluation(job["id"], "deep") or {}
        s = ev.get("scores", {})
        extra = ""
        if s:
            extra = (f"מודל: יכולת {s.get('capability')} · רצון {s.get('desire')} · סינון {s.get('screen_pass')}"
                     f" · {ev.get('verdict')}\n{ev.get('pitch', '')}")
        _show(job, extra=extra, desc_chars=600)
        if not _ask(store, job, "review"):
            break
        done += 1
    return done
