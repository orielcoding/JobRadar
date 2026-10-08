"""Stage 1b - hard filters in code (free, instant, predictable).

Everything here is a rule you can state precisely (location, seniority words
in the title, departments, age). Anything that needs judgment belongs to the
LLM stages, not here. Rules come from config/filters.yaml.
"""

from __future__ import annotations

import re

from jobradar.favorites import Favorites
from jobradar.models import Status
from jobradar.textutil import age_days

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_N = r"(\d{1,2}|" + "|".join(_NUM_WORDS) + r")"
_YEARS_RE = re.compile(
    r"(?:at least|minimum(?: of)?|min\.?|over|more than|לפחות)?\s*" + _N +
    r"\s*\+?\s*(?:(?:-|–|to|עד)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?|שנ(?:ות|ים|ה))", re.I)
_EXPERIENCE_WORD = re.compile(r"experience|exp\b|background|hands[- ]on|ניסיון|נסיון", re.I)
_DEFAULT_IGNORE = ["advantage", "a plus", "nice to have", "preferred", "bonus", "desirable", "יתרון"]


def _col(job, name: str):
    return job[name] if name in job.keys() else None


def job_age_days(job, late_sources=(), backfill_day: str | None = None) -> float | None:
    """Age used by every freshness rule (max_age_days, dedupe window, ping age).
    Most sources report when a job was posted. Late sources (filters.late_sources,
    e.g. techmap) list jobs days after posting, so their age counts from when the
    radar first saw the job - except jobs from that source's first fetch
    (`backfill_day`), which still count by posted_at so a first run is not a flood."""
    first_seen = _col(job, "first_seen_at") or ""
    seen = age_days(first_seen)
    if _col(job, "source") in late_sources and seen is not None and first_seen[:10] != backfill_day:
        return seen
    posted = age_days(_col(job, "posted_at"))
    return posted if posted is not None else seen


def required_years(description: str, ignore_words: list[str] | None = None) -> tuple[int | None, str]:
    """Highest 'minimum years of experience' stated in a requirement line.
    Returns (years, the line it came from). Lines that are about advantages /
    nice-to-haves are skipped, and the number must sit near an experience word,
    so 'founded 10 years ago' does not count. Ranges count by their low end."""
    ignore = [w.lower() for w in (ignore_words or _DEFAULT_IGNORE)]
    best, where = None, ""
    for line in (description or "").splitlines():
        low = line.lower()
        if any(w in low for w in ignore):
            continue
        for m in _YEARS_RE.finditer(line):
            window = line[max(0, m.start() - 40): m.end() + 80]
            if not _EXPERIENCE_WORD.search(window):
                continue
            raw = m.group(1).lower()
            n = int(raw) if raw.isdigit() else _NUM_WORDS[raw]
            if 0 < n <= 20 and (best is None or n > best):
                best, where = n, line.strip()[:160]
    return best, where


class HardFilter:
    def __init__(self, f: dict, is_favorite=None, backfill_days: dict | None = None):
        # favorites (companies.yaml) are exempt from locations.deny_any_of
        self.is_favorite = is_favorite or (lambda company: False)
        self.late_sources = set(f.get("late_sources") or [])
        self.backfill_days = backfill_days or {}  # source -> date of its first fetch
        loc = f.get("locations") or {}
        self.loc_allow = [s.lower() for s in loc.get("allow_any_of") or []]
        self.loc_allow_unknown = bool(loc.get("allow_unknown", True))
        self.loc_deny = [s.lower() for s in loc.get("deny_any_of") or []]
        self.workplace_allow = [s.lower() for s in (f.get("workplace") or {}).get("allow") or []]
        t = f.get("title") or {}
        self.title_inc = [re.compile(p, re.I) for p in t.get("include_any") or []]
        self.title_exc = [re.compile(p, re.I) for p in t.get("exclude_any") or []]
        self.title_exceptions = [re.compile(p, re.I) for p in t.get("exceptions") or []]
        exp = f.get("experience") or {}
        self.max_years = exp.get("max_years_required")
        self.exp_ignore = exp.get("ignore_lines_with") or _DEFAULT_IGNORE
        self.dept_exc = [re.compile(p, re.I) for p in (f.get("department") or {}).get("exclude_any") or []]
        self.desc_exc = [re.compile(p, re.I) for p in (f.get("description") or {}).get("exclude_any") or []]
        self.blocked = {c.lower() for c in f.get("companies_block") or []}
        self.max_age = f.get("max_age_days")

    def check(self, job) -> tuple[bool, str]:
        title = job["title"] or ""
        loc = (job["location"] or "").lower()
        wp = (job["workplace"] or "").lower()

        if (job["company"] or "").lower() in self.blocked:
            return False, "company blocked"
        if self.title_inc and not any(r.search(title) for r in self.title_inc):
            return False, "title not in include list"
        if not any(x.search(title) for x in self.title_exceptions):
            for r in self.title_exc:
                if r.search(title):
                    return False, f"title excluded ({r.pattern})"
        for r in self.dept_exc:
            if r.search(job["department"] or ""):
                return False, f"department excluded ({r.pattern})"
        for d in self.loc_deny:
            if d in loc and not self.is_favorite(job["company"] or ""):
                return False, f"location '{job['location']}' (denied)"
        if self.loc_allow:
            if loc:
                if not any(a in loc for a in self.loc_allow) and not (wp == "remote" and "remote" in self.loc_allow):
                    return False, f"location '{job['location']}'"
            elif not self.loc_allow_unknown:
                return False, "location unknown"
        if self.workplace_allow and wp and wp not in self.workplace_allow:
            return False, f"workplace '{wp}'"
        for r in self.desc_exc:
            if r.search(job["description"] or ""):
                return False, f"description excluded ({r.pattern})"
        if self.max_years is not None:
            years, _ = required_years(job["description"] or "", self.exp_ignore)
            if years is not None and years > int(self.max_years):
                return False, f"requires {years}+ years experience"
        if self.max_age:
            age = job_age_days(job, self.late_sources, self.backfill_days.get(_col(job, "source")))
            if age is not None and age > float(self.max_age):
                return False, f"older than {self.max_age} days"
        return True, ""


def filter_test(store, filters: dict, samples: int = 6, seed: int = 1, is_favorite=None) -> dict:
    """Dry-run the current filters over every stored job (except duplicates).
    Nothing is changed. Shows what each rule removes, what passes, and what
    would flip compared with the jobs' current status."""
    import random

    hf = HardFilter(filters, is_favorite, store.first_fetch_days())
    rows = store.db.execute("SELECT * FROM jobs WHERE status != ?", (Status.DUPLICATE.value,)).fetchall()
    by_reason: dict[str, list[str]] = {}
    passed: list[str] = []
    newly_out, newly_in = [], []
    for j in rows:
        ok, why = hf.check(j)
        label = f"#{j['id']} {j['title']} @ {j['company']}"
        if ok:
            passed.append(label)
            if j["status"] == Status.FILTERED_OUT.value:
                newly_in.append(label)
        else:
            by_reason.setdefault(why, []).append(label)
            if j["status"] not in (Status.FILTERED_OUT.value, Status.NEW.value):
                newly_out.append(label)
    rng = random.Random(seed)
    pick = lambda xs: rng.sample(xs, min(samples, len(xs)))  # noqa: E731
    return {
        "total": len(rows),
        "passed": len(passed),
        "excluded_by_rule": {k: {"count": len(v), "examples": pick(v)}
                             for k, v in sorted(by_reason.items(), key=lambda kv: -len(kv[1]))},
        "passing_examples": pick(passed),
        "would_newly_exclude": newly_out[:30],
        "would_newly_include": newly_in[:30],
    }


def reapply(store, filters: dict, is_favorite=None) -> dict:
    """Re-run the filters on jobs the model has not looked at yet
    (NEW / TRIAGE_PENDING / FILTERED_OUT) after you edit filters.yaml."""
    hf = HardFilter(filters, is_favorite, store.first_fetch_days())
    moved_in = moved_out = 0
    for status in (Status.NEW, Status.TRIAGE_PENDING, Status.FILTERED_OUT):
        for j in store.jobs_by_status(status):
            ok, why = hf.check(j)
            if ok and status != Status.TRIAGE_PENDING:
                store.set_status(j["id"], Status.TRIAGE_PENDING)
                moved_in += 1
            elif not ok and status != Status.FILTERED_OUT:
                store.set_status(j["id"], Status.FILTERED_OUT, why)
                moved_out += 1
    store.commit()
    return {"now_pending": moved_in, "now_filtered_out": moved_out}


class HardFilterStage:
    name = "hard_filter"

    def run(self, ctx) -> dict:
        hf = HardFilter(ctx.config.filters, Favorites(ctx.config).is_favorite, ctx.store.first_fetch_days())
        passed = rejected = 0
        reasons: dict[str, int] = {}
        for job in ctx.store.jobs_by_status(Status.NEW):
            ok, why = hf.check(job)
            if ok:
                ctx.store.set_status(job["id"], Status.TRIAGE_PENDING)
                passed += 1
            else:
                ctx.store.set_status(job["id"], Status.FILTERED_OUT, why)
                rejected += 1
                key = why.split(" (")[0].split(" '")[0]
                reasons[key] = reasons.get(key, 0) + 1
        ctx.store.commit()
        return {"passed": passed, "filtered_out": rejected, "reasons": reasons}
