"""Prompt templates live in prompts/*.md so you can edit them without code.

First line of each template may carry a version tag, stored with every
evaluation so you can compare prompt versions with `jobradar eval`:
    <!-- prompt_version: deep-v1 -->
Placeholders: {{output_language}}
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"<!--\s*prompt_version:\s*([\w.\-]+)\s*-->")


def load_prompt(config, filename: str) -> tuple[str, str]:
    path = config.prompt_path(filename)
    if not path.exists():  # a personal prompt kept out of git ships as <name>.example.md
        example = path.with_name(path.stem + ".example" + path.suffix)
        if not example.exists():
            raise FileNotFoundError(f"prompt template missing: {path}")
        path = example
    text = path.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text)
    version = m.group(1) if m else filename
    text = _VERSION_RE.sub("", text, count=1).strip()
    text = text.replace("{{output_language}}", str(config.get("output_language", "English")))
    return text, version
