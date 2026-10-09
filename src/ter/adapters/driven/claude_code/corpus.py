"""Importing real Claude Code sessions into a redacted research corpus (issue #34).

``python -m ter corpus import SRC --out DIR`` finds every ``*.jsonl`` under
``SRC`` (Claude Code keeps them in ``~/.claude/projects/<project>/``, with
subagent transcripts in subfolders), redacts each one
(:mod:`.redaction`) and writes::

    DIR/sessions/<project>/<file>.jsonl    redacted session
    DIR/reports/<project>/<file>.json      what redaction removed, by location
    DIR/manifest.json                      one entry per session
    DIR/.salt                              private; keeps pseudonyms stable

``<project>`` is a pseudonym of the project folder and every file or folder
name that is not a session or agent id is hashed, so project paths and names
never reach the output. Importing into an existing corpus adds to it; a
different redaction policy needs a new corpus. Nothing is written for a session before it is redacted
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
import re
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
#: Names Claude Code gives sessions and subagents: a UUID or ``agent-<hex>``.
_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|agent-[0-9a-f]{6,64}"
)
#: Folder names Claude Code itself uses below a project.
_STRUCTURAL = frozenset({"subagents"})


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
    #: Records of documented types that carry no agent activity (TER-SRC-005).
    metadata_by_type: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CorpusImport:
    """What one import wrote."""

    out: Path
    #: The sessions this import wrote (or failed to read).
    sessions: tuple[CorpusSession, ...]
    unlabelled: tuple[str, ...]
    unknown_labels: tuple[str, ...]
    #: Sessions in the manifest after the import, earlier imports included.
    total: int = 0

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
    """Redact every session under ``sources`` into ``out`` and update the manifest.

    Importing into an existing corpus adds to it: entries for files imported
    again are replaced and the others are kept. The salt in ``out/.salt`` is
    created on first use and reused, so pseudonyms stay stable. A ``policy``
    salt, if set, wins.

    Raises:
        ValueError: Before anything is written, when two sources would write
            the same corpus file, or ``out`` holds a corpus built with a
            different redaction policy or manifest schema.
    """
    base = policy or RedactionPolicy()
    settings = {
        "max_tool_output": base.max_tool_output,
        "keep_tools": sorted(base.keep_tools),
        "quote_file_contents": base.quote_file_contents,
    }
    previous = _previous_sessions(out, settings)
    plan = _plan(sources)

    out.mkdir(parents=True, exist_ok=True)
    salt = base.salt or _salt(out)
    policy = RedactionPolicy(
        max_tool_output=base.max_tool_output,
        keep_tools=base.keep_tools,
        quote_file_contents=base.quote_file_contents,
        salt=salt,
    )
    targets: dict[Path, Path] = {}
    for path, project_dir, rest in plan:
        relative = _target(project_dir, rest, salt)
        if relative in targets:
            raise ValueError(
                f"{targets[relative]} and {path} would both be written to "
                f"{relative.as_posix()}; import them into separate corpora"
            )
        targets[relative] = path

    labels = labels or {}
    sessions = sorted(
        (
            _import_one(path, relative, out, policy, labels)
            for relative, path in targets.items()
        ),
        key=lambda s: s.file,
    )
    seen = {s.session_id for s in sessions}
    written = {s.file for s in sessions}
    entries = [e for e in previous if e.get("file") not in written]
    entries += [asdict(s) for s in sessions]
    entries.sort(key=lambda e: str(e.get("file")))
    _write_json(
        out / "manifest.json",
        {"schema": CORPUS_SCHEMA, "policy": settings, "sessions": entries},
    )
    return CorpusImport(
        out,
        tuple(sessions),
        tuple(sorted(seen - set(labels))),
        tuple(sorted(set(labels) - seen)),
        total=len(entries),
    )


def _previous_sessions(
    out: Path, settings: Mapping[str, object]
) -> list[dict[str, Any]]:
    path = out / "manifest.json"
    if not path.exists():
        return []
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != CORPUS_SCHEMA:
        raise ValueError(f"{path} is not a {CORPUS_SCHEMA} manifest")
    if manifest.get("policy") != settings:
        # Mixing policies would leave, say, quoted file contents in a corpus
        # whose manifest says they were dropped.
        raise ValueError(
            f"{out} was built with redaction policy {manifest.get('policy')}; "
            "import into a new --out to use a different one"
        )
    return [e for e in manifest.get("sessions", []) if isinstance(e, dict)]


def _plan(sources: Sequence[Path]) -> list[tuple[Path, str, Path]]:
    """Each session file with its project folder name and path below it."""
    plan: list[tuple[Path, str, Path]] = []
    for source in sources:
        if source.is_file():
            plan.append((source, source.resolve().parent.name, Path(source.name)))
            continue
        files = sorted(p for p in source.rglob("*.jsonl") if p.is_file())
        # A project folder holds session files directly; a folder of projects
        # (``~/.claude/projects``) holds them one level down.
        is_project = any(p.parent == source for p in files)
        name = source.resolve().name
        for path in files:
            relative = path.relative_to(source)
            if is_project:
                plan.append((path, name, relative))
            elif len(relative.parts) > 1:
                plan.append((path, relative.parts[0], Path(*relative.parts[1:])))
            else:
                plan.append((path, name, relative))
    return plan


def _target(project_dir: str, rest: Path, salt: str) -> Path:
    project = "p-" + _sha(salt + "\0" + project_dir)
    *folders, name = rest.parts
    parts = [_safe_folder(part, salt) for part in folders]
    parts.append(_safe_file(name, salt))
    return Path("sessions", project, *parts)


def _import_one(
    path: Path,
    relative: Path,
    out: Path,
    policy: RedactionPolicy,
    labels: Mapping[str, Mapping[str, str]],
) -> CorpusSession:
    target = out / relative
    report = out / "reports" / relative.relative_to("sessions").with_suffix(".json")
    file = relative.as_posix()
    project = relative.parts[1]
    try:
        records, unparsed = _read_records(path)
    except OSError as exc:
        # Recorded, not raised: one unreadable file must not stop the import.
        return CorpusSession(
            session_id=path.stem if _ID.fullmatch(path.stem) else file,
            file=file,
            project=project,
            records=0,
            unparsed_lines=0,
            first_timestamp=None,
            last_timestamp=None,
            coverage=None,
            unrecognised_by_type={},
            redactions={},
            load_error=f"{type(exc).__name__}: {exc.strerror or 'unreadable'}",
        )
    redactor = Redactor(policy)
    redacted = redactor.redact_session(records)

    # Only redacted records are ever written (TER-SRC-006).
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in redacted:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    fallback = path.stem if _ID.fullmatch(path.stem) else file
    raw_id = _session_id(records) or fallback
    session_id = _session_id(redacted) or fallback
    coverage: float | None = None
    unrecognised: Mapping[str, int] = {}
    metadata: Mapping[str, int] = {}
    error: str | None = None
    try:
        trace = ClaudeCodeJsonlSource().read(target)
    except Exception as exc:  # noqa: BLE001 - a failed load is a corpus finding
        message = str(exc).replace(str(target), file).replace(str(out), "<corpus>")
        error = f"{type(exc).__name__}: {redactor.scrub(message)}"
    else:
        coverage = round(trace.coverage, 6)
        unrecognised = dict(sorted(trace.unrecognised_by_type.items()))
        metadata = dict(sorted(trace.metadata_by_type.items()))
    stamps = sorted(
        str(r["timestamp"]) for r in records if isinstance(r.get("timestamp"), str)
    )
    # Free-text labels get the same scrubbing as the session they describe.
    session_labels = {
        key: value if key in ("outcome", "rating") else redactor.scrub(value)
        for key, value in labels.get(raw_id, {}).items()
    }
    _write_json(
        report,
        {
            "schema": REPORT_SCHEMA,
            "file": file,
            "counts": redactor.counts(),
            "redactions": [asdict(r) for r in redactor.redactions],
        },
    )
    return CorpusSession(
        session_id=session_id,
        file=file,
        project=project,
        records=len(records),
        unparsed_lines=unparsed,
        first_timestamp=stamps[0] if stamps else None,
        last_timestamp=stamps[-1] if stamps else None,
        coverage=coverage,
        unrecognised_by_type=unrecognised,
        redactions=redactor.counts(),
        load_error=error,
        labels=session_labels,
        metadata_by_type=metadata,
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


def _safe_folder(part: str, salt: str) -> str:
    if part in _STRUCTURAL or _ID.fullmatch(part):
        return part
    return "h-" + _sha(salt + "\0" + part)


def _safe_file(name: str, salt: str) -> str:
    # Only session and agent ids survive; any other name is hashed whole.
    if name.endswith(".jsonl") and _ID.fullmatch(name[: -len(".jsonl")]):
        return name
    return "h-" + _sha(salt + "\0" + name) + ".jsonl"


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
