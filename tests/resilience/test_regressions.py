"""The inputs the fuzzers found, pinned so they stay fixed (TER-TST-009,
TER-TST-013). Each case crashed a reader before this suite existed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log import JsonlEventLog, event_to_record
from ter.domain import EventKind

PROMPT = {
    "type": "user",
    "uuid": "u1",
    "sessionId": "s1",
    "timestamp": "2026-01-15T10:00:00.000Z",
    "message": {"role": "user", "content": [{"type": "text", "text": "Fix it"}]},
}
CALL = {
    "type": "assistant",
    "uuid": "a1",
    "sessionId": "s1",
    "requestId": "r1",
    "timestamp": "2026-01-15T10:00:01.000Z",
    "message": {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "Read",
                "input": {"file_path": "a"},
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    },
}


def _read(tmp_path: Path, *records: Any) -> Any:
    path = tmp_path / "s1.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return ClaudeCodeJsonlSource().read(path)


def _with(record: dict[str, Any], **message: Any) -> dict[str, Any]:
    return record | {"message": record["message"] | message}


@pytest.mark.req("TER-TST-009")
def test_a_record_repeated_with_its_uuid_gives_its_events_once(tmp_path: Path) -> None:
    trace = _read(tmp_path, PROMPT, PROMPT, CALL)
    assert [e.kind for e in trace.events] == [
        EventKind.PROMPT,
        EventKind.TOOL_REQUESTED,
    ]


@pytest.mark.req("TER-TST-009")
@pytest.mark.parametrize(
    "broken",
    [
        PROMPT | {"message": None},
        PROMPT | {"message": "Fix it"},
        _with(PROMPT, role=None),
        _with(PROMPT, content=7),
        _with(PROMPT, content=[{"type": 3, "text": ["x"]}]),
        PROMPT | {"uuid": 5, "sessionId": [], "timestamp": 0},
        PROMPT | {"_source_lines": "x", "_merge_warnings": 1},
    ],
    ids=[
        "null-message",
        "text-message",
        "null-role",
        "number-content",
        "mistyped-block",
        "mistyped-ids",
        "loader-keys-in-file",
    ],
)
def test_a_record_of_the_wrong_shape_is_read_around(
    tmp_path: Path, broken: dict[str, Any]
) -> None:
    trace = _read(tmp_path, broken, CALL)
    assert EventKind.TOOL_REQUESTED in [e.kind for e in trace.events]


@pytest.mark.req("TER-TST-009")
def test_mistyped_tool_input_and_usage_are_dropped(tmp_path: Path) -> None:
    block = CALL["message"]["content"][0] | {"input": '{"file_path": "a"}'}
    call = _with(
        CALL, content=[block], usage={"input_tokens": "10", "output_tokens": 5}
    )
    [event] = _read(tmp_path, call).events
    assert event.tool is not None and dict(event.tool.arguments) == {}
    assert event.usage is not None and event.usage.input_tokens == 0
    assert event.usage.output_tokens == 5


@pytest.mark.req("TER-TST-013")
@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("tool", "call_id"), ["t1"]),
        (("text",), {"a": 1}),
        (("sequence",), "3"),
        (("sequence",), True),
        (("provenance", "lines"), "3"),
        (("usage", "input_tokens"), 1.5),
        (("tool", "arguments"), [["a", 1]]),
    ],
    ids=lambda v: ".".join(v) if isinstance(v, tuple) else None,
)
def test_an_event_record_of_the_wrong_type_is_skipped(
    tmp_path: Path, path: tuple[str, ...], value: Any
) -> None:
    trace = _read(tmp_path, CALL)
    good = trace.events[0]
    record = event_to_record(good)
    target = record
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    log = JsonlEventLog(tmp_path / "log")
    log.append(good)
    with log.path_for(good.session_id).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    reader = JsonlEventLog(tmp_path / "log")
    assert reader.events(good.session_id) == (good,)
    assert reader.skipped == 1
