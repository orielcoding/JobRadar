"""Small text helpers: HTML -> text, normalization for dedupe."""

from __future__ import annotations

import hashlib
import json
import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "table", "section", "article", "header", "footer",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    """Convert (possibly entity-escaped) HTML to readable plain text."""
    if not raw:
        return ""
    s = raw
    if "&lt;" in s and "<" not in s:  # Greenhouse returns escaped HTML
        s = html.unescape(s)
    if "<" not in s:
        return clean_whitespace(html.unescape(s))
    p = _TextExtractor()
    p.feed(s)
    p.close()
    return clean_whitespace("".join(p.parts))


def clean_whitespace(s: str) -> str:
    s = s.replace("\xa0", " ").replace("\r", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


_COMPANY_SUFFIXES = re.compile(
    r"\b(ltd|inc|llc|gmbh|corp|corporation|co|technologies|technology|labs|group|israel)\b\.?"
)


def norm_company(name: str) -> str:
    s = (name or "").lower().replace(".com", "").replace(".io", "").replace(".ai", "")
    s = _COMPANY_SUFFIXES.sub(" ", s)
    s = re.sub(r"[^\w֐-׿]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(title: str) -> str:
    s = (title or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)  # "(Hybrid)", "(Tel Aviv)"
    s = re.sub(r"[^\w֐-׿+#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def content_hash(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_iso(value) -> str | None:
    """Accepts ISO strings, epoch seconds or epoch milliseconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def extract_json(text: str) -> dict | None:
    """First balanced {...} in the text that parses as a JSON object."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
                    break
        start = text.find("{", start + 1)
    return None
