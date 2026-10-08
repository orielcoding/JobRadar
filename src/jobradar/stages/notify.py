"""Stage 4 - send pings.

One ping per strong match (best first, capped per run), plus at most one
digest ping listing "light matches" (jobs that passed triage but had no
description to evaluate - LinkedIn alerts, the techmap feed).

Each ping also carries networking context:
  ⭐  the company is one of your favorites
  🤝  people you know there (from your contacts)
  📌  you already have an application process with this company
"""

from __future__ import annotations

import logging

from jobradar.favorites import Favorites
from jobradar.network import Network, describe_contacts
from jobradar.stages.hard_filter import job_age_days
from jobradar.textutil import truncate

log = logging.getLogger(__name__)


def network_context(net: Network, company: str) -> dict:
    contacts = net.contacts_at(company)
    known = [c for c in contacts if (c["strength"] or 1) >= 2 or c["relationship"]]
    apps = net.applications_at(company)
    return {"contacts": contacts, "known": known, "apps": apps,
            "warmth": sum((c["strength"] or 1) for c in contacts[:5])}


def network_lines(ctx_net: dict) -> list[str]:
    lines = []
    known, contacts = ctx_net["known"], ctx_net["contacts"]
    if known:
        lines.append(f"🤝 {describe_contacts(known, 2)}")
    elif contacts:
        lines.append(f"🤝 {len(contacts)} קשרים בלינקדין בחברה (למשל {contacts[0]['name']})")
    for a in ctx_net["apps"][:1]:
        lines.append(f"📌 כבר בתהליך: {a['title']} · {a['status']}")
    return lines


def format_match(job, ev: dict, net_ctx: dict | None = None, favorite: bool = False) -> tuple[str, str]:
    s = ev.get("scores", {})
    d = ev.get("decision", {})
    title = f"{'⭐' if favorite else '🎯'} {job['title']} @ {job['company']}"
    lines = [f"יכולת {s.get('capability')} · רצון {s.get('desire')} · מעבר סינון {s.get('screen_pass')}"]
    if d.get("advice") == "referral":
        lines.append("🤝 הקו״ח לא יעבור סינון קר: עדיף דרך הפניה (jobradar net at)")
    elif d.get("advice") == "tailor" or d.get("cv_gap"):
        lines.append(f"✏️ כדאי להתאים קו״ח: /cv {job['id']}")
    if net_ctx:
        lines += network_lines(net_ctx)
    if ev.get("pitch"):
        lines.append(truncate(ev["pitch"], 220))
    if ev.get("recruiter_objection"):
        lines.append(f"⚠️ {truncate(ev['recruiter_objection'], 180)}")
    lines.append(f"#{job['id']} · {job['location'] or ''}  |  דרג: jobradar review")
    return title, "\n".join(lines)


def light_digest_allowed(values) -> set[str]:
    """Triage verdicts allowed into the digest. YAML 1.1 reads an unquoted `yes` / `no` as
    True / False, so map booleans back to the verdict words."""
    out = set()
    for v in values or ["yes"]:
        out.add({True: "yes", False: "no"}.get(v, v) if isinstance(v, bool) else str(v).lower())
    return out


def too_old(job, max_age_hours, late_sources=()) -> bool:
    """True when the job is older than notify.max_age_hours (aged like hard_filter: job_age_days)."""
    if not max_age_hours:
        return False
    age = job_age_days(job, late_sources)
    return age is not None and age * 24 > float(max_age_hours)


class NotifyStage:
    name = "notify"

    def run(self, ctx) -> dict:
        store, cfg = ctx.store, ctx.config
        net, favs = Network(store), Favorites(cfg)
        stats = {"pings": 0, "light_digest": 0, "failed": 0, "too_old": 0}
        max_pings = int(cfg.get("notify.max_pings_per_run", 8))
        max_age = cfg.get("notify.max_age_hours")
        late = set(cfg.filters.get("late_sources") or [])

        pending = []
        for job in store.pending_notifications():
            if too_old(job, max_age, late):  # stays in the report, just no ping
                stats["too_old"] += 1
                continue
            ev = store.latest_evaluation(job["id"], "deep") or {}
            nc = network_context(net, job["company"])
            rank = ev.get("decision", {}).get("rank", 0) + min(nc["warmth"], 4)  # a warm intro is worth a lot
            pending.append((rank, job, ev, nc))
        pending.sort(key=lambda x: x[0], reverse=True)

        sent_ids = []
        for _, job, ev, nc in pending[:max_pings]:
            title, body = format_match(job, ev, nc, favs.is_favorite(job["company"]))
            if self._send_all(ctx, title, body, job["url"], priority=4):
                sent_ids.append(job["id"])
                stats["pings"] += 1
            else:
                stats["failed"] += 1

        if cfg.get("notify.light_matches_digest", True):
            sent_ids += self._light_digest(ctx, net, favs, stats)

        if not ctx.dry_run:
            store.mark_notified(sent_ids)
            store.commit()
        return stats

    def _light_digest(self, ctx, net, favs, stats) -> list[int]:
        store, cfg = ctx.store, ctx.config
        light = store.pending_light_matches()
        if not light:
            return []
        allowed = light_digest_allowed(cfg.get("notify.light_digest_verdicts"))
        scored = []
        max_age = cfg.get("notify.max_age_hours")
        late = set(cfg.filters.get("late_sources") or [])
        for j in light:
            if too_old(j, max_age, late):
                continue
            fav = favs.is_favorite(j["company"])
            verdict = (store.latest_evaluation(j["id"], "triage") or {}).get("verdict", "favorite" if fav else "?")
            nc = network_context(net, j["company"])
            if fav or verdict in allowed:
                scored.append(((fav, bool(nc["known"]), len(nc["contacts"]) > 0, verdict == "yes"), j, fav, nc))
        scored.sort(key=lambda x: x[0], reverse=True)
        n = int(cfg.get("notify.light_digest_max_items", 10))
        items = []
        for _, j, fav, nc in scored[:n]:
            marks = ("⭐" if fav else "") + ("🤝" if nc["contacts"] else "")
            items.append(f"• {marks}{j['title']} @ {j['company']} (#{j['id']}) — {j['url']}")
        if items:
            more = f"\n(+{len(scored) - n} נוספות בדוח)" if len(scored) > n else ""
            body = ("עברו סינון ראשוני אבל אין להן תיאור משרה, אז לא נבדקו לעומק. "
                    "להערכה מלאה: העתק את התיאור ו-jobradar paste <id>\n" + "\n".join(items) + more)
            if self._send_all(ctx, f"👀 {len(scored)} משרות אפשריות (בלי תיאור)", body, None, priority=2):
                stats["light_digest"] = len(scored)
            else:
                return []
        # Everything looked at here is done: the rest stays visible in the daily report.
        return [j["id"] for j in light]

    @staticmethod
    def _send_all(ctx, title, body, url, priority) -> bool:
        ok = False
        for n in ctx.notifiers:
            try:
                n.send(title, body, url, priority)
                ok = True
            except Exception as e:  # noqa: BLE001
                log.error("notify via %s failed: %s", n.name, e)
        return ok
