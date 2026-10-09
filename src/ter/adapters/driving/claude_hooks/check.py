"""Checking recorded hook payloads against transcripts (issue #35, TER-OBS-012).

``python -m ter hooks check RECORDINGS TRANSCRIPTS`` replays what
``ter hook --record`` saved through the same path the live hook takes
(:func:`.entry.derive`), reads each session's transcript through the session
source, and reports how the two event streams correlate:

* for every hook-derived event, whether the transcript-derived stream has an
  event with the same id (TER-OBS-007), and if not, why: a different id rule,
  or a hook that fell back to its own key (no ``tool_use_id``; a prompt whose
  record was not written yet; a stop with no turn);
* for every Stop payload, whether its ``task.completed`` id equals the id the
  session source derives for the same stop (TER-OBS-005);
* for every SubagentStop, whether the session source derived the same
  ``subagent.completed`` from the subagent's transcript
  (``<session>/subagents/agent-<agent_id>.jsonl``), and if not, whether the
  payload had no ``agent_id``, the file is missing or it shows no finish.

The report is content-free: counts, event ids (hashes), hook names, payload
field names and fixed reason strings. It never holds a prompt, a tool input
or a tool response, so it can be shared when the recordings cannot.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ....domain.events import Event, EventKind, SessionTrace
from ...claude_code_ids import subagent_event_id
from ...claude_code_subagents import subagent_transcripts
from ...claude_code_turns import (
    PromptRecord,
    last_turn,
    prompt_record,
    read_records,
    stop_records,
)
from .entry import derive
from .record import Recording, read_recordings
from .translate import HookStatus

__all__ = [
    "HookCheck",
    "KindCorrelation",
    "Reason",
    "SessionCheck",
    "StopCheck",
    "check_recordings",
    "find_transcripts",
    "format_hook_check",
]

#: Reads one transcript into events: the session source's ``read``.
ReadTranscript = Callable[[Path], SessionTrace]

#: Event kinds the hook adapter derives.
HOOK_KINDS = (
    EventKind.PROMPT,
    EventKind.TOOL_REQUESTED,
    EventKind.TOOL_COMPLETED,
    EventKind.TASK_COMPLETED,
    EventKind.SUBAGENT_COMPLETED,
)
#: Unmatched ids listed per kind in the text report (JSON lists them all).
TEXT_IDS = 10


class Reason:
    """Why a hook event has no transcript event with its id. Fixed strings."""

    MATCHED = "matched"
    NO_TRANSCRIPT = "no transcript found for the session"
    UNREADABLE = "transcript could not be read"
    KIND_NOT_DERIVED = "the session source derives no events of this kind"
    SAME_TOOL_CALL = "same tool_use_id in the transcript, different id rule"
    SAME_PROMPT = "same prompt text in the transcript, different id rule"
    TOOL_UNKEYED = "hook tool call has no tool_use_id: keyed by its input"
    PROMPT_WRITTEN_LATER = (
        "hook prompt keyed by its text: its transcript record was not there when"
        " the hook ran (written later, or no transcript_path)"
    )
    PROMPT_UNKEYED = "hook prompt keyed by its text: no transcript record holds it"
    NO_COUNTERPART = "no transcript event for the same record"
    STOP_UNKEYED = (
        "hook stop keyed by receive time: no transcript turn was found at the stop"
    )
    STOP_NOT_RECORDED = "the transcript records no stop for the turn the hook saw"
    SUBAGENT_UNKEYED = "hook subagent stop has no agent_id: keyed by receive time"
    SUBAGENT_NO_TRANSCRIPT = (
        "no subagent transcript for the hook's agent_id under <session>/subagents"
    )
    SUBAGENT_NOT_FINISHED = (
        "the subagent's transcript holds no finish marker (still running when"
        " copied, or a finish the session source does not recognise)"
    )


@dataclass(frozen=True)
class KindCorrelation:
    """Hook events of one kind against the transcript stream."""

    kind: str
    matched: tuple[str, ...]
    unmatched: tuple[str, ...]
    reasons: tuple[tuple[str, int], ...]
    #: Transcript events of this kind with no hook event of the same id.
    source_only: int

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.unmatched)


@dataclass(frozen=True)
class StopCheck:
    """One Stop payload against the stop the transcript records."""

    #: The Stop payload's place among the session's Stop payloads, from 1.
    ordinal: int
    hook_id: str | None
    #: The session source's id for the same stop: the one with the hook's id
    #: when it exists, else the first stop the transcript records after the
    #: payload arrived.
    source_id: str | None
    reason: str

    @property
    def matched(self) -> bool:
        return self.reason == Reason.MATCHED


@dataclass(frozen=True)
class SessionCheck:
    """The check of one recorded session."""

    session_id: str
    payloads: tuple[tuple[str, int], ...]
    fields: tuple[tuple[str, tuple[str, ...]], ...]
    ignored: tuple[tuple[str, int], ...]
    #: How the transcript was found: ``transcript_path``, ``transcripts-dir``,
    #: or ``missing``.
    transcript: str
    transcript_error: str
    hook_events: tuple[tuple[str, int], ...]
    source_events: tuple[tuple[str, int], ...]
    correlation: tuple[KindCorrelation, ...]
    stops: tuple[StopCheck, ...]
    notes: tuple[str, ...] = ()

    @property
    def matched(self) -> int:
        return sum(len(c.matched) for c in self.correlation)

    @property
    def total(self) -> int:
        return sum(c.total for c in self.correlation)

    @property
    def share(self) -> float | None:
        return self.matched / self.total if self.total else None


@dataclass(frozen=True)
class HookCheck:
    """The check of every recorded session."""

    sessions: tuple[SessionCheck, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> int:
        return sum(s.matched for s in self.sessions)

    @property
    def total(self) -> int:
        return sum(s.total for s in self.sessions)

    @property
    def share(self) -> float | None:
        return self.matched / self.total if self.total else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ter.hook-check/1",
            "sessions": len(self.sessions),
            "hook_events": self.total,
            "matched": self.matched,
            "share": self.share,
            "stops": {
                "total": sum(len(s.stops) for s in self.sessions),
                "matched": sum(
                    1 for s in self.sessions for stop in s.stops if stop.matched
                ),
            },
            "by_session": [_session_dict(s) for s in self.sessions],
        }


def find_transcripts(directory: Path) -> dict[str, Path]:
    """``.jsonl`` files under ``directory`` (or the file itself) by stem.

    The shallowest file wins a stem, so a session's own transcript beats a
    subagent file of the same name.
    """
    if directory.is_file():
        return {directory.stem: directory}
    found: dict[str, Path] = {}
    paths = sorted(directory.rglob("*.jsonl"), key=lambda p: (len(p.parts), p))
    for path in paths:
        found.setdefault(path.stem, path)
    return found


def check_recordings(
    recordings: Path, transcripts: Path, read: ReadTranscript
) -> HookCheck:
    """Check every session recorded under ``recordings``.

    Raises:
        OSError, ValueError: When the recordings cannot be read.
    """
    by_folder: dict[str, list[Recording]] = {}
    for recording in read_recordings(recordings):
        by_folder.setdefault(recording.path.parent.name, []).append(recording)
    index = find_transcripts(transcripts)
    return HookCheck(
        tuple(
            _check_session(folder, items, index, read)
            for folder, items in sorted(by_folder.items())
        )
    )


@dataclass
class _Transcript:
    how: str
    error: str = ""
    trace: SessionTrace | None = None
    records: list[Mapping[str, Any]] = field(default_factory=list)
    stop_turns: set[str] = field(default_factory=set)
    #: Agent ids with a transcript under ``<session>/subagents``.
    agents: set[str] = field(default_factory=set)


def _check_session(
    folder: str,
    recordings: Sequence[Recording],
    index: Mapping[str, Path],
    read: ReadTranscript,
) -> SessionCheck:
    payloads = [r.payload for r in recordings]
    session_id = next(
        (
            p["session_id"]
            for p in payloads
            if isinstance(p, Mapping)
            and isinstance(p.get("session_id"), str)
            and p["session_id"]
        ),
        folder,
    )
    transcript = _load(session_id, payloads, index, read)

    def turns(_path: str, before: datetime | None) -> str | None:
        return last_turn(transcript.records, before) if transcript.records else None

    def prompts(
        _path: str, prompt: str, before: datetime | None
    ) -> PromptRecord | None:
        # The records written by the time the payload arrived, as the live
        # hook would have read them.
        return prompt_record(transcript.records, prompt, before)

    hook_counts: Counter[str] = Counter()
    fields: dict[str, set[str]] = {}
    ignored: Counter[str] = Counter()
    hook_events: dict[str, Event] = {}
    stop_payloads: list[tuple[int, Event | None, datetime | None]] = []
    for recording, payload in zip(recordings, payloads, strict=True):
        name = recording.hook_event_name
        hook_counts[name] += 1
        if isinstance(payload, Mapping):
            fields.setdefault(name, set()).update(str(k) for k in payload)
        if payload is None:
            ignored["invalid JSON"] += 1
            continue
        translation = derive(payload, recording.received_at, turns, prompts)
        if translation.status is HookStatus.IGNORED:
            ignored[translation.reason or "ignored"] += 1
        for event in translation.events:
            hook_events.setdefault(event.id, event)
        if translation.hook_event_name == "Stop":
            stop = next(iter(translation.events), None)
            ordinal = len(stop_payloads) + 1
            stop_payloads.append((ordinal, stop, recording.received_at))

    source = transcript.trace.events if transcript.trace is not None else ()
    correlation = tuple(
        _correlate(kind, hook_events.values(), source, transcript)
        for kind in HOOK_KINDS
        if any(e.kind is kind for e in hook_events.values())
    )
    notes = []
    if transcript.trace is not None:
        for kind in (EventKind.TASK_COMPLETED, EventKind.SUBAGENT_COMPLETED):
            if not any(e.kind is kind for e in source):
                if kind is EventKind.TASK_COMPLETED:
                    why = "it has no stop_hook_summary records"
                elif transcript.agents:
                    why = "no subagent transcript holds a finish marker"
                else:
                    why = "it has no <session>/subagents folder beside it"
                notes.append(
                    f"the session source derived no {kind.value} events from this "
                    f"transcript ({why})"
                )
    return SessionCheck(
        session_id=session_id,
        payloads=tuple(sorted(hook_counts.items())),
        fields=tuple((k, tuple(sorted(v))) for k, v in sorted(fields.items())),
        ignored=tuple(sorted(ignored.items())),
        transcript=transcript.how,
        transcript_error=transcript.error,
        hook_events=_counts(hook_events.values()),
        source_events=_counts(source),
        correlation=correlation,
        stops=tuple(_stops(stop_payloads, source, transcript)),
        notes=tuple(notes),
    )


def _load(
    session_id: str,
    payloads: Iterable[object],
    index: Mapping[str, Path],
    read: ReadTranscript,
) -> _Transcript:
    path: Path | None = None
    how = "missing"
    for payload in payloads:
        named = payload.get("transcript_path") if isinstance(payload, Mapping) else None
        if isinstance(named, str) and named and Path(named).is_file():
            path, how = Path(named), "transcript_path"
            break
    if path is None and session_id in index:
        path, how = index[session_id], "transcripts-dir"
    if path is None:
        return _Transcript(how, Reason.NO_TRANSCRIPT)
    try:
        trace = read(path)
        with open(path, encoding="utf-8") as handle:
            records = list(read_records(handle))
    except Exception as error:  # noqa: BLE001 - reported, never raised
        # The type only: a parser's message can quote the record.
        return _Transcript(how, f"{Reason.UNREADABLE}: {type(error).__name__}")
    stop_turns = {turn for _, _, turn in stop_records(enumerate(records, 1))}
    return _Transcript(
        how,
        trace=trace,
        records=records,
        stop_turns=stop_turns,
        agents=set(subagent_transcripts(path)),
    )


def _correlate(
    kind: EventKind,
    hook_events: Iterable[Event],
    source: Sequence[Event],
    transcript: _Transcript,
) -> KindCorrelation:
    mine = [e for e in hook_events if e.kind is kind]
    theirs = [e for e in source if e.kind is kind]
    ids = {e.id for e in theirs}
    calls = {e.tool.call_id for e in theirs if e.tool is not None and e.tool.call_id}
    prompts = {_digest(e.text) for e in theirs}
    matched: list[str] = []
    unmatched: list[str] = []
    reasons: Counter[str] = Counter()
    for event in mine:
        if event.id in ids:
            matched.append(event.id)
            continue
        unmatched.append(event.id)
        reasons[_reason(event, transcript, theirs, calls, prompts)] += 1
    hook_ids = {e.id for e in mine}
    return KindCorrelation(
        kind=kind.value,
        matched=tuple(matched),
        unmatched=tuple(unmatched),
        reasons=tuple(sorted(reasons.items())),
        source_only=sum(1 for e in theirs if e.id not in hook_ids),
    )


def _reason(
    event: Event,
    transcript: _Transcript,
    theirs: Sequence[Event],
    calls: set[str],
    prompts: set[str],
) -> str:
    if transcript.trace is None:
        return transcript.error or Reason.NO_TRANSCRIPT
    if event.kind is EventKind.TASK_COMPLETED:
        turn = _hook_turn(event)
        if turn is None:
            return Reason.STOP_UNKEYED
        if turn not in transcript.stop_turns:
            return Reason.STOP_NOT_RECORDED
    if event.kind is EventKind.SUBAGENT_COMPLETED:
        agent = _hook_agent(event)
        if agent is None:
            return Reason.SUBAGENT_UNKEYED
        if agent not in transcript.agents:
            return Reason.SUBAGENT_NO_TRANSCRIPT
        return Reason.SUBAGENT_NOT_FINISHED
    if not theirs:
        return Reason.KIND_NOT_DERIVED
    if event.tool is not None and event.tool.call_id in calls:
        return Reason.SAME_TOOL_CALL
    if event.tool is not None and (event.tool.call_id or "").startswith("hook:"):
        return Reason.TOOL_UNKEYED
    if event.kind is EventKind.PROMPT:
        same_text = event.provenance.fingerprint in prompts
        if event.provenance.record_id.startswith("prompt:"):
            # The fallback key: no record held the prompt when the hook ran.
            return Reason.PROMPT_WRITTEN_LATER if same_text else Reason.PROMPT_UNKEYED
        if same_text:
            return Reason.SAME_PROMPT
    return Reason.NO_COUNTERPART


def _stops(
    payloads: Sequence[tuple[int, Event | None, datetime | None]],
    source: Sequence[Event],
    transcript: _Transcript,
) -> list[StopCheck]:
    recorded = [e for e in source if e.kind is EventKind.TASK_COMPLETED]
    ids = {e.id for e in recorded}
    checks = []
    for ordinal, event, received in payloads:
        if event is None:
            checks.append(StopCheck(ordinal, None, None, "payload not translated"))
            continue
        if event.id in ids:
            checks.append(StopCheck(ordinal, event.id, event.id, Reason.MATCHED))
            continue
        after = next(
            (
                e.id
                for e in recorded
                if received is not None
                and e.timestamp is not None
                and e.timestamp >= received
            ),
            None,
        )
        reason = _reason(event, transcript, recorded, set(), set())
        checks.append(StopCheck(ordinal, event.id, after, reason))
    return checks


def _hook_turn(event: Event) -> str | None:
    prefix = "stop:turn:"
    record = event.provenance.record_id
    return record[len(prefix) :] if record.startswith(prefix) else None


def _hook_agent(event: Event) -> str | None:
    """The agent id a SubagentStop event is keyed by, or ``None`` when the
    payload named no agent (the receive-time fallback)."""
    prefix = "subagent:"
    record = event.provenance.record_id
    if not record.startswith(prefix) or record.startswith(prefix + "received"):
        return None
    agent = record[len(prefix) :]
    return agent if event.id == subagent_event_id(event.session_id, agent) else None


def _counts(events: Iterable[Event]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(e.kind.value for e in events).items()))


def _digest(text: str) -> str:
    # The hook adapter's prompt fingerprint (translate._digest).
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _session_dict(check: SessionCheck) -> dict[str, Any]:
    return {
        "session_id": check.session_id,
        "transcript": check.transcript,
        "transcript_error": check.transcript_error or None,
        "payloads": dict(check.payloads),
        "payload_fields": {name: list(keys) for name, keys in check.fields},
        "ignored": dict(check.ignored),
        "hook_events": dict(check.hook_events),
        "source_events": dict(check.source_events),
        "matched": check.matched,
        "total": check.total,
        "share": check.share,
        "by_kind": {
            c.kind: {
                "matched": len(c.matched),
                "unmatched": len(c.unmatched),
                "source_only": c.source_only,
                "reasons": dict(c.reasons),
                "matched_ids": list(c.matched),
                "unmatched_ids": list(c.unmatched),
            }
            for c in check.correlation
        },
        "stops": [
            {
                "ordinal": s.ordinal,
                "hook_id": s.hook_id,
                "source_id": s.source_id,
                "matched": s.matched,
                "reason": s.reason,
            }
            for s in check.stops
        ],
        "notes": list(check.notes),
    }


def _pairs(pairs: Iterable[tuple[str, int]]) -> str:
    return " · ".join(f"{k} {n:,}" for k, n in pairs) or "-"


def _share(matched: int, total: int) -> str:
    if not total:
        return "0/0"
    return f"{matched:,}/{total:,} ({matched / total:.1%})"


def format_hook_check(check: HookCheck) -> str:
    """The check as plain text: counts, ids and reasons, never payload content."""
    stops = [s for session in check.sessions for s in session.stops]
    lines = [
        f"TER hooks check · {len(check.sessions)} session(s)",
        f"  hook events matching a transcript event id  "
        f"{_share(check.matched, check.total)}   (TER-OBS-007)",
        f"  Stop payloads matching the transcript stop  "
        f"{_share(sum(s.matched for s in stops), len(stops))}   (TER-OBS-005)",
    ]
    for session in check.sessions:
        lines += [
            "",
            f"session {session.session_id}",
            f"  transcript     {session.transcript}"
            + (f" ({session.transcript_error})" if session.transcript_error else ""),
            f"  payloads       {_pairs(session.payloads)}",
            f"  ignored        {_pairs(session.ignored)}",
            f"  hook events    {_pairs(session.hook_events)}",
            f"  source events  {_pairs(session.source_events)}",
            f"  id matches     {_share(session.matched, session.total)}",
        ]
        for c in session.correlation:
            lines.append(
                f"    {c.kind:<19} {len(c.matched):,}/{c.total:,} matched"
                f" · {c.source_only:,} transcript-only"
            )
            for reason, n in c.reasons:
                lines.append(f"      {n:,} × {reason}")
            if c.unmatched:
                shown = ", ".join(c.unmatched[:TEXT_IDS])
                more = len(c.unmatched) - TEXT_IDS
                lines.append(
                    f"      unmatched ids: {shown}"
                    + (f" (+{more} more)" if more > 0 else "")
                )
        if session.stops:
            lines.append("  stops")
            for s in session.stops:
                lines.append(
                    f"    #{s.ordinal}  hook {s.hook_id or '-'}  "
                    f"source {s.source_id or '-'}  {s.reason}"
                )
        if session.fields:
            lines.append("  payload fields")
            for name, keys in session.fields:
                lines.append(f"    {name:<17} {', '.join(keys)}")
        for note in session.notes:
            lines.append(f"  note: {note}")
    return "\n".join(lines) + "\n"
