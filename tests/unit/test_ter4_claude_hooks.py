"""Unit tests for the Claude Code hooks driving adapter."""

from __future__ import annotations

import io
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.in_memory import FixedClock, InMemoryEventLog
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.claude_hooks import (
    HOOK_OUTPUT,
    HookStatus,
    handle_hook,
    run_hook,
    translate,
)
from ter.application import ObserveEvent
from ter.domain import EventKind, Signal, Signals, StreamReport
from ter.domain.events import Event

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    return data


def ingest() -> ObserveEvent:
    return ObserveEvent(RegexTokenizer(), InMemoryEventLog())


class TestTranslate:
    @pytest.mark.parametrize(
        ("payload", "reason"),
        [
            ([], "payload is list"),
            ("text", "payload is str"),
            ({}, "missing hook_event_name"),
            ({"hook_event_name": ""}, "missing hook_event_name"),
            ({"hook_event_name": "Stop"}, "missing session_id"),
            ({"hook_event_name": "Stop", "session_id": 3}, "missing session_id"),
            (
                {"hook_event_name": "Mystery", "session_id": "s"},
                "unsupported hook Mystery",
            ),
            (
                {"hook_event_name": "UserPromptSubmit", "session_id": "s"},
                "UserPromptSubmit without a prompt string",
            ),
            (
                {"hook_event_name": "PreToolUse", "session_id": "s"},
                "tool hook without a tool_name",
            ),
            (
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "s",
                    "tool_name": "Read",
                    "tool_input": ["not", "a", "dict"],
                },
                "tool_input is not an object",
            ),
        ],
    )
    def test_malformed_payloads_are_ignored_with_a_reason(
        self, payload: object, reason: str
    ) -> None:
        result = translate(payload)
        assert result.status is HookStatus.IGNORED
        assert result.reason == reason
        assert result.events == ()

    def test_missing_tool_input_and_use_id_fall_back_to_a_content_key(self) -> None:
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "s",
            "tool_name": "TodoWrite",
            "tool_input": None,
        }
        request, completed = translate(payload).events
        assert request.text == "{}"
        assert completed.text == ""
        assert request.tool is not None and completed.tool is not None
        assert request.tool.call_id is not None
        assert request.tool.call_id.startswith("hook:")
        assert completed.tool.call_id == request.tool.call_id
        assert request.provenance.source == "claude-code-hooks"
        assert translate(payload).events == (request, completed)

    def test_received_at_is_stamped_but_not_part_of_identity(self) -> None:
        early = datetime(2026, 1, 1, tzinfo=UTC)
        late = datetime(2026, 1, 2, tzinfo=UTC)
        a = translate(load("user_prompt_submit"), received_at=early).events[0]
        b = translate(load("user_prompt_submit"), received_at=late).events[0]
        assert a.timestamp == early and b.timestamp == late
        assert a.id == b.id

    def test_distinct_calls_get_distinct_ids(self) -> None:
        read = translate(load("post_tool_use_read")).events
        bash = translate(load("post_tool_use_bash")).events
        assert len({e.id for e in read + bash}) == 4


class TestHandleHook:
    def test_records_events_through_the_ingest(self) -> None:
        sink = ingest()
        result = handle_hook(json.dumps(load("post_tool_use_read")), sink)
        assert result.status is HookStatus.RECORDED
        assert result.appended == 2
        assert all(s.accepted for s in result.signals)
        report = sink.report(load("post_tool_use_read")["session_id"])
        assert report.count(EventKind.TOOL_COMPLETED) == 1
        assert report.open_requests == ()

    @pytest.mark.req("TER-OBS-004")
    def test_pre_then_post_tool_use_records_the_request_once(self) -> None:
        sink = ingest()
        handle_hook(load("pre_tool_use_read"), sink)
        result = handle_hook(load("post_tool_use_read"), sink)
        assert result.appended == 1
        assert result.signals[0].raised == (Signal.DUPLICATE_EVENT,)
        report = sink.report(load("pre_tool_use_read")["session_id"])
        assert report.count(EventKind.TOOL_REQUESTED) == 1

    @pytest.mark.req("TER-OBS-004")
    def test_the_same_hook_delivered_twice_changes_nothing(self) -> None:
        sink = ingest()
        session = load("post_tool_use_bash")["session_id"]
        handle_hook(load("post_tool_use_bash"), sink)
        before = sink.report(session)
        again = handle_hook(load("post_tool_use_bash"), sink)
        assert again.status is HookStatus.RECORDED and again.appended == 0
        assert sink.report(session) == before

    @pytest.mark.parametrize("raw", ["{not json", b"\xff\xfe", "[1, 2]", "null"])
    def test_bad_input_is_ignored_not_raised(self, raw: str | bytes) -> None:
        result = handle_hook(raw, ingest())
        assert result.status is HookStatus.IGNORED
        assert result.reason

    def test_lifecycle_hooks_never_build_the_ingest(self) -> None:
        def factory() -> ObserveEvent:
            raise AssertionError("ingest should not be built")

        result = handle_hook(load("session_start"), factory)
        assert result.status is HookStatus.LIFECYCLE
        assert result.session_id == load("session_start")["session_id"]

    def test_ingest_failures_fail_open(self) -> None:
        class Broken:
            def apply(self, event: Event) -> Signals:
                raise OSError("disk full")

            def report(self, session_id: str) -> StreamReport:
                raise NotImplementedError

        result = handle_hook(load("user_prompt_submit"), Broken())
        assert result.status is HookStatus.IGNORED
        assert result.reason == "OSError: disk full"

    def test_factory_is_called_for_content_hooks_and_clock_stamps(self) -> None:
        sink = ingest()
        clock = FixedClock(datetime(2026, 9, 29, tzinfo=UTC))
        result = handle_hook(load("user_prompt_submit"), lambda: sink, clock=clock)
        assert result.appended == 1
        assert sink.report(load("user_prompt_submit")["session_id"]).total_events == 1


class TestRunHook:
    @pytest.mark.req("TER-OBS-008")
    def test_prints_the_neutral_hook_output(self) -> None:
        out = io.StringIO()
        sink = ingest()
        result = run_hook(
            io.StringIO(json.dumps(load("user_prompt_submit"))), out, sink
        )
        assert out.getvalue() == HOOK_OUTPUT + "\n"
        assert result.status is HookStatus.RECORDED

    @pytest.mark.req("TER-OBS-008")
    def test_unreadable_stdin_fails_open(self) -> None:
        class Exploding(io.StringIO):
            def read(self, size: int | None = -1) -> str:
                raise OSError("closed")

        out = io.StringIO()
        result = run_hook(Exploding(), out, ingest())
        assert result.status is HookStatus.IGNORED
        assert out.getvalue() == HOOK_OUTPUT + "\n"


@pytest.mark.req("TER-OBS-003")
def test_post_tool_use_is_appended_within_50ms_at_p95() -> None:
    """Benchmark: PostToolUse payload to a tool.completed event in the log.

    Measures the adapter path a hook process runs per event (decode JSON,
    translate, apply to the engine, append to the log) against a session that
    already holds 2,000 events. Process start-up is outside the adapter and
    outside this bound. The bound is the requirement's; typical runs take
    well under a millisecond, which leaves CI noise a wide margin.
    """
    sink = ObserveEvent(RegexTokenizer(), InMemoryEventLog())
    template = load("post_tool_use_bash")
    session = template["session_id"]
    for n in range(1000):
        warm = dict(template, tool_use_id=f"warm-{n}")
        warm["tool_input"] = {"command": f"echo {n}"}
        handle_hook(warm, sink)
    assert sink.report(session).total_events == 2000

    samples: list[float] = []
    for n in range(300):
        payload = dict(template, tool_use_id=f"bench-{n}")
        payload["tool_input"] = {"command": f"pytest -k case_{n}"}
        raw = json.dumps(payload)
        start = time.perf_counter()
        result = handle_hook(raw, sink)
        samples.append(time.perf_counter() - start)
        assert result.appended == 2
    p95 = statistics.quantiles(samples, n=20)[-1]
    assert p95 < 0.050, f"p95 {p95 * 1000:.2f} ms"
