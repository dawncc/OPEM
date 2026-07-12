"""Synchronize completed Codex rollout turns to OPEM."""
from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from urllib.request import Request, urlopen

from memory_common.codex_history import discover_rollouts, parse_rollout


def post(server: str, payload: dict) -> dict:
    request = Request(
        f"{server.rstrip('/')}/api/v1/chat/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.environ.get("MEMORY_SERVER_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--project", default=None, help="Use one project name instead of deriving it from each session cwd")
    parser.add_argument("--since", default=None, help="Only scan rollout files on or after YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    totals = {"files": 0, "sessions": 0, "completed_turns": 0, "messages": 0, "accepted": 0, "duplicates": 0, "errors": 0}
    for path in discover_rollouts(Path(args.codex_home), args.since):
        totals["files"] += 1
        try:
            session = parse_rollout(path, args.project)
            if not session:
                continue
            totals["sessions"] += 1
            totals["completed_turns"] += session.completed_turns
            totals["messages"] += len(session.messages)
            if args.dry_run:
                continue
            chunks = [session.messages[index:index + 1000] for index in range(0, len(session.messages), 1000)]
            for index, messages in enumerate(chunks):
                final_chunk = index == len(chunks) - 1
                result = post(args.server, {
                    "project": session.project, "session_id": session.session_id,
                    "source_host": socket.gethostname(), "messages": messages,
                    "session_status": ("active" if session.has_active_turn else "completed") if final_chunk else None,
                    "session_summary": session.summary if final_chunk else None,
                    "deduplicate_by_content": True,
                })
                totals["accepted"] += result.get("accepted", 0)
                totals["duplicates"] += result.get("duplicates", 0)
        except Exception as exc:
            totals["errors"] += 1
            print(f"ERROR {path}: {exc}")
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(run())
