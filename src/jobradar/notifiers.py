"""Notification channels.

Contract: a notifier has `name` and
    send(title: str, body: str, url: str | None = None, priority: int = 3) -> None
`body` is plain text with light markdown. Configure channels in config.yaml
`notify.channels`; secrets in .env.

  ntfy      free phone push, no account. .env: NTFY_TOPIC (created by `jobradar init`)
  telegram  .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  email     sends to yourself via Gmail SMTP. Uses gmail.user + GMAIL_APP_PASSWORD
  console   prints to the terminal (dry runs)
"""

from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

from jobradar import http

log = logging.getLogger(__name__)


class ConsoleNotifier:
    name = "console"

    def send(self, title, body, url=None, priority=3):
        print(f"\n=== {title} ===\n{body}\n{url or ''}")


class NtfyNotifier:
    name = "ntfy"

    def __init__(self, config):
        self.server = config.get("notify.ntfy_server").rstrip("/")
        self.topic = config.secret("NTFY_TOPIC")
        if not self.topic:
            raise ValueError("NTFY_TOPIC missing in .env (run `jobradar init`)")

    def send(self, title, body, url=None, priority=3):
        payload = {"topic": self.topic, "title": title, "message": body,
                   "priority": priority, "markdown": True, "tags": ["dart"]}
        if url:
            payload["click"] = url
            payload["actions"] = [{"action": "view", "label": "פתח משרה", "url": url}]
        # JSON publishing (POST to the server root) keeps Hebrew safe in titles.
        http.post_json(self.server, payload)


class TelegramNotifier:
    name = "telegram"

    def __init__(self, config):
        self.token = config.secret("TELEGRAM_BOT_TOKEN")
        self.chat = config.secret("TELEGRAM_CHAT_ID")
        if not (self.token and self.chat):
            raise ValueError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env")

    def send(self, title, body, url=None, priority=3):
        text = f"<b>{html.escape(title)}</b>\n{html.escape(body)}"
        if url:
            text += f'\n<a href="{html.escape(url)}">פתח משרה</a>'
        http.post_json(f"https://api.telegram.org/bot{self.token}/sendMessage",
                       {"chat_id": self.chat, "text": text, "parse_mode": "HTML",
                        "disable_web_page_preview": True})


class EmailNotifier:
    name = "email"

    def __init__(self, config):
        self.user = config.get("gmail.user")
        self.password = (config.secret("GMAIL_APP_PASSWORD") or "").replace(" ", "")
        self.to = config.secret("NOTIFY_EMAIL_TO") or self.user
        if not (self.user and self.password):
            raise ValueError("email notifier needs gmail.user and GMAIL_APP_PASSWORD")

    def send(self, title, body, url=None, priority=3):
        msg = EmailMessage()
        msg["Subject"] = f"[JobRadar] {title}"
        msg["From"] = self.user
        msg["To"] = self.to
        msg.set_content(body + (f"\n\n{url}" if url else ""))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(self.user, self.password)
            s.send_message(msg)


_TYPES = {"console": ConsoleNotifier, "ntfy": NtfyNotifier, "telegram": TelegramNotifier, "email": EmailNotifier}


def build_notifiers(config, dry_run: bool = False) -> list:
    if dry_run:
        return [ConsoleNotifier()]
    out = []
    for name in config.get("notify.channels") or []:
        cls = _TYPES.get(name)
        if not cls:
            log.warning("unknown notify channel '%s'", name)
            continue
        try:
            out.append(cls() if cls is ConsoleNotifier else cls(config))
        except ValueError as e:
            log.warning("notify channel %s disabled: %s", name, e)
    return out or [ConsoleNotifier()]
