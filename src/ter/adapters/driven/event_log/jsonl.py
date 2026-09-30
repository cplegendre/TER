"""An :class:`~ter.ports.driven.EventLog` kept as one JSONL file per session.

Each append writes one line in append mode, so concurrent hook processes of
the same session interleave whole lines on local file systems. A line that
cannot be decoded (for example one cut short by a crash) is skipped and
counted in :attr:`JsonlEventLog.skipped`, never raised: analysis of a live log
must not fail because one write was interrupted. An append after such a
line starts on a fresh line, so the interrupted record never swallows the
next one.

Records hold prompts and tool input and output in plain text, so the
directory is created private to its owner (0700) and each file 0600 on
POSIX systems; an existing directory or file that is readable by others is
tightened when this user owns it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from ....domain.events import Event
from .codec import event_from_record, event_to_record

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SUFFIX = ".events.jsonl"
_POSIX = os.name == "posix"
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class JsonlEventLog:
    """Stores one ``<directory>/<session>.events.jsonl`` file per session."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.skipped = 0

    def path_for(self, session_id: str) -> Path:
        """The file holding one session; unsafe ids are hashed into a name."""
        if _SAFE_NAME.fullmatch(session_id) and ".." not in session_id:
            stem = session_id
        else:
            digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
            stem = f"sid-{digest}"
        return self.directory / f"{stem}{_SUFFIX}"

    def append(self, event: Event) -> None:
        self._make_private_directory()
        line = json.dumps(event_to_record(event), ensure_ascii=False, sort_keys=True)
        data = (line + "\n").encode("utf-8")
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(self.path_for(event.session_id), flags, _FILE_MODE)
        try:
            _restrict(fd, _FILE_MODE)
            size = os.fstat(fd).st_size
            if size and _last_byte(fd, size) != b"\n":
                # The last record was cut short: start this one on its own line.
                data = b"\n" + data
            os.write(fd, data)
        finally:
            os.close(fd)

    def _make_private_directory(self) -> None:
        self.directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        if _POSIX:
            info = self.directory.stat()
            if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077:
                self.directory.chmod(_DIR_MODE)

    def events(self, session_id: str) -> tuple[Event, ...]:
        path = self.path_for(session_id)
        if not path.exists():
            return ()
        return tuple(e for e in self._read(path) if e.session_id == session_id)

    def sessions(self) -> tuple[str, ...]:
        if not self.directory.is_dir():
            return ()
        found: set[str] = set()
        for path in self.directory.glob(f"*{_SUFFIX}"):
            found.update(e.session_id for e in self._read(path)[:1])
        return tuple(sorted(found))

    def _read(self, path: Path) -> list[Event]:
        events: list[Event] = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    events.append(event_from_record(json.loads(line)))
                except (ValueError, KeyError, TypeError, AttributeError):
                    self.skipped += 1
        return events


def _restrict(fd: int, mode: int) -> None:
    """Tighten an owned file that others can read (umask or an older version)."""
    if not _POSIX:
        return
    info = os.fstat(fd)
    if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077:
        os.fchmod(fd, mode)


def _last_byte(fd: int, size: int) -> bytes:
    os.lseek(fd, size - 1, os.SEEK_SET)
    return os.read(fd, 1)
