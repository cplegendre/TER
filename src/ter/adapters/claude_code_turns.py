"""Which Claude Code turn a stop closes: one id rule for hooks and transcripts.

A Stop hook payload carries no id for the stop, and the transcript records a
stop only after the stop hooks ran (a ``system`` record with subtype
``stop_hook_summary``). What both sides can see is the turn the stop closes:
the last main-chain ``assistant`` record written before it. So a stop is
identified by that record's ``uuid``::

    task.completed id = make_event_id(session_id, turn_uuid, "stop", "task.completed")

The shape mirrors the transcript rule for content events (session, record
uuid, block slot, kind), with ``"stop"`` in the block slot.

* The hook adapter reads the tail of ``transcript_path`` when the Stop hook
  fires and takes the last main-chain assistant record written at or before
  the moment it received the payload (:func:`transcript_turn`).
* The session source takes, for each ``stop_hook_summary`` record, the last
  main-chain assistant record before it in the file (:func:`stop_records`).

Both use :func:`is_turn_record`, :func:`last_turn` and :func:`stop_event_id`
from here, so the rule cannot drift apart (TER-OBS-005). Like the tool map,
this module lives beside the Claude Code adapters because both share it, and
it imports nothing heavy so the hook entry point stays light.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain.events import EventId, EventKind, make_event_id

__all__ = [
    "STOP_SUMMARY_SUBTYPE",
    "TAIL_LIMIT",
    "is_stop_summary",
    "is_turn_record",
    "last_turn",
    "read_records",
    "record_time",
    "stop_event_id",
    "stop_records",
    "transcript_turn",
]

#: The ``system`` record subtype Claude Code writes after the stop hooks ran.
STOP_SUMMARY_SUBTYPE = "stop_hook_summary"
#: Bytes read from the end of a transcript at first, doubled until a turn is
#: found or :data:`TAIL_LIMIT` is reached.
TAIL_START = 256 * 1024
#: The most of a transcript the hook reads to find the turn a stop closes.
TAIL_LIMIT = 16 * 1024 * 1024


def stop_event_id(session_id: str, turn_uuid: str) -> EventId:
    """The ``task.completed`` id for the stop that closes ``turn_uuid``."""
    return make_event_id(session_id, turn_uuid, "stop", EventKind.TASK_COMPLETED.value)


def is_turn_record(record: Mapping[str, Any]) -> bool:
    """A main-chain assistant record with a uuid: a step of the agent's turn."""
    uuid = record.get("uuid")
    return (
        record.get("type") == "assistant"
        and not record.get("isSidechain")
        and isinstance(uuid, str)
        and bool(uuid)
    )


def is_stop_summary(record: Mapping[str, Any]) -> bool:
    """The record Claude Code writes on the main chain after a Stop's hooks ran."""
    return (
        record.get("type") == "system"
        and record.get("subtype") == STOP_SUMMARY_SUBTYPE
        and not record.get("isSidechain")
    )


def record_time(record: Mapping[str, Any]) -> datetime | None:
    """A record's ``timestamp`` as an aware datetime, or ``None``."""
    value = record.get("timestamp")
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _aware(moment)


def last_turn(
    records: Iterable[Mapping[str, Any]], before: datetime | None = None
) -> str | None:
    """The uuid of the last turn record, ignoring those written after ``before``.

    A record without a timestamp counts as written in file order.
    """
    bound = _aware(before) if before is not None else None
    turn: str | None = None
    for record in records:
        if not is_turn_record(record):
            continue
        if bound is not None:
            written = record_time(record)
            if written is not None and written > bound:
                continue
        turn = str(record["uuid"])
    return turn


def stop_records(
    records: Iterable[tuple[int, Mapping[str, Any]]],
) -> Iterator[tuple[int, Mapping[str, Any], str]]:
    """``(line, summary record, turn uuid)`` for each stop a transcript records.

    A stop is a ``stop_hook_summary`` record after at least one turn record;
    a second summary for the same turn (hooks set up twice) is the same stop.
    """
    turn: str | None = None
    seen: set[str] = set()
    for line, record in records:
        if is_turn_record(record):
            turn = str(record["uuid"])
        elif is_stop_summary(record) and turn is not None and turn not in seen:
            seen.add(turn)
            yield line, record, turn


def read_records(lines: Iterable[str]) -> Iterator[Mapping[str, Any]]:
    """JSON object records from JSONL lines; anything else is skipped."""
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, Mapping):
            yield record


def transcript_turn(path: str | Path, before: datetime | None) -> str | None:
    """The turn a Stop received at ``before`` closes, from the transcript tail.

    Reads at most :data:`TAIL_LIMIT` bytes from the end of ``path``. Returns
    ``None`` when the file cannot be read or holds no turn record yet.
    Raises nothing for an unreadable file: the hook falls back to keying the
    stop by the second it arrived.
    """
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            span = TAIL_START
            while True:
                start = max(0, size - span)
                handle.seek(start)
                chunk = handle.read(size - start)
                lines = chunk.decode("utf-8", errors="replace").splitlines()
                if start > 0 and lines:
                    lines = lines[1:]  # the first line is probably cut
                turn = last_turn(read_records(lines), before)
                if turn is not None or start == 0 or span >= TAIL_LIMIT:
                    return turn
                span *= 2
    except OSError:
        return None


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
