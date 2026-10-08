"""Recording raw hook payloads, so real ones can become test fixtures (issue #35).

``python -m ter hook --record DIR`` writes every payload it reads, before
translating it, to ``DIR/<session_id>/<seq>-<hook_event_name>.json``::

    {"schema": "ter.hook-recording/1", "received_at": "...", "payload": {...}}

A payload that is not JSON is kept as text under ``"raw"`` instead, because
the point of a recording is to see what Claude Code really sent. Recording is
observe-only and fails open like the rest of the hook: a write that fails is
reported, never raised.

Sequence numbers are claimed by creating the file exclusively, so hooks that
run concurrently in one session (parallel tool calls) never overwrite each
other.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["RECORDING_SCHEMA", "Recording", "read_recordings", "record_payload"]

RECORDING_SCHEMA = "ter.hook-recording/1"

#: Names safe to use as one path component; anything else is hashed.
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
_UNKNOWN = "_unknown"
#: More recordings than this in one session directory means something is wrong.
_MAX_SEQUENCE = 10**6


@dataclass(frozen=True)
class Recording:
    """One recorded hook payload, read back."""

    path: Path
    sequence: int
    hook_event_name: str
    received_at: datetime | None
    payload: object
    raw: str | None = None


def record_payload(
    raw: str, directory: Path, *, received_at: datetime | None = None
) -> Path:
    """Write one raw hook payload under ``directory`` and return its path.

    Raises:
        OSError: If the recording cannot be written. Callers in the hook path
            catch it, so the agent carries on.
    """
    payload: object
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    fields = payload if isinstance(payload, Mapping) else {}
    session = _component(fields.get("session_id"))
    hook = _component(fields.get("hook_event_name"))
    record: dict[str, Any] = {
        "schema": RECORDING_SCHEMA,
        "received_at": received_at.isoformat() if received_at else None,
    }
    if payload is None:
        record["raw"] = raw
    else:
        record["payload"] = payload
    body = json.dumps(record, ensure_ascii=False, indent=2) + "\n"

    # Recordings hold tool inputs and output: private to their owner, like
    # the event log.
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = directory / session
    folder.mkdir(mode=0o700, exist_ok=True)
    sequence = sum(1 for _ in folder.iterdir())
    while sequence < _MAX_SEQUENCE:
        path = folder / f"{sequence:06d}-{hook}.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            sequence += 1
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        return path
    raise OSError(f"too many recordings in {folder}")


def read_recordings(directory: Path) -> Iterator[Recording]:
    """Yield every recording under ``directory``, by session then sequence."""
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        for path in sorted(folder.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, Mapping) or data.get("schema") != RECORDING_SCHEMA:
                raise ValueError(f"{path} is not a {RECORDING_SCHEMA} file")
            sequence, _, hook = path.stem.partition("-")
            received = data.get("received_at")
            yield Recording(
                path=path,
                sequence=int(sequence),
                hook_event_name=hook,
                received_at=datetime.fromisoformat(received) if received else None,
                payload=data.get("payload"),
                raw=data.get("raw"),
            )


def _component(value: object) -> str:
    if not isinstance(value, str) or not value:
        return _UNKNOWN
    if _SAFE_NAME.fullmatch(value):
        return value
    return "h-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
