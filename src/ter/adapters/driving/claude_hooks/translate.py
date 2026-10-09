"""Claude Code hook payloads translated into ``ter.event`` events.

Pure translation: no IO, no clock unless one is passed in, and no state. The
same payload always yields events with the same ids, so a hook that fires
twice, or a PreToolUse and a PostToolUse for the same call, collapse into one
event each once the engine applies them (TER-OBS-004).

========================  ==================================================
Hook                      Events
========================  ==================================================
``UserPromptSubmit``      ``intent.stated`` (user), keyed by the prompt's record
``PreToolUse``            ``tool.requested`` (assistant), keyed by tool_use_id
``PostToolUse``           ``tool.requested`` and ``tool.completed`` (tool)
``Stop``                  ``task.completed`` (system), keyed by the turn it closes
``SubagentStop``          ``subagent.completed`` (system), in the parent session,
                          keyed by ``agent_id``; none for an internal helper
                          agent (status ``internal``, see below)
``SessionStart`` etc.     none: lifecycle only, reported as ``lifecycle``
anything else             none: reported as ``ignored`` with a reason
========================  ==================================================

PostToolUse repeats the request because many installations register only
PostToolUse. Its request carries the id PreToolUse would have produced, so
where both hooks run the second copy is discarded as a duplicate.

Ids follow the rules the session source uses for the same records
(:mod:`ter.adapters.claude_code_ids`, TER-OBS-007): tool events by session,
``tool_use_id`` and kind; a prompt by the uuid of its transcript record, when
the caller found it (``prompt_at``); a stop by the turn it closes (``turn``);
a subagent stop by the payload's ``agent_id``.
Without those keys each falls back to a hook-only key that cannot correlate.

Internal helper agents. Claude Code also fires ``SubagentStop`` for helper
agents of its own (observed on Windows, 9 October 2026: an empty
``agent_type``, an ``agent_transcript_path`` that is never written, and stops
in sessions that started no Agent-tool subagent). Such a stop is not a
handoff the developer's agent made and no transcript will ever record it, so
it yields no event: status ``internal``. An event would put a
``subagent.completed`` in the live stream that the session source can never
derive (live would no longer equal the batch read of the same session) and
would count a handoff the agent never made. The caller says whether the
transcript file is missing (``agent_transcript_missing``); this module does
no IO. A typed agent whose file is missing still gets its event: that is a
real miss, and the hooks check reports it as one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Any

from ....domain.events import (
    Actor,
    Event,
    EventId,
    EventKind,
    Provenance,
    ToolCall,
    make_event_id,
)
from ...claude_code_ids import (
    prompt_event_id,
    stop_event_id,
    subagent_event_id,
    tool_event_id,
)
from ...claude_code_tools import tool_kind
from ...claude_code_turns import PromptRecord

__all__ = [
    "LIFECYCLE_HOOKS",
    "INTERNAL_HELPER",
    "HookStatus",
    "HookTranslation",
    "is_internal_helper",
    "translate",
]

#: Hooks that mark session lifecycle and produce no event.
LIFECYCLE_HOOKS = frozenset(
    {
        "SessionStart",
        "SessionEnd",
        "SubagentStart",
        "PreCompact",
        "Notification",
    }
)

_SOURCE = "claude-code-hooks"


class HookStatus(StrEnum):
    """Outcome of handling one hook payload."""

    RECORDED = "recorded"
    LIFECYCLE = "lifecycle"
    IGNORED = "ignored"
    #: A SubagentStop of a Claude Code internal helper agent: no event.
    INTERNAL = "internal"


#: The reason an internal helper agent's SubagentStop carries.
INTERNAL_HELPER = (
    "internal helper agent: no agent_type and no transcript file (by design)"
)


@dataclass(frozen=True)
class HookTranslation:
    """Events translated from one hook payload, or why there are none."""

    status: HookStatus
    hook_event_name: str | None = None
    session_id: str | None = None
    events: tuple[Event, ...] = ()
    reason: str = ""


class _Malformed(ValueError):
    """A payload that does not have the shape its hook promises."""


def translate(
    payload: object,
    *,
    received_at: datetime | None = None,
    turn: str | None = None,
    prompt_at: PromptRecord | None = None,
    agent_transcript_missing: bool = False,
) -> HookTranslation:
    """Translate one decoded hook payload. Never raises for bad input.

    ``turn`` is, for a Stop, the uuid of the transcript turn the stop closes
    (:func:`ter.adapters.claude_code_turns.transcript_turn`); with it the
    ``task.completed`` id is the one the session source derives for the same
    stop (TER-OBS-005). ``prompt_at`` is, for a UserPromptSubmit, the
    transcript record holding the prompt
    (:func:`ter.adapters.claude_code_turns.transcript_prompt`); with it the
    ``intent.stated`` id is the session source's (TER-OBS-007).
    ``agent_transcript_missing`` is, for a SubagentStop, whether the file its
    ``agent_transcript_path`` names does not exist; with an empty
    ``agent_type`` that makes it an internal helper agent
    (:func:`is_internal_helper`). Other hooks ignore all three.
    """
    if not isinstance(payload, Mapping):
        return HookTranslation(
            HookStatus.IGNORED, reason=f"payload is {type(payload).__name__}"
        )
    name = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    if not isinstance(name, str) or not name:
        return HookTranslation(HookStatus.IGNORED, reason="missing hook_event_name")
    if not isinstance(session_id, str) or not session_id:
        return HookTranslation(
            HookStatus.IGNORED, hook_event_name=name, reason="missing session_id"
        )
    if name in LIFECYCLE_HOOKS:
        return HookTranslation(HookStatus.LIFECYCLE, name, session_id)
    if name == "SubagentStop" and is_internal_helper(payload, agent_transcript_missing):
        return HookTranslation(
            HookStatus.INTERNAL, name, session_id, reason=INTERNAL_HELPER
        )
    try:
        events = _translate_content(
            name, session_id, payload, received_at, turn, prompt_at
        )
    except _Malformed as error:
        return HookTranslation(HookStatus.IGNORED, name, session_id, reason=str(error))
    if events is None:
        return HookTranslation(
            HookStatus.IGNORED, name, session_id, reason=f"unsupported hook {name}"
        )
    return HookTranslation(HookStatus.RECORDED, name, session_id, events)


def is_internal_helper(
    payload: Mapping[str, Any], agent_transcript_missing: bool
) -> bool:
    """Whether a SubagentStop payload is a Claude Code internal helper's.

    All three must hold: the payload has no ``agent_type`` (missing, not a
    string, or blank), it names an ``agent_transcript_path``, and that file
    does not exist (``agent_transcript_missing``, found by the caller). A
    payload without ``agent_transcript_path`` (older releases) is never one.
    """
    agent_type = payload.get("agent_type")
    if isinstance(agent_type, str) and agent_type.strip():
        return False
    path = payload.get("agent_transcript_path")
    return isinstance(path, str) and bool(path) and agent_transcript_missing


def _translate_content(
    name: str,
    session_id: str,
    payload: Mapping[str, Any],
    received_at: datetime | None,
    turn: str | None = None,
    prompt_at: PromptRecord | None = None,
) -> tuple[Event, ...] | None:
    source = _source(payload)
    if name == "UserPromptSubmit":
        prompt = payload.get("prompt")
        if not isinstance(prompt, str):
            raise _Malformed("UserPromptSubmit without a prompt string")
        digest = _digest(prompt)
        if prompt_at is not None:
            # The shared rule: the session source keys the same record so.
            return (
                Event(
                    id=prompt_event_id(
                        session_id, prompt_at.uuid, prompt_at.block_index
                    ),
                    session_id=session_id,
                    sequence=0,
                    kind=EventKind.PROMPT,
                    actor=Actor.USER,
                    text=prompt,
                    provenance=Provenance(
                        source,
                        prompt_at.uuid,
                        block_index=prompt_at.block_index,
                        fingerprint=digest,
                    ),
                    timestamp=received_at,
                ),
            )
        # Fallback, when the transcript holds no record for the prompt yet.
        # The payload carries no id for the submission itself, so the second
        # it was received tells two submissions of the same text apart, while
        # one submission seen twice within that second (the same hook set up
        # in two settings files) keeps one id. Without a clock the id is the
        # text's alone (deterministic, as in tests).
        parts: tuple[str, ...] = (_SOURCE, session_id, name, digest)
        if received_at is not None:
            parts += (received_at.replace(microsecond=0).isoformat(),)
        return (
            Event(
                id=make_event_id(*parts),
                session_id=session_id,
                sequence=0,
                kind=EventKind.PROMPT,
                actor=Actor.USER,
                text=prompt,
                provenance=Provenance(source, f"prompt:{digest}", fingerprint=digest),
                timestamp=received_at,
            ),
        )
    if name in ("PreToolUse", "PostToolUse"):
        request, key = _request(session_id, payload, source, received_at)
        if name == "PreToolUse":
            return (request,)
        return (request, _completion(session_id, payload, request, key, received_at))
    if name == "Stop":
        return (_stop(session_id, source, received_at, turn),)
    if name == "SubagentStop":
        return (_subagent_stop(session_id, payload, source, received_at),)
    return None


def _second(received_at: datetime | None) -> tuple[str, ...]:
    # As for prompts: the second a payload arrived tells two stops apart,
    # while one stop delivered twice within it keeps one id.
    if received_at is None:
        return ()
    return (received_at.replace(microsecond=0).isoformat(),)


def _stop(
    session_id: str, source: str, received_at: datetime | None, turn: str | None
) -> Event:
    if turn:
        # The shared rule: the transcript records this stop too, keyed by the
        # same turn (TER-OBS-005).
        return Event(
            id=stop_event_id(session_id, turn),
            session_id=session_id,
            sequence=0,
            kind=EventKind.TASK_COMPLETED,
            actor=Actor.SYSTEM,
            text="",
            provenance=Provenance(source, f"stop:turn:{turn}"),
            timestamp=received_at,
        )
    # Without a readable transcript the stop is keyed by when it arrived; it
    # then cannot correlate with the transcript.
    when = _second(received_at)
    return Event(
        id=make_event_id(_SOURCE, session_id, "Stop", *when),
        session_id=session_id,
        sequence=0,
        kind=EventKind.TASK_COMPLETED,
        actor=Actor.SYSTEM,
        text="",
        provenance=Provenance(source, ":".join(("stop", *when))),
        timestamp=received_at,
    )


def _subagent_stop(
    session_id: str,
    payload: Mapping[str, Any],
    source: str,
    received_at: datetime | None,
) -> Event:
    # session_id is the parent's, so the event joins the parent session.
    # Newer Claude Code releases name the subagent; with that id a stop needs
    # no clock to be told apart from another subagent's. Without it, parallel
    # subagents can finish within one second, so the full receive time keys
    # the stop: a lost subagent costs more than a rare double delivery.
    # The named form is the shared rule (TER-OBS-007): the session source
    # derives the same id from the subagent's transcript file.
    agent_id = payload.get("agent_id")
    agent_type = payload.get("agent_type")
    if isinstance(agent_id, str) and agent_id:
        event_id = subagent_event_id(session_id, agent_id)
        record = f"subagent:{agent_id}"
    else:
        # The receive-time key; the record id says so, as the hooks check
        # reports these as unkeyed.
        when = (received_at.isoformat(),) if received_at is not None else ()
        event_id = make_event_id(_SOURCE, session_id, "SubagentStop", *when)
        record = ":".join(("subagent", "received", *when))
    return Event(
        id=event_id,
        session_id=session_id,
        sequence=0,
        kind=EventKind.SUBAGENT_COMPLETED,
        actor=Actor.SYSTEM,
        text=agent_type if isinstance(agent_type, str) else "",
        provenance=Provenance(source, record),
        timestamp=received_at,
    )


def _request(
    session_id: str,
    payload: Mapping[str, Any],
    source: str,
    received_at: datetime | None,
) -> tuple[Event, str]:
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise _Malformed("tool hook without a tool_name")
    tool_input = payload.get("tool_input", {})
    if tool_input is None:
        tool_input = {}
    if not isinstance(tool_input, Mapping):
        raise _Malformed("tool_input is not an object")
    text = _canonical(dict(tool_input))
    tool_use_id = payload.get("tool_use_id")
    if isinstance(tool_use_id, str) and tool_use_id:
        key = tool_use_id
    else:
        # Without a tool_use_id, identical calls in one session share an id;
        # current Claude Code releases always send one.
        key = f"hook:{_digest(tool_name + chr(0) + text)}"
    event = Event(
        id=_tool_id(session_id, key, EventKind.TOOL_REQUESTED),
        session_id=session_id,
        sequence=0,
        kind=EventKind.TOOL_REQUESTED,
        actor=Actor.ASSISTANT,
        text=text,
        provenance=Provenance(source, key, fingerprint=_digest(text)),
        timestamp=received_at,
        tool=ToolCall(
            native_name=tool_name,
            kind=tool_kind(tool_name),
            call_id=key,
            arguments=dict(tool_input),
        ),
    )
    return event, key


def _completion(
    session_id: str,
    payload: Mapping[str, Any],
    request: Event,
    key: str,
    received_at: datetime | None,
) -> Event:
    assert request.tool is not None
    response = payload.get("tool_response")
    text = response if isinstance(response, str) else _canonical(response)
    return Event(
        id=_tool_id(session_id, key, EventKind.TOOL_COMPLETED),
        session_id=session_id,
        sequence=0,
        kind=EventKind.TOOL_COMPLETED,
        actor=Actor.TOOL,
        text=text,
        provenance=Provenance(
            request.provenance.source, key, fingerprint=_digest(text)
        ),
        timestamp=received_at,
        tool=ToolCall(
            native_name=request.tool.native_name,
            kind=request.tool.kind,
            call_id=key,
        ),
        parent_id=request.id,
    )


def _tool_id(session_id: str, key: str, kind: EventKind) -> EventId:
    if key.startswith("hook:"):
        # No tool_use_id: a hook-only key, which cannot match the transcript.
        return make_event_id(_SOURCE, session_id, key, kind.value)
    return tool_event_id(session_id, key, kind)


def _source(payload: Mapping[str, Any]) -> str:
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        return f"{_SOURCE}:{PurePath(transcript).name}"
    return _SOURCE


def _canonical(value: object) -> str:
    if value is None:
        return ""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
