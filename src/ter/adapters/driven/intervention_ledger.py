"""The intervention ledger as JSONL files, behind the
:class:`~ter.ports.driven.InterventionLedger` port (TER-INT-009).

One ``<directory>/<session>.ledger.jsonl`` file per session, append-only.
Each line is a ``ter.intervention-ledger/1`` object: a ``record`` (one engine
decision) or an ``observation`` (the outcome of a delivered intervention,
naming its record). Reading folds the lines: the first record for an id
stays, and an observation attaches to its record.

Recording appends one line without reading the file, so a hook's cost does
not grow with the session; a repeated record id is folded away on read.
Observing reads the session first, because an outcome may only be attached
once, to a delivered record. A line that cannot be decoded (one cut short by
a crash) is skipped and counted in :attr:`JsonlInterventionLedger.skipped`,
and an append after it starts on a fresh line.

Records cite event ids and rule names, not session content, but they still
describe how someone works, so the directory is created private to its
owner (0700) and each file 0600 on POSIX systems.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from ...domain.ledger import LEDGER_SCHEMA, LedgerError, LedgerRecord, Observation

__all__ = ["JsonlInterventionLedger"]

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SUFFIX = ".ledger.jsonl"
_POSIX = os.name == "posix"
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class JsonlInterventionLedger:
    """Keeps one append-only ledger file per session under ``directory``."""

    name = "jsonl"

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser()
        self.skipped = 0

    def path_for(self, session_id: str) -> Path:
        """The file holding one session; unsafe ids are hashed into a name."""
        if _SAFE_NAME.fullmatch(session_id) and ".." not in session_id:
            stem = session_id
        else:
            digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
            stem = f"sid-{digest}"
        return self.directory / f"{stem}{_SUFFIX}"

    def record(self, entry: LedgerRecord) -> None:
        if entry.observation is not None:
            raise LedgerError(
                f"record {entry.id}: record the decision, then observe its outcome"
            )
        line = {"schema": LEDGER_SCHEMA, "type": "record", **entry.as_dict()}
        self._append(entry.session_id, line)

    def observe(
        self, session_id: str, record_id: str, observation: Observation
    ) -> None:
        current = {e.id: e for e in self.entries(session_id)}
        if record_id not in current:
            raise LedgerError(f"no ledger record {record_id} in session {session_id}")
        current[record_id].observed(observation)  # raises if it cannot take one
        line = {
            "schema": LEDGER_SCHEMA,
            "type": "observation",
            "session_id": session_id,
            "record": record_id,
            **observation.as_dict(),
        }
        self._append(session_id, line)

    def entries(self, session_id: str) -> tuple[LedgerRecord, ...]:
        path = self.path_for(session_id)
        if not path.exists():
            return ()
        records: dict[str, LedgerRecord] = {}
        for line in self._read(path):
            if line.get("session_id") != session_id:
                continue
            try:
                if line.get("type") == "record":
                    entry = LedgerRecord.from_mapping(line)
                    records.setdefault(entry.id, entry)
                elif line.get("type") == "observation":
                    rid = str(line["record"])
                    if rid in records and records[rid].observation is None:
                        records[rid] = records[rid].observed(
                            Observation.from_mapping(line)
                        )
                    else:
                        self.skipped += 1
                else:
                    self.skipped += 1
            except (LedgerError, KeyError):
                self.skipped += 1
        return tuple(records.values())

    def sessions(self) -> tuple[str, ...]:
        if not self.directory.is_dir():
            return ()
        found: set[str] = set()
        for path in self.directory.glob(f"*{_SUFFIX}"):
            for line in self._read(path)[:1]:
                if isinstance(line.get("session_id"), str):
                    found.add(line["session_id"])
        return tuple(sorted(found))

    def _read(self, path: Path) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                except ValueError:
                    self.skipped += 1
                    continue
                if isinstance(line, dict) and line.get("schema") == LEDGER_SCHEMA:
                    lines.append(line)
                else:
                    self.skipped += 1
        return lines

    def _append(self, session_id: str, line: dict[str, Any]) -> None:
        self._make_private_directory()
        text = json.dumps(line, ensure_ascii=False, sort_keys=True)
        data = (text + "\n").encode("utf-8")
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(self.path_for(session_id), flags, _FILE_MODE)
        try:
            _restrict(fd, _FILE_MODE)
            size = os.fstat(fd).st_size
            if size and _last_byte(fd, size) != b"\n":
                # The last line was cut short: start this one on its own line.
                data = b"\n" + data
            _write_all(fd, data)
        finally:
            os.close(fd)

    def _make_private_directory(self) -> None:
        self.directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        if _POSIX:
            info = self.directory.stat()
            if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077:
                self.directory.chmod(_DIR_MODE)


def _restrict(fd: int, mode: int) -> None:
    """Tighten an owned file that others can read."""
    if not _POSIX:
        return
    info = os.fstat(fd)
    if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077:
        os.fchmod(fd, mode)


def _write_all(fd: int, data: bytes) -> None:
    # os.write may write less than asked (a disk filling up mid-write); the
    # rest follows, or the next call raises and the cut line is skipped.
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if not written:
            raise OSError(errno.ENOSPC, "no bytes written")
        view = view[written:]


def _last_byte(fd: int, size: int) -> bytes:
    os.lseek(fd, size - 1, os.SEEK_SET)
    return os.read(fd, 1)
