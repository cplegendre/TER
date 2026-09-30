"""Claude Code JSONL transcripts as a :class:`~ter.ports.driven.SessionSource`.

Reconstruction reuses the TER 3 loader (``ter_calculator.loader``), so sibling
merging, request-id de-duplication and content fingerprints behave exactly as
they do in TER 3. This adapter adds the translation into neutral events and
an honest count of records it could not map.
"""

from __future__ import annotations

import json
from pathlib import Path

from ter_calculator.loader import load_session
from ter_calculator.models import ContentBlock, Message
from ter_calculator.models import TokenUsage as LegacyUsage

from ....domain.events import (
    Actor,
    Event,
    EventId,
    EventKind,
    Provenance,
    SessionTrace,
    TokenUsage,
    ToolCall,
    UnrecognisedRecord,
    make_event_id,
)
from ...claude_code_tools import tool_kind

#: Record types that carry conversation content and are mapped to events.
_CONTENT_TYPES = frozenset({"user", "assistant"})


class ClaudeCodeJsonlSource:
    """Reads Claude Code ``.jsonl`` session transcripts."""

    format_name = "claude-code-jsonl"

    def read(self, ref: str | Path) -> SessionTrace:
        path = Path(ref)
        session = load_session(path)
        unrecognised = _scan_unrecognised(path)
        source = path.name

        events: list[Event] = []
        requests: dict[str, ToolCall] = {}
        previous: EventId | None = None
        for message in session.messages:
            usage = _usage(message.usage)
            for index, block in enumerate(message.content_blocks):
                kind, actor = _classify(message.role, block.block_type)
                if kind is None or actor is None:
                    continue
                block_index = (
                    block.block_index if block.block_index is not None else index
                )
                tool = _tool(block, requests)
                if (
                    tool is not None
                    and kind is EventKind.TOOL_REQUESTED
                    and tool.call_id
                ):
                    requests[tool.call_id] = tool
                event_id = make_event_id(
                    session.session_id, message.uuid, block_index, kind.value
                )
                events.append(
                    Event(
                        id=event_id,
                        session_id=session.session_id,
                        sequence=len(events),
                        kind=kind,
                        actor=actor,
                        text=_text(block),
                        provenance=Provenance(
                            source=source,
                            record_id=message.uuid,
                            lines=_lines(message, block),
                            block_index=block_index,
                            fingerprint=block.content_fingerprint,
                        ),
                        timestamp=message.timestamp,
                        tool=tool,
                        # Usage belongs to the model turn, so it is attached once
                        # (to the turn's first event) and never double counted.
                        usage=usage,
                        parent_id=previous,
                    )
                )
                usage = None
                previous = event_id

        return SessionTrace(
            session_id=session.session_id,
            source_format=self.format_name,
            events=tuple(events),
            unrecognised=unrecognised,
        )


def _scan_unrecognised(path: Path) -> tuple[UnrecognisedRecord, ...]:
    """List records whose type carries no conversation content."""
    found: list[UnrecognisedRecord] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                found.append(UnrecognisedRecord(line_number, type(record).__name__))
                continue
            record_type = str(record.get("type", ""))
            if record_type not in _CONTENT_TYPES:
                found.append(
                    UnrecognisedRecord(line_number, record_type or "<missing>")
                )
    return tuple(found)


def _classify(role: str, block_type: str) -> tuple[EventKind | None, Actor | None]:
    if role == "user":
        if block_type == "text":
            return EventKind.PROMPT, Actor.USER
        if block_type == "tool_result":
            return EventKind.TOOL_COMPLETED, Actor.TOOL
        return None, None
    if role == "assistant":
        if block_type == "thinking":
            return EventKind.REASONING, Actor.ASSISTANT
        if block_type == "text":
            return EventKind.RESPONSE, Actor.ASSISTANT
        if block_type == "tool_use":
            return EventKind.TOOL_REQUESTED, Actor.ASSISTANT
    return None, None


def _text(block: ContentBlock) -> str:
    if block.block_type == "tool_use":
        return json.dumps(block.tool_input or {}, sort_keys=True, separators=(",", ":"))
    return block.text or ""


def _tool(block: ContentBlock, requests: dict[str, ToolCall]) -> ToolCall | None:
    if block.block_type == "tool_use":
        return ToolCall(
            native_name=block.tool_name or "",
            kind=tool_kind(block.tool_name),
            call_id=block.tool_use_id,
            arguments=dict(block.tool_input or {}),
        )
    if block.block_type == "tool_result":
        # A result carries its request's name and kind, so detectors can pair
        # them without re-deriving the mapping. An orphan result stays ``other``.
        request = requests.get(block.tool_use_id or "")
        return ToolCall(
            native_name=request.native_name if request else "",
            kind=request.kind if request else tool_kind(None),
            call_id=block.tool_use_id,
        )
    return None


def _usage(usage: LegacyUsage | None) -> TokenUsage | None:
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_input_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
    )


def _lines(message: Message, block: ContentBlock) -> tuple[int, ...]:
    lines = block.source_lines or ([block.source_line] if block.source_line else [])
    return tuple(lines or message.source_lines)
