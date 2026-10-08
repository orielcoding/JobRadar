"""Tailored-CV markdown -> one-page, ATS-safe HTML -> PDF (headless Chrome / Edge).

Only the small markdown subset of the CV skeleton in prompts/cv_tailor.md §7 is
supported: `#` name, `##` sections, `###` entry lines, `-` bullets (with indented
continuation lines), `**bold**`, `*italic*` and [links](url). One column, no
tables or icons, real text - so ATS parsers read it like a Word file.
"""

from __future__ import annotations

import html
import re
import subprocess
import tempfile
from pathlib import Path

from jobradar.browser import any_chromium

# (font pt, line height, page margin in) - tried in order until the CV fits on one page
_SIZES = [(10.5, 1.3, 0.5), (10.25, 1.25, 0.5), (10.0, 1.22, 0.45)]

_CSS = """
@page { size: A4; margin: %(margin)sin; }
* { box-sizing: border-box; }
html { background: #fff; }
body { margin: 0; color: #111; font: %(font)spt/%(lh)s Calibri, Carlito, Arial, sans-serif; }
h1 { font-size: 1.75em; margin: 0 0 1pt; letter-spacing: .01em; }
.contact { color: #333; font-size: .93em; margin: 0 0 6pt; }
.headline { margin: 0 0 4pt; }
h2 { font-size: 1.02em; text-transform: uppercase; letter-spacing: .06em; margin: 8pt 0 3pt;
     padding-bottom: 1pt; border-bottom: .75pt solid #555; }
h3 { font-size: 1em; margin: 5pt 0 1pt; font-weight: 700; display: flex; gap: 12pt; justify-content: space-between; }
h3 .when { font-weight: 400; white-space: nowrap; }
p { margin: 0 0 3pt; }
ul { margin: 0 0 2pt; padding-inline-start: 13pt; }
li { margin: 0 0 1.5pt; }
a { color: #1f4e9c; text-decoration: none; }
@media screen {   /* the dashboard preview: a white A4 sheet with the print margins */
  html { background: #e9e9e6; }
  body { background: #fff; width: 210mm; min-height: 297mm; margin: 0 auto; padding: %(margin)sin; }
}
"""

_CONTACT_LINK = re.compile(r"([\w.+-]+@[\w-]+\.[\w.]+)|((?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com)/[^\s·]+)")


def _contact(s: str) -> str:
    def link(m):
        text = m.group(0)
        href = "mailto:" + text if m.group(1) else (text if text.startswith("http") else "https://" + text)
        return f'<a href="{href}">{text}</a>'
    return _CONTACT_LINK.sub(link, html.escape(s, quote=False))


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", s)
    return s


def markdown_to_body(md: str) -> str:
    out: list[str] = []
    in_list = False
    seen_h1 = contact_done = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</li></ul>")
            in_list = False

    for raw in md.replace("\r", "").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            close_list()
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
            seen_h1 = True
            continue
        if seen_h1 and not contact_done and not stripped.startswith(("#", "-", "**")):
            out.append(f'<p class="contact">{_contact(stripped)}</p>')
            contact_done = True
            continue
        contact_done = contact_done or seen_h1
        if stripped.startswith("## "):
            close_list()
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            close_list()
            # "Title | Employer | dates": the last part sits on the right, like the earlier PDFs
            parts = [_inline(p.strip()) for p in stripped[4:].split(" | ")]
            left, right = (parts[:-1], parts[-1]) if len(parts) > 1 else (parts, "")
            out.append(f'<h3><span>{" | ".join(left)}</span><span class="when">{right}</span></h3>')
        elif re.match(r"^[-*] ", stripped) and not line.startswith(("  ", "\t")):
            if in_list:
                out.append("</li>")
            else:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}")
        elif in_list and line.startswith(("  ", "\t")):
            out.append(f"<br>{_inline(stripped)}")   # e.g. "Relevant coursework: ..." under a degree
        else:
            close_list()
            cls = ' class="headline"' if stripped.startswith("**") and stripped.endswith("**") else ""
            out.append(f"<p{cls}>{_inline(stripped)}</p>")
    close_list()
    return "\n".join(out)


def render_html(md: str, size: int = 0, title: str = "CV") -> str:
    font, lh, margin = _SIZES[min(size, len(_SIZES) - 1)]
    css = _CSS % {"font": font, "lh": lh, "margin": margin}
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{css}</style></head><body>{markdown_to_body(md)}</body></html>")


def pdf_page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


def print_pdf(html_text: str, out: Path, timeout: int = 90) -> int:
    """Print HTML to `out` with headless Chrome/Edge. Returns the page count."""
    exe = any_chromium()
    if not exe:
        raise RuntimeError("Chrome or Edge is needed to make the PDF, and neither was found")
    with tempfile.TemporaryDirectory(prefix="jobradar_pdf_") as tmp:
        src = Path(tmp) / "cv.html"
        src.write_text(html_text, encoding="utf-8")
        tmp_pdf = Path(tmp) / "cv.pdf"
        cmd = [str(exe), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--no-pdf-header-footer", f"--user-data-dir={Path(tmp) / 'profile'}",
               f"--print-to-pdf={tmp_pdf}", src.as_uri()]
        subprocess.run(cmd, capture_output=True, timeout=timeout)
        if not tmp_pdf.exists():
            raise RuntimeError("the browser did not produce a PDF")
        data = tmp_pdf.read_bytes()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return pdf_page_count(data)


def markdown_to_pdf(md: str, out: Path, title: str = "CV") -> dict:
    """Render, shrinking slightly (never below 10 pt) until it fits on one page."""
    pages = 0
    for size in range(len(_SIZES)):
        pages = print_pdf(render_html(md, size, title), out)
        if pages <= 1:
            return {"pages": pages, "font_pt": _SIZES[size][0]}
    return {"pages": pages, "font_pt": _SIZES[-1][0]}
