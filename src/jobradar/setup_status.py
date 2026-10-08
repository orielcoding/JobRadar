"""`jobradar setup-status`: where is the user in the setup, and what is next?

Derives every step from files and the database (nothing is stored separately),
so it is always truthful. Designed for both people and agents:
    jobradar setup-status          human checklist
    jobradar setup-status --json   machine-readable (used by CLAUDE.md / /onboard)

No model calls, no network - safe to run any time.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys

from jobradar.config import Config


def _filled(text: str, min_len: int) -> bool:
    return len(text) >= min_len and not re.search(r"\bTODO\b", text)


def _scheduler_installed() -> tuple[bool | None, str]:
    system = platform.system()
    try:
        if system == "Windows":
            r = subprocess.run(["schtasks", "/Query", "/TN", "JobRadar"], capture_output=True, text=True, timeout=20)
            return r.returncode == 0, "Windows Task Scheduler: JobRadar"
        if system == "Darwin":
            r = subprocess.run(["launchctl", "list", "com.jobradar.daily"], capture_output=True, text=True, timeout=20)
            return r.returncode == 0, "launchd: com.jobradar.daily"
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=20)
        return ("jobradar" in r.stdout), "crontab"
    except (OSError, subprocess.SubprocessError):
        return None, "could not check"


def collect(cfg: Config) -> dict:
    steps: list[dict] = []

    def step(sid, title, done, required, detail, next_action):
        steps.append({"id": sid, "title": title, "done": done, "required": required,
                      "detail": detail, "next": None if done else next_action})

    home = cfg.home
    claude = shutil.which(cfg.get("llm.claude_bin") or "claude")
    step("prereqs", "Python 3.10+ and Claude Code installed",
         sys.version_info >= (3, 10) and bool(claude), True,
         f"python {sys.version.split()[0]}; claude: {claude or 'not found'}",
         "Install Claude Code (docs/SETUP.md step 0), open a new terminal, run `claude` once and log in with the Pro account.")

    cfg_files = ["config/config.yaml", "config/companies.yaml", "config/filters.yaml", ".env"]
    missing = [f for f in cfg_files if not (home / f).exists()]
    step("init", "Config files created", not missing, True,
         "missing: " + ", ".join(missing) if missing else "ok", "Run `jobradar init`.")

    prof = cfg.read_text("paths.profile")
    step("profile", "Candidate profile written (profile/profile.md)", _filled(prof, 400), True,
         f"{len(prof)} chars" + (" (still has TODO)" if "TODO" in prof else ""),
         "Run the profile interview (prompts/profile_interview.md) with the user and write profile/profile.md.")

    cv = cfg.read_text("paths.cv")
    step("cv", "CV text saved (profile/cv.md)", _filled(cv, 300), True,
         f"{len(cv)} chars" + (" (still has TODO)" if "TODO" in cv else ""),
         "Build a master CV from the user's real CV versions (truthful lines only) and save it as profile/cv.md.")

    resolved = [c for c in cfg.companies if c.get("ats") and c.get("enabled", True) is not False]
    unresolved = [c.get("name") or c.get("careers_url") for c in cfg.companies
                  if not c.get("ats") and c.get("careers_url")]  # name-only favorites are fine
    example_only = {c.get("slug") for c in resolved} <= {"riskified", "lemonade"}
    step("companies", "Companies to watch (config/companies.yaml)", bool(resolved) and not example_only, True,
         f"{len(resolved)} resolved, {len(unresolved)} unresolved" + (" (only the examples)" if example_only else ""),
         "Ask the user which companies interest them (careers-page URLs), then `jobradar detect <urls> --add` and `jobradar test-sources`.")

    ex = home / "config" / "filters.example.yaml"
    real = home / "config" / "filters.yaml"
    customized = real.exists() and (not ex.exists() or real.read_text(encoding="utf-8") != ex.read_text(encoding="utf-8"))
    step("filters", "Hard filters reviewed (config/filters.yaml)", customized, False,
         "customized" if customized else "still identical to the template",
         "Ask about locations, work mode, seniority/domain words to exclude and max years of experience; edit "
         "config/filters.yaml, then check with `jobradar filter-test` (after the first fetch).")

    favs = [c.get("name") for c in cfg.companies if c.get("favorite")]
    step("favorites", "Favorite companies marked ⭐ (highlighted, not exclusive)", bool(favs), False,
         f"{len(favs)} favorites" + (f": {', '.join(favs[:5])}" if favs else ""),
         "Ask which companies they especially want; add them to config/companies.yaml with `favorite: true` "
         "(a name-only entry is fine when there is no career board).")

    feed = bool(cfg.get("techmap.enabled")) and bool(cfg.get("techmap.categories"))
    n_companies = len(resolved)
    step("coverage", "Broad coverage: role-based feed + Israeli companies", feed and n_companies >= 50, False,
         f"techmap feed {'on: ' + ', '.join(cfg.get('techmap.categories') or []) if feed else 'off'}; {n_companies} watched companies",
         "Pick role categories from the profile and enable `techmap` in config.yaml; run "
         "`jobradar import-techmap --sizes m,l,xl` (ask about industries); then `jobradar test-sources`.")

    channels = cfg.get("notify.channels") or []
    have_channel = (("ntfy" in channels and cfg.secret("NTFY_TOPIC"))
                    or ("telegram" in channels and cfg.secret("TELEGRAM_BOT_TOKEN"))
                    or ("email" in channels and cfg.secret("GMAIL_APP_PASSWORD")))
    step("notify", "Phone notifications configured", bool(have_channel), True,
         f"channels={channels}; NTFY_TOPIC {'set' if cfg.secret('NTFY_TOPIC') else 'missing'}",
         "User installs the ntfy app and subscribes to NTFY_TOPIC from .env; then `jobradar doctor --ping` and ask them to confirm the ping arrived.")

    step("token", "Long-lived Claude token in .env (recommended for scheduled runs)",
         bool(cfg.secret("CLAUDE_CODE_OAUTH_TOKEN")), False,
         "set" if cfg.secret("CLAUDE_CODE_OAUTH_TOKEN") else "not set",
         "User runs `claude setup-token` in their own terminal and pastes the token into .env as CLAUDE_CODE_OAUTH_TOKEN (they paste it, you never print it).")

    if cfg.get("gmail.enabled"):
        ok = bool(cfg.get("gmail.user") and cfg.secret("GMAIL_APP_PASSWORD"))
        step("gmail", "LinkedIn / email alerts via Gmail", ok, False,
             "enabled" + ("" if ok else " but user or GMAIL_APP_PASSWORD missing"),
             "Finish docs/SETUP.md step 7, then `jobradar test-sources --only gmail`.")
    else:
        step("gmail", "LinkedIn / email alerts via Gmail", False, False, "not enabled (optional)",
             "Optional: offer LinkedIn alerts via Gmail (docs/SETUP.md step 7).")

    db = cfg.path("paths.db")
    n_jobs = n_labels = n_deep = 0
    if db.exists():
        from jobradar.store import Store
        st = Store(db)
        n_jobs = st.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        n_labels = st.db.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
        n_deep = st.db.execute("SELECT COUNT(*) FROM evaluations WHERE stage='deep'").fetchone()[0]
        st.close()
    n_contacts = n_manual = 0
    if db.exists():
        from jobradar.store import Store
        st = Store(db)
        n_contacts = st.db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        n_manual = st.db.execute("SELECT COUNT(*) FROM contacts WHERE coalesce(strength,1) >= 2").fetchone()[0]
        st.close()
    step("network", "Networking: LinkedIn connections imported, close contacts marked", n_contacts > 0, False,
         f"{n_contacts} contacts, {n_manual} marked as known/close",
         "User exports Connections.csv from LinkedIn (docs/NETWORKING.md); run `jobradar net import-linkedin <file>`; "
         "then ask who among them they actually know and mark with `jobradar net update <id> --strength 2|3`.")
    step("fetch", "First fetch done", n_jobs > 0, True, f"{n_jobs} jobs in DB", "Run `jobradar fetch`.")
    step("calibrate", "Calibration ratings (aim for 20+)", n_labels >= 20, True, f"{n_labels} ratings",
         "Rate ~25 jobs with the user: `jobradar list --for calibrate --json`, discuss each, then "
         "`jobradar label <id> good|ok|bad --note \"why\"` (or the user runs `jobradar calibrate` in a terminal).")
    step("first_run", "First full run with the model", n_deep > 0, True, f"{n_deep} deep evaluations",
         "Run `jobradar run` (uses the user's Pro quota) and show them the report in reports/.")

    sched, where = _scheduler_installed()
    step("schedule", "Daily schedule installed", bool(sched), True, where,
         "Run `jobradar schedule <windows|macos|linux> --time 08:15` and have the user (or you, with permission) run the printed install command.")

    nxt = next((s for s in steps if s["required"] and not s["done"]), None)
    return {
        "home": str(home),
        "os": platform.system(),
        "complete": nxt is None,
        "next_step": nxt["id"] if nxt else None,
        "steps": steps,
    }


def render(status: dict) -> str:
    lines = [f"JobRadar setup – {status['home']} ({status['os']})", ""]
    for s in status["steps"]:
        mark = "✅" if s["done"] else ("⬜" if s["required"] else "➖")
        lines.append(f"{mark} {s['title']}  [{s['detail']}]")
    lines.append("")
    if status["complete"]:
        lines.append("ההקמה הושלמה. מה שנשאר: jobradar review פעם בשבוע.")
    else:
        nxt = next(s for s in status["steps"] if s["id"] == status["next_step"])
        lines.append(f"השלב הבא: {nxt['title']}\n  → {nxt['next']}")
    return "\n".join(lines)
