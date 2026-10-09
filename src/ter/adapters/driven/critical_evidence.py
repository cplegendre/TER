"""Reads critical evidence lists: the evidence a session's task cannot do
without, written by a person (TER-EVD-005).

Two formats, chosen by the file suffix.

JSON (``.json``)::

    {
      "schema": "ter.critical-evidence/1",
      "sessions": {
        "<session id>": [
          "src/shop/pricing.py",
          {"path": "src/shop/tax.py", "symbol": "vat_rate"}
        ]
      }
    }

CSV (any other suffix), with a header row::

    session_id,path,symbol
    <session id>,src/shop/pricing.py,
    <session id>,src/shop/tax.py,vat_rate

Paths are repository paths, relative to the repository root with ``/``
separators (a leading ``./`` and backslashes are normalised). ``symbol`` is
optional: a name the file defines that the task depends on. Duplicate items
are kept once; items come back sorted.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from ...domain.context_metrics import CriticalEvidence, CriticalItem

__all__ = ["CRITICAL_SCHEMA", "CriticalEvidenceError", "read_critical_evidence"]

CRITICAL_SCHEMA = "ter.critical-evidence/1"


class CriticalEvidenceError(ValueError):
    """The file is not a critical evidence list."""


def _path(raw: str) -> str:
    path = raw.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/"):
        raise CriticalEvidenceError(f"not a repository path: {raw!r}")
    return path


def _item(path: object, symbol: object) -> CriticalItem:
    if not isinstance(path, str):
        raise CriticalEvidenceError(f"path must be a string, got {path!r}")
    if symbol is not None and not isinstance(symbol, str):
        raise CriticalEvidenceError(f"symbol must be a string, got {symbol!r}")
    return CriticalItem(_path(path), (symbol or "").strip() or None)


def _from_json(text: str) -> dict[str, list[CriticalItem]]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CriticalEvidenceError(f"invalid JSON: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != CRITICAL_SCHEMA:
        raise CriticalEvidenceError(f"expected an object with schema {CRITICAL_SCHEMA}")
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        raise CriticalEvidenceError("'sessions' must map session ids to lists")
    out: dict[str, list[CriticalItem]] = {}
    for session, items in sessions.items():
        if not isinstance(items, list):
            raise CriticalEvidenceError(f"session {session!r}: expected a list")
        found = out.setdefault(str(session), [])
        for entry in items:
            if isinstance(entry, str):
                found.append(_item(entry, None))
            elif isinstance(entry, dict):
                found.append(_item(entry.get("path"), entry.get("symbol")))
            else:
                raise CriticalEvidenceError(f"session {session!r}: bad item {entry!r}")
    return out


def _from_csv(text: str) -> dict[str, list[CriticalItem]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or not {"session_id", "path"} <= set(
        reader.fieldnames
    ):
        raise CriticalEvidenceError("CSV needs a header with session_id and path")
    out: dict[str, list[CriticalItem]] = {}
    for row in reader:
        session = (row.get("session_id") or "").strip()
        if not session:
            raise CriticalEvidenceError(f"row {reader.line_num}: no session_id")
        out.setdefault(session, []).append(
            _item(row.get("path") or "", row.get("symbol"))
        )
    return out


def read_critical_evidence(
    path: str | Path, session_id: str
) -> CriticalEvidence | None:
    """The list for ``session_id`` in the file at ``path`` (``None`` when the
    file lists nothing for that session).

    Raises:
        CriticalEvidenceError: the file is not a critical evidence list.
        OSError: the file cannot be read.
    """
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    parsed = _from_json(text) if file.suffix.lower() == ".json" else _from_csv(text)
    items = parsed.get(session_id)
    if items is None:
        return None
    return CriticalEvidence(session_id, tuple(sorted(set(items))), file.name)
