"""Importing real Claude Code sessions into a redacted research corpus (issue #34).

``python -m ter corpus import SRC --out DIR`` finds every ``*.jsonl`` under
``SRC`` (Claude Code keeps them in ``~/.claude/projects/<project>/``, with
subagent transcripts in subfolders), redacts each one
(:mod:`.redaction`) and writes::

    DIR/sessions/<project>/<file>.jsonl    redacted session
    DIR/reports/<project>/<file>.json      what redaction removed, by location
    DIR/manifest.json                      one entry per session
    DIR/.salt                              private; keeps pseudonyms stable

``<project>`` is a pseudonym of the source folder, so project paths never
reach the output. Nothing is written for a session before it is redacted
(TER-SRC-006), and the raw files are only read. The manifest entry carries the
session's record count, dates, coverage by the Claude Code session source and
any labels given in a CSV file (``session_id,task_category,task,outcome,
rating,licence``).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .session_source import ClaudeCodeJsonlSource
from .redaction import RedactionPolicy, Redactor

__all__ = [
    "CORPUS_SCHEMA",
    "OUTCOMES",
    "CorpusImport",
    "CorpusSession",
    "import_corpus",
    "read_labels",
]

CORPUS_SCHEMA = "ter.corpus-manifest/1"
REPORT_SCHEMA = "ter.redaction-report/1"
#: Allowed values of the ``outcome`` label.
OUTCOMES = ("merged", "abandoned", "partial", "unknown")
LABEL_FIELDS = ("task_category", "task", "outcome", "rating", "licence")


@dataclass(frozen=True)
class CorpusSession:
    """One manifest entry."""

    session_id: str
    file: str
    project: str
    records: int
    unparsed_lines: int
    first_timestamp: str | None
    last_timestamp: str | None
    coverage: float | None
    unrecognised_by_type: Mapping[str, int]
    redactions: Mapping[str, int]
    load_error: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CorpusImport:
    """What one import wrote."""

    out: Path
    sessions: tuple[CorpusSession, ...]
    unlabelled: tuple[str, ...]
    unknown_labels: tuple[str, ...]

    @property
    def load_failures(self) -> tuple[CorpusSession, ...]:
        return tuple(s for s in self.sessions if s.load_error is not None)


def read_labels(path: Path) -> dict[str, dict[str, str]]:
    """Read a label CSV keyed by ``session_id``.

    Raises:
        ValueError: On a missing ``session_id`` column, a duplicate session,
            an outcome outside :data:`OUTCOMES` or a rating outside 1 to 5.
    """
    labels: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "session_id" not in reader.fieldnames:
            raise ValueError(f"{path}: no session_id column")
        for row_number, row in enumerate(reader, 2):
            session = (row.get("session_id") or "").strip()
            if not session:
                raise ValueError(f"{path}:{row_number}: empty session_id")
            if session in labels:
                raise ValueError(f"{path}:{row_number}: {session} labelled twice")
            entry = {
                key: (row.get(key) or "").strip()
                for key in LABEL_FIELDS
                if (row.get(key) or "").strip()
            }
            outcome = entry.get("outcome")
            if outcome is not None and outcome not in OUTCOMES:
                raise ValueError(
                    f"{path}:{row_number}: outcome {outcome!r} is not one of "
                    + ", ".join(OUTCOMES)
                )
            rating = entry.get("rating")
            if rating is not None and rating not in {"1", "2", "3", "4", "5"}:
                raise ValueError(f"{path}:{row_number}: rating {rating!r} is not 1-5")
            labels[session] = entry
    return labels


def import_corpus(
    sources: Sequence[Path],
    out: Path,
    *,
    policy: RedactionPolicy | None = None,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> CorpusImport:
    """Redact every session under ``sources`` into ``out`` and write the manifest.

    The salt in ``out/.salt`` is created on first use and reused, so a
    re-import gives the same pseudonyms. A ``policy`` salt, if set, wins.
    """
    out.mkdir(parents=True, exist_ok=True)
    salt = (policy.salt if policy and policy.salt else None) or _salt(out)
    base = policy or RedactionPolicy()
    policy = RedactionPolicy(
        max_tool_output=base.max_tool_output,
        keep_tools=base.keep_tools,
        quote_file_contents=base.quote_file_contents,
        salt=salt,
    )
    labels = labels or {}
    sessions: list[CorpusSession] = []
    for source in sources:
        for path in _session_files(source):
            sessions.append(_import_one(path, source, out, policy, labels))
    sessions.sort(key=lambda s: s.file)
    seen = {s.session_id for s in sessions}
    unlabelled = tuple(sorted(seen - set(labels)))
    unknown = tuple(sorted(set(labels) - seen))
    manifest = {
        "schema": CORPUS_SCHEMA,
        "policy": {
            "max_tool_output": policy.max_tool_output,
            "keep_tools": sorted(policy.keep_tools),
            "quote_file_contents": policy.quote_file_contents,
        },
        "sessions": [asdict(s) for s in sessions],
    }
    _write_json(out / "manifest.json", manifest)
    return CorpusImport(out, tuple(sessions), unlabelled, unknown)


def _session_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    return sorted(p for p in source.rglob("*.jsonl") if p.is_file())


def _import_one(
    path: Path,
    source: Path,
    out: Path,
    policy: RedactionPolicy,
    labels: Mapping[str, Mapping[str, str]],
) -> CorpusSession:
    records, unparsed = _read_records(path)
    redactor = Redactor(policy)
    redacted = redactor.redact_session(records)

    root = source if source.is_dir() else source.parent
    relative = path.relative_to(root)
    project_dir = relative.parts[0] if len(relative.parts) > 1 else root.name
    project = "p-" + _sha(policy.salt + "\0" + project_dir)
    rest = Path(*relative.parts[1:]) if len(relative.parts) > 1 else relative
    # Folder and file names below the project are session and agent ids;
    # anything that is not id-shaped is hashed too.
    rest = Path(*(_safe_part(part, policy.salt) for part in rest.parts))
    target = out / "sessions" / project / rest
    report = out / "reports" / project / rest.with_suffix(".json")

    # Only redacted records are ever written (TER-SRC-006).
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in redacted:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    _write_json(
        report,
        {
            "schema": REPORT_SCHEMA,
            "file": target.relative_to(out).as_posix(),
            "counts": redactor.counts(),
            "redactions": [asdict(r) for r in redactor.redactions],
        },
    )

    session_id = _session_id(records) or path.stem
    coverage: float | None = None
    unrecognised: Mapping[str, int] = {}
    error: str | None = None
    try:
        trace = ClaudeCodeJsonlSource().read(target)
    except Exception as exc:  # noqa: BLE001 - a failed load is a corpus finding
        error = f"{type(exc).__name__}: {exc}"
    else:
        coverage = round(trace.coverage, 6)
        unrecognised = dict(sorted(trace.unrecognised_by_type.items()))
    stamps = sorted(
        str(r["timestamp"]) for r in records if isinstance(r.get("timestamp"), str)
    )
    return CorpusSession(
        session_id=session_id,
        file=target.relative_to(out).as_posix(),
        project=project,
        records=len(records),
        unparsed_lines=unparsed,
        first_timestamp=stamps[0] if stamps else None,
        last_timestamp=stamps[-1] if stamps else None,
        coverage=coverage,
        unrecognised_by_type=unrecognised,
        redactions=redactor.counts(),
        load_error=error,
        labels=dict(labels.get(session_id, {})),
    )


def _read_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    unparsed = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                # Not copied: an unparsable line cannot be redacted safely.
                unparsed += 1
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                unparsed += 1
    return records, unparsed


def _session_id(records: Sequence[Mapping[str, Any]]) -> str | None:
    for record in records:
        value = record.get("sessionId")
        if isinstance(value, str) and value:
            return value
    return None


def _safe_part(part: str, salt: str) -> str:
    stem, dot, suffix = part.partition(".")
    if stem and all(c.isalnum() or c in "-_" for c in stem) and len(stem) <= 80:
        if any(c.isdigit() for c in stem) and not stem.startswith("-"):
            return part
    return "h-" + _sha(salt + "\0" + stem) + (dot + suffix if dot else "")


def _salt(out: Path) -> str:
    path = out / ".salt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_hex(16)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
