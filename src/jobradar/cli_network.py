"""CLI commands for networking and applications.

    jobradar net import-linkedin Connections.csv
    jobradar net add --name "Dana Levi" --company Wix --role "Data Lead" --relationship friend --strength 3
    jobradar net update 12 --strength 3 --notes "..."
    jobradar net find dana            jobradar net at Wix            jobradar net show 12
    jobradar net log "Dana" --summary "..." --channel whatsapp --follow-up +7 [--app 3]
    jobradar net followups [--days 7]   jobradar net done <interaction_id>

    jobradar apps add --job 123 --status applied --via 12
    jobradar apps add --company Wix --title "Data Engineer" --url ... --status interested
    jobradar apps update 3 --status interview --note "first round on Sunday" --next "prepare SQL" --next-on +3
    jobradar apps list [--all] [--json]

    jobradar view            -> reports/network.html (local page, never published)

Every listing command has --json so the local agent can read results reliably.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobradar.network import APP_STATUSES, CHANNELS, RELATIONSHIPS, STRENGTH_TXT, Network


def _net(cfg):
    from jobradar.store import Store
    store = Store(cfg.path("paths.db"))
    return store, Network(store)


def _row(r) -> dict:
    return {k: r[k] for k in r.keys()}


def _print_rows(rows, args, fmt) -> None:
    if getattr(args, "json", False):
        print(json.dumps([_row(r) for r in rows], ensure_ascii=False, indent=1))
    elif not rows:
        print("(אין)")
    else:
        for r in rows:
            print(fmt(r))


def _contact_line(c) -> str:
    bits = [b for b in (c["role"], c["relationship"], STRENGTH_TXT.get(c["strength"]) if c["strength"] else "") if b]
    return f"#{c['id']:<5} {c['name']} @ {c['company'] or '-'}" + (f"  ({', '.join(bits)})" if bits else "")


def _app_line(a) -> str:
    ref = f" · via {a['referral_name']}" if "referral_name" in a.keys() and a["referral_name"] else ""
    nxt = f" · next: {a['next_step']} ({a['next_step_on'] or '-'})" if a["next_step"] else ""
    return f"#{a['id']:<4} [{a['status']}] {a['title']} @ {a['company']}{ref}{nxt}"


# ------------------------------------------------------------------ net
def cmd_net(cfg, args) -> int:
    store, net = _net(cfg)
    try:
        return _NET[args.net_cmd](net, store, cfg, args) or 0
    except ValueError as e:
        print(f"שגיאה: {e}")
        return 1
    finally:
        store.close()


def _import(net, store, cfg, args):
    res = net.import_linkedin_csv(Path(args.file))
    print(f"יובאו: {res['created']} חדשים, {res['updated']} עודכנו, {res['skipped']} דולגו.")
    print("קשרים מיובאים מסומנים כ'קשר רופף'. כדאי לסמן את הקרובים: jobradar net update <id> --strength 3")


def _add(net, store, cfg, args):
    cid, created = net.add_contact(name=args.name, company=args.company or "", role=args.role or "",
                                   relationship=args.relationship or "", strength=args.strength,
                                   how_met=args.how_met or "", linkedin_url=args.linkedin or "",
                                   email=args.email or "", tags=args.tags or "", notes=args.notes or "")
    print(f"{'נוסף' if created else 'עודכן (כבר קיים)'}: " + _contact_line(net.get_contact(cid)))


def _update(net, store, cfg, args):
    c = net.resolve_contact(args.ref)
    notes = args.notes
    if args.append_note:
        notes = ((c["notes"] + "\n") if c["notes"] else "") + args.append_note
    net.update_contact(c["id"], company=args.company, role=args.role, relationship=args.relationship,
                       strength=args.strength, how_met=args.how_met, linkedin_url=args.linkedin,
                       email=args.email, tags=args.tags, notes=notes, name=args.name)
    print("עודכן: " + _contact_line(net.get_contact(c["id"])))


def _find(net, store, cfg, args):
    _print_rows(net.find_contacts(args.query, args.limit), args, _contact_line)


def _at(net, store, cfg, args):
    _print_rows(net.contacts_at(args.company), args, _contact_line)


def _show(net, store, cfg, args):
    c = net.resolve_contact(args.ref)
    inter = net.interactions_for(c["id"])
    apps = [a for a in net.applications() if a["referral_contact_id"] == c["id"]]
    if args.json:
        print(json.dumps({"contact": _row(c), "interactions": [_row(i) for i in inter],
                          "referred_applications": [_row(a) for a in apps]}, ensure_ascii=False, indent=1))
        return
    print(_contact_line(c))
    for k in ("how_met", "linkedin_url", "email", "tags", "notes", "connected_on"):
        if c[k]:
            print(f"  {k}: {c[k]}")
    if inter:
        print("שיחות:")
        for i in inter:
            fu = f"  ⏰ {i['follow_up_on']}{' ✓' if i['follow_up_done'] else ''}" if i["follow_up_on"] else ""
            print(f"  {i['date']} [{i['channel']}] {i['summary']}{fu}")
    for a in apps:
        print("  הפנה ל: " + _app_line(a))


def _log(net, store, cfg, args):
    cid = net.resolve_contact(args.contact)["id"] if args.contact else None
    iid = net.log(cid, args.summary, args.channel, args.date, args.follow_up, args.app)
    due = store.db.execute("SELECT follow_up_on FROM interactions WHERE id = ?", (iid,)).fetchone()[0]
    print(f"נרשם (interaction #{iid})" + (f", תזכורת ב-{due}" if due else ""))


def _followups(net, store, cfg, args):
    rows = net.followups(args.days)
    _print_rows(rows, args, lambda r: f"#{r['id']:<5} ⏰ {r['follow_up_on']}  {r['contact_name'] or '-'}"
                                      f" ({r['contact_company'] or '-'}): {r['summary']}")


def _done(net, store, cfg, args):
    net.mark_followup_done(args.interaction_id)
    print("סומן כבוצע.")


_NET = {"import-linkedin": _import, "add": _add, "update": _update, "find": _find, "at": _at,
        "show": _show, "log": _log, "followups": _followups, "done": _done}


# ----------------------------------------------------------------- apps
def cmd_apps(cfg, args) -> int:
    store, net = _net(cfg)
    try:
        if args.apps_cmd == "add":
            via = net.resolve_contact(args.via)["id"] if args.via else None
            company, title, url = args.company, args.title, args.url or ""
            if args.job:
                job = store.get_job(args.job)
                if not job:
                    raise ValueError(f"no job #{args.job}")
                company, title, url = company or job["company"], title or job["title"], url or job["url"]
            if not (company and title):
                raise ValueError("give --job, or both --company and --title")
            app_id = net.add_application(company, title, url, args.job, args.status, via, args.note or "")
            print("נוסף: " + _app_line(next(x for x in net.applications() if x["id"] == app_id)))
        elif args.apps_cmd == "update":
            via = net.resolve_contact(args.via)["id"] if args.via else None
            net.update_application(args.id, args.status, args.note, args.next, args.next_on, via)
            a = next(x for x in net.applications() if x["id"] == args.id)
            print("עודכן: " + _app_line(a))
        elif args.apps_cmd == "list":
            _print_rows(net.applications(active_only=not args.all), args, _app_line)
        elif args.apps_cmd == "show":
            a = next((x for x in net.applications() if x["id"] == args.id), None)
            if not a:
                raise ValueError(f"no application #{args.id}")
            ev = net.app_events(args.id)
            if args.json:
                print(json.dumps({"application": _row(a), "events": [_row(e) for e in ev]}, ensure_ascii=False, indent=1))
            else:
                print(_app_line(a))
                if a["url"]:
                    print(f"  {a['url']}")
                for e in ev:
                    print(f"  {e['date']} {e['status']}" + (f" — {e['note']}" if e["note"] else ""))
        return 0
    except ValueError as e:
        print(f"שגיאה: {e}")
        return 1
    finally:
        store.close()


def cmd_view(cfg, args) -> int:
    from jobradar.view import write_network_view
    store, net = _net(cfg)
    try:
        path = write_network_view(cfg, store, net)
    finally:
        store.close()
    print(f"נוצר {path}  (קובץ מקומי – פותחים בדפדפן. לא מתפרסם לשום מקום.)")
    return 0


# --------------------------------------------------------------- parser
def register(sub) -> None:
    n = sub.add_parser("net", help="נטוורקינג: אנשי קשר, שיחות ותזכורות")
    ns = n.add_subparsers(dest="net_cmd", required=True)
    d = ns.add_parser("import-linkedin", help="ייבוא Connections.csv מלינקדין")
    d.add_argument("file")
    d = ns.add_parser("add", help="הוספת איש קשר (או עדכון אם קיים)")
    d.add_argument("--name", required=True)
    for f in ("company", "role", "how-met", "linkedin", "email", "tags", "notes"):
        d.add_argument(f"--{f}")
    d.add_argument("--relationship", choices=RELATIONSHIPS)
    d.add_argument("--strength", type=int, choices=[1, 2, 3], help="1 רופף · 2 מכירים · 3 קרוב/יפנה")
    d = ns.add_parser("update", help="עדכון איש קשר (id או שם)")
    d.add_argument("ref")
    for f in ("name", "company", "role", "how-met", "linkedin", "email", "tags", "notes", "append-note"):
        d.add_argument(f"--{f}")
    d.add_argument("--relationship", choices=RELATIONSHIPS)
    d.add_argument("--strength", type=int, choices=[1, 2, 3])
    d = ns.add_parser("find", help="חיפוש לפי שם/חברה/תפקיד/תגיות")
    d.add_argument("query")
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--json", action="store_true")
    d = ns.add_parser("at", help="את מי אני מכיר בחברה")
    d.add_argument("company")
    d.add_argument("--json", action="store_true")
    d = ns.add_parser("show", help="איש קשר + היסטוריית שיחות")
    d.add_argument("ref")
    d.add_argument("--json", action="store_true")
    d = ns.add_parser("log", help="רישום שיחה/הודעה/פגישה")
    d.add_argument("contact", nargs="?", help="id או שם (אופציונלי)")
    d.add_argument("--summary", required=True)
    d.add_argument("--channel", choices=CHANNELS, default="other")
    d.add_argument("--date", help="YYYY-MM-DD (ברירת מחדל היום)")
    d.add_argument("--follow-up", help="תאריך תזכורת: YYYY-MM-DD או +7")
    d.add_argument("--app", type=int, help="קישור לתהליך הגשה")
    d = ns.add_parser("followups", help="תזכורות שהגיע זמנן")
    d.add_argument("--days", type=int, default=7)
    d.add_argument("--json", action="store_true")
    d = ns.add_parser("done", help="סימון תזכורת כבוצעה")
    d.add_argument("interaction_id", type=int)
    n.set_defaults(fn=cmd_net)

    a = sub.add_parser("apps", help="תהליכי הגשה")
    as_ = a.add_subparsers(dest="apps_cmd", required=True)
    d = as_.add_parser("add", help="תהליך חדש: --job <id> או --company + --title")
    d.add_argument("--job", type=int)
    d.add_argument("--company")
    d.add_argument("--title")
    d.add_argument("--url")
    d.add_argument("--status", choices=APP_STATUSES, default="interested")
    d.add_argument("--via", help="איש הקשר שהפנה (id או שם)")
    d.add_argument("--note")
    d = as_.add_parser("update", help="עדכון סטטוס / צעד הבא")
    d.add_argument("id", type=int)
    d.add_argument("--status", choices=APP_STATUSES)
    d.add_argument("--note")
    d.add_argument("--next", help="הצעד הבא")
    d.add_argument("--next-on", help="תאריך הצעד הבא: YYYY-MM-DD או +3")
    d.add_argument("--via")
    d = as_.add_parser("list", help="תהליכים פעילים (או --all)")
    d.add_argument("--all", action="store_true")
    d.add_argument("--json", action="store_true")
    d = as_.add_parser("show")
    d.add_argument("id", type=int)
    d.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_apps)

    sub.add_parser("view", help="עמוד מקומי: תהליכים, תזכורות ואנשי קשר").set_defaults(fn=cmd_view)
