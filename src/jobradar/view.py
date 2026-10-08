"""Local HTML view of the networking side: follow-ups, application pipeline,
warm companies and people. Written to reports/network.html and opened in a
browser. It contains personal data about other people, so it is a local file
only - never publish or upload it.
"""

from __future__ import annotations

import html
from datetime import date

from jobradar.models import Status
from jobradar.network import ACTIVE_STATUSES, APP_STATUSES, STRENGTH_TXT
from jobradar.textutil import norm_company

_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e6e3dc;--accent:#2f5d50;--warn:#a4441c;--chip:#eef2ef}
@media (prefers-color-scheme:dark){:root{--bg:#141516;--fg:#ececec;--muted:#9a9aa0;--card:#1d1f20;--line:#2c2f31;--accent:#7fc0ab;--warn:#f0956b;--chip:#243029}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Arial,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
.muted{color:var(--muted)}.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.col h3{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
.item{border-top:1px solid var(--line);padding:8px 0}.item:first-of-type{border-top:0}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;font-size:12px;margin-inline-start:4px}
.overdue{color:var(--warn);font-weight:600}a{color:var(--accent)}table{width:100%;border-collapse:collapse}
td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:start;vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600}
.kanban{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def write_network_view(cfg, store, net):
    today = date.today().isoformat()
    apps = net.applications()
    follow = net.followups(within_days=14)
    contacts = store.db.execute(
        "SELECT * FROM contacts WHERE coalesce(strength,1) >= 2 OR relationship IS NOT NULL AND relationship != '' "
        "ORDER BY coalesce(strength,1) DESC, name").fetchall()
    recent = store.db.execute(
        """SELECT i.*, c.name AS cname, c.company AS ccompany FROM interactions i
           LEFT JOIN contacts c ON c.id = i.contact_id ORDER BY i.date DESC, i.id DESC LIMIT 25""").fetchall()
    counts = net.counts()

    # Warm companies: matches you were pinged about / evaluated well where you know someone.
    jobs = store.db.execute(
        "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY id DESC LIMIT 400",
        (Status.EVALUATED.value, Status.LIGHT_MATCH.value)).fetchall()
    warm: dict[str, dict] = {}
    for j in jobs:
        if j["status"] == Status.EVALUATED.value and j["decision"] != "notify":
            continue
        key = norm_company(j["company"])
        if key in warm:
            warm[key]["jobs"].append(j)
            continue
        people = net.contacts_at(j["company"])
        if people:
            warm[key] = {"company": j["company"], "people": people, "jobs": [j]}

    out = [f"<!doctype html><html lang='he' dir='rtl'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>JobRadar Network</title>"
           f"<style>{_CSS}</style></head><body><main>",
           f"<h1>נטוורקינג והגשות</h1><div class='muted'>עודכן {today} · {counts['contacts']} אנשי קשר "
           f"({counts['contacts_manual']} שהוספת ידנית) · {counts['interactions']} שיחות · "
           f"{counts['applications']} תהליכים · עמוד מקומי, לא לשיתוף</div>"]

    out.append("<h2>⏰ תזכורות (14 הימים הקרובים)</h2>")
    if follow:
        out.append("<div class='card'>")
        for f in follow:
            cls = "overdue" if f["follow_up_on"] < today else ""
            out.append(f"<div class='item'><span class='{cls}'>{_e(f['follow_up_on'])}</span> · "
                       f"<b>{_e(f['contact_name'] or '-')}</b> {_e(f['contact_company'] or '')} — {_e(f['summary'])} "
                       f"<span class='chip'>done: jobradar net done {f['id']}</span></div>")
        out.append("</div>")
    else:
        out.append("<div class='muted'>אין תזכורות פתוחות.</div>")

    out.append("<h2>📌 תהליכי הגשה</h2><div class='kanban'>")
    for st in APP_STATUSES:
        items = [a for a in apps if a["status"] == st]
        if not items and st not in ACTIVE_STATUSES:
            continue
        out.append(f"<div class='card col'><h3>{st} · {len(items)}</h3>")
        for a in items:
            link = f"<a href='{_e(a['url'])}'>{_e(a['title'])}</a>" if a["url"] else _e(a["title"])
            via = f"<div class='muted'>via {_e(a['referral_name'])}</div>" if a["referral_name"] else ""
            nxt = (f"<div>➜ {_e(a['next_step'])} <span class='muted'>{_e(a['next_step_on'] or '')}</span></div>"
                   if a["next_step"] else "")
            out.append(f"<div class='item'><b>{_e(a['company'])}</b><div>{link} <span class='chip'>#{a['id']}</span></div>"
                       f"{via}{nxt}</div>")
        out.append("</div>")
    out.append("</div>")

    out.append("<h2>🤝 חברות עם התאמות שבהן אתה מכיר מישהו</h2>")
    if warm:
        out.append("<table><tr><th>חברה</th><th>משרות</th><th>אנשים</th></tr>")
        for w in sorted(warm.values(), key=lambda w: -max((p["strength"] or 1) for p in w["people"])):
            js = "<br>".join(f"<a href='{_e(j['url'])}'>{_e(j['title'])}</a> <span class='chip'>#{j['id']}</span>"
                             for j in w["jobs"][:4])
            ps = "<br>".join(f"{_e(p['name'])} <span class='muted'>{_e(p['role'] or '')}</span>"
                             f"<span class='chip'>{_e(STRENGTH_TXT.get(p['strength'] or 1))}</span>" for p in w["people"][:4])
            out.append(f"<tr><td><b>{_e(w['company'])}</b></td><td>{js}</td><td>{ps}</td></tr>")
        out.append("</table>")
    else:
        out.append("<div class='muted'>עדיין אין חפיפה בין התאמות לאנשי קשר.</div>")

    out.append("<h2>💬 שיחות אחרונות</h2><div class='card'>")
    for i in recent:
        out.append(f"<div class='item'><span class='muted'>{_e(i['date'])} · {_e(i['channel'])}</span> "
                   f"<b>{_e(i['cname'] or '-')}</b> {_e(i['ccompany'] or '')} — {_e(i['summary'])}</div>")
    if not recent:
        out.append("<div class='muted'>עוד לא נרשמו שיחות.</div>")
    out.append("</div>")

    out.append("<h2>👥 אנשי קשר משמעותיים</h2><div class='grid'>")
    for c in contacts[:120]:
        link = f" · <a href='{_e(c['linkedin_url'])}'>LinkedIn</a>" if c["linkedin_url"] else ""
        out.append(f"<div class='card'><b>{_e(c['name'])}</b> <span class='chip'>#{c['id']}</span>"
                   f"<div>{_e(c['role'] or '')} @ {_e(c['company'] or '-')}</div>"
                   f"<div class='muted'>{_e(c['relationship'] or '')} · {_e(STRENGTH_TXT.get(c['strength'] or 1))}{link}</div>"
                   + (f"<div class='muted'>{_e(c['notes'][:160])}</div>" if c["notes"] else "") + "</div>")
    if not contacts:
        out.append("<div class='muted'>סמן אנשי קשר קרובים: jobradar net update &lt;id&gt; --strength 3</div>")
    out.append("</div></main></body></html>")

    folder = cfg.path("paths.reports")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "network.html"
    path.write_text("\n".join(out), encoding="utf-8")
    return path
