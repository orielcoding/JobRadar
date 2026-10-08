"""Tracker - what you did with every job the radar put in front of you.

The pipeline ends at "here is a match". The tracker picks up from there:

  to_review   בהמתנה לעיון    surfaced, you have not looked yet (default)
  to_apply    בהמתנה להגשה    you looked and did not reject it
  applied     הוגש            you sent an application (also opens an `apps` process)
  dismissed   נדחה לאחר עיון  you looked and decided no (optional one-line reason)

"Surfaced" = everything the radar showed you: light matches (passed triage, no
description) and deep evaluations whose verdict is not "no". Rows are created
lazily by `sync()`, so the pipeline stages stay untouched (they still talk only
through job status). Your decisions also become implicit ratings (labels with
origin "tracker") unless you already rated the job yourself, so the evaluator
learns from what you actually do.

UI: `jobradar inbox` (inbox_server.py). Daily nudge: `jobradar inbox --push`.
See docs/TRACKER.md.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from jobradar.models import Status
from jobradar.textutil import now_iso

STATES = ["to_review", "to_apply", "applied", "dismissed"]
STATE_HE = {"to_review": "בהמתנה לעיון", "to_apply": "בהמתנה להגשה",
            "applied": "הוגש", "dismissed": "נדחה לאחר עיון"}
WINDOWS = {"2d": 2, "7d": 7, "30d": 30}
_LABEL_FOR = {"to_apply": "good", "applied": "good", "dismissed": "bad"}


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local_day(iso: str) -> str:
    """The calendar day (local time) of an ISO timestamp."""
    return _parse(iso).astimezone().date().isoformat()


class Tracker:
    def __init__(self, store):
        self.store = store
        self.db = store.db

    # ---------------------------------------------------------------- sync
    def sync(self) -> int:
        """Create a row for every newly surfaced job. Starts as to_review, unless you already
        acted on it elsewhere: an application process -> applied, your own rating
        good -> to_apply, bad -> dismissed. Returns how many rows were added."""
        rows = self.db.execute(
            """SELECT j.id, j.first_seen_at,
                      (SELECT MIN(e.created_at) FROM evaluations e
                        WHERE e.job_id = j.id AND e.stage IN ('triage', 'deep')) AS evaluated_at,
                      (SELECT a.status FROM applications a WHERE a.job_id = j.id
                        ORDER BY a.id DESC LIMIT 1) AS app_status,
                      l.label, l.note AS label_note, l.created_at AS labeled_at
               FROM jobs j LEFT JOIN labels l ON l.job_id = j.id AND l.origin != 'tracker'
               WHERE j.id NOT IN (SELECT job_id FROM job_tracking)
                 AND (j.status = ? OR (j.status = ? AND coalesce(j.status_reason, '') != 'no'))""",
            (Status.LIGHT_MATCH.value, Status.EVALUATED.value)).fetchall()
        now = now_iso()
        for r in rows:
            note = None
            if r["app_status"] not in (None, "interested", "withdrawn"):
                state, reviewed, applied = "applied", now, now
            elif r["label"] in ("good", "bad"):
                state = "to_apply" if r["label"] == "good" else "dismissed"
                reviewed, applied = r["labeled_at"], None
                note = r["label_note"] if r["label"] == "bad" else None
            else:
                state, reviewed, applied = "to_review", None, None
            self.db.execute(
                """INSERT INTO job_tracking(job_id, state, surfaced_at, reviewed_at, applied_at, note, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (r["id"], state, r["evaluated_at"] or r["first_seen_at"], reviewed, applied, note, now))
        self.db.commit()
        return len(rows)

    # --------------------------------------------------------------- state
    def set_state(self, job_id: int, state: str, note: str | None = None) -> dict:
        """Move a job to `state`. Applied opens an application process (once); moving back
        from applied marks that process withdrawn. Returns the updated row."""
        if state not in STATES:
            raise ValueError(f"state must be one of {STATES}")
        self.sync()
        row = self.db.execute("SELECT * FROM job_tracking WHERE job_id = ?", (job_id,)).fetchone()
        job = self.store.get_job(job_id)
        if not job:
            raise ValueError(f"no job #{job_id}")
        now = now_iso()
        if not row:  # e.g. a job you found yourself, or one the radar filtered out
            self.db.execute("INSERT INTO job_tracking(job_id, state, surfaced_at, updated_at) VALUES (?,?,?,?)",
                            (job_id, "to_review", now, now))
            row = self.db.execute("SELECT * FROM job_tracking WHERE job_id = ?", (job_id,)).fetchone()
        prev = row["state"]
        reviewed_at = None if state == "to_review" else (row["reviewed_at"] or now)
        applied_at = (row["applied_at"] or now) if state == "applied" else None
        self.db.execute(
            "UPDATE job_tracking SET state=?, reviewed_at=?, applied_at=?, note=?, updated_at=? WHERE job_id=?",
            (state, reviewed_at, applied_at, note if note is not None else row["note"], now, job_id))
        self._sync_application(job, prev, state)
        self._implicit_label(job_id, state, note)
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM job_tracking WHERE job_id = ?", (job_id,)).fetchone())

    def _sync_application(self, job, prev: str, state: str) -> None:
        from jobradar.network import Network
        net = Network(self.store)
        app = net.application_for_job(job["id"])
        if state == "applied":
            if not app:
                net.add_application(job["company"], job["title"], job["url"] or "", job_id=job["id"],
                                    status="applied", notes="נפתח מהמעקב")
            elif app["status"] in ("interested", "withdrawn"):
                net.update_application(app["id"], status="applied", note="סומן כהוגש במעקב")
        elif prev == "applied" and app and app["status"] == "applied":
            net.update_application(app["id"], status="withdrawn", note="הסימון 'הוגש' בוטל במעקב")

    def _implicit_label(self, job_id: int, state: str, note: str | None) -> None:
        """Your decision is a rating too - but never overwrite a rating you gave yourself."""
        cur = self.db.execute("SELECT origin FROM labels WHERE job_id = ?", (job_id,)).fetchone()
        if cur and cur["origin"] != "tracker":
            return
        if state in _LABEL_FOR:
            self.db.execute(
                """INSERT INTO labels(job_id, label, note, origin, created_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(job_id) DO UPDATE SET label=excluded.label, note=excluded.note,
                   created_at=excluded.created_at""",
                (job_id, _LABEL_FOR[state], note or None, "tracker", now_iso()))
        elif cur:  # back to to_review: no decision any more
            self.db.execute("DELETE FROM labels WHERE job_id = ? AND origin = 'tracker'", (job_id,))

    # ------------------------------------------------------------- metrics
    def funnel(self, days: int, now: datetime | None = None) -> dict:
        """Of the jobs surfaced in the last `days` days: how many you reviewed, and how
        many of the reviewed you applied to (a cohort view, by surfaced date)."""
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=days)).isoformat()
        rows = self.db.execute("SELECT state FROM job_tracking WHERE surfaced_at >= ?", (since,)).fetchall()
        n = {s: 0 for s in STATES}
        for r in rows:
            n[r["state"]] += 1
        surfaced = len(rows)
        reviewed = surfaced - n["to_review"]
        return {"days": days, "surfaced": surfaced, "reviewed": reviewed, "applied": n["applied"],
                "to_apply": n["to_apply"], "dismissed": n["dismissed"], "to_review": n["to_review"],
                "reviewed_pct": round(100 * reviewed / surfaced) if surfaced else None,
                "applied_pct": round(100 * n["applied"] / reviewed) if reviewed else None}

    def funnels(self) -> dict:
        return {k: self.funnel(d) for k, d in WINDOWS.items()}

    def open_counts(self, days: int = 7) -> dict:
        """What is waiting for you: to_review / to_apply, inside the window and older."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        q = "SELECT COUNT(*) FROM job_tracking WHERE state = ? AND surfaced_at {} ?"
        one = lambda st, op: self.db.execute(q.format(op), (st, since)).fetchone()[0]  # noqa: E731
        return {"to_review": one("to_review", ">="), "to_apply": one("to_apply", ">="),
                "older_to_review": one("to_review", "<"), "older_to_apply": one("to_apply", "<")}

    # --------------------------------------------------------------- items
    def items(self, days: int = 7, is_favorite=None) -> list[dict]:
        """Everything surfaced in the last `days` days, with what you need to decide."""
        from jobradar.network import Network
        from jobradar.stages.report import main_reason
        is_favorite = is_favorite or (lambda company: False)
        net = Network(self.store)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.execute(
            """SELECT t.*, j.title, j.company, j.url, j.location, j.source, j.status AS job_status,
                      length(coalesce(j.description, '')) AS desc_len
               FROM job_tracking t JOIN jobs j ON j.id = t.job_id
               WHERE t.surfaced_at >= ? ORDER BY t.surfaced_at DESC, t.job_id DESC""", (since,)).fetchall()
        out = []
        for r in rows:
            deep = self.store.latest_evaluation(r["job_id"], "deep") or {}
            tri = self.store.latest_evaluation(r["job_id"], "triage") or {}
            scores = deep.get("scores") or {}
            contacts = net.contacts_at(r["company"])
            known = [c for c in contacts if (c["strength"] or 1) >= 2 or c["relationship"]]
            app = net.application_for_job(r["job_id"])
            out.append({
                "id": r["job_id"], "state": r["state"], "note": r["note"] or "",
                "surfaced_at": r["surfaced_at"], "day": local_day(r["surfaced_at"]),
                "title": r["title"], "company": r["company"], "url": r["url"] or "",
                "location": r["location"] or "", "source": r["source"],
                "kind": "deep" if deep else "light", "has_description": r["desc_len"] > 200,
                "verdict": deep.get("verdict") or "", "triage": tri.get("verdict") or "",
                "scores": {k: scores.get(k) for k in ("capability", "desire", "screen_pass")} if scores else None,
                "why_yes": deep.get("pitch") or tri.get("reason") or "",
                "why_no": main_reason(deep) if deep else "",
                "favorite": bool(is_favorite(r["company"])),
                "contacts": len(contacts), "known": [c["name"] for c in known[:3]],
                "app_status": app["status"] if app else "",
            })
        return out

    # -------------------------------------------------------------- digest
    def digest_text(self) -> tuple[str, str]:
        """Title and body of the daily push."""
        oc, f = self.open_counts(), self.funnels()
        title = f"📋 JobRadar · {oc['to_review']} לעיון · {oc['to_apply']} להגשה"
        lines = []
        for key, label in (("2d", "יומיים"), ("7d", "שבוע"), ("30d", "חודש")):
            x = f[key]
            rp = f" ({x['reviewed_pct']}%)" if x["reviewed_pct"] is not None else ""
            ap = f" ({x['applied_pct']}% מהנבדקות)" if x["applied_pct"] is not None else ""
            lines.append(f"{label}: {x['surfaced']} הגיעו · {x['reviewed']} נבדקו{rp} · {x['applied']} הוגשו{ap}")
        if oc["older_to_review"]:
            lines.append(f"ועוד {oc['older_to_review']} ישנות משבוע שלא נבדקו")
        lines.append("פתח במחשב: קיצור הדרך JobRadar או jobradar inbox")
        return title, "\n".join(lines)


def summary_json(store) -> str:
    t = Tracker(store)
    t.sync()
    return json.dumps({"open": t.open_counts(), "funnels": t.funnels(), "today": date.today().isoformat()},
                      ensure_ascii=False, indent=1)
