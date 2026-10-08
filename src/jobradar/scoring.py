"""Deterministic parts of the evaluation method (prompts/deep_eval.md).

The model judges the inputs (big-no check, primary activity, gap per requirement,
desire, screen signals and caps). The arithmetic on top of them is fixed, so code
recomputes it:

  deep-v5 (now)
    risk_points_for / decision_for   Part A step 6 (the apply decision, incl. far-fetched B7)
    fit_score                        display / ranking number derived from the decision fields
    screen_from_check                Part B4 (weights, base, caps)
  deep-v4 (old rows, still readable)
    capability_from_analysis, verdict_for

The decision IS taken from code; mismatches with the model's own are recorded.
"""

from __future__ import annotations

from jobradar.llm.schemas import SCREEN_CAPS

_DROP = {"none": "none", "ramp": "none", "months": "ramp", "absent": "months"}
_PTS = {"months": (2, 1), "absent": (4, 3)}  # (on primary, elsewhere)
_STOPS = ("stop_scope", "stop_profession")

SCREEN_WEIGHTS = {
    "engineering":   dict(title=2, keywords=3, years=2, education=1, domain=1, results=1),
    "analytics":     dict(title=2, keywords=3, years=2, education=1, domain=1, results=1),
    "algorithms_ds": dict(title=2, keywords=2, years=2, education=2, domain=1, results=1),
    "research":      dict(title=1, keywords=2, years=1, education=2, domain=2, results=2),
    "product":       dict(title=3, keywords=1, years=3, education=1, domain=1, results=1),
    "program":       dict(title=3, keywords=1, years=3, education=1, domain=1, results=1),
    "solutions":     dict(title=3, keywords=1, years=3, education=1, domain=1, results=1),
}
_HIT = {"bullet": 1.0, "skills": 0.5, "none": 0.0}
# bindings a better CV can fix vs. ones that need a referral / introduction
TAILOR_BINDINGS = {"keywords", "results", "domain"}


def gate_result(result: dict) -> str:
    return ((result.get("job_analysis") or {}).get("gate") or {}).get("result", "")


def capability_from_analysis(result: dict) -> int | None:
    """Capability from the model's own gate / must-have fields. None if they are missing (deep-v3)."""
    a = result.get("job_analysis") or {}
    gate = gate_result(result)
    if not gate or "must_haves" not in a:
        return None
    calc = result.get("capability_calc") or {}
    if gate == "stop_scope":
        return 3
    if gate == "stop_profession":
        return 1 if (calc.get("result") or 1) <= 1 else 2
    pts = ramps = 0
    for m in a["must_haves"]:
        g = m.get("gap", "none")
        if m.get("weight") == "supporting":
            g = _DROP.get(g, g)
        if g in _PTS:
            pts += _PTS[g][0 if m.get("weight") == "primary" else 1]
        ramps += g == "ramp"
    band = (8 if ramps == 0 else 7) if pts == 0 else max(2, 7 - pts)
    adj = set(calc.get("adjustments") or [])
    score = band
    if pts == 0 and ramps >= 3:
        score -= 1  # ramp_pileup
    if gate == "scope_stretch":
        score -= 1
    if band == 8 and "proven_above_role" in adj:
        score += 2
    elif band == 8 and "proven_in_role" in adj:
        score += 1
    if "cap_title_only" in adj:
        score = min(score, 6)
    elif "cap_vague" in adj:
        score = min(score, 7)
    return max(2, min(10, score))


def _keywords_signal(hits: list) -> int:
    if not hits:
        return 1
    mean = sum(_HIT.get(h, 0.0) for h in hits) / len(hits)
    sig = 2 if mean >= 0.75 else 1 if mean >= 0.40 else 0
    return min(sig, 1) if hits[0] == "none" else sig


def screen_from_check(result: dict) -> int | None:
    """screen_pass from the model's screen_check. None if it is missing (deep-v3)."""
    sc = result.get("screen_check") or {}
    s = sc.get("signals") or {}
    if sc.get("family") not in SCREEN_WEIGHTS or not s:
        return None
    w = dict(SCREEN_WEIGHTS[sc["family"]])
    level = sc.get("level", "mid")
    hits = sc.get("keyword_hits") or []
    if level == "entry":
        w["education"] += w["years"]
        w["years"] = 0
    raw = sum(w[k] * int(s.get(k, 0)) for k in w)
    base = 5 if all(int(s.get(k, 0)) == 1 for k in w) else max(1, raw // 2)
    base -= len(sc.get("adjustments") or [])
    caps = [SCREEN_CAPS[c["code"]] for c in sc.get("caps") or [] if c.get("code") in SCREEN_CAPS]
    if int(s.get("keywords", 1)) == 0:
        caps.append(3)
    third = (int(s.get("education", 2)) <= 1) if level == "entry" else (int(s.get("years", 2)) == 0)
    misses = int(int(s.get("title", 1)) == 0) + int(bool(hits) and hits[0] == "none") + int(third)
    if misses:
        caps.append({1: 6, 2: 3, 3: 2}[misses])
    return max(1, min([10, base] + caps))


def knockout_codes(result: dict) -> set[str]:
    return {c.get("code") for c in (result.get("screen_check") or {}).get("caps") or []}


def verdict_for(result: dict) -> str:
    """Part D of the prompt, applied to the model's scores."""
    s = result.get("scores") or {}
    cap, des, scr = (int(s.get(k) or 0) for k in ("capability", "desire", "screen_pass"))
    if (gate_result(result) in _STOPS or cap <= 4 or des <= 3
            or knockout_codes(result) & {"KO1", "KO2"} or (cap <= 6 and scr <= 2)):
        return "no"
    if cap >= 8 and scr >= 6:
        return "strong"
    if cap >= 7:
        return "good"
    return "stretch"


# --- deep-v5: apply decision -------------------------------------------------

DECISION_LABEL_HE = {"strong_apply": "להגיש עכשיו", "apply": "להגיש", "long_shot": "סיכוי נמוך, שווה לשקול",
                     "big_no": "לא רלוונטי"}
_PRIMARY_PTS = {"met": 0, "partial": 1, "absent": 2}


def is_v5(result: dict) -> bool:
    return "big_no_check" in result


def risk_points_for(result: dict) -> int:
    """`risk` and `cap` requirements, plus the primary activity (met 0, partial 1, absent 2)."""
    a = result.get("job_analysis") or {}
    pts = sum(1 for r in a.get("requirements") or [] if r.get("gap") in ("risk", "cap"))
    return pts + _PRIMARY_PTS.get((a.get("primary_activity") or {}).get("status"), 0)


def big_no_rule(result: dict) -> str:
    return ((result.get("big_no_check") or {}).get("rule") or "none")


def decision_for(result: dict) -> str:
    """Part A step 6: the first rule that matches."""
    a = result.get("job_analysis") or {}
    primary = (a.get("primary_activity") or {}).get("status")
    gaps = [r.get("gap") for r in a.get("requirements") or []]
    pts = risk_points_for(result)
    desire = int(((result.get("desire") or {}).get("score")) or (result.get("scores") or {}).get("desire") or 0)
    if big_no_rule(result) != "none":
        return "big_no"
    if primary == "met" and pts == 0 and desire >= 8 and "(inferred)" not in (a.get("primary_activity") or {}).get("activity", ""):
        return "strong_apply"
    if primary != "absent" and pts <= 2 and desire >= 5 and "cap" not in gaps:
        return "apply"
    if pts >= 3 and desire <= 4:
        return "big_no"  # B7: far-fetched and unwanted
    return "long_shot"


def fit_score(result: dict) -> int:
    """A 1-10 number for display, ranking and the CV advice: 2 for a big-no rule, else 8 - risk points (min 3)."""
    if big_no_rule(result) != "none":
        return 2
    return max(3, 8 - risk_points_for(result))


def advice_for(result: dict, gap_flag: int) -> str | None:
    """'tailor' (a better CV closes the gap), 'referral' (it cannot), or None (no big gap)."""
    s = result.get("scores") or {}
    if int(s.get("capability") or 0) - int(s.get("screen_pass") or 0) < gap_flag:
        return None
    binding = (result.get("screen_check") or {}).get("binding")
    if binding is None:  # deep-v3 evaluation
        return "tailor"
    return "tailor" if binding in TAILOR_BINDINGS else "referral"
