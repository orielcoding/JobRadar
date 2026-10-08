"""Deterministic stand-in for an LLM. Used by tests and `--backend fake`
dry runs, so the whole pipeline can be exercised without spending usage."""

from __future__ import annotations

from jobradar.llm import LLMResult


class FakeBackend:
    def __init__(self, config):
        self.keywords = [k.lower() for k in config.get("llm.fake_keywords") or []]

    def _hits(self, text: str) -> int:
        t = (text or "").lower()
        return sum(1 for k in self.keywords if k in t)

    def complete_json(self, *, system, user, schema, model, purpose="llm", context=None, **_opts) -> LLMResult:
        context = context or {}
        if purpose == "cv_posting":
            return LLMResult({"found": False, "url": "", "description": "", "note": "fake: no search"}, model,
                             {"fake": True})
        if purpose == "cv":
            job = context.get("job", {})
            cv = (f"# Fake Candidate\nTel Aviv · <email> · <phone> · <LinkedIn URL> · <GitHub URL>\n\n"
                  f"**Fake Headline for {job.get('title', '')}**\n\n## Summary\nFake summary sentence.\n\n"
                  f"## Experience\n### Fake Role | Fake Co | 2024–present\n- Built a fake thing.\n\n"
                  f"## Education\n- **B.Sc. Fake** | Fake University | 2020–2023\n\n## Skills\n- **Programming:** Python\n")
            return LLMResult({"cv_markdown": cv, "note_markdown": "# הערה (fake)\n", "headline": "Fake Headline",
                              "family": "engineering", "main_risk": "fake risk", "summary_he": "fake",
                              "open_questions": [{"question": "fake?", "default_used": "no"}],
                              "master_cv_proposals": []}, model, {"fake": True})
        if purpose == "triage":
            results = []
            for j in context.get("jobs", []):
                h = self._hits(j["title"])
                verdict = "yes" if h >= 1 else ("maybe" if self._hits(j.get("excerpt", "")) >= 2 else "no")
                results.append({"job_id": str(j["id"]), "verdict": verdict, "reason": f"fake: {h} title keyword hits"})
            return LLMResult({"results": results}, model, {"fake": True})
        if purpose in ("deep", "probe"):
            job = context.get("job", {})
            h = self._hits(job.get("title", "")) * 2 + self._hits(job.get("description", ""))
            cap = max(1, min(10, 3 + h))
            data = fake_deep_result(job.get("title", ""), cap)
            if purpose == "probe":
                data = {"ok": True}
            return LLMResult(data, model, {"fake": True})
        return LLMResult({"ok": True}, model, {"fake": True})


def fake_deep_result(title: str, cap: int, desire: int | None = None, screen: int | None = None) -> dict:
    """A deep-v4-shaped evaluation with the given capability (tests and dry runs)."""
    desire = min(10, cap) if desire is None else desire
    screen = max(1, cap - 3) if screen is None else screen
    return {
        "job_analysis": {
            "role_summary": f"fake summary of {title}",
            "real_problem": "fake",
            "seniority_signal": "S2: fake",
            "gate": {"role_scope": "S2", "candidate_scope": "S2", "result": "pass", "reason": "fake"},
            "must_haves": [{"requirement": "fake requirement", "weight": "primary", "evidence": "fake",
                            "status": "met", "gap": "none"}],
            "nice_to_haves": [],
        },
        "capability_calc": {"gap_points": 0, "band": 8, "adjustments": [], "result": cap},
        "red_flags": [],
        "cv_tailoring": {"cv_language": "en", "keywords": ["fake"], "headline": "Fake Headline",
                         "summary": ["fake summary"], "section_order": ["summary", "experience"],
                         "entries": [{"entry": "Fake Role", "lines": [{"src": "Fake line", "text": "",
                                                                       "serves": "fake"}]}],
                         "skills": "Python", "leave_out": [], "do_not_claim": [], "add_to_master": []},
        "screen_check": {"family": "engineering", "level": "mid", "years_required": 2, "years_countable": 1,
                         "years_basis": "fake", "keyword_hits": ["bullet"],
                         "signals": {"title": 1, "keywords": 2, "years": 1, "education": 2, "domain": 1,
                                     "results": 1},
                         "adjustments": [], "caps": [], "binding": "years"},
        "recruiter_objection": "fake objection",
        "score_rationale": {"capability": "fake", "desire": "fake", "screen_pass": "fake"},
        "scores": {"capability": cap, "desire": desire, "screen_pass": screen},
        "pitch": "fake pitch",
        "verdict": "strong" if cap >= 8 else ("good" if cap >= 7 else ("stretch" if cap >= 5 else "no")),
    }
