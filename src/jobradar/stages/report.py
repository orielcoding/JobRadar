"""Stage 5 - write a daily markdown report (reports/YYYY-MM-DD.md).

One section per run, every job one table row: deep evaluations to review,
deep evaluations rejected, triage passes without a description, triage rejects.
`render_evaluation` (the full write-up of one job) is used by `jobradar show`.
"""

from __future__ import annotations

import json
from datetime import date

from jobradar.models import Status
from jobradar.network import STRENGTH_TXT
from jobradar.scoring import DECISION_LABEL_HE, is_v5

_STATUS_ICON = {"met": "✅", "partial": "🟡", "transferable": "🔁", "missing": "❌"}
_GAP_ICON = {"none": "✅", "learnable": "🔁", "risk": "🟡", "cap": "🟠", "blocker": "❌"}
_GAP_HE = {"none": "יש", "learnable": "נלמד בעבודה", "risk": "מוריד סיכוי", "cap": "מגביל ל'סיכוי נמוך'",
           "blocker": "חוסם"}
REJECTED = ("no", "big_no")


def decision_label(ev: dict) -> str:
    v = ev.get("verdict") or ""
    return DECISION_LABEL_HE.get(v, v)


def render_recipe(rec, d: dict | None = None) -> list[str]:
    """The CV recipe: deep-v4 object, or the deep-v3 list of {change, why}."""
    if not rec:
        return []
    mark = {"tailor": " ✏️", "referral": " 🤝"}.get((d or {}).get("advice") or "", "")
    out = ["\n**מתכון קו״ח**" + mark]
    if isinstance(rec, list):
        return out + [f"- {t.get('change')} — _{t.get('why')}_" for t in rec]
    out.append(f"- כותרת: {rec.get('headline', '')}")
    for s in rec.get("summary") or []:
        out.append(f"- תקציר: {s}")
    for e in rec.get("entries") or []:
        lines = "; ".join((ln.get("text") or ln.get("src", "")) + f" ({ln.get('serves', '')})"
                          for ln in e.get("lines") or [])
        out.append(f"- **{e.get('entry', '')}**: {lines}")
    if rec.get("skills"):
        out.append(f"- כישורים: {rec['skills']}")
    for k, label in (("leave_out", "להשמיט"), ("do_not_claim", "לא לטעון"), ("add_to_master", "להוסיף לקו״ח האב")):
        if rec.get(k):
            out.append(f"- {label}: " + "; ".join(rec[k]))
    return out


def main_reason(ev: dict) -> str:
    """The one-line reason (deep-v5). deep-v4: the gate, else a capability gap, else the objection."""
    if ev.get("reason_line"):
        return ev["reason_line"]
    gate = (ev.get("job_analysis") or {}).get("gate") or {}
    if gate.get("result") in ("stop_scope", "stop_profession"):
        return gate.get("reason", "")
    if int((ev.get("scores") or {}).get("capability") or 0) <= 4:
        return (ev.get("score_rationale") or {}).get("capability", "") or ev.get("recruiter_objection", "")
    return ev.get("recruiter_objection", "")


def render_evaluation(job, ev: dict, net_ctx: dict | None = None) -> str:
    s, d, a = ev.get("scores", {}), ev.get("decision", {}), ev.get("job_analysis", {})
    flag = " 🔔" if d.get("notify") else ""
    star = "⭐ " if d.get("favorite") else ""
    v5 = is_v5(ev)
    head = f"**{decision_label(ev)}** — {ev.get('reason_line', '')}" if v5 else f"verdict: **{ev.get('verdict')}**"
    out = [
        f"### #{job['id']} {star}{job['title']} @ {job['company']}{flag}",
        f"{job['location'] or ''} · [קישור]({job['url']}) · {head}",
        "",
        f"| {'התאמה' if v5 else 'יכולת'} | רצון | מעבר סינון |\n|---|---|---|\n"
        f"| {s.get('capability')} | {s.get('desire')} | {s.get('screen_pass')} |",
        "",
        f"**מה התפקיד באמת:** {a.get('role_summary', '')}",
        f"**הבעיה שהם פותרים:** {a.get('real_problem', '')}",
        f"**בכירות:** {a.get('seniority_signal', '')}",
        "",
    ]
    if v5:
        bn = ev.get("big_no_check") or {}
        if bn.get("rule") not in (None, "none"):
            out.append(f"**סיבת פסילה:** {bn.get('rule')} — {bn.get('evidence', '')}")
        elif (ev.get("checks") or {}).get("far_fetched"):
            out.append("**סיבת פסילה:** הרבה פערים ורצון נמוך (B7)")
        pa = a.get("primary_activity") or {}
        out.append(f"**העבודה המרכזית:** {pa.get('activity', '')} — {pa.get('status', '')} ({pa.get('candidate_evidence', '')})")
        out.append("\n**דרישות**")
        for r in a.get("requirements", []):
            g = r.get("gap")
            out.append(f"- {_GAP_ICON.get(g, '•')} {r.get('requirement')} [{_GAP_HE.get(g, g)}] — {r.get('evidence')}")
    else:
        out.append("**דרישות הכרחיות**")
        gate = a.get("gate") or {}
        if gate:
            out.insert(-2, f"**שער רמה:** {gate.get('result')} (משרה {gate.get('role_scope')}, "
                           f"מועמד {gate.get('candidate_scope')}) — {gate.get('reason', '')}")
        for r in a.get("must_haves", []):
            extra = f" [{r.get('weight')}, gap: {r.get('gap')}]" if r.get("gap") else ""
            out.append(f"- {_STATUS_ICON.get(r.get('status'), '•')} {r.get('requirement')}{extra} — {r.get('evidence')}")
    if a.get("nice_to_haves"):
        out.append("\n**נחמד שיהיה**")
        for r in a["nice_to_haves"]:
            out.append(f"- {_STATUS_ICON.get(r.get('status'), '•')} {r.get('requirement')} — {r.get('evidence')}")
    calc = ev.get("capability_calc") or {}
    if calc:
        out.append(f"\n**חישוב יכולת:** נקודות פער {calc.get('gap_points')} · רצועה {calc.get('band')} · "
                   f"התאמות {', '.join(calc.get('adjustments') or []) or '—'} · תוצאה {calc.get('result')}")
    sc = ev.get("screen_check") or {}
    if sc:
        sig = ", ".join(f"{k} {v}" for k, v in (sc.get("signals") or {}).items())
        caps = ", ".join(c.get("code", "") for c in sc.get("caps") or []) or "—"
        out.append(f"**בדיקת סינון:** {sc.get('family')}/{sc.get('level')} · שנים {sc.get('years_countable')} "
                   f"מתוך {sc.get('years_required')} · {sig} · תקרות {caps} · מגביל: {sc.get('binding')}")
    rat = ev.get("score_rationale", {})
    if v5:
        out += ["", f"**רצון:** {(ev.get('desire') or {}).get('rationale', '')}",
                f"**מעבר סינון:** {rat.get('screen_pass', '')}"]
    else:
        out += ["", f"**נימוק הציונים:** יכולת: {rat.get('capability', '')} | רצון: {rat.get('desire', '')} | "
                    f"סינון: {rat.get('screen_pass', '')}"]
    if ev.get("red_flags"):
        out.append("**דגלים אדומים:** " + "; ".join(ev["red_flags"]))
    out.append(f"**ההתנגדות החזקה של מגייס:** {ev.get('recruiter_objection', '')}")
    out += render_recipe(ev.get("cv_tailoring"), d)
    if net_ctx and (net_ctx["contacts"] or net_ctx["apps"]):
        out.append("\n**נטוורקינג**")
        for c in net_ctx["contacts"][:5]:
            bits = ", ".join(b for b in (c["role"], c["relationship"],
                                         STRENGTH_TXT.get(c["strength"] or 1)) if b)
            out.append(f"- 🤝 {c['name']} (#{c['id']}) — {bits}")
        if len(net_ctx["contacts"]) > 5:
            out.append(f"- ועוד {len(net_ctx['contacts']) - 5}: `jobradar net at \"{job['company']}\"`")
        for ap in net_ctx["apps"][:3]:
            out.append(f"- 📌 תהליך #{ap['id']}: {ap['title']} · {ap['status']}")
        if net_ctx["known"] and not net_ctx["apps"]:
            out.append(f"- 💡 שקול לבקש הפניה מ-{net_ctx['known'][0]['name']} לפני שמגישים")
    out += ["", f"**פיץ׳:** {ev.get('pitch', '')}", "", "---", ""]
    return "\n".join(out)


class ReportStage:
    name = "report"

    def run(self, ctx) -> dict:
        since = ctx.store.run_started_at(ctx.run_id) if ctx.run_id else "1970"
        rows = ctx.store.evaluated_since(since)
        light = ctx.store.triaged_since(since, Status.LIGHT_MATCH)
        triage_no = ctx.store.triaged_since(since, Status.REJECTED_TRIAGE)
        if not rows and not ctx.stats:
            return {"written": False}
        path = write_report(ctx.config, rows, light, ctx.stats, ctx.store, triage_no)
        return {"written": str(path), "evaluations": len(rows)}


def _cell(text, n: int = 170) -> str:
    """One table cell: single line, no pipes, bounded length."""
    s = " ".join(str(text or "").split()).replace("|", "/")
    return s if len(s) <= n else s[:n - 1] + "…"


def _job_cell(r, favs, notified: bool = False) -> str:
    star = "⭐ " if favs.is_favorite(r["company"]) else ""
    bell = " 🔔" if notified else ""
    return f"{star}[{_cell(r['title'], 70)} @ {_cell(r['company'], 30)}]({r['url']}){bell}"


_EVAL_HEAD = ("| # | משרה | החלטה | רצון | סינון | למה |\n"
              "|---|---|---|---|---|---|")


def _eval_rows(evs, favs) -> list[str]:
    out = [_EVAL_HEAD]
    for r, ev in evs:
        s, d = ev.get("scores", {}), ev.get("decision", {})
        out.append(f"| {r['id']} | {_job_cell(r, favs, d.get('notify'))} | {decision_label(ev)} | "
                   f"{s.get('desire')} | {s.get('screen_pass')} | {_cell(main_reason(ev), 200)} |")
    return out


def _triage_rows(rows, favs) -> list[str]:
    out = ["| תאריך | # | משרה | סיבה (Haiku) |\n|---|---|---|---|"]
    for r in rows:
        reason = (json.loads(r["eval_result"] or "{}")).get("reason", "")
        out.append(f"| {str(r['triaged_at'])[:10]} | {r['id']} | {_job_cell(r, favs)} | {_cell(reason, 140)} |")
    return out


def write_report(cfg, rows, light, stats: dict, store=None, triage_no=None):
    """Append one run to reports/YYYY-MM-DD.md. Every job appears as one table row;
    the full reasoning for any job is `jobradar show <id>`."""
    from jobradar.favorites import Favorites
    favs = Favorites(cfg)
    folder = cfg.path("paths.reports")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{date.today().isoformat()}.md"
    evs = [(r, json.loads(r["eval_result"])) for r in rows]
    evs.sort(key=lambda x: x[1].get("decision", {}).get("rank", 0), reverse=True)
    review = [(r, ev) for r, ev in evs if ev.get("verdict") not in REJECTED]
    rejected = [(r, ev) for r, ev in evs if ev.get("verdict") in REJECTED]
    triage_no = triage_no or []
    st = {k: v for k, v in stats.items() if not k.startswith("_")}
    hf, tr = st.get("hard_filter", {}), st.get("triage", {})
    parts = [f"## הרצה {stats.get('_started', '')}", "",
             "| נאספו (חדשות) | נפסלו בקוד | Haiku: כן / אולי / לא | הערכה מעמיקה | פינגים |",
             "|---|---|---|---|---|",
             f"| {st.get('ingest', {}).get('new', '–')} | {hf.get('filtered_out', '–')} | "
             f"{tr.get('yes', '–')} / {tr.get('maybe', '–')} / {tr.get('no', '–')} | {len(evs)} | "
             f"{st.get('notify', {}).get('pings', '–')} |", ""]
    parts += [f"### לסקירה ({len(review)})", ""] + (_eval_rows(review, favs) if review else ["אין."]) + [""]
    parts += [f"### נפסלו בהערכה המעמיקה ({len(rejected)})", ""] + \
        (_eval_rows(rejected, favs) if rejected else ["אין."]) + [""]
    if light:
        parts += [f"### עברו את Haiku בלי תיאור משרה ({len(light)})", "",
                  "להערכה מלאה: מעתיקים את התיאור ומריצים `jobradar paste <id>`.", ""]
        parts += _triage_rows(light, favs) + [""]
    parts += [f"### נפלו ב-Haiku ({len(triage_no)})", ""] + \
        (_triage_rows(triage_no, favs) if triage_no else ["אין."]) + [""]
    parts += ["פירוט מלא לכל משרה: `jobradar show <id>`", "", "---", ""]
    existing = path.read_text(encoding="utf-8") if path.exists() else f"# JobRadar — {date.today().isoformat()}\n\n"
    path.write_text(existing + "\n".join(parts) + "\n", encoding="utf-8")
    return path
