"""LLM backend contract.

Any backend implements:
    complete_json(system, user, schema, model, purpose, context) -> LLMResult

`system` / `user` are full prompt texts; `schema` is a JSON Schema the answer
must follow; `context` carries the structured inputs (used only by the fake
backend for tests - real backends ignore it).

Backends:
  claude_cli  runs `claude -p` locally on your Claude Pro subscription (default)
  fake        deterministic keyword heuristic, for tests and dry runs

To add one (e.g. a local Ollama model for triage), implement the same method
and register it in get_backend().
"""

from __future__ import annotations

from dataclasses import dataclass, field


class LLMError(Exception):
    pass


class UsageLimitReached(LLMError):
    """Subscription usage window exhausted - stop LLM stages for this run."""


@dataclass
class LLMResult:
    data: dict
    model: str
    meta: dict = field(default_factory=dict)


def get_backend(config, name: str | None = None):
    name = name or config.get("llm.backend")
    if name == "claude_cli":
        from jobradar.llm.claude_cli import ClaudeCLIBackend
        return ClaudeCLIBackend(config)
    if name == "fake":
        from jobradar.llm.fake import FakeBackend
        return FakeBackend(config)
    raise ValueError(f"unknown llm.backend '{name}' (use claude_cli or fake)")
