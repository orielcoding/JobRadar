#!/usr/bin/env python3
"""Stand-in for the `claude` binary, used by tests to exercise ClaudeCLIBackend.
Behaviour is chosen by env FAKE_CLAUDE_MODE: ok | limit | nostructured | error."""
import json
from pathlib import Path
import os
import sys

args = sys.argv[1:]
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")

if "--version" in args:
    print("9.9.9 (fake)")
    sys.exit(0)

required = ["-p", "--output-format", "--model", "--system-prompt-file", "--json-schema"]
missing = [r for r in required if r not in args]
if missing:
    print(f"fake claude: missing flags {missing}", file=sys.stderr)
    sys.exit(2)

if os.environ.get("ANTHROPIC_API_KEY"):
    print(json.dumps({"type": "result", "is_error": True, "result": "API key leaked into env"}))
    sys.exit(1)

schema = json.loads(args[args.index("--json-schema") + 1])
system = open(args[args.index("--system-prompt-file") + 1], encoding="utf-8").read()
user = sys.stdin.read()
props = schema.get("properties", {})

if mode == "limit":
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True,
                      "result": "Claude AI usage limit reached|1759999999"}))
    sys.exit(1)
if mode == "error":
    print("something exploded", file=sys.stderr)
    sys.exit(1)

if "results" in props:
    import re
    ids = re.findall(r'<job id="(\d+)">', user)
    data = {"results": [{"job_id": i, "verdict": "yes", "reason": "fake"} for i in ids]}
elif "scores" in props:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from jobradar.llm.fake import fake_deep_result
    data = fake_deep_result("fake", 8, desire=7, screen=4)
    data["job_analysis"]["must_haves"][0].update({"requirement": "Python", "evidence": "CV"})
else:
    data = {"ok": True}

out = {"type": "result", "subtype": "success", "is_error": False, "session_id": "fake",
       "total_cost_usd": 0.0, "usage": {"input_tokens": len(user) // 4},
       "system_seen": len(system) > 0}
if mode == "nostructured":
    out["result"] = "Here you go:\n```json\n" + json.dumps(data) + "\n```"
else:
    out["result"] = ""
    out["structured_output"] = data
print(json.dumps(out))
