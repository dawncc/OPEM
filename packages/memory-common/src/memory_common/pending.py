import json
import os
from pathlib import Path
from typing import Any


def pending_path() -> Path:
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    path = home / "codex-memory" / "pending.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_pending(payload: dict[str, Any]) -> None:
    with pending_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def drain_pending(send) -> int:
    path = pending_path()
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    remaining: list[str] = []
    sent = 0
    for index, line in enumerate(lines):
        try:
            send(json.loads(line))
            sent += 1
        except Exception:
            remaining.extend(lines[index:])
            break
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
    temporary.replace(path)
    return sent
