"""Claude Code JSONL transcripts as a :class:`~ter.ports.driven.SessionSource`.

Reconstruction reuses the TER 3 loader (``ter_calculator.loader``), so sibling
merging, request-id de-duplication and content fingerprints behave exactly as
they do in TER 3. This adapter adds the translation into neutral events and
an honest count of records it could not map.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ter_calculator.loader import load_session
from ter_calculator.models import ContentBlock, Message
from ter_calculator.models import TokenUsage as LegacyUsage

from ....domain.events import (
    Actor,
    Event,
    EventId,
    EventKind,
    MetadataRecord,
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

#: Record types Claude Code writes that carry no agent activity: titles,
#: bookmarks, queue and permission bookkeeping, harness notices (``system``)
#: and context the harness injects (``attachment``). They are recognised as
#: metadata, never as events (TER-SRC-005). Observed in Claude Code 2.1.x on
#: the reference corpus; a type not listed here stays unrecognised.
METADATA_TYPES = frozenset(
    {
        "ai-title",
        "atis-latch",
        "attachment",
        "cost-state",
        "custom-title",
        "file-history-delta",
        "file-history-snapshot",
        "last-prompt",
        "mode",
        "permission-mode",
        "pr-link",
        "queue-operation",
        "summary",
        "system",
    }
)


def record_class(record_type: str) -> str:
    """How the session source treats a record type: ``content`` (mapped to
    events), ``metadata`` (documented, no agent activity) or ``unrecognised``."""
    if record_type in _CONTENT_TYPES:
        return "content"
    if record_type in METADATA_TYPES:
        return "metadata"
    return "unrecognised"


#: An attachment carrying a prompt the developer typed while the agent was
#: working: Claude Code delivers it as ``queued_command`` rather than as a
#: user record, so it is the developer's intent and maps to ``intent.stated``.
_QUEUED = "queued_command"


class ClaudeCodeJsonlSource:
    """Reads Claude Code ``.jsonl`` session transcripts."""

    format_name = "claude-code-jsonl"

    def read(self, ref: str | Path) -> SessionTrace:
        path = Path(ref)
        session = load_session(path)
        scan = _scan(path)
        usage_meta = scan.usage
        queued = list(scan.prompts)
        source = path.name

        events: list[Event] = []
        requests: dict[str, ToolCall] = {}
        previous: EventId | None = None

        def flush_queued(before: int | None) -> None:
            # Queued prompts take their place in file order among the messages.
            nonlocal previous
            while queued and (before is None or queued[0].line < before):
                prompt = queued.pop(0)
                event_id = make_event_id(
                    session.session_id, prompt.uuid, 0, EventKind.PROMPT.value
                )
                events.append(
                    Event(
                        id=event_id,
                        session_id=session.session_id,
                        sequence=len(events),
                        kind=EventKind.PROMPT,
                        actor=Actor.USER,
                        text=prompt.text,
                        provenance=Provenance(
                            source=source,
                            record_id=prompt.uuid,
                            lines=(prompt.line,),
                            block_index=0,
                        ),
                        timestamp=prompt.timestamp,
                        parent_id=previous,
                    )
                )
                previous = event_id

        for message in session.messages:
            flush_queued(min(message.source_lines, default=None))
            usage = _usage(message.usage, _meta(message, usage_meta))
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
        flush_queued(None)

        return SessionTrace(
            session_id=session.session_id,
            source_format=self.format_name,
            events=tuple(events),
            unrecognised=scan.unrecognised,
            metadata=scan.metadata,
        )


#: Usage facts the TER 3 loader drops, by source line: the model named on the
#: record and whether its usage block carried any cache field.
_UsageMeta = dict[int, tuple[str | None, bool]]

_CACHE_KEYS = ("cache_creation_input_tokens", "cache_read_input_tokens")


@dataclass(frozen=True)
class _QueuedPrompt:
    line: int
    uuid: str
    text: str
    timestamp: datetime | None


@dataclass(frozen=True)
class _Scan:
    unrecognised: tuple[UnrecognisedRecord, ...]
    metadata: tuple[MetadataRecord, ...]
    usage: _UsageMeta
    prompts: tuple[_QueuedPrompt, ...]


def _scan(path: Path) -> _Scan:
    """Sort every record into content, documented metadata or unrecognised,
    read the usage facts (model, cache fields present) of content records, and
    collect the prompts the developer queued while the agent worked."""
    found: list[UnrecognisedRecord] = []
    metadata: list[MetadataRecord] = []
    meta: _UsageMeta = {}
    prompts: list[_QueuedPrompt] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                found.append(UnrecognisedRecord(line_number, type(record).__name__))
                continue
            record_type = str(record.get("type", ""))
            if record_type in METADATA_TYPES:
                prompt = _queued_prompt(line_number, record)
                if prompt is not None:
                    prompts.append(prompt)
                else:
                    metadata.append(MetadataRecord(line_number, record_type))
                continue
            if record_type not in _CONTENT_TYPES:
                found.append(
                    UnrecognisedRecord(line_number, record_type or "<missing>")
                )
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            model = message.get("model")
            meta[line_number] = (
                model if isinstance(model, str) and model else None,
                isinstance(usage, dict) and any(k in usage for k in _CACHE_KEYS),
            )
    return _Scan(tuple(found), tuple(metadata), meta, tuple(prompts))


def _queued_prompt(line: int, record: dict[str, object]) -> _QueuedPrompt | None:
    """A ``queued_command`` attachment the developer typed, else ``None``.

    Only a typed prompt counts: ``commandMode`` is ``prompt``, it is not a
    harness meta message, and its origin, when given, is a human. Task
    notifications and messages from other agents are harness context.
    """
    if record.get("type") != "attachment":
        return None
    attachment = record.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != _QUEUED:
        return None
    if attachment.get("commandMode") != "prompt" or attachment.get("isMeta"):
        return None
    origin = attachment.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return None
    text = attachment.get("prompt")
    uuid = record.get("uuid")
    if not isinstance(text, str) or not text or not isinstance(uuid, str):
        return None
    return _QueuedPrompt(line, uuid, text, _timestamp(record.get("timestamp")))


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _meta(message: Message, meta: _UsageMeta) -> tuple[str | None, bool]:
    """The model and cache-field presence over a message's merged records."""
    model: str | None = None
    cache = False
    for line in message.source_lines:
        line_model, line_cache = meta.get(line, (None, False))
        model = model or line_model
        cache = cache or line_cache
    return model, cache


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


def _usage(
    usage: LegacyUsage | None, meta: tuple[str | None, bool]
) -> TokenUsage | None:
    if usage is None:
        return None
    model, cache_reported = meta
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_input_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        model=model,
        cache_reported=cache_reported,
    )


def _lines(message: Message, block: ContentBlock) -> tuple[int, ...]:
    lines = block.source_lines or ([block.source_line] if block.source_line else [])
    return tuple(lines or message.source_lines)
