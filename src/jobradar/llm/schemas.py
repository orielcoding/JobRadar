"""JSON Schemas for the LLM answers. The field ORDER matters: the model fills
fields top to bottom, so analysis fields come before the scores they justify."""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["yes", "maybe", "no"]},
                    "reason": {"type": "string"},
                },
                "required": ["job_id", "verdict", "reason"],
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

# --- deep-v4: capability method (archive/briefs/2026-10-01-method-design/outputs/capability_method.md)
_MUST = {
    "type": "object",
    "properties": {
        "requirement": {"type": "string"},
        "weight": {"type": "string", "enum": ["primary", "core", "supporting"]},
        "evidence": {"type": "string"},  # "<source>: <pointer>; asked Lx, shown Ly"
        "status": {"type": "string", "enum": ["met", "partial", "transferable", "missing"]},
        "gap": {"type": "string", "enum": ["none", "ramp", "months", "absent"]},
    },
    "required": ["requirement", "weight", "evidence", "status", "gap"],
}

_GATE = {
    "type": "object",
    "properties": {
        "role_scope": {"type": "string", "enum": ["S1", "S2", "S3", "S4"]},
        "candidate_scope": {"type": "string", "enum": ["S1", "S2", "S3", "S4"]},
        "result": {"type": "string", "enum": ["pass", "scope_stretch", "stop_scope", "stop_profession"]},
        "reason": {"type": "string"},
    },
    "required": ["role_scope", "candidate_scope", "result", "reason"],
}

CAPABILITY_ADJUSTMENTS = ["ramp_pileup", "scope_stretch", "proven_in_role", "proven_above_role",
                          "cap_vague", "cap_title_only"]

_CAP_CALC = {
    "type": "object",
    "properties": {
        "gap_points": {"type": "integer", "minimum": 0},
        "band": {"type": "integer", "minimum": 1, "maximum": 8},
        "adjustments": {"type": "array", "items": {"type": "string", "enum": CAPABILITY_ADJUSTMENTS}},
        "result": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["gap_points", "band", "adjustments", "result"],
}

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

# Field order is the order of work (prompts/deep_eval.md): capability analysis -> capability number ->
# CV recipe -> screen check -> objection -> rationales -> scores -> pitch -> verdict.
DEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "job_analysis": {
            "type": "object",
            "properties": {
                "role_summary": {"type": "string"},
                "real_problem": {"type": "string"},
                "seniority_signal": {"type": "string"},
                "gate": _GATE,
                "must_haves": {"type": "array", "items": _MUST},
                "nice_to_haves": {"type": "array", "items": _REQ},
            },
            "required": ["role_summary", "real_problem", "seniority_signal", "gate", "must_haves",
                         "nice_to_haves"],
        },
        "capability_calc": _CAP_CALC,
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "cv_tailoring": CV_TAILORING,
        "screen_check": SCREEN_CHECK,
        "recruiter_objection": {"type": "string"},
        "score_rationale": {
            "type": "object",
            "properties": {"capability": {"type": "string"}, "desire": {"type": "string"},
                           "screen_pass": {"type": "string"}},
            "required": ["capability", "desire", "screen_pass"],
        },
        "scores": {
            "type": "object",
            "properties": {"capability": _SCORE, "desire": _SCORE, "screen_pass": _SCORE},
            "required": ["capability", "desire", "screen_pass"],
        },
        "pitch": {"type": "string"},
        "verdict": {"type": "string", "enum": ["strong", "good", "stretch", "no"]},
    },
    "required": ["job_analysis", "capability_calc", "red_flags", "cv_tailoring", "screen_check",
                 "recruiter_objection", "score_rationale", "scores", "pitch", "verdict"],
}

PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
