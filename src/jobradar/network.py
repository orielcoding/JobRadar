"""Networking: contacts, interactions (with follow-ups) and application processes.

Everything lives in the same SQLite file as the jobs, so the pipeline can say
"you know 2 people at this company" next to a match, and an application can
point to the job it came from and the person who referred you.

Ways in:
  - LinkedIn connections export (Connections.csv) -> contacts, weak ties
  - the local agent, from plain sentences ("I talked with Dana from Wix...")
    via `jobradar net ...` / `jobradar apps ...` commands
See docs/NETWORKING.md.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from jobradar.textutil import norm_company, now_iso

RELATIONSHIPS = ["friend", "ex-colleague", "alumni", "recruiter", "hiring-manager", "met-once", "other"]
CHANNELS = ["linkedin", "whatsapp", "call", "meeting", "email", "event", "other"]
APP_STATUSES = ["interested", "applied", "screening", "interview", "offer", "rejected", "withdrawn", "ghosted"]
ACTIVE_STATUSES = {"interested", "applied", "screening", "interview", "offer"}
STRENGTH_TXT = {1: "קשר רופף", 2: "מכירים", 3: "קרוב – יכול להפנות"}


def today() -> str:
    return date.today().isoformat()


def parse_date(s: str | None) -> str | None:
    """Accepts YYYY-MM-DD, 'today', '+7' (days from today), or LinkedIn's '15 Mar 2024'."""
    if not s:
        return None
    s = s.strip()
    if s == "today":
        return today()
    if re.fullmatch(r"\+\d+", s):
        return (date.today() + timedelta(days=int(s[1:]))).isoformat()
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date '{s}' (use YYYY-MM-DD, today or +N)")


def companies_match(a_norm: str, b_norm: str) -> bool:
    """Same company after normalization, or one name is a whole-word prefix of the other
    ('wsc sports' vs 'wsc sports technologies' is already equal after normalization;
    'check point' vs 'check point software')."""
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm:
        return True
    short, long_ = sorted((a_norm, b_norm), key=len)
    return len(short) >= 4 and (long_ + " ").startswith(short + " ")


class Network:
    def __init__(self, store):
        self.db = store.db

    # ------------------------------------------------------------ contacts
    def add_contact(self, name: str, company: str = "", role: str = "", relationship: str = "",
                    strength: int | None = None, how_met: str = "", linkedin_url: str = "",
                    email: str = "", tags: str = "", notes: str = "", source: str = "manual",
                    connected_on: str | None = None) -> tuple[int, bool]:
        """Insert, or update the existing contact with the same LinkedIn URL / name+company.
        Returns (id, created)."""
        now = now_iso()
        existing = self._find_existing(name, company, linkedin_url)
        fields = {"name": name, "company": company, "company_norm": norm_company(company), "role": role,
                  "relationship": relationship, "strength": strength, "how_met": how_met,
                  "linkedin_url": linkedin_url or None, "email": email, "tags": tags, "notes": notes,
                  "connected_on": connected_on}
        if existing:
            # Never overwrite something you wrote by hand with an empty value.
            updates = {k: v for k, v in fields.items() if v not in (None, "")}
            if source == "linkedin_csv":  # a re-import must not clobber manual details
                updates = {k: v for k, v in updates.items()
                           if k in ("company", "company_norm", "role", "connected_on", "linkedin_url")
                           or existing[k] in (None, "")}
            self._update("contacts", existing["id"], updates)
            return existing["id"], False
        cur = self.db.execute(
            """INSERT INTO contacts(name, company, company_norm, role, relationship, strength, how_met,
               linkedin_url, email, tags, notes, source, connected_on, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, company, norm_company(company), role, relationship, strength, how_met,
             linkedin_url or None, email, tags, notes, source, connected_on, now, now))
        self.db.commit()
        return int(cur.lastrowid), True

    def _find_existing(self, name, company, linkedin_url):
        if linkedin_url:
            row = self.db.execute("SELECT * FROM contacts WHERE linkedin_url = ?", (linkedin_url,)).fetchone()
            if row:
                return row
        rows = self.db.execute("SELECT * FROM contacts WHERE lower(name) = lower(?)", (name.strip(),)).fetchall()
        cn = norm_company(company)
        for r in rows:
            if not cn or not r["company_norm"] or companies_match(cn, r["company_norm"]):
                return r
        return None

    def update_contact(self, contact_id: int, **fields) -> None:
        fields = {k: v for k, v in fields.items() if v is not None}
        if "company" in fields:
            fields["company_norm"] = norm_company(fields["company"])
        self._update("contacts", contact_id, fields)

    def _update(self, table: str, row_id: int, fields: dict) -> None:
        if not fields:
            return
        fields = dict(fields, updated_at=now_iso()) if table != "interactions" else fields
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*fields.values(), row_id])
        self.db.commit()

    def get_contact(self, contact_id: int):
        return self.db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()

    def find_contacts(self, query: str, limit: int = 20) -> list:
        q = f"%{query.strip().lower()}%"
        return self.db.execute(
            """SELECT * FROM contacts WHERE lower(name) LIKE ? OR lower(coalesce(company,'')) LIKE ?
               OR lower(coalesce(role,'')) LIKE ? OR lower(coalesce(tags,'')) LIKE ?
               OR lower(coalesce(notes,'')) LIKE ?
               ORDER BY coalesce(strength, 1) DESC, name LIMIT ?""", (q, q, q, q, q, limit)).fetchall()

    def resolve_contact(self, ref: str):
        """'12' -> by id; otherwise a unique name match. Raises ValueError if ambiguous/missing."""
        if ref.isdigit():
            row = self.get_contact(int(ref))
            if row:
                return row
            raise ValueError(f"no contact #{ref}")
        rows = self.db.execute("SELECT * FROM contacts WHERE lower(name) LIKE ?",
                               (f"%{ref.strip().lower()}%",)).fetchall()
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise ValueError(f"no contact matching '{ref}' - add it first with `jobradar net add`")
        names = ", ".join(f"#{r['id']} {r['name']} ({r['company'] or '-'})" for r in rows[:8])
        raise ValueError(f"'{ref}' matches several contacts: {names} - use the id")

    def contacts_at(self, company: str) -> list:
        cn = norm_company(company)
        if not cn:
            return []
        first_word = cn.split()[0]
        rows = self.db.execute("SELECT * FROM contacts WHERE company_norm LIKE ?", (f"{first_word}%",)).fetchall()
        hits = [r for r in rows if companies_match(cn, r["company_norm"] or "")]
        return sorted(hits, key=lambda r: (-(r["strength"] or 1), r["name"]))

    # -------------------------------------------------------- interactions
    def log(self, contact_id: int | None, summary: str, channel: str = "other", on: str | None = None,
            follow_up_on: str | None = None, application_id: int | None = None) -> int:
        cur = self.db.execute(
            """INSERT INTO interactions(contact_id, application_id, date, channel, summary, follow_up_on, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (contact_id, application_id, parse_date(on) or today(), channel, summary,
             parse_date(follow_up_on), now_iso()))
        self.db.commit()
        return int(cur.lastrowid)

    def interactions_for(self, contact_id: int) -> list:
        return self.db.execute("SELECT * FROM interactions WHERE contact_id = ? ORDER BY date DESC, id DESC",
                               (contact_id,)).fetchall()

    def followups(self, within_days: int = 7) -> list:
        horizon = (date.today() + timedelta(days=within_days)).isoformat()
        return self.db.execute(
            """SELECT i.*, c.name AS contact_name, c.company AS contact_company FROM interactions i
               LEFT JOIN contacts c ON c.id = i.contact_id
               WHERE i.follow_up_on IS NOT NULL AND i.follow_up_done = 0 AND i.follow_up_on <= ?
               ORDER BY i.follow_up_on""", (horizon,)).fetchall()

    def mark_followup_done(self, interaction_id: int) -> None:
        self.db.execute("UPDATE interactions SET follow_up_done = 1 WHERE id = ?", (interaction_id,))
        self.db.commit()

    # -------------------------------------------------------- applications
    def add_application(self, company: str, title: str, url: str = "", job_id: int | None = None,
                        status: str = "interested", referral_contact_id: int | None = None,
                        notes: str = "", applied_on: str | None = None) -> int:
        if status not in APP_STATUSES:
            raise ValueError(f"status must be one of {APP_STATUSES}")
        now = now_iso()
        if status == "applied" and not applied_on:
            applied_on = today()
        cur = self.db.execute(
            """INSERT INTO applications(job_id, company, company_norm, title, url, status, referral_contact_id,
               applied_on, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, company, norm_company(company), title, url, status, referral_contact_id,
             parse_date(applied_on), notes, now, now))
        app_id = int(cur.lastrowid)
        self.db.execute("INSERT INTO app_events(application_id, date, status, note) VALUES (?,?,?,?)",
                        (app_id, today(), status, notes or None))
        self.db.commit()
        return app_id

    def update_application(self, app_id: int, status: str | None = None, note: str | None = None,
                           next_step: str | None = None, next_step_on: str | None = None,
                           referral_contact_id: int | None = None) -> None:
        app = self.get_application(app_id)
        if not app:
            raise ValueError(f"no application #{app_id}")
        fields: dict = {}
        if status:
            if status not in APP_STATUSES:
                raise ValueError(f"status must be one of {APP_STATUSES}")
            fields["status"] = status
            if status == "applied" and not app["applied_on"]:
                fields["applied_on"] = today()
        if next_step is not None:
            fields["next_step"] = next_step
        if next_step_on is not None:
            fields["next_step_on"] = parse_date(next_step_on)
        if referral_contact_id is not None:
            fields["referral_contact_id"] = referral_contact_id
        if note:
            fields["notes"] = ((app["notes"] + "\n") if app["notes"] else "") + f"[{today()}] {note}"
        self._update("applications", app_id, fields)
        if status or note:
            self.db.execute("INSERT INTO app_events(application_id, date, status, note) VALUES (?,?,?,?)",
                            (app_id, today(), status or app["status"], note))
            self.db.commit()

    def get_application(self, app_id: int):
        return self.db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()

    def applications(self, active_only: bool = False) -> list:
        rows = self.db.execute(
            """SELECT a.*, c.name AS referral_name FROM applications a
               LEFT JOIN contacts c ON c.id = a.referral_contact_id ORDER BY a.updated_at DESC""").fetchall()
        return [r for r in rows if not active_only or r["status"] in ACTIVE_STATUSES]

    def applications_at(self, company: str) -> list:
        cn = norm_company(company)
        return [a for a in self.applications() if companies_match(cn, a["company_norm"])]

    def application_for_job(self, job_id: int):
        return self.db.execute("SELECT * FROM applications WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                               (job_id,)).fetchone()

    def app_events(self, app_id: int) -> list:
        return self.db.execute("SELECT * FROM app_events WHERE application_id = ? ORDER BY id", (app_id,)).fetchall()

    def counts(self) -> dict:
        one = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "contacts": one("SELECT COUNT(*) FROM contacts"),
            "contacts_manual": one("SELECT COUNT(*) FROM contacts WHERE source != 'linkedin_csv'"),
            "interactions": one("SELECT COUNT(*) FROM interactions"),
            "applications": one("SELECT COUNT(*) FROM applications"),
            "open_followups": one("SELECT COUNT(*) FROM interactions WHERE follow_up_on IS NOT NULL AND follow_up_done = 0"),
        }

    # ------------------------------------------------------ LinkedIn import
    def import_linkedin_csv(self, path: Path) -> dict:
        """LinkedIn: Settings -> Data privacy -> Get a copy of your data -> Connections.
        The file starts with a few 'Notes' lines before the real header."""
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        start = next((i for i, l in enumerate(lines) if l.startswith("First Name")), None)
        if start is None:
            raise ValueError("no 'First Name,...' header found - is this LinkedIn's Connections.csv?")
        reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
        created = updated = skipped = 0
        for r in reader:
            name = f"{(r.get('First Name') or '').strip()} {(r.get('Last Name') or '').strip()}".strip()
            if not name:
                skipped += 1
                continue
            try:
                connected = parse_date(r.get("Connected On"))
            except ValueError:
                connected = None
            _, was_created = self.add_contact(
                name=name, company=(r.get("Company") or "").strip(), role=(r.get("Position") or "").strip(),
                linkedin_url=(r.get("URL") or "").strip(), email=(r.get("Email Address") or "").strip(),
                strength=None, source="linkedin_csv", connected_on=connected)
            created += was_created
            updated += not was_created
        self.db.commit()
        return {"created": created, "updated": updated, "skipped": skipped}


def describe_contacts(contacts: list, max_items: int = 3) -> str:
    """'Dana Levi (Data Lead, friend) · Avi Cohen (Recruiter) +2'"""
    parts = []
    for c in contacts[:max_items]:
        bits = [b for b in (c["role"], c["relationship"]) if b]
        parts.append(f"{c['name']}" + (f" ({', '.join(bits)})" if bits else ""))
    more = f" +{len(contacts) - max_items}" if len(contacts) > max_items else ""
    return " · ".join(parts) + more
