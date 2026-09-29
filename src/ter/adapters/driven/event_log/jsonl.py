"""An :class:`~ter.ports.driven.EventLog` kept as one JSONL file per session.

Each append writes one line in append mode, so concurrent hook processes of
the same session interleave whole lines on local file systems. A line that
cannot be decoded (for example one cut short by a crash) is skipped and
counted in :attr:`JsonlEventLog.skipped`, never raised: analysis of a live log
must not fail because one write was interrupted.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ....domain.events import Event
from .codec import event_from_record, event_to_record

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SUFFIX = ".events.jsonl"


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
        self.directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event_to_record(event), ensure_ascii=False, sort_keys=True)
        with open(self.path_for(event.session_id), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

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
