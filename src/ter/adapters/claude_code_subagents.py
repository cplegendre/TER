"""Where Claude Code keeps subagent transcripts, and when one shows a finish.

Claude Code writes each subagent's transcript next to its parent's::

    <project>/<session id>.jsonl
    <project>/<session id>/subagents/agent-<agent id>.jsonl
    <project>/<session id>/subagents/agent-<agent id>.meta.json   (agentType, ...)
    <project>/<session id>/subagents/agent-<agent id>.prefix.json (prompt cache)

``<agent id>`` is the ``agent_id`` a ``SubagentStop`` payload carries and the
``agentId`` every record of the subagent's transcript carries, so both the
hook adapter and the session source can key a ``subagent.completed`` event by
it (:func:`ter.adapters.claude_code_ids.subagent_event_id`, TER-OBS-007).

A subagent file exists from the subagent's first record, so a file alone does
not say the subagent stopped. A subagent counts as finished when any of these
finish markers is present (observed in Claude Code 2.1.x transcripts):

``parent-result``
    a record of the parent transcript whose ``toolUseResult`` names the
    ``agentId`` with ``status: completed``: a foreground Agent call that
    returned its result.
``parent-notification``
    a ``queued_command`` attachment of the parent with ``commandMode:
    task-notification`` whose text names ``<task-id>`` the agent id and
    ``<status>completed</status>``: a background agent that finished. A
    background agent resumed later is notified again.
``final-turn``
    the subagent's own transcript ends with an assistant turn whose
    ``stop_reason`` is not ``tool_use``: the subagent ended its turn with no
    tool call pending.

The event takes the time of the latest marker, because a subagent resumed
more than once stops more than once and the hook adapter keeps one event per
agent id (the last stop is the one the developer saw). Redaction drops
``toolUseResult``, so on a redacted corpus a foreground agent is found by its
``final-turn`` marker; notifications and the subagent's own records survive.

Pure apart from :func:`subagent_transcripts`, which lists a folder.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .claude_code_ids import QUEUED_COMMAND
from .claude_code_turns import record_time

__all__ = [
    "AGENT_PREFIX",
    "SUBAGENTS_DIR",
    "FinishMarker",
    "final_turn",
    "parent_finishes",
    "subagent_transcripts",
]

#: The folder below ``<session id>/`` that holds subagent transcripts.
SUBAGENTS_DIR = "subagents"
#: Subagent transcript files are ``agent-<agent id>.jsonl``.
AGENT_PREFIX = "agent-"

_TASK_ID = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
_STATUS = re.compile(r"<status>\s*([^<\s]+)\s*</status>")
_FINISHED = "completed"


@dataclass(frozen=True)
class FinishMarker:
    """One record showing a subagent finished."""

    #: ``parent-result``, ``parent-notification`` or ``final-turn``.
    kind: str
    line: int
    record_id: str
    timestamp: datetime | None


def subagent_transcripts(transcript: Path) -> dict[str, Path]:
    """The subagent transcripts of the session at ``transcript``, by agent id.

    Empty when the session has no ``<session id>/subagents`` folder.
    """
    folder = transcript.parent / transcript.stem / SUBAGENTS_DIR
    if not folder.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in sorted(folder.glob(f"{AGENT_PREFIX}*.jsonl")):
        agent_id = path.stem[len(AGENT_PREFIX) :]
        if agent_id and path.is_file():
            found[agent_id] = path
    return found


def parent_finishes(
    records: Iterable[tuple[int, Mapping[str, Any]]],
) -> dict[str, list[FinishMarker]]:
    """Finish markers the parent transcript holds, by agent id, in file order."""
    found: dict[str, list[FinishMarker]] = {}
    for line, record in records:
        marker = _parent_marker(line, record)
        if marker is not None:
            agent_id, finish = marker
            found.setdefault(agent_id, []).append(finish)
    return found


def final_turn(records: Iterable[tuple[int, Mapping[str, Any]]]) -> FinishMarker | None:
    """The subagent transcript's ``final-turn`` marker, if it ends a turn.

    Claude Code writes one record per content block, and only some carry the
    message's ``stop_reason``; so the marker is the last assistant record
    with a ``stop_reason`` other than ``tool_use``, provided no user record
    (a tool result or a new instruction) follows it.
    """
    marker: FinishMarker | None = None
    for line, record in records:
        kind = record.get("type")
        if kind == "user":
            marker = None
            continue
        if kind != "assistant":
            continue
        message = record.get("message")
        reason = message.get("stop_reason") if isinstance(message, Mapping) else None
        if reason == "tool_use":
            marker = None
        elif isinstance(reason, str) and reason:
            marker = FinishMarker(
                "final-turn", line, str(record.get("uuid") or ""), record_time(record)
            )
    return marker


def _parent_marker(
    line: int, record: Mapping[str, Any]
) -> tuple[str, FinishMarker] | None:
    result = record.get("toolUseResult")
    if isinstance(result, Mapping):
        agent_id = result.get("agentId")
        if isinstance(agent_id, str) and agent_id and result.get("status") == _FINISHED:
            return agent_id, _marker("parent-result", line, record)
        return None
    attachment = record.get("attachment")
    if record.get("type") != "attachment" or not isinstance(attachment, Mapping):
        return None
    if (
        attachment.get("type") != QUEUED_COMMAND
        or attachment.get("commandMode") != "task-notification"
    ):
        return None
    text = attachment.get("prompt")
    if not isinstance(text, str):
        return None
    task = _TASK_ID.search(text)
    status = _STATUS.search(text)
    if task is None or status is None or status.group(1) != _FINISHED:
        return None
    return task.group(1), _marker("parent-notification", line, record)


def _marker(kind: str, line: int, record: Mapping[str, Any]) -> FinishMarker:
    return FinishMarker(kind, line, str(record.get("uuid") or ""), record_time(record))
