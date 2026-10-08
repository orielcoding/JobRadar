"""SQLite storage. One file (data/jobradar.db), no server.

Tables
  jobs         one row per posting; `status` drives the pipeline
  evaluations  every LLM result (triage / deep / eval), with model + prompt version
  labels       your own ratings (good / ok / bad + optional note) - the training signal
  runs         one row per pipeline run with stats
  seen_emails  Message-IDs already parsed from Gmail
  meta         schema version (migration seam)

Networking (schema v2, see network.py and docs/NETWORKING.md)
  contacts       people you know (imported from LinkedIn or added by the agent)
  interactions   conversations / messages / meetings, with optional follow-up date
  applications   application processes, optionally linked to a job and a referrer
  app_events     status history of each application

Tracker (schema v3, see tracker.py and docs/TRACKER.md)
  job_tracking   what you did with each job the radar surfaced:
                 to_review | to_apply | applied | dismissed
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from jobradar.models import JobPosting, Status
from jobradar.textutil import content_hash, norm_company, norm_title, now_iso

SCHEMA_VERSION = "3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,
  source TEXT NOT NULL,
  company TEXT NOT NULL,
  company_norm TEXT NOT NULL,
  title TEXT NOT NULL,
  title_norm TEXT NOT NULL,
  url TEXT,
  location TEXT,
  workplace TEXT,
  department TEXT,
  description TEXT,
  posted_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL,
  status_reason TEXT,
  decision TEXT,
  duplicate_of INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0,
  notified_at TEXT,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_norm);

CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  stage TEXT NOT NULL,
  model TEXT,
  prompt_version TEXT,
  result TEXT NOT NULL,
  meta TEXT,
  run_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_job ON evaluations(job_id, stage);

CREATE TABLE IF NOT EXISTS labels (
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
  label TEXT NOT NULL CHECK (label IN ('good','ok','bad')),
  note TEXT,
  origin TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  stats TEXT
);

CREATE TABLE IF NOT EXISTS seen_emails (message_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  company TEXT,
  company_norm TEXT,
  role TEXT,
  relationship TEXT,          -- friend | ex-colleague | alumni | recruiter | hiring-manager | met-once | other
  strength INTEGER,           -- 1 weak (connection only) · 2 know each other · 3 close / would refer
  how_met TEXT,
  linkedin_url TEXT UNIQUE,
  email TEXT,
  tags TEXT,
  notes TEXT,
  source TEXT,                -- linkedin_csv | manual
  connected_on TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_norm);

CREATE TABLE IF NOT EXISTS interactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER REFERENCES contacts(id),
  application_id INTEGER REFERENCES applications(id),
  date TEXT NOT NULL,
  channel TEXT,               -- linkedin | whatsapp | call | meeting | email | event | other
  summary TEXT NOT NULL,
  follow_up_on TEXT,          -- YYYY-MM-DD
  follow_up_done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER REFERENCES jobs(id),
  company TEXT NOT NULL,
  company_norm TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT,
  status TEXT NOT NULL,       -- interested | applied | screening | interview | offer | rejected | withdrawn | ghosted
  referral_contact_id INTEGER REFERENCES contacts(id),
  applied_on TEXT,
  next_step TEXT,
  next_step_on TEXT,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apps_company ON applications(company_norm);

CREATE TABLE IF NOT EXISTS app_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL REFERENCES applications(id),
  date TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS job_tracking (
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
  state TEXT NOT NULL,        -- to_review | to_apply | applied | dismissed
  surfaced_at TEXT NOT NULL,  -- when the radar first put it in front of you
  reviewed_at TEXT,           -- first move out of to_review
  applied_at TEXT,
  note TEXT,                  -- e.g. why you dismissed it
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracking_surfaced ON job_tracking(surfaced_at);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        # Additive migrations only (new tables via CREATE IF NOT EXISTS), so the
        # version is simply bumped. A breaking change would need a real migration here.
        self.db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---------------------------------------------------------------- jobs
    def upsert_job(self, p: JobPosting) -> tuple[int, bool]:
        """Insert a new posting (status NEW) or refresh an existing one.
        Returns (job_id, is_new). Status of existing jobs is never changed here."""
        now = now_iso()
        h = content_hash(p.title, p.location, p.description)
        row = self.db.execute("SELECT id, content_hash FROM jobs WHERE key = ?", (p.key,)).fetchone()
        if row:
            if row["content_hash"] != h:
                self.db.execute(
                    """UPDATE jobs SET title=?, title_norm=?, location=?, workplace=?, department=?,
                       description=?, url=?, content_hash=?, last_seen_at=? WHERE id=?""",
                    (p.title, norm_title(p.title), p.location, p.workplace, p.department,
                     p.description, p.url, h, now, row["id"]),
                )
            else:
                self.db.execute("UPDATE jobs SET last_seen_at=? WHERE id=?", (now, row["id"]))
            return row["id"], False
        cur = self.db.execute(
            """INSERT INTO jobs(key, source, company, company_norm, title, title_norm, url, location,
               workplace, department, description, posted_at, first_seen_at, last_seen_at,
               content_hash, status, extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p.key, p.source, p.company, norm_company(p.company), p.title, norm_title(p.title),
             p.url, p.location, p.workplace, p.department, p.description, p.posted_at or now,
             now, now, h, Status.NEW.value, json.dumps(p.extra, ensure_ascii=False)),
        )
        return int(cur.lastrowid), True

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def jobs_by_status(self, status: Status, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM jobs WHERE status = ? ORDER BY posted_at DESC, id DESC"
        args: list = [status.value]
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return self.db.execute(sql, args).fetchall()

    def first_fetch_days(self) -> dict[str, str]:
        """source -> date (YYYY-MM-DD) of the first job ever stored from it."""
        rows = self.db.execute("SELECT source, MIN(first_seen_at) FROM jobs GROUP BY source").fetchall()
        return {r[0]: (r[1] or "")[:10] for r in rows}

    def known_jobs(self, source: str, company: str) -> dict[str, sqlite3.Row]:
        """native_id -> stored row, for sources that fetch details only for new postings."""
        rows = self.db.execute("SELECT * FROM jobs WHERE source = ? AND company = ?", (source, company)).fetchall()
        return {r["key"].split(":", 1)[1]: r for r in rows}

    def jobs_same_company(self, company_norm: str, exclude_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM jobs WHERE company_norm = ? AND id != ? AND status != ?",
            (company_norm, exclude_id, Status.DUPLICATE.value),
        ).fetchall()

    def set_status(self, job_id: int, status: Status, reason: str | None = None, **cols) -> None:
        sets = ["status = ?", "status_reason = ?"]
        args: list = [status.value, reason]
        for k, v in cols.items():
            sets.append(f"{k} = ?")
            args.append(v)
        args.append(job_id)
        self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)

    def bump_attempts(self, job_ids: Iterable[int]) -> None:
        self.db.executemany("UPDATE jobs SET attempts = attempts + 1 WHERE id = ?", [(i,) for i in job_ids])

    def set_description(self, job_id: int, description: str) -> None:
        self.db.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))

    def merge_extra(self, job_id: int, values: dict) -> None:
        row = self.db.execute("SELECT extra FROM jobs WHERE id = ?", (job_id,)).fetchone()
        extra = json.loads(row["extra"] or "{}") if row else {}
        extra.update(values)
        self.db.execute("UPDATE jobs SET extra = ? WHERE id = ?", (json.dumps(extra, ensure_ascii=False), job_id))

    def mark_notified(self, job_ids: Iterable[int]) -> None:
        now = now_iso()
        self.db.executemany("UPDATE jobs SET notified_at = ? WHERE id = ?", [(now, i) for i in job_ids])

    def pending_notifications(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM jobs WHERE status = ? AND decision = 'notify' AND notified_at IS NULL",
            (Status.EVALUATED.value,),
        ).fetchall()

    def pending_light_matches(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM jobs WHERE status = ? AND notified_at IS NULL ORDER BY id",
            (Status.LIGHT_MATCH.value,),
        ).fetchall()

    def counts_by_status(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    # --------------------------------------------------------- evaluations
    def add_evaluation(self, job_id: int, stage: str, result: dict, model: str | None,
                       prompt_version: str | None, meta: dict | None = None,
                       run_id: int | None = None) -> None:
        self.db.execute(
            """INSERT INTO evaluations(job_id, stage, model, prompt_version, result, meta, run_id, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, stage, model, prompt_version, json.dumps(result, ensure_ascii=False),
             json.dumps(meta or {}, ensure_ascii=False), run_id, now_iso()),
        )

    def latest_evaluation(self, job_id: int, stage: str) -> dict | None:
        row = self.db.execute(
            "SELECT result FROM evaluations WHERE job_id = ? AND stage = ? ORDER BY id DESC LIMIT 1",
            (job_id, stage),
        ).fetchone()
        return json.loads(row["result"]) if row else None

    def evaluated_since(self, since_iso: str) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT j.*, e.result AS eval_result FROM jobs j
               JOIN evaluations e ON e.job_id = j.id AND e.stage = 'deep'
               WHERE e.created_at >= ? ORDER BY e.id""",
            (since_iso,),
        ).fetchall()

    def triaged_since(self, since_iso: str, status: Status) -> list[sqlite3.Row]:
        """Jobs triaged since `since_iso` that are now in `status` (e.g. REJECTED_TRIAGE)."""
        return self.db.execute(
            """SELECT j.*, e.result AS eval_result, e.created_at AS triaged_at FROM jobs j
               JOIN evaluations e ON e.job_id = j.id AND e.stage = 'triage'
               WHERE e.created_at >= ? AND j.status = ? ORDER BY e.id""",
            (since_iso, status.value),
        ).fetchall()

    # -------------------------------------------------------------- labels
    def set_label(self, job_id: int, label: str, note: str | None, origin: str) -> None:
        self.db.execute(
            """INSERT INTO labels(job_id, label, note, origin, created_at) VALUES (?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET label=excluded.label, note=excluded.note,
               origin=excluded.origin, created_at=excluded.created_at""",
            (job_id, label, note or None, origin, now_iso()),
        )
        self.db.commit()

    def labeled_jobs(self) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT j.*, l.label, l.note, l.created_at AS labeled_at FROM labels l
               JOIN jobs j ON j.id = l.job_id ORDER BY l.created_at DESC"""
        ).fetchall()

    def is_labeled(self, job_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM labels WHERE job_id = ?", (job_id,)).fetchone() is not None

    # ---------------------------------------------------------------- runs
    def start_run(self) -> int:
        cur = self.db.execute("INSERT INTO runs(started_at) VALUES (?)", (now_iso(),))
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict) -> None:
        self.db.execute("UPDATE runs SET finished_at = ?, stats = ? WHERE id = ?",
                        (now_iso(), json.dumps(stats, ensure_ascii=False), run_id))
        self.db.commit()

    def run_started_at(self, run_id: int) -> str:
        return self.db.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()["started_at"]

    # -------------------------------------------------------------- emails
    def seen_email_ids(self) -> set[str]:
        return {r["message_id"] for r in self.db.execute("SELECT message_id FROM seen_emails")}

    def add_seen_emails(self, ids: Iterable[str]) -> None:
        now = now_iso()
        self.db.executemany("INSERT OR IGNORE INTO seen_emails(message_id, seen_at) VALUES (?, ?)",
                            [(i, now) for i in ids])

    def commit(self) -> None:
        self.db.commit()
