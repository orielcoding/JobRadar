"""Command line interface. Run `jobradar --help` or `jobradar <command> --help`."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from jobradar import __version__
from jobradar.config import Config
from jobradar.models import JobPosting, Status

# Hebrew output on Windows consoles
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

TEMPLATES = [
    ("config/config.example.yaml", "config/config.yaml"),
    ("config/companies.example.yaml", "config/companies.yaml"),
    ("config/filters.example.yaml", "config/filters.yaml"),
    ("profile/profile.example.md", "profile/profile.md"),
    ("profile/cv.example.md", "profile/cv.md"),
]


# ------------------------------------------------------------------ helpers
def _ctx(cfg, args, need_llm=True, dry_run=False):
    from jobradar.pipeline import open_context
    return open_context(cfg, backend=getattr(args, "backend", None), dry_run=dry_run, need_llm=need_llm)


def _read_body(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    print("הדבק את תיאור המשרה ואז שורה ריקה + Ctrl-D (Mac/Linux) או Ctrl-Z ואז Enter (Windows):")
    return sys.stdin.read()


def _print_eval(job, ev):
    from jobradar.stages.report import render_evaluation
    print(render_evaluation(job, ev))


# ----------------------------------------------------------------- commands
def cmd_init(cfg: Config, args) -> int:
    home = cfg.home
    for src, dst in TEMPLATES:
        s, d = home / src, home / dst
        if d.exists():
            print(f"✓ קיים: {dst}")
        elif s.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(s, d)
            print(f"+ נוצר: {dst}  (מתוך {src})")
        else:
            print(f"! חסרה תבנית {src} – האם אתה בתיקיית הפרויקט?")
    env = home / ".env"
    if env.exists():
        print("✓ קיים: .env")
    else:
        example = home / ".env.example"
        text = example.read_text(encoding="utf-8") if example.exists() else "NTFY_TOPIC=\n"
        topic = "jobradar-" + secrets.token_urlsafe(12).replace("_", "x").replace("-", "z").lower()
        text = text.replace("NTFY_TOPIC=", f"NTFY_TOPIC={topic}", 1)
        env.write_text(text, encoding="utf-8")
        print(f"+ נוצר: .env  (נושא ntfy פרטי: {topic})")
    for d in ("data", "reports"):
        (home / d).mkdir(exist_ok=True)
    print("\nהשלבים הבאים: docs/SETUP.md (רשימת המשימות המלאה).")
    return 0


def cmd_doctor(cfg: Config, args) -> int:
    from jobradar.llm import LLMError, get_backend
    from jobradar.llm.schemas import PROBE_SCHEMA
    from jobradar.sources import build_sources

    ok = True

    def line(good: bool, msg: str):
        nonlocal ok
        ok = ok and good
        print(("✅ " if good else "❌ ") + msg)

    line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}")
    for _, dst in TEMPLATES:
        line((cfg.home / dst).exists(), f"{dst}")
    prof = cfg.read_text("paths.profile")
    line("TODO" not in prof and len(prof) > 400, "profile.md מולא (אין TODO, יותר מ-400 תווים)")
    cv = cfg.read_text("paths.cv")
    line("TODO" not in cv and len(cv) > 300, "cv.md מולא")
    sources, warnings = build_sources(cfg)
    line(bool(sources), f"{len(sources)} מקורות מוגדרים")
    for w in warnings:
        print(f"   ⚠️  {w}")

    claude = shutil.which(cfg.get("llm.claude_bin"))
    line(bool(claude), f"Claude Code: {claude or 'לא נמצא ב-PATH'}")
    if claude:
        try:
            v = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
            print(f"   גרסה: {v}")
        except Exception as e:  # noqa: BLE001
            print(f"   לא הצלחתי לקרוא גרסה: {e}")
    print(f"   CLAUDE_CODE_OAUTH_TOKEN ב-.env: {'כן' if cfg.secret('CLAUDE_CODE_OAUTH_TOKEN') else 'לא (ישתמש בהתחברות הרגילה)'}")
    if not args.skip_llm and (claude or args.backend == "fake"):
        try:
            be = get_backend(cfg, args.backend)
            res = be.complete_json(system="Reply with ok=true.", user="ping", schema=PROBE_SCHEMA,
                                   model=cfg.get("llm.triage_model"), purpose="probe")
            line(res.data.get("ok") is True,
                 f"קריאה ל-{cfg.get('llm.triage_model')} עבדה ({res.meta.get('duration_s', '?')}s, "
                 f"structured_output={'כן' if res.meta.get('structured', True) else 'לא – נקרא מהטקסט, זה בסדר'})")
        except LLMError as e:
            line(False, f"קריאה למודל נכשלה: {e}")
            print("   רמזים: הרץ `claude` פעם אחת ידנית והתחבר; אם השגיאה על --setting-sources "
                  "או --tools, שנה את llm.setting_sources / llm.isolation_args ב-config.yaml.")

    if cfg.get("gmail.enabled"):
        try:
            from jobradar.sources.gmail_alerts import GmailAlertsSource
            n = len(GmailAlertsSource(cfg).fetch())
            line(True, f"Gmail: התחברות ותווית '{cfg.get('gmail.label')}' תקינות ({n} משרות במיילים האחרונים)")
        except Exception as e:  # noqa: BLE001
            line(False, f"Gmail: {e}")

    if args.ping:
        from jobradar.notifiers import build_notifiers
        for n in build_notifiers(cfg):
            try:
                n.send("JobRadar – בדיקה ✅", "אם אתה רואה את זה, ההתראות עובדות.", None, 3)
                line(True, f"נשלחה התראת בדיקה דרך {n.name}")
            except Exception as e:  # noqa: BLE001
                line(False, f"התראה דרך {n.name} נכשלה: {e}")
    print("\nהכל תקין." if ok else "\nיש דברים לתקן (מסומנים ב-❌).")
    return 0 if ok else 1


def cmd_detect(cfg: Config, args) -> int:
    from jobradar.sources.detect import detect, guess_name
    found = []
    for url in args.urls:
        entry, why = detect(url, fetch_page=not args.no_fetch)
        if entry:
            entry = {"name": guess_name(url, entry), **entry}
            if "careers_url" not in entry:
                entry["careers_url"] = url
            found.append(entry)
            print(f"✅ {url}\n   {why}: {entry}")
        else:
            print(f"❌ {url}\n   {why}")
    if found and args.add:
        path = cfg.home / "config" / "companies.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        data = data or {}
        existing = {(c.get("ats"), c.get("slug")) for c in data.get("companies") or []}
        new = [e for e in found if (e["ats"], e["slug"]) not in existing]
        if new:
            with path.open("a", encoding="utf-8") as f:
                if not data.get("companies"):
                    f.write("\ncompanies:\n")
                for e in new:
                    f.write(f"  - name: {json.dumps(e['name'], ensure_ascii=False)}\n")
                    for k, v in e.items():
                        if k != "name":
                            f.write(f"    {k}: {json.dumps(v, ensure_ascii=False)}\n")
            print(f"\nנוספו {len(new)} חברות ל-config/companies.yaml (בדוק את השמות).")
    elif found:
        print("\nלהוספה אוטומטית ל-companies.yaml הוסף --add")
    return 0


def cmd_test_sources(cfg: Config, args) -> int:
    from jobradar import http
    from jobradar.sources import build_sources
    http.configure(cfg.get("fetch.user_agent"), cfg.get("fetch.timeout_sec"))
    sources, warnings = build_sources(cfg, only=args.only)
    for w in warnings:
        print(f"⚠️  {w}")
    bad = 0
    for s in sources:
        try:
            jobs = s.fetch()
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"❌ {s.name}: {e}")
            continue
        with_desc = sum(1 for j in jobs if len(j.description) > 200)
        print(f"✅ {s.name}: {len(jobs)} משרות ({with_desc} עם תיאור)")
        for j in jobs[: args.show]:
            print(f"     • {j.title} | {j.company} | {j.location or '-'} | {j.url}")
    print(f"\n{len(sources) - bad}/{len(sources)} מקורות עובדים.")
    return 0 if not bad else 1


def cmd_run(cfg: Config, args) -> int:
    from jobradar.pipeline import run_pipeline
    stages = args.stages.split(",") if args.stages else None
    if args.no_fetch:
        stages = [s for s in (stages or cfg.get("pipeline.stages")) if s != "ingest"]
    overrides = {"max_deep": args.max_deep, "max_triage_calls": args.max_triage_calls}
    stats = run_pipeline(cfg, stages, backend=args.backend, dry_run=args.dry_run, overrides=overrides)
    print(json.dumps({k: v for k, v in stats.items() if not k.startswith("_")}, ensure_ascii=False, indent=1))
    return 0


def cmd_fetch(cfg: Config, args) -> int:
    args.stages = "ingest,dedupe,hard_filter"
    args.no_fetch = False
    args.dry_run = True
    return cmd_run(cfg, args)


def cmd_calibrate(cfg: Config, args) -> int:
    from jobradar.review import calibrate
    ctx = _ctx(cfg, args, need_llm=False)
    n = calibrate(ctx.store, args.n)
    print(f"\nנשמרו {n} דירוגים. סה\"כ דירוגים: {len(ctx.store.labeled_jobs())}")
    return 0


def cmd_review(cfg: Config, args) -> int:
    from jobradar.review import review
    ctx = _ctx(cfg, args, need_llm=False)
    n = review(ctx.store)
    print(f"\nנשמרו {n} דירוגים.")
    return 0


def cmd_show(cfg: Config, args) -> int:
    ctx = _ctx(cfg, args, need_llm=False)
    job = ctx.store.get_job(args.id)
    if not job:
        print("לא נמצא")
        return 1
    print(f"#{job['id']} [{job['status']}] {job['title']} @ {job['company']}\n{job['url']}\n"
          f"source={job['source']} reason={job['status_reason'] or '-'}")
    tri = ctx.store.latest_evaluation(job["id"], "triage")
    if tri:
        print(f"triage: {tri.get('verdict')} — {tri.get('reason')}")
    ev = ctx.store.latest_evaluation(job["id"], "deep")
    if ev:
        from jobradar.network import Network
        from jobradar.stages.notify import network_context
        from jobradar.stages.report import render_evaluation
        print(render_evaluation(job, ev, network_context(Network(ctx.store), job["company"])))
    elif args.description:
        print(job["description"])
    return 0


def _evaluate_now(cfg, args, job_id: int) -> int:
    from jobradar.llm import LLMError
    from jobradar.stages.deep_eval import evaluate_job
    ctx = _ctx(cfg, args)
    job = ctx.store.get_job(job_id)
    try:
        ev = evaluate_job(ctx, job)
    except LLMError as e:
        print(f"ההערכה נכשלה: {e}\nהמשרה תוערך בהרצה הבאה.")
        return 1
    ctx.store.set_status(job_id, Status.EVALUATED, ev.get("verdict"),
                         decision="notify" if ev["decision"]["notify"] else "skip", notified_at=None)
    ctx.store.commit()
    _print_eval(job, ev)
    return 0


def cmd_paste(cfg: Config, args) -> int:
    ctx = _ctx(cfg, args, need_llm=False)
    job = ctx.store.get_job(args.id)
    if not job:
        print("לא נמצא")
        return 1
    body = _read_body(args.file).strip()
    if len(body) < 100:
        print("התיאור קצר מדי.")
        return 1
    ctx.store.set_description(args.id, body)
    ctx.store.set_status(args.id, Status.DEEP_PENDING, "description pasted manually", notified_at=None)
    ctx.store.commit()
    ctx.store.close()
    return 0 if args.later else _evaluate_now(cfg, args, args.id)


def cmd_add(cfg: Config, args) -> int:
    from jobradar.textutil import content_hash
    body = _read_body(args.file).strip()
    p = JobPosting(source="manual", native_id=content_hash(args.url or "", args.title, args.company),
                   company=args.company, title=args.title, url=args.url or "", location=args.location or "",
                   description=body)
    ctx = _ctx(cfg, args, need_llm=False)
    job_id, _ = ctx.store.upsert_job(p)
    ctx.store.set_status(job_id, Status.DEEP_PENDING, "added manually")
    ctx.store.commit()
    ctx.store.close()
    print(f"נוספה משרה #{job_id}")
    return 0 if args.later else _evaluate_now(cfg, args, job_id)


def cmd_eval(cfg: Config, args) -> int:
    from jobradar.evaluate import run_eval
    ctx = _ctx(cfg, args)
    ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    run_eval(ctx, args.limit, ids)
    return 0


def cmd_stats(cfg: Config, args) -> int:
    ctx = _ctx(cfg, args, need_llm=False)
    counts = ctx.store.counts_by_status()
    print("משרות לפי סטטוס:")
    for s in Status:
        if counts.get(s.value):
            print(f"  {s.value:<16} {counts[s.value]}")
    labels = ctx.store.labeled_jobs()
    by = {k: sum(1 for r in labels if r["label"] == k) for k in ("good", "ok", "bad")}
    print(f"דירוגים שלך: {len(labels)}  (good {by['good']} · ok {by['ok']} · bad {by['bad']})")
    runs = ctx.store.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 5").fetchall()
    print("הרצות אחרונות:")
    for r in runs:
        st = json.loads(r["stats"] or "{}")
        ing, tri, deep, no = (st.get(k, {}) for k in ("ingest", "triage", "deep_eval", "notify"))
        print(f"  #{r['id']} {r['started_at']}  new={ing.get('new', '-')} triage_calls={tri.get('calls', '-')} "
              f"deep={deep.get('evaluated', '-')} pings={no.get('pings', '-')} {st.get('note', '')}")
    return 0


def cmd_setup_status(cfg: Config, args) -> int:
    from jobradar.setup_status import collect, render
    status = collect(cfg)
    print(json.dumps(status, ensure_ascii=False, indent=1) if args.json else render(status))
    return 0


def cmd_list(cfg: Config, args) -> int:
    from jobradar.review import calibration_candidates, job_summary, review_candidates
    ctx = _ctx(cfg, args, need_llm=False)
    rows = (calibration_candidates(ctx.store, args.n, args.seed) if args.purpose == "calibrate"
            else review_candidates(ctx.store, args.n))
    items = [job_summary(ctx.store, r, args.chars) for r in rows]
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=1))
    else:
        for it in items:
            sc = it["scores"] or {}
            extra = f"  C{sc.get('capability')} D{sc.get('desire')} S{sc.get('screen_pass')}" if sc else ""
            print(f"#{it['id']:<5} {it['title']} @ {it['company']} | {it['location'] or '-'}{extra}\n       {it['url']}")
    return 0


def cmd_label(cfg: Config, args) -> int:
    ctx = _ctx(cfg, args, need_llm=False)
    job = ctx.store.get_job(args.id)
    if not job:
        print("לא נמצא")
        return 1
    ctx.store.set_label(args.id, args.label, args.note, args.origin)
    print(f"#{args.id} {job['title']} @ {job['company']} → {args.label}" + (f" ({args.note})" if args.note else ""))
    return 0


def cmd_jobs(cfg: Config, args) -> int:
    ctx = _ctx(cfg, args, need_llm=False)
    q = f"%{args.query.lower()}%"
    rows = ctx.store.db.execute(
        "SELECT * FROM jobs WHERE (lower(title) LIKE ? OR lower(company) LIKE ?) AND status != 'duplicate' "
        "ORDER BY id DESC LIMIT ?", (q, q, args.limit)).fetchall()
    items = [{"id": r["id"], "title": r["title"], "company": r["company"], "status": r["status"],
              "decision": r["decision"], "url": r["url"], "first_seen": r["first_seen_at"][:10]} for r in rows]
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=1))
    else:
        for it in items:
            print(f"#{it['id']:<6} [{it['status']}] {it['title']} @ {it['company']}  {it['url']}")
    return 0


def cmd_filter_test(cfg: Config, args) -> int:
    from jobradar.favorites import Favorites
    from jobradar.stages.hard_filter import filter_test, reapply
    ctx = _ctx(cfg, args, need_llm=False)
    if args.apply:
        print(json.dumps(reapply(ctx.store, cfg.filters, Favorites(cfg).is_favorite), ensure_ascii=False))
        return 0
    res = filter_test(ctx.store, cfg.filters, samples=args.samples, is_favorite=Favorites(cfg).is_favorite)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print(f"{res['total']} משרות ב-DB · עוברות {res['passed']} · נפסלות {res['total'] - res['passed']}\n")
    for rule, d in res["excluded_by_rule"].items():
        print(f"[{d['count']}] {rule}")
        for ex in d["examples"]:
            print(f"     ✗ {ex}")
    print("\nדוגמאות שעוברות:")
    for ex in res["passing_examples"]:
        print(f"     ✓ {ex}")
    if res["would_newly_exclude"]:
        print(f"\n⚠️  ייפסלו עכשיו משרות שכבר עברו קודם ({len(res['would_newly_exclude'])}):")
        for ex in res["would_newly_exclude"][:10]:
            print(f"     {ex}")
    if res["would_newly_include"]:
        print(f"\nיעברו עכשיו משרות שנפסלו קודם ({len(res['would_newly_include'])}). להחלה: jobradar filter-test --apply")
    return 0


def cmd_import_techmap(cfg: Config, args) -> int:
    """Add Israeli companies with a known ATS (from the Israeli Tech Map snapshot) to companies.yaml."""
    seed = cfg.home / "seeds" / "techmap_companies.json"
    if not seed.exists():
        seed = Path(__file__).resolve().parents[2] / "seeds" / "techmap_companies.json"
    data = json.loads(seed.read_text(encoding="utf-8"))
    sizes = set(args.sizes.split(",")) if args.sizes else None
    industries = {s.strip().lower() for s in args.industries.split(",")} if args.industries else None
    path = cfg.home / "config" / "companies.yaml"
    existing = {(c.get("ats"), str(c.get("slug", "")).lower()) for c in cfg.companies}
    picked = []
    for c in data["companies"]:
        if sizes and c.get("size") not in sizes:
            continue
        if industries and c.get("industry", "").lower() not in industries:
            continue
        if (c["ats"], c["slug"].lower()) in existing:
            continue
        picked.append(c)
    print(f"{len(picked)} חברות חדשות (מתוך {len(data['companies'])} בקובץ; מקור: {data['source']})")
    for c in picked[:15]:
        print(f"   {c['name']} · {c['industry']} · {data['sizes'].get(c['size'], c['size'])} · {c['ats']}")
    if len(picked) > 15:
        print(f"   ... ועוד {len(picked) - 15}")
    if args.dry_run or not picked:
        return 0
    text = path.read_text(encoding="utf-8") if path.exists() else "companies:\n"
    if not (yaml.safe_load(text) or {}).get("companies"):
        text = text.replace("companies: []", "").rstrip() + "\ncompanies:\n"
    lines = [f"\n  # --- imported from Israeli Tech Map ({data['built_on']}) ---"]
    for c in picked:
        lines.append(f"  - name: {json.dumps(c['name'], ensure_ascii=False)}")
        for k in ("ats", "slug", "uid", "careers_url"):
            if c.get(k):
                lines.append(f"    {k}: {json.dumps(c[k], ensure_ascii=False)}")
        lines.append(f"    source: techmap  # {c.get('industry', '')}, {data['sizes'].get(c.get('size'), '')}")
    path.write_text(text.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print("נוספו ל-config/companies.yaml. בדיקה: jobradar test-sources")
    return 0


def cmd_schedule(cfg: Config, args) -> int:
    from jobradar.scheduling import GENERATORS, windows_inbox
    times = []
    for part in (args.time or ("12:00" if args.inbox else "08:15")).split(","):
        hh, mm = (int(x) for x in part.strip().split(":"))
        times.append((hh, mm))
    if args.inbox:
        if args.os != "windows":
            print("--inbox זמין כרגע רק ל-windows. בינתיים: jobradar inbox --push מתוך cron/launchd.")
            return 1
        print(windows_inbox(cfg, times))
        return 0
    print(GENERATORS[args.os](cfg, times))
    return 0


# ------------------------------------------------------------------ tracker
def cmd_inbox(cfg: Config, args) -> int:
    from jobradar.inbox_server import run_inbox
    from jobradar.tracker import Tracker
    if args.push:
        from jobradar.notifiers import build_notifiers
        ctx = _ctx(cfg, args, need_llm=False)
        t = Tracker(ctx.store)
        t.sync()
        title, body = t.digest_text()
        ctx.store.close()
        for n in build_notifiers(cfg):
            try:
                n.send(title, body, None, 3)
            except Exception as e:  # noqa: BLE001 - the window still opens
                print(f"push via {n.name} failed: {e}")
        print(title)
        print(body)
    if args.no_window and args.push:
        return 0
    url = f"http://127.0.0.1:{args.port or cfg.get('tracker.port', 8765)}/"
    print(f"חלון המעקב: {url}  (נסגר לבד כמה דקות אחרי שסוגרים את החלון. Ctrl+C לעצירה)")
    run_inbox(cfg, open_it=not args.no_window, port=args.port)
    return 0


def cmd_track(cfg: Config, args) -> int:
    from jobradar.tracker import STATE_HE, Tracker
    ctx = _ctx(cfg, args, need_llm=False)
    try:
        row = Tracker(ctx.store).set_state(args.id, args.state, args.note)
    except ValueError as e:
        print(f"שגיאה: {e}")
        return 1
    job = ctx.store.get_job(args.id)
    print(f"#{args.id} {job['title']} @ {job['company']} → {STATE_HE[row['state']]}"
          + (f" ({row['note']})" if row["note"] else ""))
    return 0


def cmd_tracker(cfg: Config, args) -> int:
    from jobradar.favorites import Favorites
    from jobradar.tracker import STATE_HE, Tracker
    ctx = _ctx(cfg, args, need_llm=False)
    t = Tracker(ctx.store)
    t.sync()
    items = t.items(args.days, Favorites(cfg).is_favorite)
    if args.json:
        print(json.dumps({"open": t.open_counts(), "funnels": t.funnels(), "items": items},
                         ensure_ascii=False, indent=1))
        return 0
    title, body = t.digest_text()
    print(title)
    print(body)
    print()
    for it in items:
        if args.state and it["state"] != args.state:
            continue
        print(f"  #{it['id']:<5} {it['day']}  {STATE_HE[it['state']]:<16} {it['title']} @ {it['company']}")
    return 0


def cmd_cv(cfg: Config, args) -> int:
    from jobradar.cv_builder import CVBuilder, NeedPosting
    steps = {"posting": "מאתר את תיאור המשרה…", "posting_ats": "קורא את המשרה מה-API של מערכת הגיוס…",
             "posting_page": "קורא את דף המשרה…", "posting_search": "מחפש את המשרה באתר החברה (קריאת מודל)…",
             "writing": "כותב קו״ח (קריאת מודל, 2–5 דקות)…", "pdf": "מייצר PDF…"}
    b = CVBuilder(cfg, args.backend, progress=lambda s: print(steps.get(s, s)))
    posting = Path(args.posting_file).read_text(encoding="utf-8") if args.posting_file else None
    try:
        meta = b.build(args.id, posting)
    except NeedPosting:
        print("לא מצאתי את תיאור המשרה (לינקדין לא נפתח). העתק אותו לקובץ והרץ: "
              f"jobradar cv {args.id} --posting-file <path>")
        return 2
    print(f"\nPDF: {b.pdf_dir / meta['pdf']}  ({meta.get('pages')} עמ׳, {meta.get('words')} מילים)")
    print(f"קבצי עבודה: {b.work_dir / meta['base']}.*")
    print(f"כותרת: {meta.get('headline')}\nסיכון עיקרי: {meta.get('main_risk')}")
    for q in meta.get("open_questions") or []:
        print(f"  ? {q['question']}  (ברירת מחדל: {q['default_used']})")
    return 0


def cmd_cv_pdf(cfg: Config, args) -> int:
    from jobradar.cv_builder import CVBuilder
    b = CVBuilder(cfg)
    base = args.target
    if base.isdigit():
        meta = b.find(int(base))
        if not meta:
            print(f"אין קו״ח למשרה #{base}")
            return 1
        base = meta["base"]
    base = Path(base).name.removesuffix(".md")
    meta = b.render_pdf(base)
    print(f"PDF: {b.pdf_dir / meta['pdf']}  ({meta.get('pages')} עמ׳, גופן {meta.get('font_pt')}pt)")
    return 0 if meta.get("pages") == 1 else 3


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobradar", description="רדאר משרות אישי – ראה README.md")
    p.add_argument("--home", help="תיקיית הפרויקט (ברירת מחדל: התיקייה הנוכחית או $JOBRADAR_HOME)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="יצירת קבצי הגדרות מהתבניות + .env").set_defaults(fn=cmd_init)

    d = sub.add_parser("doctor", help="בדיקת התקנה: קבצים, Claude Code, Gmail, התראות")
    d.add_argument("--ping", action="store_true", help="שלח התראת בדיקה לטלפון")
    d.add_argument("--skip-llm", action="store_true")
    d.add_argument("--backend")
    d.set_defaults(fn=cmd_doctor)

    d = sub.add_parser("detect", help="זיהוי מערכת הגיוס מקישור לדף קריירה")
    d.add_argument("urls", nargs="+")
    d.add_argument("--add", action="store_true", help="הוסף ל-companies.yaml")
    d.add_argument("--no-fetch", action="store_true", help="זיהוי לפי הקישור בלבד")
    d.set_defaults(fn=cmd_detect)

    d = sub.add_parser("test-sources", help="משיכה ניסיונית מכל מקור, בלי לשמור")
    d.add_argument("--only", help="רק חברה/מקור שהשם שלו מכיל את הטקסט")
    d.add_argument("--show", type=int, default=3)
    d.set_defaults(fn=cmd_test_sources)

    d = sub.add_parser("fetch", help="משיכה + כפילויות + מסננים, בלי מודל")
    d.add_argument("--backend")
    d.set_defaults(fn=cmd_fetch, max_deep=None, max_triage_calls=None)

    d = sub.add_parser("run", help="הרצה מלאה (זה מה שהמתזמן מריץ)")
    d.add_argument("--dry-run", action="store_true", help="התראות למסך בלבד")
    d.add_argument("--backend", help="claude_cli | fake")
    d.add_argument("--no-fetch", action="store_true")
    d.add_argument("--stages", help="רשימה מופרדת בפסיקים, למשל triage,deep_eval")
    d.add_argument("--max-deep", type=int)
    d.add_argument("--max-triage-calls", type=int)
    d.set_defaults(fn=cmd_run)

    d = sub.add_parser("calibrate", help="דרג מדגם משרות כדי ללמד את המודל את הטעם שלך")
    d.add_argument("-n", type=int, default=25)
    d.set_defaults(fn=cmd_calibrate)

    sub.add_parser("review", help="דרג משרות שקיבלת עליהן פינג").set_defaults(fn=cmd_review)

    d = sub.add_parser("show", help="הצג משרה והערכה")
    d.add_argument("id", type=int)
    d.add_argument("--description", action="store_true")
    d.set_defaults(fn=cmd_show)

    d = sub.add_parser("paste", help="הדבק תיאור למשרה בלי תיאור (למשל מלינקדין) והערך")
    d.add_argument("id", type=int)
    d.add_argument("--file")
    d.add_argument("--later", action="store_true", help="אל תעריך עכשיו, רק בהרצה הבאה")
    d.add_argument("--backend")
    d.set_defaults(fn=cmd_paste)

    d = sub.add_parser("add", help="הוסף משרה ידנית והערך אותה")
    d.add_argument("--title", required=True)
    d.add_argument("--company", required=True)
    d.add_argument("--url")
    d.add_argument("--location")
    d.add_argument("--file")
    d.add_argument("--later", action="store_true")
    d.add_argument("--backend")
    d.set_defaults(fn=cmd_add)

    d = sub.add_parser("cv", help="קו״ח מותאם למשרה: MD + הערות + PDF (מהמכסה)")
    d.add_argument("id", type=int)
    d.add_argument("--posting-file", help="תיאור המשרה מקובץ (למשל הועתק מלינקדין)")
    d.add_argument("--backend")
    d.set_defaults(fn=cmd_cv)

    d = sub.add_parser("cv-pdf", help="הדפסה מחדש של PDF מקובץ ה-MD של קו״ח (בלי מודל)")
    d.add_argument("target", help="מספר משרה, או שם/נתיב של profile/tailored/work/<base>.md")
    d.set_defaults(fn=cmd_cv_pdf)

    d = sub.add_parser("eval", help="מדידה: כמה המודל מסכים עם הדירוגים שלך")
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--ids", help="רק משרות מסוימות, למשל 7036,6538")
    d.add_argument("--backend")
    d.set_defaults(fn=cmd_eval)

    sub.add_parser("stats", help="סיכום מצב").set_defaults(fn=cmd_stats)

    d = sub.add_parser("jobs", help="חיפוש משרה ב-DB לפי כותרת/חברה (כדי למצוא id)")
    d.add_argument("query")
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_jobs)

    d = sub.add_parser("import-techmap", help="הוספת חברות ישראליות עם מערכת גיוס ידועה (Israeli Tech Map)")
    d.add_argument("--sizes", help="למשל m,l,xl (s=11-50, m=51-200, l=201-1000, xl=1001+)")
    d.add_argument("--industries", help="למשל Fintech,Cybersecurity,AI/ML")
    d.add_argument("--dry-run", action="store_true")
    d.set_defaults(fn=cmd_import_techmap)

    from jobradar import cli_network
    cli_network.register(sub)

    d = sub.add_parser("filter-test", help="הרצת המסננים על המשרות הקיימות בלי לשנות כלום (או --apply)")
    d.add_argument("--samples", type=int, default=6)
    d.add_argument("--json", action="store_true")
    d.add_argument("--apply", action="store_true", help="החל מחדש על משרות שהמודל עוד לא ראה")
    d.set_defaults(fn=cmd_filter_test)

    d = sub.add_parser("setup-status", help="איפה אני בהקמה ומה השלב הבא (בלי מודל, בלי רשת)")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_setup_status)

    d = sub.add_parser("list", help="רשימת משרות לדירוג (גם כ-JSON, לשימוש סוכן)")
    d.add_argument("--for", dest="purpose", choices=["calibrate", "review"], default="review")
    d.add_argument("-n", type=int, default=25)
    d.add_argument("--seed", type=int)
    d.add_argument("--chars", type=int, default=800, help="אורך קטע התיאור")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_list)

    d = sub.add_parser("label", help="דירוג משרה בלי ממשק אינטראקטיבי")
    d.add_argument("id", type=int)
    d.add_argument("label", choices=["good", "ok", "bad"])
    d.add_argument("--note", help="למה – משפט אחד")
    d.add_argument("--origin", default="agent")
    d.set_defaults(fn=cmd_label)

    d = sub.add_parser("schedule", help="יצירת קובץ תזמון יומי למערכת ההפעלה שלך")
    d.add_argument("os", choices=["macos", "windows", "linux"])
    d.add_argument("--time", help="HH:MM, אפשר כמה מופרדים בפסיק: 08:15,17:15 (ברירת מחדל 08:15, ועם --inbox 12:00)")
    d.add_argument("--inbox", action="store_true", help="תזמון חלון המעקב + פוש יומי, וקיצור דרך בשולחן העבודה")
    d.set_defaults(fn=cmd_schedule)

    d = sub.add_parser("inbox", help="חלון המעקב: משרות השבוע, סטטוסים ומשפך (מקומי בלבד)")
    d.add_argument("--push", action="store_true", help="שלח קודם פוש יומי עם הסיכום")
    d.add_argument("--no-window", action="store_true", help="בלי לפתוח חלון (עם --push: רק פוש)")
    d.add_argument("--port", type=int)
    d.set_defaults(fn=cmd_inbox)

    d = sub.add_parser("track", help="עדכון סטטוס מעקב של משרה (to_review / to_apply / applied / dismissed)")
    d.add_argument("id", type=int)
    d.add_argument("state", choices=["to_review", "to_apply", "applied", "dismissed"])
    d.add_argument("--note", help="למשל למה נדחתה – משפט אחד")
    d.set_defaults(fn=cmd_track)

    d = sub.add_parser("tracker", help="סיכום המעקב: משפך 2/7/30 ימים ומשרות השבוע")
    d.add_argument("--days", type=int, default=7)
    d.add_argument("--state", choices=["to_review", "to_apply", "applied", "dismissed"])
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_tracker)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.discover(args.home)
    if args.cmd not in ("init",):
        from jobradar.pipeline import setup_logging
        setup_logging(cfg, args.verbose)
    try:
        return args.fn(cfg, args) or 0
    except KeyboardInterrupt:
        print("\nבוטל.")
        return 130
    except RuntimeError as e:
        print(f"שגיאה: {e}")
        return 1
