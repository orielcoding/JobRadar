"""Runs Claude locally through Claude Code's headless mode (`claude -p`),
billed to your Claude Pro/Max subscription - no API key involved.

Isolation: each call runs in an empty sandbox folder OUTSIDE the project (in
the system temp dir), with `--setting-sources ""` (no user / project / local
settings, and therefore no CLAUDE.md), built-in tools and MCP servers turned
off, a replaced system prompt and no saved session. So neither your personal
Claude Code setup nor the project's own CLAUDE.md (written for the agent that
helps you maintain JobRadar) leaks into evaluations, and the model cannot touch
files. Verified: with `--setting-sources project`, a CLAUDE.md in a parent
folder of the working directory DOES reach the model. ANTHROPIC_API_KEY is removed from the child environment on purpose, so
a stray key can never switch billing to the API.

Auth for unattended runs: put CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`)
in .env; otherwise the normal `claude` login on this machine is used.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from jobradar.llm import LLMError, LLMResult, UsageLimitReached
from jobradar.textutil import extract_json  # noqa: F401  (re-exported)

log = logging.getLogger(__name__)

_LIMIT_RE = re.compile(
    r"(usage limit|rate.?limit|limit reached|limit will reset|hit your limit|out of extra usage|"
    r"5-hour limit|weekly limit|too many requests|\b429\b)",
    re.I,
)
_TASK_PROMPT = ("Follow the system instructions. The input data is provided below. "
                "Return only the structured result.")


class ClaudeCLIBackend:
    def __init__(self, config):
        self.cfg = config
        self.bin = shutil.which(config.get("llm.claude_bin")) or config.get("llm.claude_bin")
        self.timeout = int(config.get("llm.timeout_sec", 300))
        self.max_turns = config.get("llm.max_turns")
        self.setting_sources = config.get("llm.setting_sources")
        self.isolation = list(config.get("llm.isolation_args") or [])
        self.extra = list(config.get("llm.extra_args") or [])
        self.sandbox = Path(config.get("llm.sandbox_dir") or Path(tempfile.gettempdir()) / "jobradar_sandbox")
        self.sandbox.mkdir(parents=True, exist_ok=True)

    def _env(self) -> dict:
        env = dict(os.environ)
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_SIMPLE"):
            env.pop(k, None)
        token = self.cfg.secret("CLAUDE_CODE_OAUTH_TOKEN")
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        return env

    def build_command(self, system_file: str, schema: dict, model: str, tools: list[str] | None = None,
                      deny: list[str] | None = None, max_turns: int | None = None) -> list[str]:
        """`tools` turns on only these built-in tools (e.g. WebSearch for the CV posting search);
        `deny` adds permission rules such as "WebFetch(domain:linkedin.com)". Default: no tools."""
        cmd = [self.bin, "-p", _TASK_PROMPT,
               "--output-format", "json",
               "--model", model,
               "--system-prompt-file", system_file,
               "--json-schema", json.dumps(schema, separators=(",", ":"))]
        isolation = list(self.isolation)
        if tools:
            if "--tools" in isolation:
                isolation[isolation.index("--tools") + 1] = ",".join(tools)
            else:
                isolation += ["--tools", ",".join(tools)]
            isolation += ["--allowedTools", *tools]
        if deny:
            if "--disallowedTools" in isolation:
                i = isolation.index("--disallowedTools") + 2
                isolation[i:i] = deny
            else:
                isolation += ["--disallowedTools", *deny]
        cmd += isolation
        if self.setting_sources is not None:  # "" is meaningful: load no settings at all
            cmd += ["--setting-sources", str(self.setting_sources)]
        turns = max_turns or self.max_turns
        if turns:
            cmd += ["--max-turns", str(turns)]
        return cmd + self.extra

    def complete_json(self, *, system: str, user: str, schema: dict, model: str,
                      purpose: str = "llm", context: dict | None = None, tools: list[str] | None = None,
                      deny: list[str] | None = None, max_turns: int | None = None,
                      timeout: int | None = None) -> LLMResult:
        sys_file = self.sandbox / f"system_{purpose}_{uuid.uuid4().hex[:8]}.md"
        sys_file.write_text(system + "\n\nAnswer with JSON that matches the provided schema.",
                            encoding="utf-8")
        cmd = self.build_command(str(sys_file), schema, model, tools, deny, max_turns)
        timeout = timeout or self.timeout
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                                  timeout=timeout, cwd=str(self.sandbox), env=self._env())
        except FileNotFoundError as e:
            raise LLMError(f"Claude Code not found ('{self.bin}'). Install it or set llm.claude_bin.") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude -p timed out after {timeout}s") from e
        finally:
            sys_file.unlink(missing_ok=True)

        payload = _parse_stdout(proc.stdout)
        if payload is None:
            msg = (proc.stderr or proc.stdout or "").strip()[-800:]
            if _LIMIT_RE.search(msg):
                raise UsageLimitReached(msg)
            raise LLMError(f"claude exited {proc.returncode} without JSON output: {msg}")
        if payload.get("is_error") or proc.returncode != 0:
            msg = str(payload.get("result") or payload.get("subtype") or proc.stderr)[-800:]
            if _LIMIT_RE.search(msg):
                raise UsageLimitReached(msg)
            raise LLMError(f"claude reported an error: {msg}")

        data = payload.get("structured_output")
        if not isinstance(data, dict):
            data = extract_json(str(payload.get("result") or ""))
        if not isinstance(data, dict):
            raise LLMError("no JSON object in Claude's answer: " + str(payload.get("result"))[:300])

        meta = {
            "duration_s": round(time.time() - t0, 1),
            "usage": payload.get("usage"),
            "cost_estimate_usd": payload.get("total_cost_usd"),
            "structured": "structured_output" in payload,
        }
        return LLMResult(data=data, model=model, meta=meta)


def _parse_stdout(stdout: str) -> dict | None:
    stdout = (stdout or "").strip()
    if not stdout:
        return None
    try:
        obj = json.loads(stdout)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed(stdout.splitlines()):  # tolerate log lines before the JSON
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "type" in obj:
                    return obj
            except json.JSONDecodeError:
                continue
    return None
