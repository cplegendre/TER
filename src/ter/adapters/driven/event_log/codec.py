"""JSON encoding of ``ter.event`` values, one object per event.

The encoding is lossless for every field of :class:`~ter.domain.Event`, so an
event read back from a log is equal to the event that was appended. Tool
arguments must be JSON values; anything else is stored as its ``str``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ....domain.events import (
    EVENT_SCHEMA_VERSION,
    READABLE_SCHEMA_VERSIONS,
    Actor,
    Event,
    EventId,
    EventKind,
    Provenance,
    TokenUsage,
    ToolCall,
    ToolKind,
)

__all__ = ["event_from_record", "event_to_record"]


def event_to_record(event: Event) -> dict[str, Any]:
    """Encode an event as a JSON-ready dict."""
    tool = event.tool
    usage = event.usage
    prov = event.provenance
    return {
        "schema": EVENT_SCHEMA_VERSION,
        "id": event.id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "kind": event.kind.value,
        "actor": event.actor.value,
        "text": event.text,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "parent_id": event.parent_id,
        "provenance": {
            "source": prov.source,
            "record_id": prov.record_id,
            "lines": list(prov.lines),
            "block_index": prov.block_index,
            "fingerprint": prov.fingerprint,
        },
        "tool": None
        if tool is None
        else {
            "native_name": tool.native_name,
            "kind": tool.kind.value,
            "call_id": tool.call_id,
            # Round-trip through JSON so the record holds JSON values only.
            "arguments": json.loads(json.dumps(dict(tool.arguments), default=str)),
        },
        "usage": None
        if usage is None
        else {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_tokens": usage.cache_creation_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "model": usage.model,
            "cache_reported": usage.cache_reported,
        },
    }


def event_from_record(record: Mapping[str, Any]) -> Event:
    """Decode a dict written by :func:`event_to_record`.

    Every field must have the type the writer gives it: a record that was
    edited or damaged into another shape is refused, not coerced into an
    event the analysis would trip over (TER-TST-011).

    Raises:
        ValueError: If the record is not a ``ter.event`` of a readable schema
            version (this one or an earlier one it extends), or names an
            unknown kind, actor or tool kind.
        KeyError, TypeError: If required fields are missing or mistyped.
    """
    if record.get("schema") not in READABLE_SCHEMA_VERSIONS:
        raise ValueError(
            f"Not a {EVENT_SCHEMA_VERSION} record: {record.get('schema')!r}"
        )
    prov = _mapping(record["provenance"], "provenance")
    tool = record.get("tool")
    usage = record.get("usage")
    timestamp = _optional_text(record.get("timestamp"), "timestamp")
    parent = _optional_text(record.get("parent_id"), "parent_id")
    lines = prov.get("lines", ())
    if not isinstance(lines, list | tuple):
        raise TypeError("provenance.lines must be a list")
    return Event(
        id=EventId(_text(record["id"], "id")),
        session_id=_text(record["session_id"], "session_id"),
        sequence=_int(record["sequence"], "sequence"),
        kind=EventKind(_text(record["kind"], "kind")),
        actor=Actor(_text(record["actor"], "actor")),
        text=_text(record["text"], "text"),
        provenance=Provenance(
            source=_text(prov["source"], "provenance.source"),
            record_id=_text(prov["record_id"], "provenance.record_id"),
            lines=tuple(_int(n, "provenance.lines") for n in lines),
            block_index=_optional_int(prov.get("block_index"), "block_index"),
            fingerprint=_optional_text(prov.get("fingerprint"), "fingerprint"),
        ),
        timestamp=datetime.fromisoformat(timestamp) if timestamp else None,
        tool=None if tool is None else _tool(_mapping(tool, "tool")),
        usage=None if usage is None else _usage(_mapping(usage, "usage")),
        parent_id=EventId(parent) if parent else None,
    )


def _tool(tool: Mapping[str, Any]) -> ToolCall:
    arguments = tool.get("arguments")
    if arguments is not None and not isinstance(arguments, Mapping):
        raise TypeError("tool.arguments must be an object")
    return ToolCall(
        native_name=_text(tool["native_name"], "tool.native_name"),
        kind=ToolKind(_text(tool["kind"], "tool.kind")),
        call_id=_optional_text(tool.get("call_id"), "tool.call_id"),
        arguments=dict(arguments or {}),
    )


def _usage(usage: Mapping[str, Any]) -> TokenUsage:
    cache_creation = _int(usage["cache_creation_tokens"], "cache_creation_tokens")
    cache_read = _int(usage["cache_read_tokens"], "cache_read_tokens")
    reported = usage.get("cache_reported")
    if reported is not None and not isinstance(reported, bool):
        raise TypeError("usage.cache_reported must be a boolean")
    return TokenUsage(
        input_tokens=_int(usage["input_tokens"], "input_tokens"),
        output_tokens=_int(usage["output_tokens"], "output_tokens"),
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        model=_optional_text(usage.get("model"), "usage.model"),
        # Records before ter.event/0.4 did not say. Non-zero cache figures
        # prove the source reported them; all-zero ones prove nothing, so the
        # turn is read as unreported and its cost stays an estimate.
        cache_reported=reported
        if reported is not None
        else bool(cache_creation or cache_read),
    )


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _optional_text(value: object, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be an integer")
    return value


def _optional_int(value: object, field: str) -> int | None:
    return None if value is None else _int(value, field)
