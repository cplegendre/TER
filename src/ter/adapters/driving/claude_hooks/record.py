"""Recording raw hook payloads, so real ones can become test fixtures (issue #35).

``python -m ter hook --record DIR`` writes every payload it reads, before
translating it, to ``DIR/<session_id>/<seq>-<hook_event_name>.json``::

    {"schema": "ter.hook-recording/1", "received_at": "...", "raw": "..."}

``raw`` is the hook input exactly as it arrived, valid JSON or not, because
the point of a recording is to see what Claude Code really sent. Recording is
observe-only and fails open like the rest of the hook: a write that fails is
reported, never raised.

``<seq>`` is the write time in nanoseconds, 20 digits wide, so file names sort
in arrival order without listing the directory. A file is written in full
under a temporary name and then linked into place exclusively, so hooks that
run concurrently in one session never overwrite each other and a reader never
sees a half-written recording.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["RECORDING_SCHEMA", "Recording", "read_recordings", "record_payload"]

RECORDING_SCHEMA = "ter.hook-recording/1"

#: Names safe to use as one path component; anything else is hashed.
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")
_UNKNOWN = "_unknown"
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_POSIX = os.name == "posix"
#: Nanosecond slots tried past the write time before giving up.
_ATTEMPTS = 1000


@dataclass(frozen=True)
class Recording:
    """One recorded hook payload, read back."""

    path: Path
    sequence: int
    hook_event_name: str
    received_at: datetime | None
    raw: str

    @property
    def payload(self) -> object:
        """The decoded payload, or ``None`` when the input was not JSON."""
        try:
            return json.loads(self.raw)
        except ValueError:
            return None


def record_payload(
    raw: str,
    directory: Path,
    *,
    received_at: datetime | None = None,
    now_ns: Callable[[], int] = time.time_ns,
) -> Path:
    """Write one raw hook payload under ``directory`` and return its path.

    Raises:
        OSError: If the recording cannot be written. Callers in the hook path
            catch it, so the agent carries on.
    """
    try:
        payload: object = json.loads(raw)
    except ValueError:
        payload = None
    fields: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    session = _component(fields.get("session_id"))
    hook = _component(fields.get("hook_event_name"))
    record = {
        "schema": RECORDING_SCHEMA,
        "received_at": received_at.isoformat() if received_at else None,
        "raw": raw,
    }
    body = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    # Recordings hold tool inputs and output: private to their owner, like
    # the event log.
    _private_directory(directory, parents=True)
    folder = directory / session
    _private_directory(folder)
    temporary = folder / f".partial-{os.getpid()}-{secrets.token_hex(6)}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        start = now_ns()
        for offset in range(_ATTEMPTS):
            path = folder / f"{start + offset:020d}-{hook}.json"
            try:
                os.link(temporary, path)
            except FileExistsError:
                continue
            return path
        raise OSError(f"no free recording name in {folder}")
    finally:
        temporary.unlink(missing_ok=True)


def read_recordings(directory: Path) -> Iterator[Recording]:
    """Yield every recording under ``directory``, by session then arrival.

    Raises:
        ValueError: For a ``.json`` file that is not a recording.
    """
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        for path in sorted(folder.glob("[0-9]*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, Mapping)
                or data.get("schema") != RECORDING_SCHEMA
                or not isinstance(data.get("raw"), str)
            ):
                raise ValueError(f"{path} is not a {RECORDING_SCHEMA} file")
            sequence, _, hook = path.stem.partition("-")
            received = data.get("received_at")
            yield Recording(
                path=path,
                sequence=int(sequence),
                hook_event_name=hook,
                received_at=datetime.fromisoformat(received) if received else None,
                raw=data["raw"],
            )


def _private_directory(path: Path, *, parents: bool = False) -> None:
    path.mkdir(mode=_DIR_MODE, parents=parents, exist_ok=True)
    if _POSIX:
        info = path.stat()
        if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077:
            path.chmod(_DIR_MODE)


def _component(value: object) -> str:
    if not isinstance(value, str) or not value:
        return _UNKNOWN
    if _SAFE_NAME.fullmatch(value):
        return value
    return "h-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
