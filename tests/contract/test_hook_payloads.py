"""Contract tests pinning Claude Code hook payload shapes to ``ter.event`` output.

Each fixture in ``tests/fixtures/hooks`` is an example hook input in the shape
Claude Code documents (common fields ``session_id``, ``transcript_path``,
``cwd``, ``hook_event_name`` plus per-hook fields). If Claude Code changes a
shape, or the adapter changes its translation, these tests say which.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driving.claude_hooks import HookStatus, translate
from ter.domain import Actor, EventKind, ToolKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"
COMMON = {"session_id", "transcript_path", "cwd", "hook_event_name"}


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    return data


ALL = sorted(p.stem for p in FIXTURES.glob("*.json"))

#: fixture -> (status, [(kind, actor, tool kind, native name)])
EXPECTED: dict[str, tuple[HookStatus, list[tuple[EventKind, Actor, Any, Any]]]] = {
    "session_start": (HookStatus.LIFECYCLE, []),
    "subagent_stop": (HookStatus.LIFECYCLE, []),
    "pre_compact": (HookStatus.LIFECYCLE, []),
    "stop": (HookStatus.LIFECYCLE, []),
    "session_end": (HookStatus.LIFECYCLE, []),
    "user_prompt_submit": (
        HookStatus.RECORDED,
        [(EventKind.PROMPT, Actor.USER, None, None)],
    ),
    "pre_tool_use_read": (
        HookStatus.RECORDED,
        [(EventKind.TOOL_REQUESTED, Actor.ASSISTANT, ToolKind.FS_READ, "Read")],
    ),
    "post_tool_use_read": (
        HookStatus.RECORDED,
        [
            (EventKind.TOOL_REQUESTED, Actor.ASSISTANT, ToolKind.FS_READ, "Read"),
            (EventKind.TOOL_COMPLETED, Actor.TOOL, ToolKind.FS_READ, "Read"),
        ],
    ),
    "post_tool_use_edit": (
        HookStatus.RECORDED,
        [
            (EventKind.TOOL_REQUESTED, Actor.ASSISTANT, ToolKind.FS_EDIT, "Edit"),
            (EventKind.TOOL_COMPLETED, Actor.TOOL, ToolKind.FS_EDIT, "Edit"),
        ],
    ),
    "post_tool_use_bash": (
        HookStatus.RECORDED,
        [
            (EventKind.TOOL_REQUESTED, Actor.ASSISTANT, ToolKind.EXEC_SHELL, "Bash"),
            (EventKind.TOOL_COMPLETED, Actor.TOOL, ToolKind.EXEC_SHELL, "Bash"),
        ],
    ),
    "post_tool_use_mcp": (
        HookStatus.RECORDED,
        [
            (
                EventKind.TOOL_REQUESTED,
                Actor.ASSISTANT,
                ToolKind.OTHER,
                "mcp__github__get_me",
            ),
            (
                EventKind.TOOL_COMPLETED,
                Actor.TOOL,
                ToolKind.OTHER,
                "mcp__github__get_me",
            ),
        ],
    ),
}


def test_every_fixture_has_an_expectation() -> None:
    assert set(ALL) == set(EXPECTED)


@pytest.mark.parametrize("name", ALL)
def test_fixture_carries_the_common_hook_fields(name: str) -> None:
    payload = load(name)
    assert COMMON <= set(payload)
    if payload["hook_event_name"] in ("PreToolUse", "PostToolUse"):
        assert {"tool_name", "tool_input", "tool_use_id"} <= set(payload)
    if payload["hook_event_name"] == "PostToolUse":
        assert "tool_response" in payload
    if payload["hook_event_name"] == "UserPromptSubmit":
        assert isinstance(payload["prompt"], str)


@pytest.mark.parametrize("name", ALL)
def test_fixture_translates_to_the_pinned_events(name: str) -> None:
    payload = load(name)
    status, expected = EXPECTED[name]
    result = translate(payload)
    assert result.status is status
    assert result.hook_event_name == payload["hook_event_name"]
    assert result.session_id == payload["session_id"]
    shape = [
        (
            e.kind,
            e.actor,
            e.tool.kind if e.tool else None,
            e.tool.native_name if e.tool else None,
        )
        for e in result.events
    ]
    assert shape == expected
    for event in result.events:
        assert event.session_id == payload["session_id"]
        assert event.provenance.source.endswith(Path(payload["transcript_path"]).name)


@pytest.mark.req("TER-OBS-004")
def test_pre_and_post_tool_use_share_the_request_identity() -> None:
    pre = translate(load("pre_tool_use_read")).events
    post = translate(load("post_tool_use_read")).events
    assert pre[0] == post[0]
    assert post[1].parent_id == post[0].id
    assert post[1].tool is not None and post[0].tool is not None
    assert post[1].tool.call_id == post[0].tool.call_id == "toolu_01ReadParser"


@pytest.mark.parametrize("name", ALL)
def test_translation_is_deterministic(name: str) -> None:
    assert translate(load(name)) == translate(load(name))


def test_tool_request_carries_arguments_and_canonical_text() -> None:
    event = translate(load("post_tool_use_edit")).events[0]
    assert event.tool is not None
    assert event.tool.arguments["file_path"] == "/home/dev/app/src/parser.py"
    assert json.loads(event.text) == load("post_tool_use_edit")["tool_input"]


def test_string_tool_response_is_kept_verbatim() -> None:
    completed = translate(load("post_tool_use_mcp")).events[1]
    assert completed.text == '{"login": "dev"}'
