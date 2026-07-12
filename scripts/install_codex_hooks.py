"""Install or update Onevom hooks without removing other hooks."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


EVENTS = ("UserPromptSubmit", "PostToolUse", "PreCompact", "Stop")
MARKER = "codex-lan-memory/hooks/capture.py"


def quoted(path: Path) -> str:
    return f'"{path}"'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--project", default=None, help="Override project name; by default the working-directory name is used")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    capture = repo / "hooks" / "capture.py"
    home = Path(args.codex_home).expanduser().resolve()
    hooks_path = home / "hooks.json"
    home.mkdir(parents=True, exist_ok=True)
    try:
        document = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = {"hooks": {}}
    hooks = document.setdefault("hooks", {})
    command = f"{quoted(Path(args.python).resolve())} {quoted(capture)}"
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        for group in groups:
            group["hooks"] = [item for item in group.get("hooks", []) if MARKER not in str(item.get("command", "")).replace("\\", "/")]
        groups[:] = [group for group in groups if group.get("hooks")]
        groups.append({"hooks": [{
            "type": "command", "command": command, "timeout": 10,
            "statusMessage": f"onevom: capture {event}",
        }]})
    hooks_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    client_dir = home / "codex-memory"
    client_dir.mkdir(parents=True, exist_ok=True)
    client_config = {"server": args.server.rstrip("/")}
    if args.project:
        client_config["project"] = args.project
    (client_dir / "client.json").write_text(json.dumps(client_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Installed real-time capture hooks in {hooks_path}")
    print(f"Memory server: {client_config['server']}")
    print("Open /hooks in Codex and trust the new hook definitions, then start a new task.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
