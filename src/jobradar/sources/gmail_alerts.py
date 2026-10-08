"""Reads job-alert emails from one Gmail label over IMAP (read-only).

Needs (see docs/SETUP.md):
  config.yaml   gmail.enabled: true, gmail.user, gmail.label
  .env          GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx  (Google "App password")
  Gmail filter  that puts the alert emails under that label
"""

from __future__ import annotations

import email
import imaplib
import logging
from datetime import date, timedelta
from email import policy

from jobradar.models import JobPosting
from jobradar.sources.email_parsers import parse_linkedin, parse_links

log = logging.getLogger(__name__)


class GmailAlertsSource:
    name = "gmail"

    def __init__(self, config):
        self.cfg = config
        self.user = config.get("gmail.user")
        self.label = config.get("gmail.label")
        self.host = config.get("gmail.imap_host")
        self.lookback = int(config.get("gmail.lookback_days", 3))
        self.password = (config.secret("GMAIL_APP_PASSWORD") or "").replace(" ", "")
        self.rules = config.get("email_sources") or []
        self._seen: set[str] = set()
        self._new_ids: list[str] = []
        if not self.user or not self.password:
            raise ValueError("gmail.user and GMAIL_APP_PASSWORD are required")

    def prepare(self, store) -> None:
        self._seen = store.seen_email_ids()

    def after_ingest(self, store) -> None:
        store.add_seen_emails(self._new_ids)

    def _rule_for(self, sender: str) -> dict | None:
        s = sender.lower()
        for r in self.rules:
            if r.get("from_contains", "").lower() in s:
                return r
        return None

    def fetch(self) -> list[JobPosting]:
        out: list[JobPosting] = []
        imap = imaplib.IMAP4_SSL(self.host)
        try:
            imap.login(self.user, self.password)
            typ, _ = imap.select(f'"{self.label}"', readonly=True)
            if typ != "OK":
                raise RuntimeError(f"Gmail label '{self.label}' not found")
            since = (date.today() - timedelta(days=self.lookback)).strftime("%d-%b-%Y")
            typ, data = imap.search(None, f"(SINCE {since})")
            for num in (data[0] or b"").split():
                typ, msg_data = imap.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
                mid = (msg.get("Message-ID") or f"num-{num.decode()}").strip()
                if mid in self._seen:
                    continue
                rule = self._rule_for(msg.get("From", ""))
                if not rule:
                    continue
                jobs = self.parse_message(msg, rule)
                log.info("gmail: %s -> %d jobs (%s)", rule.get("name"), len(jobs), msg.get("Subject", "")[:60])
                out.extend(jobs)
                self._new_ids.append(mid)
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass
        return out

    @staticmethod
    def parse_message(msg, rule: dict) -> list[JobPosting]:
        name = rule.get("name", "email")
        if rule.get("parser") == "linkedin":
            return parse_linkedin(msg, source=name)
        if rule.get("parser") == "links" and rule.get("link_pattern"):
            return parse_links(msg, source=name, link_pattern=rule["link_pattern"])
        return []
