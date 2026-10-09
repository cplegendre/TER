"""Which transcript record a hook payload is about, for hooks and transcripts.

Two hook payloads carry no id of their own: ``Stop`` and ``UserPromptSubmit``.
For each, this module holds the one rule both sides use to find the
transcript record the event is keyed by (the id rules themselves are in
:mod:`ter.adapters.claude_code_ids`).

Stops
-----

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
from here, so the rule cannot drift apart (TER-OBS-005).

Prompts
-------
A ``UserPromptSubmit`` payload carries the prompt's text but not the uuid of
the record Claude Code writes for it, which is what the session source keys
``intent.stated`` by. So the hook reads the transcript tail
(:func:`transcript_prompt`, at most :data:`PROMPT_TAIL` bytes) for the last
record holding this prompt written by the time the payload arrived
(:func:`prompt_record`): a main-chain ``user`` record's text block, or a
``queued_command`` attachment for a prompt the developer queued while the
agent worked (TER-SRC-024). The id is then
:func:`ter.adapters.claude_code_ids.prompt_event_id` of that record and block,
as the session source derives it. The text is only used to find the record on
the hook side; the id never depends on it, so redaction cannot move it.

A record is taken as written no earlier than the latest timestamp at or
before it in the file (the file is append-only), so a queued attachment
stamped with its enqueue time but written later is not seen early.

Claude Code 2.1 runs the ``UserPromptSubmit`` hooks before it writes the
prompt's record, so live the lookup usually finds nothing and the hook falls
back to keying the prompt by its text (see ``translate``); ``python -m ter
hooks check`` reports those prompts as unkeyed.

Like the tool map, this module lives beside the Claude Code adapters because
both share it, and it imports nothing heavy so the hook entry point stays
light.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from .claude_code_ids import queued_prompt_text, stop_event_id

__all__ = [
    "PROMPT_TAIL",
    "STOP_SUMMARY_SUBTYPE",
    "TAIL_LIMIT",
    "PromptRecord",
    "is_stop_summary",
    "is_turn_record",
    "last_turn",
    "prompt_blocks",
    "prompt_record",
    "read_records",
    "record_time",
    "stop_event_id",
    "stop_records",
    "transcript_prompt",
    "transcript_turn",
]

#: The ``system`` record subtype Claude Code writes after the stop hooks ran.
STOP_SUMMARY_SUBTYPE = "stop_hook_summary"
#: Bytes read from the end of a transcript at first, doubled until a turn is
#: found or :data:`TAIL_LIMIT` is reached.
TAIL_START = 256 * 1024
#: The most of a transcript the hook reads to find the turn a stop closes.
TAIL_LIMIT = 16 * 1024 * 1024
#: The most of a transcript the hook reads to find a prompt's record. When
#: written, it is the newest record or close to it, so one window will do; a
#: prompt longer than this is left unkeyed.
PROMPT_TAIL = 1024 * 1024


@dataclass(frozen=True)
class PromptRecord:
    """Where a prompt sits in the transcript: its record and text block."""

    uuid: str
    block_index: int


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


def prompt_blocks(record: Mapping[str, Any]) -> Iterator[tuple[int, str]]:
    """``(block index, text)`` for each prompt a main-chain record holds.

    The blocks the session source maps to ``intent.stated``: each text block
    of a ``user`` record (a string content is block 0), and the prompt of a
    developer's ``queued_command`` attachment (block 0).
    """
    uuid = record.get("uuid")
    if not isinstance(uuid, str) or not uuid or record.get("isSidechain"):
        return
    queued = queued_prompt_text(record)
    if queued is not None:
        yield 0, queued
        return
    if record.get("type") != "user":
        return
    message = record.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, str):
        yield 0, content
        return
    if not isinstance(content, list):
        return
    for index, block in enumerate(content):
        if isinstance(block, str):
            yield index, block
        elif isinstance(block, Mapping) and block.get("type", "text") == "text":
            text = block.get("text")
            if isinstance(text, str):
                yield index, text


def prompt_record(
    records: Iterable[Mapping[str, Any]], prompt: str, before: datetime | None = None
) -> PromptRecord | None:
    """The last record holding ``prompt`` written by ``before``, or ``None``.

    Records are append-only, so a record counts as written no earlier than
    the latest timestamp at or before it; reading stops at the first record
    past ``before``. Text is compared with surrounding whitespace stripped.
    """
    bound = _aware(before) if before is not None else None
    wanted = prompt.strip()
    written: datetime | None = None
    found: PromptRecord | None = None
    for record in records:
        moment = record_time(record)
        if moment is not None and (written is None or moment > written):
            written = moment
        if bound is not None and written is not None and written > bound:
            break
        for index, text in prompt_blocks(record):
            if text.strip() == wanted:
                found = PromptRecord(str(record["uuid"]), index)
    return found


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
                start, lines = _tail(handle, size, span)
                turn = last_turn(read_records(lines), before)
                if turn is not None or start == 0 or span >= TAIL_LIMIT:
                    return turn
                span *= 2
    except OSError:
        return None


def transcript_prompt(
    path: str | Path, prompt: str, before: datetime | None
) -> PromptRecord | None:
    """The record holding a prompt received at ``before``, from the transcript tail.

    Reads at most :data:`PROMPT_TAIL` bytes from the end of ``path``. Returns
    ``None`` when the file cannot be read or no record holds the prompt yet;
    the hook then keys the prompt by its text.
    """
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            _, lines = _tail(handle, size, PROMPT_TAIL)
    except OSError:
        return None
    return prompt_record(read_records(lines), prompt, before)


def _tail(handle: IO[bytes], size: int, span: int) -> tuple[int, list[str]]:
    """The whole lines in the last ``span`` bytes, and where they start."""
    start = max(0, size - span)
    handle.seek(start)
    chunk = handle.read(size - start)
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # the first line is probably cut
    return start, lines


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
