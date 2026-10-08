"""JSON Schemas for the LLM answers. The field ORDER matters: the model fills
fields top to bottom, so analysis fields come before the scores they justify."""

TRIAGE_RULES = ["none", "out_family", "blocker_skill", "dealbreaker", "uninteresting_company"]

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "rule": {"type": "string", "enum": TRIAGE_RULES},
                    "reason": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["yes", "maybe", "no"]},
                },
                "required": ["job_id", "rule", "reason", "verdict"],
            },
        }
    },
    "required": ["results"],
}

_REQ = {
    "type": "object",
    "properties": {
        "requirement": {"type": "string"},
        "status": {"type": "string", "enum": ["met", "partial", "transferable", "missing"]},
        "evidence": {"type": "string"},
    },
    "required": ["requirement", "status", "evidence"],
}

_SCORE = {"type": "integer", "minimum": 1, "maximum": 10}

# --- deep-v4: screening method (archive/briefs/2026-10-01-method-design/outputs/screening_method.md)
SCREEN_SIGNALS = ("title", "keywords", "years", "education", "domain", "results")
SCREEN_CAPS = {"KO1": 1, "KO5": 1, "KO2": 2, "KO3": 2, "KO4": 2, "YEARS2": 2, "MISS3": 2,
               "YEARS3": 3, "KEYWORDS0": 3, "MISS2": 3, "YEARS4": 4, "OVERQUALIFIED": 4, "MISS1": 6}
SCREEN_FAMILIES = ["engineering", "analytics", "algorithms_ds", "research", "product", "program", "solutions"]
SCREEN_BINDINGS = ["knockout", "years", "overqualified", "keywords", "title", "education", "domain",
                   "results", "none"]

_CV_LINE = {
    "type": "object",
    "properties": {"src": {"type": "string"}, "text": {"type": "string"}, "serves": {"type": "string"}},
    "required": ["src", "text", "serves"],
}

CV_TAILORING = {
    "type": "object",
    "properties": {
        "cv_language": {"type": "string", "enum": ["en", "he"]},
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "headline": {"type": "string"},
        "summary": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "section_order": {"type": "array", "items": {"type": "string", "enum": [
            "summary", "experience", "research", "projects", "education", "publications", "skills"]}},
        "entries": {"type": "array", "items": {
            "type": "object",
            "properties": {"entry": {"type": "string"},
                           "lines": {"type": "array", "maxItems": 4, "items": _CV_LINE}},
            "required": ["entry", "lines"]}},
        "skills": {"type": "string"},
        "leave_out": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "do_not_claim": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "add_to_master": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    },
    "required": ["cv_language", "keywords", "headline", "summary", "section_order", "entries",
                 "skills", "leave_out", "do_not_claim", "add_to_master"],
}

_SIG = {"type": "integer", "minimum": 0, "maximum": 2}

SCREEN_CHECK = {
    "type": "object",
    "properties": {
        "family": {"type": "string", "enum": SCREEN_FAMILIES},
        "level": {"type": "string", "enum": ["entry", "mid", "senior"]},
        "years_required": {"type": "number", "minimum": 0},
        "years_countable": {"type": "number", "minimum": 0},
        "years_basis": {"type": "string"},
        "keyword_hits": {"type": "array", "maxItems": 6,
                         "items": {"type": "string", "enum": ["bullet", "skills", "none"]}},
        "signals": {"type": "object", "properties": {k: _SIG for k in SCREEN_SIGNALS},
                    "required": list(SCREEN_SIGNALS)},
        "adjustments": {"type": "array", "items": {"type": "string", "enum": ["timeline", "location"]}},
        "caps": {"type": "array", "items": {
            "type": "object",
            "properties": {"code": {"type": "string", "enum": list(SCREEN_CAPS)}, "detail": {"type": "string"}},
            "required": ["code", "detail"]}},
        "binding": {"type": "string", "enum": SCREEN_BINDINGS},
    },
    "required": ["family", "level", "years_required", "years_countable", "years_basis", "keyword_hits",
                 "signals", "adjustments", "caps", "binding"],
}

# --- deep-v5: apply decision (archive/briefs/2026-10-08-apply-decision/outputs/)
BIG_NO_RULES = ["none", "B1_dealbreaker", "B2_out_family", "B3_blocker_skill",
                "B4_prior_role", "B5_years_cap", "B6_eligibility"]  # B7 (far-fetched) is set by code
DECISIONS = ["strong_apply", "apply", "long_shot", "big_no"]

_PRIMARY = {
    "type": "object",
    "properties": {
        "activity": {"type": "string"},            # verb + object; "(inferred)" if thin
        "candidate_evidence": {"type": "string"},  # "<source>: <pointer>" or "none"
        "status": {"type": "string", "enum": ["met", "partial", "absent"]},
    },
    "required": ["activity", "candidate_evidence", "status"],
}

_REQUIREMENT = {
    "type": "object",
    "properties": {
        "requirement": {"type": "string"},
        "kind": {"type": "string", "enum": ["activity", "skill", "tool", "domain", "years",
                                            "degree", "practice", "eligibility"]},
        "evidence": {"type": "string"},            # candidate side, <= 20 words
        "gap": {"type": "string", "enum": ["none", "learnable", "risk", "cap", "blocker"]},
    },
    "required": ["requirement", "kind", "evidence", "gap"],
}

# Field order is the order of work (prompts/deep_eval.md): read the posting -> big-no checks ->
# desire -> decision and reason line -> CV recipe -> screen check -> objection -> screen score -> pitch.
DEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "job_analysis": {
            "type": "object",
            "properties": {
                "role_summary": {"type": "string"},
                "real_problem": {"type": "string"},
                "seniority_signal": {"type": "string"},
                "primary_activity": _PRIMARY,
                "requirements": {"type": "array", "maxItems": 6, "items": _REQUIREMENT},
                "nice_to_haves": {"type": "array", "maxItems": 4, "items": _REQ},
            },
            "required": ["role_summary", "real_problem", "seniority_signal", "primary_activity",
                         "requirements", "nice_to_haves"],
        },
        "big_no_check": {
            "type": "object",
            "properties": {
                "rule": {"type": "string", "enum": BIG_NO_RULES},
                "evidence": {"type": "string"},    # posting words + profile line / rating reason
            },
            "required": ["rule", "evidence"],
        },
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "desire": {
            "type": "object",
            "properties": {"rationale": {"type": "string"}, "score": _SCORE},
            "required": ["rationale", "score"],
        },
        "risk_points": {"type": "integer", "minimum": 0},
        "apply_decision": {"type": "string", "enum": DECISIONS},
        "reason_line": {"type": "string"},
        "cv_tailoring": CV_TAILORING,
        "screen_check": SCREEN_CHECK,
        "recruiter_objection": {"type": "string"},
        "score_rationale": {
            "type": "object",
            "properties": {"screen_pass": {"type": "string"}},
            "required": ["screen_pass"],
        },
        "scores": {
            "type": "object",
            "properties": {"screen_pass": _SCORE},
            "required": ["screen_pass"],
        },
        "pitch": {"type": "string"},
    },
    "required": ["job_analysis", "big_no_check", "red_flags", "desire", "risk_points", "apply_decision",
                 "reason_line", "cv_tailoring", "screen_check", "recruiter_objection", "score_rationale",
                 "scores", "pitch"],
}

PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
