"""Event id rules for Claude Code: one rule per kind, shared by both sides.

Claude Code is observed twice: live, through hook payloads
(:mod:`ter.adapters.driving.claude_hooks`), and afterwards, through the
transcript the session source reads
(:mod:`ter.adapters.driven.claude_code.session_source`). The same record must
get the same event id on both sides (TER-OBS-007), so each kind's id is
defined here once and both adapters call it.

==================  ====================================================  ==========================
Kind                Id                                                    Key both sides can see
==================  ====================================================  ==========================
``tool.requested``  ``make_event_id(session, tool_use_id, kind)``         ``tool_use_id`` of the call
``tool.completed``  ``make_event_id(session, tool_use_id, kind)``         ``tool_use_id`` of the call
``intent.stated``   ``make_event_id(session, record_uuid, block, kind)``  uuid of the record holding it
``task.completed``  ``make_event_id(session, turn_uuid, "stop", kind)``   uuid of the turn it closes
others              ``make_event_id(session, record_uuid, block, kind)``  transcript only
==================  ====================================================  ==========================

Fallbacks, each of which can no longer correlate across the two sides:

* A transcript ``tool_use`` or ``tool_result`` block without an id (older
  transcripts), or a second block of the same kind that repeats an id already
  used in the session, is keyed by the record rule (:func:`record_event_id`),
  as every block was before ids were shared.
* A hook payload without a ``tool_use_id`` keeps the hook's own key (a digest
  of the tool name and input); a ``UserPromptSubmit`` whose record is not in
  the transcript yet keeps the prompt-text key; a ``Stop`` with no readable
  turn keeps the receive-time key. ``python -m ter hooks check`` reports
  these as unkeyed.

Prompts are never keyed by their text on the transcript side: redaction
changes text, and a redacted session must give the same ids as the raw one
(TER-SRC-022). Record uuids and tool_use_ids survive redaction.

This module imports nothing heavy, so the hook entry point stays light.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.events import EventId, EventKind, make_event_id

__all__ = [
    "QUEUED_COMMAND",
    "TOOL_KINDS",
    "prompt_event_id",
    "queued_prompt_text",
    "record_event_id",
    "stop_event_id",
    "tool_event_id",
]

#: Kinds keyed by the tool call's ``tool_use_id`` rather than by record.
TOOL_KINDS = frozenset({EventKind.TOOL_REQUESTED, EventKind.TOOL_COMPLETED})

#: An attachment carrying a prompt the developer typed while the agent was
#: working: Claude Code delivers it as ``queued_command`` rather than as a
#: user record (TER-SRC-024).
QUEUED_COMMAND = "queued_command"


def record_event_id(
    session_id: str, record_uuid: str, block_index: int, kind: EventKind
) -> EventId:
    """The record rule: block ``block_index`` of transcript record ``record_uuid``."""
    return make_event_id(session_id, record_uuid, block_index, kind.value)


def tool_event_id(session_id: str, tool_use_id: str, kind: EventKind) -> EventId:
    """The tool rule: the request or completion of call ``tool_use_id``.

    The transcript's ``tool_use``/``tool_result`` blocks and the
    Pre/PostToolUse payloads all carry the call's ``tool_use_id``.
    """
    return make_event_id(session_id, tool_use_id, kind.value)


def prompt_event_id(session_id: str, record_uuid: str, block_index: int = 0) -> EventId:
    """The prompt rule: text block ``block_index`` of the record that holds it.

    A typed prompt is a ``user`` record; a queued one is a ``queued_command``
    attachment, keyed with block 0.
    """
    return record_event_id(session_id, record_uuid, block_index, EventKind.PROMPT)


def stop_event_id(session_id: str, turn_uuid: str) -> EventId:
    """The ``task.completed`` id for the stop that closes ``turn_uuid``.

    See :mod:`ter.adapters.claude_code_turns` for how both sides find the turn.
    """
    return make_event_id(session_id, turn_uuid, "stop", EventKind.TASK_COMPLETED.value)


def queued_prompt_text(record: Mapping[str, Any]) -> str | None:
    """The prompt a ``queued_command`` attachment carries, if the developer typed it.

    Only a typed prompt counts: ``commandMode`` is ``prompt``, it is not a
    harness meta message, and its origin, when given, is a human. Task
    notifications and messages from other agents are harness context.
    """
    if record.get("type") != "attachment":
        return None
    attachment = record.get("attachment")
    if not isinstance(attachment, Mapping) or attachment.get("type") != QUEUED_COMMAND:
        return None
    if attachment.get("commandMode") != "prompt" or attachment.get("isMeta"):
        return None
    origin = attachment.get("origin")
    if isinstance(origin, Mapping) and origin.get("kind") not in (None, "human"):
        return None
    text = attachment.get("prompt")
    if not isinstance(text, str) or not text:
        return None
    return text
