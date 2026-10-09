"""Coverage of real Claude Code record types (TER-SRC-005, TER-SRC-024)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.claude_code.session_source import (
    METADATA_TYPES,
    record_class,
)
from ter.domain import EventKind

REFERENCE = (
    Path(__file__).parents[1] / "fixtures" / "corpus" / "reference-record-types.json"
)
TARGET = 0.99


def _write(path: Path, records: list[Any]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _user(uuid: str, text: str, ts: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "sessionId": "s1",
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def _assistant(uuid: str, text: str, ts: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": "s1",
        "timestamp": ts,
        "requestId": f"r-{uuid}",
        "message": {
            "id": f"m-{uuid}",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }


def _queued(
    uuid: str, prompt: str, origin: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    attachment = {"type": "queued_command", "prompt": prompt, "commandMode": "prompt"}
    attachment.update(origin=origin, **extra)
    return {
        "type": "attachment",
        "uuid": uuid,
        "sessionId": "s1",
        "timestamp": "2026-10-09T08:00:03Z",
        "attachment": attachment,
    }


def _metadata_records() -> list[dict[str, Any]]:
    # Shapes as Claude Code 2.1.x writes them, with placeholder values.
    return [
        {"type": "ai-title", "aiTitle": "t", "sessionId": "s1"},
        {"type": "atis-latch", "atis": "", "sessionId": "s1"},
        {"type": "last-prompt", "leafUuid": "u1", "sessionId": "s1"},
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "s1"},
        {"type": "mode", "sessionId": "s1"},
        {"type": "permission-mode", "sessionId": "s1"},
        {"type": "pr-link", "sessionId": "s1"},
        {"type": "custom-title", "sessionId": "s1"},
        {"type": "summary", "summary": "s", "leafUuid": "u1"},
        {"type": "file-history-snapshot", "messageId": "u1", "snapshot": {}},
        {"type": "file-history-delta", "messageId": "u1"},
        {"type": "cost-state", "sessionId": "s1"},
        {
            "type": "system",
            "subtype": "stop_hook_summary",
            "uuid": "y1",
            "sessionId": "s1",
        },
        {
            "type": "attachment",
            "uuid": "x1",
            "sessionId": "s1",
            "attachment": {"type": "hook_additional_context", "content": ["c"]},
        },
    ]


@pytest.mark.req("TER-SRC-005")
def test_every_documented_metadata_type_is_accounted_for(tmp_path: Path) -> None:
    records = [_user("u1", "fix the parser", "2026-10-09T08:00:00Z")]
    records += _metadata_records()
    records.append(_assistant("a1", "done", "2026-10-09T08:00:01Z"))
    trace = ClaudeCodeJsonlSource().read(_write(tmp_path / "s.jsonl", records))
    assert trace.unrecognised == ()
    assert set(trace.metadata_by_type) == METADATA_TYPES
    assert trace.coverage == 1.0
    # Metadata never becomes an event.
    assert [e.kind for e in trace.events] == [EventKind.PROMPT, EventKind.RESPONSE]


@pytest.mark.req("TER-SRC-005")
def test_an_undocumented_type_stays_unrecognised(tmp_path: Path) -> None:
    records = [
        _user("u1", "fix the parser", "2026-10-09T08:00:00Z"),
        {"type": "brand-new-record", "sessionId": "s1"},
    ]
    trace = ClaudeCodeJsonlSource().read(_write(tmp_path / "s.jsonl", records))
    assert trace.unrecognised_by_type == {"brand-new-record": 1}
    assert trace.coverage == pytest.approx(1 / 2)
    assert record_class("brand-new-record") == "unrecognised"


def _reference_sessions() -> list[dict[str, Any]]:
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert data["schema"] == "ter.corpus-record-types/1"
    return list(data["sessions"])


def _accounted_share(types: dict[str, int]) -> float:
    total = sum(types.values())
    if total == 0:
        return 1.0
    known = sum(n for t, n in types.items() if record_class(t) != "unrecognised")
    return known / total


@pytest.mark.req("TER-SRC-005")
def test_the_reference_corpus_is_at_least_99_percent_accounted_for() -> None:
    sessions = _reference_sessions()
    assert sessions, "the reference corpus is empty"
    below = {
        s["session"]: round(_accounted_share(s["types"]), 4)
        for s in sessions
        if _accounted_share(s["types"]) < TARGET
    }
    assert below == {}


@pytest.mark.req("TER-SRC-005")
def test_the_reference_corpus_holds_real_sessions_from_more_than_one_source() -> None:
    sessions = _reference_sessions()
    labels = {str(s["session"]).rsplit("-", 1)[0] for s in sessions}
    assert "ter-cloud" in labels
    assert all(s["records"] == sum(s["types"].values()) for s in sessions)


@pytest.mark.req("TER-SRC-024")
def test_a_queued_human_prompt_becomes_intent_in_file_order(tmp_path: Path) -> None:
    records = [
        _user("u1", "fix the parser", "2026-10-09T08:00:00Z"),
        _assistant("a1", "looking", "2026-10-09T08:00:01Z"),
        _queued("q1", "also cover the empty file", {"kind": "human"}),
        _assistant("a2", "covered", "2026-10-09T08:00:04Z"),
    ]
    trace = ClaudeCodeJsonlSource().read(_write(tmp_path / "s.jsonl", records))
    assert [(e.kind, e.text) for e in trace.events] == [
        (EventKind.PROMPT, "fix the parser"),
        (EventKind.RESPONSE, "looking"),
        (EventKind.PROMPT, "also cover the empty file"),
        (EventKind.RESPONSE, "covered"),
    ]
    queued = trace.events[2]
    assert queued.provenance.record_id == "q1" and queued.provenance.lines == (3,)
    assert queued.parent_id == trace.events[1].id
    assert [e.sequence for e in trace.events] == [0, 1, 2, 3]
    assert trace.coverage == 1.0


@pytest.mark.req("TER-SRC-024")
@pytest.mark.parametrize(
    ("origin", "extra"),
    [
        ({"kind": "task-notification", "producer": "session-task"}, {}),
        ({"kind": "peer", "from": "worker"}, {}),
        ({"kind": "human"}, {"isMeta": True}),
    ],
)
def test_harness_messages_in_the_queue_are_not_intent(
    tmp_path: Path, origin: dict[str, Any], extra: dict[str, Any]
) -> None:
    records = [
        _user("u1", "fix the parser", "2026-10-09T08:00:00Z"),
        _queued("q1", "<task-notification>done</task-notification>", origin, **extra),
    ]
    trace = ClaudeCodeJsonlSource().read(_write(tmp_path / "s.jsonl", records))
    assert [e.kind for e in trace.events] == [EventKind.PROMPT]
    assert trace.metadata_by_type == {"attachment": 1}


@pytest.mark.req("TER-SRC-024")
def test_a_queued_prompt_after_the_last_message_is_kept(tmp_path: Path) -> None:
    records = [
        _user("u1", "fix the parser", "2026-10-09T08:00:00Z"),
        _queued("q1", "and the docs", {"kind": "human"}),
    ]
    trace = ClaudeCodeJsonlSource().read(_write(tmp_path / "s.jsonl", records))
    assert [e.text for e in trace.events] == ["fix the parser", "and the docs"]
    # Re-reading gives the same ids (the id rule is the record uuid).
    again = ClaudeCodeJsonlSource().read(tmp_path / "s.jsonl")
    assert [e.id for e in again.events] == [e.id for e in trace.events]
