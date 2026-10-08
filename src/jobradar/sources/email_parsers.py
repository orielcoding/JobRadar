"""Parsers that turn job-alert emails into JobPostings.

Email alerts are how JobRadar gets LinkedIn (and AllJobs / Drushim / Indeed)
without scraping those sites: the site does the search, emails you, and we
read the email. Alerts carry title / company / location and a link, but
usually NOT the description, so these jobs can only become "light matches"
unless dedupe finds the same job on the company's own career board.

Parsers:
  linkedin  dedicated parser for jobalerts-noreply@linkedin.com
  links     generic: every <a> whose href matches `link_pattern` (regex with
            one capture group = job id) becomes a job titled by the link text.
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from jobradar.models import JobPosting
from jobradar.textutil import clean_whitespace, to_iso

_LI_VIEW_RE = re.compile(r"https?://[\w.]*linkedin\.com/(?:comm/)?jobs/view/(\d+)[^\s>\"]*")
_LI_NOISE = re.compile(
    r"^(view job|apply|easy apply|promoted|actively recruiting|fast growing|be an early applicant|"
    r"\d+ (connection|alum|school alum|company alum)|.*applicants?$|new$|saved$|see all jobs|"
    r"apply with .*|this company is actively hiring|\d+[+,]* (new )?jobs?.*|-{3,}.*|_{3,}.*|"
    r"your job alert.*|.*match your preferences.*|.*jobs? match.*)",
    re.I,
)


def _parts(msg: EmailMessage) -> tuple[str, str]:
    text, html_ = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.is_multipart():
            continue
        try:
            payload = part.get_content()
        except (LookupError, ValueError):
            continue
        if ctype == "text/plain" and not text:
            text = payload
        elif ctype == "text/html" and not html_:
            html_ = payload
    return text, html_


def _email_date(msg: EmailMessage) -> str | None:
    try:
        return to_iso(parsedate_to_datetime(msg["Date"]).isoformat())
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ LinkedIn
def parse_linkedin(msg: EmailMessage, source: str = "linkedin_email") -> list[JobPosting]:
    text, html_ = _parts(msg)
    posted = _email_date(msg)
    jobs = _linkedin_from_text(text, source, posted) if text else []
    if not jobs and html_:
        jobs = _linkedin_from_html(html_, source, posted)
    return jobs


def _linkedin_from_text(text: str, source: str, posted: str | None) -> list[JobPosting]:
    out, seen = [], set()
    block: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        m = _LI_VIEW_RE.search(line)
        if m:
            job_id = m.group(1)
            lines = [b for b in block if not _LI_NOISE.match(b) and not b.startswith("http")]
            block = []
            if job_id in seen or not lines:
                continue
            seen.add(job_id)
            lines = lines[-3:] if len(lines) > 3 else lines
            title = lines[0]
            company = lines[1] if len(lines) > 1 else ""
            location = lines[2] if len(lines) > 2 else ""
            if " · " in company and not location:  # "Acme · Tel Aviv, Israel" on one line
                company, location = (x.strip() for x in company.split(" · ", 1))
            out.append(_li_job(job_id, title, company, location, source, posted))
        elif line:
            block.append(line)
        elif block and len(block) > 6:  # long header paragraph - drop it
            block = []
    return out


class _AnchorTokens(HTMLParser):
    """Flattens HTML into a token list: ("a", href, text) and ("t", text)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._flush()
            self._href = dict(attrs).get("href") or ""

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.tokens.append(("a", self._href, clean_whitespace("".join(self._buf))))
            self._buf, self._href = [], None
        elif tag in ("p", "div", "td", "tr", "br", "span", "table"):
            if self._href is None:
                self._flush()

    def handle_data(self, data):
        self._buf.append(data)

    def _flush(self):
        t = clean_whitespace("".join(self._buf))
        if t and self._href is None:
            self.tokens.append(("t", t))
            self._buf = []
        elif self._href is None:
            self._buf = []

    def close(self):
        super().close()
        self._flush()


def _linkedin_from_html(html_: str, source: str, posted: str | None) -> list[JobPosting]:
    p = _AnchorTokens()
    p.feed(html_)
    p.close()
    out, seen = [], set()
    toks = p.tokens
    for i, tok in enumerate(toks):
        if tok[0] != "a":
            continue
        m = _LI_VIEW_RE.search(tok[1])
        if not m or not tok[2] or _LI_NOISE.match(tok[2]) or m.group(1) in seen:
            continue
        job_id = m.group(1)
        seen.add(job_id)
        following = []
        for t in toks[i + 1:]:
            if t[0] == "a" and _LI_VIEW_RE.search(t[1]) and _LI_VIEW_RE.search(t[1]).group(1) != job_id:
                break
            txt = t[2] if t[0] == "a" else t[1]
            for piece in re.split(r"\s+·\s+|\n", txt):
                piece = piece.strip()
                if piece and not _LI_NOISE.match(piece) and piece != tok[2]:
                    following.append(piece)
            if len(following) >= 2:
                break
        company = following[0] if following else ""
        location = following[1] if len(following) > 1 else ""
        out.append(_li_job(job_id, tok[2], company, location, source, posted))
    return out


def _li_job(job_id, title, company, location, source, posted) -> JobPosting:
    return JobPosting(
        source=source,
        native_id=job_id,
        company=company or "(unknown)",
        title=title,
        url=f"https://www.linkedin.com/jobs/view/{job_id}/",
        location=location,
        workplace="remote" if "remote" in location.lower() else ("hybrid" if "hybrid" in location.lower() else ""),
        posted_at=posted,
        extra={"from_email": True},
    )


# --------------------------------------------------------------- generic links
def parse_links(msg: EmailMessage, source: str, link_pattern: str) -> list[JobPosting]:
    _, html_ = _parts(msg)
    if not html_:
        return []
    rx = re.compile(link_pattern)
    p = _AnchorTokens()
    p.feed(html_)
    p.close()
    posted = _email_date(msg)
    sender = (msg.get("From") or "").split("<")[0].strip().strip('"')
    out, seen = [], set()
    for tok in p.tokens:
        if tok[0] != "a" or not tok[2]:
            continue
        m = rx.search(tok[1])
        if not m:
            continue
        jid = m.group(1) if m.groups() else m.group(0)
        if jid in seen:
            continue
        seen.add(jid)
        out.append(JobPosting(source=source, native_id=jid, company=sender or source, title=tok[2],
                              url=tok[1], posted_at=posted, extra={"from_email": True}))
    return out
