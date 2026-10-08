"""Stop and SubagentStop as events, and recording raw hook payloads (issue #35)."""

from __future__ import annotations

import io
import json
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.in_memory import FixedClock, InMemoryEventLog
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.claude_hooks import (
    HOOK_OUTPUT,
    RECORDING_SCHEMA,
    HookStatus,
    handle_hook,
    read_recordings,
    record_payload,
    run_hook,
    translate,
)
from ter.adapters.driving.cli import CliServices, main
from ter.application import ObserveEvent
from ter.domain import Actor, EventClass, EventKind
from ter.domain.lean import explain

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"
T0 = datetime(2026, 10, 8, 12, 0, 0, 250_000, tzinfo=UTC)


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    return data


def ingest() -> ObserveEvent:
    return ObserveEvent(RegexTokenizer(), InMemoryEventLog())


class TestStop:
    @pytest.mark.req("TER-OBS-009")
    def test_stop_appends_one_task_completed_event(self) -> None:
        sink = ingest()
        payload = load("stop")
        result = handle_hook(payload, sink, clock=FixedClock(T0))
        assert result.status is HookStatus.RECORDED
        assert result.appended == 1
        [event] = translate(payload, received_at=T0).events
        assert event.kind is EventKind.TASK_COMPLETED
        assert event.actor is Actor.SYSTEM
        assert event.session_id == payload["session_id"]
        assert event.timestamp == T0
        report = sink.report(payload["session_id"])
        assert report.count(EventKind.TASK_COMPLETED) == 1

    @pytest.mark.req("TER-OBS-009", "TER-OBS-004")
    def test_one_stop_delivered_twice_within_a_second_is_kept_once(self) -> None:
        sink = ingest()
        later = T0 + timedelta(milliseconds=500)
        first = handle_hook(load("stop"), sink, clock=FixedClock(T0))
        again = handle_hook(load("stop"), sink, clock=FixedClock(later))
        assert (first.appended, again.appended) == (1, 0)

    def test_stops_in_different_seconds_are_different_tasks(self) -> None:
        first = translate(load("stop"), received_at=T0).events[0]
        second = translate(load("stop"), received_at=T0 + timedelta(seconds=3))
        assert first.id != second.events[0].id

    def test_without_a_clock_the_id_is_deterministic(self) -> None:
        assert translate(load("stop")) == translate(load("stop"))


class TestSubagentStop:
    @pytest.mark.req("TER-OBS-006")
    def test_subagent_stop_joins_the_parent_session(self) -> None:
        payload = load("subagent_stop")
        sink = ingest()
        result = handle_hook(payload, sink, clock=FixedClock(T0))
        assert result.appended == 1
        [event] = translate(payload, received_at=T0).events
        assert event.kind is EventKind.SUBAGENT_COMPLETED
        assert event.actor is Actor.SYSTEM
        assert event.session_id == payload["session_id"]
        assert (
            sink.report(payload["session_id"]).count(EventKind.SUBAGENT_COMPLETED) == 1
        )

    @pytest.mark.req("TER-OBS-006")
    def test_a_named_subagent_is_identified_without_a_clock(self) -> None:
        payload = load("subagent_stop") | {
            "agent_id": "agent-7f3a",
            "agent_type": "Explore",
        }
        early = translate(payload, received_at=T0).events[0]
        late = translate(payload, received_at=T0 + timedelta(minutes=5)).events[0]
        other = translate(payload | {"agent_id": "agent-0001"}).events[0]
        assert early.id == late.id != other.id
        assert early.text == "Explore"
        assert early.provenance.record_id == "subagent:agent-7f3a"

    @pytest.mark.req("TER-OBS-006")
    def test_unnamed_subagents_finishing_in_one_second_are_both_kept(self) -> None:
        sink = ingest()
        later = T0 + timedelta(milliseconds=200)
        first = handle_hook(load("subagent_stop"), sink, clock=FixedClock(T0))
        second = handle_hook(load("subagent_stop"), sink, clock=FixedClock(later))
        assert (first.appended, second.appended) == (1, 1)

    @pytest.mark.parametrize("agent_id", [None, "", 42])
    def test_a_missing_or_odd_agent_id_falls_back_to_the_clock(
        self, agent_id: object
    ) -> None:
        payload = load("subagent_stop") | {"agent_id": agent_id, "agent_type": 3}
        result = translate(payload, received_at=T0)
        assert result.status is HookStatus.RECORDED
        assert result.events[0].text == ""


class TestLifecycleInAnalysis:
    def _events(self) -> list[Any]:
        prompt = translate(load("user_prompt_submit"), received_at=T0).events
        tool = translate(load("post_tool_use_read"), received_at=T0).events
        stop = translate(load("stop"), received_at=T0).events
        sub = translate(load("subagent_stop"), received_at=T0).events
        return [*prompt, *tool, *sub, *stop]

    def test_lifecycle_events_are_their_own_class_and_never_scored(self) -> None:
        sink = ingest()
        for event in self._events():
            sink.apply(event)
        report = sink.report(load("stop")["session_id"])
        assert report.count(EventClass.LIFECYCLE) == report.lifecycle_events == 2
        assert report.count(EventClass.TOOL) == 1
        assert not EventKind.TASK_COMPLETED.is_generated
        assert not EventKind.SUBAGENT_COMPLETED.is_generated

    def test_lifecycle_events_add_no_lean_steps(self) -> None:
        events = self._events()
        content = [e for e in events if not e.kind.is_lifecycle]
        tokenizer = RegexTokenizer()
        assert explain(events, tokenizer) == explain(content, tokenizer)


def counter(start: int = 0) -> Any:
    """A deterministic nanosecond clock for recording names."""
    ticks = iter(range(start, start + 10**6))
    return lambda: next(ticks)


NAME = re.compile(r"\d{20}-[A-Za-z_]+\.json")


class TestRecord:
    @pytest.mark.req("TER-OBS-010")
    def test_record_writes_the_raw_payload_before_translating(
        self, tmp_path: Path
    ) -> None:
        payload = load("post_tool_use_read")
        raw = json.dumps(payload)
        out = io.StringIO()
        result = run_hook(
            io.StringIO(raw),
            out,
            ingest(),
            clock=FixedClock(T0),
            record_to=tmp_path / "rec",
        )
        assert out.getvalue() == HOOK_OUTPUT + "\n"
        assert result.status is HookStatus.RECORDED
        assert result.recorded_to is not None
        assert result.recorded_to.parent == tmp_path / "rec" / payload["session_id"]
        assert NAME.fullmatch(result.recorded_to.name)
        assert result.recorded_to.name.endswith("-PostToolUse.json")
        saved = json.loads(result.recorded_to.read_text(encoding="utf-8"))
        assert saved == {
            "schema": RECORDING_SCHEMA,
            "received_at": T0.isoformat(),
            "raw": raw,
        }

    @pytest.mark.req("TER-OBS-010")
    def test_the_payload_is_kept_byte_for_byte(self, tmp_path: Path) -> None:
        # Duplicate keys and spacing would not survive a parse and re-dump.
        raw = '{"session_id": "s", "hook_event_name": "Stop",  "x": 1, "x": 2}\n'
        path = record_payload(raw, tmp_path)
        [recording] = read_recordings(tmp_path)
        assert recording.path == path
        assert recording.raw == raw
        assert recording.payload == {
            "session_id": "s",
            "hook_event_name": "Stop",
            "x": 2,
        }

    @pytest.mark.req("TER-OBS-010")
    def test_ignored_payloads_are_recorded_too(self, tmp_path: Path) -> None:
        result = run_hook(
            io.StringIO("{not json"), io.StringIO(), ingest(), record_to=tmp_path
        )
        assert result.status is HookStatus.IGNORED
        assert result.recorded_to is not None
        assert result.recorded_to.parent == tmp_path / "_unknown"
        assert result.recorded_to.name.endswith("-_unknown.json")
        [recording] = read_recordings(tmp_path)
        assert (recording.raw, recording.payload) == ("{not json", None)
        assert recording.received_at is None

    def test_names_sort_in_arrival_order_whatever_the_hook(
        self, tmp_path: Path
    ) -> None:
        clock = counter(10**18)
        paths = [
            record_payload(json.dumps(load(n)), tmp_path, now_ns=clock)
            for n in ("user_prompt_submit", "pre_tool_use_read", "stop")
        ]
        assert [p.name for p in paths] == [
            "01000000000000000000-UserPromptSubmit.json",
            "01000000000000000001-PreToolUse.json",
            "01000000000000000002-Stop.json",
        ]
        assert [r.path for r in read_recordings(tmp_path)] == paths

    def test_a_taken_name_is_skipped_not_overwritten(self, tmp_path: Path) -> None:
        payload = load("stop")
        first = record_payload(json.dumps(payload), tmp_path, now_ns=lambda: 7)
        second = record_payload(json.dumps(payload), tmp_path, now_ns=lambda: 7)
        assert (first.name, second.name) == (
            "00000000000000000007-Stop.json",
            "00000000000000000008-Stop.json",
        )
        assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")

    def test_concurrent_writers_never_clobber_each_other(self, tmp_path: Path) -> None:
        payloads = [load("stop") | {"n": n} for n in range(40)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(
                pool.map(
                    lambda p: record_payload(json.dumps(p), tmp_path, now_ns=lambda: 1),
                    payloads,
                )
            )
        assert len(set(paths)) == len(payloads)
        recorded = sorted(r.payload["n"] for r in read_recordings(tmp_path))  # type: ignore[index]
        assert recorded == list(range(40))

    def test_no_partial_file_is_left_behind(self, tmp_path: Path) -> None:
        record_payload(json.dumps(load("stop")), tmp_path)
        folder = tmp_path / load("stop")["session_id"]
        assert [p.name for p in folder.iterdir() if p.name.startswith(".")] == []

    @pytest.mark.parametrize(
        "session", ["../../etc", "..", ".hidden", "a/b", "x" * 300, "sp ace"]
    )
    def test_unsafe_names_never_escape_the_directory(
        self, tmp_path: Path, session: str
    ) -> None:
        raw = json.dumps({"session_id": session, "hook_event_name": "../Stop"})
        path = record_payload(raw, tmp_path)
        assert path.parent.parent == tmp_path
        assert path.parent.name.startswith("h-")

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
    def test_recordings_are_private_to_their_owner(self, tmp_path: Path) -> None:
        old = os.umask(0o022)
        try:
            path = record_payload(json.dumps(load("stop")), tmp_path / "rec")
        finally:
            os.umask(old)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "rec").stat().st_mode) == 0o700

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
    def test_existing_open_directories_are_tightened(self, tmp_path: Path) -> None:
        root = tmp_path / "rec"
        folder = root / load("stop")["session_id"]
        folder.mkdir(parents=True)
        root.chmod(0o755)
        folder.chmod(0o777)
        record_payload(json.dumps(load("stop")), root)
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700

    @pytest.mark.req("TER-OBS-010", "TER-OBS-008")
    def test_a_failed_recording_still_handles_the_payload(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("", encoding="utf-8")
        out = io.StringIO()
        result = run_hook(
            io.StringIO(json.dumps(load("stop"))), out, ingest(), record_to=blocker
        )
        assert out.getvalue() == HOOK_OUTPUT + "\n"
        assert result.status is HookStatus.RECORDED
        assert result.recorded_to is None
        assert result.record_error

    @pytest.mark.req("TER-OBS-008")
    def test_a_failing_clock_is_handled_as_without_recording(
        self, tmp_path: Path
    ) -> None:
        class Broken:
            def now(self) -> datetime:
                raise RuntimeError("no time")

        raw = json.dumps(load("stop"))
        plain = run_hook(io.StringIO(raw), io.StringIO(), ingest(), clock=Broken())
        recorded = run_hook(
            io.StringIO(raw),
            io.StringIO(),
            ingest(),
            clock=Broken(),
            record_to=tmp_path,
        )
        assert plain.status is recorded.status is HookStatus.IGNORED
        assert recorded.reason == plain.reason == "RuntimeError: no time"
        assert recorded.record_error == ""
        [recording] = read_recordings(tmp_path)
        assert recording.received_at is None

    @pytest.mark.req("TER-OBS-010")
    def test_recordings_replay_to_the_same_events(self, tmp_path: Path) -> None:
        names = sorted(p.stem for p in FIXTURES.glob("*.json"))
        clock = FixedClock(T0)
        for name in names:
            run_hook(
                io.StringIO(json.dumps(load(name))),
                io.StringIO(),
                ingest(),
                clock=clock,
                record_to=tmp_path,
            )
        # Every fixture shares one session, so the recordings come back in
        # the order they were made.
        recordings = list(read_recordings(tmp_path))
        assert len(recordings) == len(names)
        for name, recording in zip(names, recordings, strict=True):
            assert recording.payload == load(name)
            assert recording.hook_event_name == load(name)["hook_event_name"]
            assert translate(
                recording.payload, received_at=recording.received_at
            ) == translate(load(name), received_at=T0)

    @pytest.mark.req("TER-OBS-010")
    def test_a_recording_replays_to_the_id_recorded_live(self, tmp_path: Path) -> None:
        class Ticking:
            """A clock that moves a second on every reading."""

            def __init__(self) -> None:
                self.calls = 0

            def now(self) -> datetime:
                self.calls += 1
                return T0 + timedelta(seconds=self.calls)

        sink = ingest()
        run_hook(
            io.StringIO(json.dumps(load("stop"))),
            io.StringIO(),
            sink,
            clock=Ticking(),
            record_to=tmp_path,
        )
        [recording] = read_recordings(tmp_path)
        replayed = translate(recording.payload, received_at=recording.received_at)
        live = sink.report(load("stop")["session_id"]).timeline
        assert [row.event_id for row in live] == [e.id for e in replayed.events]

    def test_read_recordings_rejects_foreign_files(self, tmp_path: Path) -> None:
        (tmp_path / "s").mkdir()
        (tmp_path / "s" / "000000-Stop.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="not a ter.hook-recording/1"):
            list(read_recordings(tmp_path))


class TestCli:
    def _services(self, tmp_path: Path) -> CliServices:
        def unused(*_: object) -> Any:
            raise AssertionError("not used by the hook command")

        return CliServices(
            analyse_transcript=unused,
            log_sessions=unused,
            analyse_log=unused,
            hook_ingest=lambda _: ingest(),
            default_log_dir=tmp_path / "log",
            hook_clock=FixedClock(T0),
        )

    @pytest.mark.req("TER-OBS-010")
    def test_hook_record_option_saves_the_payload(self, tmp_path: Path) -> None:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["hook", "--record", str(tmp_path / "rec")],
            self._services(tmp_path),
            stdin=io.StringIO(json.dumps(load("stop"))),
            stdout=out,
            stderr=err,
        )
        assert code == 0
        assert out.getvalue() == HOOK_OUTPUT + "\n"
        assert err.getvalue() == ""
        [recording] = read_recordings(tmp_path / "rec")
        assert recording.payload == load("stop")

    def test_a_failed_recording_is_reported_on_stderr(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("", encoding="utf-8")
        err = io.StringIO()
        code = main(
            ["hook", "--record", str(blocker)],
            self._services(tmp_path),
            stdin=io.StringIO(json.dumps(load("stop"))),
            stdout=io.StringIO(),
            stderr=err,
        )
        assert code == 0
        assert err.getvalue().startswith("ter hook: payload not recorded: ")
