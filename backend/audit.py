"""Append-only audit trail at output/audit_trail.json.

Every agent loop step, tool call and orchestration decision is appended as one
entry to a JSON array. Entries are appended in place (earlier entries are never
rewritten) and the file is never erased: if it can't be parsed it is set aside
under a new name and a fresh trail is started next to it.
"""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

AUDIT_PATH = Path(__file__).resolve().parent.parent / "output" / "audit_trail.json"
MAX_TEXT = 1500


def _clip(value: Any) -> Any:
    """Keep entries readable: shorten long strings, recursively."""
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "...[truncated]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v) for v in value]
    return value


class Audit:
    def __init__(self, path: Path = AUDIT_PATH):
        self.path = path
        self.run_id = uuid.uuid4().hex[:8]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.load()  # sets aside an unreadable file before we append to it

    def load(self) -> list:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path.rename(self.path.with_name(f"audit_trail.unreadable-{stamp}.json"))
        return []

    def log(self, event: str, agent: str = "runner", ticket_id: int | None = None, **detail: Any) -> None:
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "ticket_id": ticket_id,
            "agent": agent,
            "event": event,
            **_clip(json.loads(json.dumps(detail, default=str))),
        }
        text = json.dumps(entry, indent=2)
        # OneDrive can hold a brief lock on the file while it syncs; wait it out.
        for attempt in range(50):
            try:
                self._append(text)
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.1)

    def _append(self, text: str) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            self.path.write_text(f"[\n{text}\n]\n", encoding="utf-8")
            return
        with open(self.path, "r+b") as f:
            f.seek(0, 2)
            end = f.tell()
            start = max(0, end - 64)
            f.seek(start)
            close = start + f.read().rfind(b"]")
            f.seek(0)
            empty = f.read(close).strip() == b"["
            f.seek(close)
            f.truncate()
            f.write(("\n" if empty else ",\n").encode() + text.encode("utf-8") + b"\n]\n")
