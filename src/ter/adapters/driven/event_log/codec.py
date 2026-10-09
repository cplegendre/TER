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

    Raises:
        ValueError: If the record is not a ``ter.event`` of a readable schema
            version (this one or an earlier one it extends).
        KeyError, TypeError: If required fields are missing or mistyped.
    """
    if record.get("schema") not in READABLE_SCHEMA_VERSIONS:
        raise ValueError(
            f"Not a {EVENT_SCHEMA_VERSION} record: {record.get('schema')!r}"
        )
    prov = record["provenance"]
    tool = record.get("tool")
    usage = record.get("usage")
    timestamp = record.get("timestamp")
    parent = record.get("parent_id")
    return Event(
        id=EventId(str(record["id"])),
        session_id=str(record["session_id"]),
        sequence=int(record["sequence"]),
        kind=EventKind(record["kind"]),
        actor=Actor(record["actor"]),
        text=str(record["text"]),
        provenance=Provenance(
            source=str(prov["source"]),
            record_id=str(prov["record_id"]),
            lines=tuple(int(n) for n in prov.get("lines", ())),
            block_index=prov.get("block_index"),
            fingerprint=prov.get("fingerprint"),
        ),
        timestamp=datetime.fromisoformat(timestamp) if timestamp else None,
        tool=None
        if tool is None
        else ToolCall(
            native_name=str(tool["native_name"]),
            kind=ToolKind(tool["kind"]),
            call_id=tool.get("call_id"),
            arguments=dict(tool.get("arguments") or {}),
        ),
        usage=None if usage is None else _usage(usage),
        parent_id=EventId(str(parent)) if parent else None,
    )


def _usage(usage: Mapping[str, Any]) -> TokenUsage:
    cache_creation = int(usage["cache_creation_tokens"])
    cache_read = int(usage["cache_read_tokens"])
    reported = usage.get("cache_reported")
    model = usage.get("model")
    return TokenUsage(
        input_tokens=int(usage["input_tokens"]),
        output_tokens=int(usage["output_tokens"]),
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        model=None if model is None else str(model),
        # Records before ter.event/0.4 did not say. Non-zero cache figures
        # prove the source reported them; all-zero ones prove nothing, so the
        # turn is read as unreported and its cost stays an estimate.
        cache_reported=bool(reported)
        if reported is not None
        else bool(cache_creation or cache_read),
    )
