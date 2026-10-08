"""Tailored one-page CV for one job: `jobradar cv <id>` and the dashboard's "CV" button.

Spends Pro quota: one `claude -p` call to write the CV, plus one web-search call
when the posting text has to be found. The method is the same one the /cv chat
command uses (prompts/cv_tailor.md), run headless with prompts/cv_dashboard.md:
there is no conversation, so the questions /cv would ask come back as "open
questions" with the safe defaults that were used.

1. Posting text, first that works:
   - the job's description in the DB (>= 300 chars);
   - the public ATS API, for Comeet / Lever / Greenhouse links (stages/enrich.py);
   - the posting page itself, for other non-LinkedIn links;
   - a `claude -p` web search for the same job on the company's own careers page
     or ATS (WebSearch + WebFetch only, LinkedIn blocked);
   - otherwise the caller must supply it (the dashboard asks for a paste).
   LinkedIn is never opened (project rule).
2. The CV: one call with no tools. Inputs are profile.md, cv.md, interview notes,
   the posting, the latest deep evaluation and earlier tailored CVs. Contact data
   never goes to the model: `cv.contact_line` in config.yaml fills it in locally.
3. Files:
     profile/tailored/CV <First> <Last> - <Role> - <Company>.pdf   the CV to send
     profile/tailored/work/<date>_<id>_<company>_<role>.md     the CV source (edit + `jobradar cv-pdf`)
                                              .note.md         why each line, risks, interview points
                                              .posting.txt     the posting it was tailored to
                                              .json            metadata the dashboard shows
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from jobradar import http
from jobradar.cv_render import markdown_to_pdf, render_html
from jobradar.llm import get_backend
from jobradar.prompts import load_prompt
from jobradar.store import Store
from jobradar.textutil import html_to_text, norm_company

log = logging.getLogger(__name__)

MIN_POSTING = 300
_LEGACY_BASE = re.compile(r"^\d{4}-\d\d-\d\d_(\d+)_")

CV_SCHEMA = {
    "type": "object",
    "properties": {
        "cv_markdown": {"type": "string"},
        "note_markdown": {"type": "string"},
        "headline": {"type": "string"},
        "family": {"type": "string"},
        "main_risk": {"type": "string"},
        "summary_he": {"type": "string"},
        "open_questions": {"type": "array", "items": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "default_used": {"type": "string"}},
            "required": ["question", "default_used"]}},
        "master_cv_proposals": {"type": "array", "items": {
            "type": "object",
            "properties": {"before": {"type": "string"}, "after": {"type": "string"}, "why": {"type": "string"}},
            "required": ["before", "after", "why"]}},
    },
    "required": ["cv_markdown", "note_markdown", "headline", "main_risk", "summary_he", "open_questions",
                 "master_cv_proposals"],
}

POSTING_SCHEMA = {
    "type": "object",
    "properties": {"found": {"type": "boolean"}, "url": {"type": "string"},
                   "description": {"type": "string"}, "note": {"type": "string"}},
    "required": ["found", "url", "description", "note"],
}
_NO_LINKEDIN = ["WebFetch(domain:linkedin.com)", "WebFetch(domain:www.linkedin.com)",
                "WebFetch(domain:il.linkedin.com)", "WebFetch(domain:lnkd.in)"]


class NeedPosting(Exception):
    """No posting text could be found automatically; ask the user to paste it."""


def is_linkedin(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return host == "lnkd.in" or host == "linkedin.com" or host.endswith(".linkedin.com")


def slug(text: str, maxlen: int = 40) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > maxlen:
        s = s[:maxlen].rsplit("-", 1)[0]
    return s


def title_case_slug(s: str) -> str:
    return "-".join(w if w.isdigit() else w[:1].upper() + w[1:] for w in s.split("-") if w)


def _file_part(text: str, maxlen: int = 60) -> str:
    """A job title or company as readable ASCII that is safe in a Windows file name."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-_")
    if len(s) > maxlen:
        s = s[:maxlen].rsplit(" ", 1)[0]
    return s


def pdf_filename(name: str, role: str, company: str) -> str:
    """'CV Dana Levi - Data Scientist - Acme.pdf'. name is 'First_Last' from the CV heading."""
    parts = [f"CV {name.replace('_', ' ')}" if name and name != "CV" else "CV",
             _file_part(role), _file_part(company, 40)]
    return " - ".join(x for x in parts if x) + ".pdf"


def _words(md: str) -> int:
    return len(re.findall(r"[\w’'+#.-]+", re.sub(r"[#*\-|]", " ", md)))


class CVBuilder:
    def __init__(self, cfg, backend_name: str | None = None, progress=None):
        self.cfg = cfg
        self.backend_name = backend_name
        self.progress = progress or (lambda step: None)
        self.pdf_dir = cfg.path("paths.tailored")
        self.work_dir = self.pdf_dir / "work"

    # ------------------------------------------------------------ lookup
    def cv_index(self) -> dict[int, str]:
        """job id -> base name of its latest CV (for the dashboard's 📄 mark)."""
        out: dict[int, tuple[float, str]] = {}
        if not self.work_dir.exists():
            return {}
        for p in self.work_dir.glob("*.md"):
            if p.name.endswith(".note.md"):
                continue
            m = _LEGACY_BASE.match(p.name)
            if m:
                jid, t = int(m.group(1)), p.stat().st_mtime
                if jid not in out or t > out[jid][0]:
                    out[jid] = (t, p.stem)
        return {k: v[1] for k, v in out.items()}

    def find(self, job_id: int) -> dict | None:
        base = self.cv_index().get(job_id)
        if not base:
            return None
        meta_path = self.work_dir / f"{base}.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        meta.setdefault("base", base)
        meta.setdefault("job_id", job_id)
        pdf = self.pdf_dir / meta["pdf"] if meta.get("pdf") else None
        meta["pdf_exists"] = bool(pdf and pdf.exists())
        return meta

    def cv_markdown(self, base: str) -> str:
        return (self.work_dir / f"{base}.md").read_text(encoding="utf-8")

    def cv_html(self, base: str) -> str:
        return render_html(self.cv_markdown(base), title=base)

    # ----------------------------------------------------------- posting
    def resolve_posting(self, job, backend) -> tuple[str, str]:
        """(text, where it came from). Raises NeedPosting."""
        desc = (job["description"] or "").strip()
        if len(desc) >= MIN_POSTING:
            return desc, "jobradar DB"
        url = job["url"] or ""
        from jobradar.stages.enrich import EnrichStage, match_url
        if url and match_url(url):
            self.progress("posting_ats")
            try:
                text = EnrichStage().fetch_description(url)
                if len(text) >= MIN_POSTING:
                    return text, f"public ATS API ({url})"
            except Exception as e:  # noqa: BLE001
                log.info("cv: ATS fetch failed for #%s: %s", job["id"], e)
        elif url and not is_linkedin(url):
            self.progress("posting_page")
            try:
                text = html_to_text(http.get_text(url, headers={"Accept": "text/html"}))
                title_words = [w for w in re.findall(r"[a-z]{4,}", (job["title"] or "").lower())]
                if len(text) >= 500 and (not title_words or any(w in text.lower() for w in title_words)):
                    return text[:20000], f"posting page ({url})"
            except Exception as e:  # noqa: BLE001
                log.info("cv: page fetch failed for #%s: %s", job["id"], e)
        if self.cfg.get("cv.search_posting", True):
            self.progress("posting_search")
            found = self._search_posting(job, backend)
            if found:
                return found
        if desc:
            return desc, "jobradar DB (short)"
        raise NeedPosting(job["id"])

    def _search_posting(self, job, backend) -> tuple[str, str] | None:
        system, _ver = load_prompt(self.cfg, "cv_posting_search.md")
        user = (f"title: {job['title']}\ncompany: {job['company']}\nlocation: {job['location'] or ''}\n"
                f"seen at: {job['url'] or ''}\n")
        try:
            res = backend.complete_json(system=system, user=user, schema=POSTING_SCHEMA,
                                        model=self.cfg.get("cv.search_model", "sonnet"), purpose="cv_posting",
                                        tools=["WebSearch", "WebFetch"], deny=_NO_LINKEDIN, max_turns=12,
                                        timeout=int(self.cfg.get("cv.search_timeout_sec", 300)))
        except Exception as e:  # noqa: BLE001 - a failed search only means "ask for a paste"
            log.info("cv: posting search failed for #%s: %s", job["id"], e)
            return None
        d = res.data
        text = (d.get("description") or "").strip()
        if d.get("found") and len(text) >= MIN_POSTING and not is_linkedin(d.get("url", "")):
            return text, f"web search ({d.get('url')})"
        log.info("cv: posting search for #%s found nothing: %s", job["id"], d.get("note"))
        return None

    # ------------------------------------------------------------- build
    def _inputs(self, store: Store, job, posting: str, source: str) -> str:
        profile_path = self.cfg.path("paths.profile")
        notes = profile_path.parent / "interview_notes.md"
        parts = []

        def mtime(p: Path) -> str:
            return dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")

        for title, p in (("profile.md", profile_path), ("cv.md (the pool)", self.cfg.path("paths.cv")),
                         ("interview_notes.md", notes)):
            if p.exists():  # HTML comments are notes for the user, not the model (as in Config.read_text)
                text = re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8"), flags=re.S).strip()
                parts.append(f"===== {title} (modified {mtime(p)}) =====\n{text}")
        parts.append("===== job =====\n" + "\n".join(
            f"{k}: {job[k]}" for k in ("id", "title", "company", "location", "workplace", "url")))
        parts.append(f"===== posting (source: {source}) =====\n{posting}")
        ev = store.db.execute("SELECT prompt_version, created_at, result FROM evaluations WHERE job_id = ? "
                              "AND stage = 'deep' ORDER BY id DESC LIMIT 1", (job["id"],)).fetchone()
        parts.append(f"===== latest deep evaluation ({ev['prompt_version']}, created {ev['created_at']}) =====\n"
                     f"{ev['result']}" if ev else "===== latest deep evaluation =====\nnone")
        earlier = sorted((p for p in self.work_dir.glob("*.md") if not p.name.endswith(".note.md")),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:3] if self.work_dir.exists() else []
        for p in earlier:
            parts.append(f"===== earlier tailored CV {p.name} =====\n{p.read_text(encoding='utf-8')[:6000]}")
        return "\n\n".join(parts)

    def build(self, job_id: int, posting_text: str | None = None) -> dict:
        backend = get_backend(self.cfg, self.backend_name)
        store = Store(self.cfg.path("paths.db"))
        try:
            job = store.get_job(job_id)
            if not job:
                raise ValueError(f"no job #{job_id}")
            if job["status"] == "duplicate" and job["duplicate_of"]:
                job = store.get_job(job["duplicate_of"]) or job
            self.progress("posting")
            if posting_text and len(posting_text.strip()) >= 50:
                posting, source = posting_text.strip(), "pasted by the user"
            else:
                posting, source = self.resolve_posting(job, backend)
            if len(posting) > len(job["description"] or ""):
                store.set_description(job["id"], posting)  # `jobradar show` and later runs see it too
                store.commit()

            self.progress("writing")
            method, ver = load_prompt(self.cfg, "cv_tailor.md")
            headless, hver = load_prompt(self.cfg, "cv_dashboard.md")
            model = self.cfg.get("cv.model", "sonnet")
            res = backend.complete_json(system=method + "\n\n---\n\n" + headless,
                                        user=self._inputs(store, job, posting, source), schema=CV_SCHEMA,
                                        model=model, purpose="cv", context={"job": dict(job)},
                                        timeout=int(self.cfg.get("cv.timeout_sec", 900)))
            d = res.data
            cv_md = self._fill_contact(d["cv_markdown"].strip() + "\n")

            company_slug = slug(norm_company(job["company"]), 30) or slug(job["company"], 30) or "company"
            role_slug = slug(job["title"]) or "role"
            base = f"{dt.date.today().isoformat()}_{job_id}_{company_slug}_{role_slug}"
            name = self._name(cv_md)
            pdf_name = pdf_filename(name, job["title"] or title_case_slug(role_slug).replace("-", " "),
                                    job["company"] or "")
            self.work_dir.mkdir(parents=True, exist_ok=True)
            (self.work_dir / f"{base}.md").write_text(cv_md, encoding="utf-8")
            (self.work_dir / f"{base}.note.md").write_text(d["note_markdown"].strip() + "\n", encoding="utf-8")
            (self.work_dir / f"{base}.posting.txt").write_text(
                f"{job['title']} @ {job['company']}\n{job['url'] or ''}\nsource: {source}\n\n{posting}\n",
                encoding="utf-8")

            self.progress("pdf")
            pdf_info = markdown_to_pdf(cv_md, self.pdf_dir / pdf_name, title=pdf_name[:-4])
            meta = {
                "job_id": job_id, "base": base, "pdf": pdf_name, "title": job["title"], "company": job["company"],
                "url": job["url"], "posting_source": source,
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "model": res.model, "prompt_versions": [ver, hver], "words": _words(cv_md), **pdf_info,
                **{k: d.get(k) for k in ("headline", "family", "main_risk", "summary_he", "open_questions",
                                         "master_cv_proposals")},
            }
            (self.work_dir / f"{base}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                                        encoding="utf-8")
            store.merge_extra(job_id, {"cv": base})
            store.commit()
            meta["pdf_exists"] = True
            return meta
        finally:
            store.close()

    def render_pdf(self, base: str) -> dict:
        """Re-print the PDF after the .md was edited (e.g. after /cv in chat)."""
        meta_path = self.work_dir / f"{base}.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"base": base}
        cv_md = self._fill_contact(self.cv_markdown(base))
        m = re.match(r"^\d{4}-\d\d-\d\d_[^_]+_(.+?)(?:_(.+))?$", base)
        company, role = (m.group(1), m.group(2) or "") if m else (base, "")
        company = meta.get("company") or title_case_slug(company).replace("-", " ")
        role = meta.get("title") or title_case_slug(role).replace("-", " ")
        old = meta.get("pdf")
        meta["pdf"] = pdf_filename(self._name(cv_md), role, company)
        if old and old != meta["pdf"]:  # an older naming scheme: replace the file, don't leave a copy
            (self.pdf_dir / old).unlink(missing_ok=True)
        meta.update(markdown_to_pdf(cv_md, self.pdf_dir / meta["pdf"], title=meta["pdf"][:-4]))
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return meta

    # ---------------------------------------------------------- helpers
    def _fill_contact(self, cv_md: str) -> str:
        line = (self.cfg.get("cv.contact_line") or "").strip()
        if not line:
            return cv_md
        lines = cv_md.split("\n")
        for i, s in enumerate(lines):
            if s.startswith("# "):
                for j in range(i + 1, min(i + 4, len(lines))):
                    if lines[j].strip():
                        if not lines[j].lstrip().startswith(("#", "-", "**")):
                            lines[j] = line
                        break
                break
        return "\n".join(lines)

    @staticmethod
    def _name(cv_md: str) -> str:
        m = re.search(r"^# (.+)$", cv_md, re.M)
        n = unicodedata.normalize("NFKD", m.group(1) if m else "").encode("ascii", "ignore").decode()
        n = re.sub(r"[^A-Za-z ]+", "", n).strip()
        return "_".join(n.split()) or "CV"

    def open_path(self, job_id: int, what: str) -> Path:
        """The local file or folder the dashboard's buttons open."""
        meta = self.find(job_id)
        if what == "folder":
            return self.pdf_dir
        if not meta:
            raise ValueError("no CV for this job yet")
        if what == "pdf":
            if not meta.get("pdf_exists"):
                meta = self.render_pdf(meta["base"])
            return self.pdf_dir / meta["pdf"]
        if what in ("note", "md", "posting"):
            suffix = {"note": ".note.md", "md": ".md", "posting": ".posting.txt"}[what]
            return self.work_dir / f"{meta['base']}{suffix}"
        raise ValueError(f"unknown file kind '{what}'")


def open_local(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"missing: {path.name}")
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # noqa: S606 - a local file the user asked to open
    else:
        import webbrowser
        webbrowser.open(path.as_uri())
