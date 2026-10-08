"""Stop and SubagentStop as events, and recording raw hook payloads (issue #35)."""

from __future__ import annotations

import io
import json
import os
import stat
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
        assert result.recorded_to.name == "000000-PostToolUse.json"
        saved = json.loads(result.recorded_to.read_text(encoding="utf-8"))
        assert saved == {
            "schema": RECORDING_SCHEMA,
            "received_at": T0.isoformat(),
            "payload": payload,
        }

    @pytest.mark.req("TER-OBS-010")
    def test_ignored_payloads_are_recorded_too(self, tmp_path: Path) -> None:
        result = run_hook(
            io.StringIO("{not json"), io.StringIO(), ingest(), record_to=tmp_path
        )
        assert result.status is HookStatus.IGNORED
        assert result.recorded_to == tmp_path / "_unknown" / "000000-_unknown.json"
        saved = json.loads(result.recorded_to.read_text(encoding="utf-8"))
        assert saved == {
            "schema": RECORDING_SCHEMA,
            "received_at": None,
            "raw": "{not json",
        }

    def test_each_payload_gets_the_next_sequence_number(self, tmp_path: Path) -> None:
        names = [
            record_payload(json.dumps(load(n)), tmp_path).name
            for n in ("user_prompt_submit", "pre_tool_use_read", "stop")
        ]
        assert names == [
            "000000-UserPromptSubmit.json",
            "000001-PreToolUse.json",
            "000002-Stop.json",
        ]

    def test_a_taken_sequence_number_is_skipped_not_overwritten(
        self, tmp_path: Path
    ) -> None:
        payload = load("stop")
        folder = tmp_path / payload["session_id"]
        folder.mkdir()
        (folder / "000001-Stop.json").write_text("kept", encoding="utf-8")
        # One file present: the next writer tries 000001, finds it taken.
        path = record_payload(json.dumps(payload), tmp_path)
        assert path.name == "000002-Stop.json"
        assert (folder / "000001-Stop.json").read_text(encoding="utf-8") == "kept"

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
        assert [r.sequence for r in recordings] == list(range(len(names)))
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
