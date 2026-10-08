"""Configuration loading.

Layout of a JobRadar home folder (the folder you run commands from, or
--home / $JOBRADAR_HOME):

    config/config.yaml      system settings (models, limits, thresholds, notify)
    config/companies.yaml   which company career boards to watch
    config/filters.yaml     hard filters applied in code before any LLM call
    profile/profile.md      your candidate profile (the most important input)
    profile/cv.md           your CV as plain text / markdown
    prompts/*.md            the LLM instructions (versioned)
    .env                    secrets (tokens, passwords) - never commit
    data/                   SQLite DB, logs (created automatically)
    reports/                daily markdown reports

Every key has a default below, so config files only need what you change.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "output_language": "Hebrew",
    "paths": {
        "profile": "profile/profile.md",
        "cv": "profile/cv.md",
        "tailored": "profile/tailored",   # CV PDFs; their working files go to <tailored>/work/
        "prompts": "prompts",
        "db": "data/jobradar.db",
        "reports": "reports",
        "logs": "data/logs",
    },
    "fetch": {
        "workers": 4,
        "timeout_sec": 30,
        "user_agent": "JobRadar/0.1 (personal job search tool)",
    },
    "llm": {
        "backend": "claude_cli",  # claude_cli | fake
        "claude_bin": "claude",
        "triage_model": "haiku",
        "deep_model": "sonnet",
        "timeout_sec": 300,
        "max_turns": 4,
        # Passed as --setting-sources. "" = load no settings and no CLAUDE.md,
        # so neither your personal Claude Code setup nor this project's CLAUDE.md
        # leaks into evaluations. null = omit the flag entirely.
        "setting_sources": "",
        "sandbox_dir": "",  # "" = <system temp>/jobradar_sandbox (outside the project on purpose)
        "isolation_args": [
            "--tools", "",
            "--strict-mcp-config",
            "--disallowedTools", "mcp__*",
            "--no-session-persistence",
        ],
        "extra_args": [],
        "fake_keywords": ["engineer", "data", "python", "product"],
    },
    "enrich": {
        "max_per_run": 80,        # public ATS links (Comeet / Lever / Greenhouse) fetched per run for a description
    },
    "triage": {
        "enabled": True,          # False = every filtered job goes straight to deep eval
        "batch_size": 25,
        "max_calls_per_run": 8,
        "excerpt_chars": 700,
        "min_description_chars": 200,  # below this a job can only be a "light match"
    },
    "deep": {
        "max_per_run": 12,
        "max_description_chars": 12000,
    },
    "thresholds": {
        "min_capability": 7,
        "min_desire": 6,
        "notify_verdicts": ["strong", "good"],
        "cv_gap_flag": 3,  # capability - screen_pass >= this -> "tailor your CV" flag
    },
    "examples": {
        "triage_max": 20,
        "deep_max_good": 4,
        "deep_max_ok": 2,
        "deep_max_bad": 4,
        "excerpt_chars": 500,
    },
    "notify": {
        "channels": ["ntfy"],  # ntfy | telegram | email | console
        "ntfy_server": "https://ntfy.sh",
        "max_pings_per_run": 8,
        "light_matches_digest": True,
        "light_digest_max_items": 10,
        "light_digest_verdicts": ["yes"],  # triage verdicts that make it into the digest ping (favorites always do)
        "max_age_hours": None,  # no ping for jobs older than this at ping time (aged like filters.max_age_days)
    },
    "favorites": {
        "skip_triage": True,
        "min_capability": 6,
        "min_desire": 5,
        "notify_verdicts": ["strong", "good", "stretch"],
        "rank_bonus": 4,
    },
    "techmap": {"enabled": False, "categories": []},
    "tracker": {
        "port": 8765,           # local window: http://127.0.0.1:8765 (localhost only)
        "idle_minutes": 5,      # the local server stops this long after the window closes
        "window_width": 1280,
        "window_height": 880,
    },
    "cv": {                     # tailored CVs (`jobradar cv`, the dashboard's CV button) - spends Pro quota
        "model": "sonnet",
        "timeout_sec": 900,
        "search_posting": True,  # no posting text -> one web-search call for it (never LinkedIn)
        "search_model": "sonnet",
        "search_timeout_sec": 300,
        "contact_line": "",      # your own contact line; filled in locally, never sent to the model
    },
    "gmail": {
        "enabled": False,
        "user": "",
        "label": "jobradar",
        "lookback_days": 3,
        "imap_host": "imap.gmail.com",
    },
    "email_sources": [
        {"name": "linkedin_email", "from_contains": "linkedin.com", "parser": "linkedin"},
    ],
    "pipeline": {
        "stages": ["ingest", "dedupe", "hard_filter", "enrich", "triage", "deep_eval", "notify", "report"],
    },
}

FILTER_DEFAULTS: dict[str, Any] = {
    "locations": {"allow_any_of": [], "allow_unknown": True},
    "workplace": {"allow": []},  # e.g. ["remote", "hybrid"]; empty = any
    "title": {"include_any": [], "exclude_any": []},
    "department": {"exclude_any": []},
    "description": {"exclude_any": []},
    "companies_block": [],
    "max_age_days": 45,
    "late_sources": ["techmap"],  # aggregators that list jobs days late: age counts from first seen
}


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def load_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE, # comments, optional quotes)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env


class Config:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.env = load_env_file(self.home / ".env")
        self.data = deep_merge(DEFAULTS, load_yaml(self.home / "config" / "config.yaml"))
        self.filters = deep_merge(FILTER_DEFAULTS, load_yaml(self.home / "config" / "filters.yaml"))
        self.companies: list[dict] = load_yaml(self.home / "config" / "companies.yaml").get("companies") or []

    @classmethod
    def discover(cls, home: str | None = None) -> "Config":
        root = Path(home or os.environ.get("JOBRADAR_HOME") or Path.cwd())
        return cls(root)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def path(self, dotted: str) -> Path:
        p = Path(self.get(dotted))
        return p if p.is_absolute() else self.home / p

    def secret(self, name: str) -> str | None:
        """Environment wins over .env so a scheduler can inject values."""
        return os.environ.get(name) or self.env.get(name) or None

    def read_text(self, dotted: str) -> str:
        """Read a text input (profile, CV). HTML comments (<!-- -->) are
        instructions for you, not for the model, so they are stripped."""
        p = self.path(dotted)
        if not p.exists():
            return ""
        return re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8"), flags=re.S).strip()

    def prompt_path(self, name: str) -> Path:
        return self.path("paths.prompts") / name
