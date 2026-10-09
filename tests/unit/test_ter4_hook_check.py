"""``python -m ter hooks check``: recorded hook payloads against transcripts.

Hermetic: every recording set and transcript is built here, in the shapes of
``tests/fixtures/hooks/*.json`` and of real Claude Code transcript records
(``user``, ``assistant``, ``attachment``, ``system``/``stop_hook_summary``).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.claude_code_turns import (
    TAIL_START,
    last_turn,
    stop_event_id,
    transcript_turn,
)
from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.in_memory import FixedClock, InMemoryEventLog
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.claude_hooks import handle_hook, record_payload, translate
from ter.adapters.driving.claude_hooks.check import (
    HookCheck,
    Reason,
    check_recordings,
    find_transcripts,
    format_hook_check,
)
from ter.adapters.driving.cli import CliServices, main
from ter.application import ObserveEvent
from ter.domain import EventKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"
T0 = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
SESSION = "3f0c9a1e-hook-demo"
#: Strings that stand for private content: none may appear in a report.
PROMPT = "SECRET-PROMPT fix the duration parser"
TOOL_INPUT = "/home/dev/app/src/SECRET-INPUT.py"
TOOL_OUTPUT = "SECRET-OUTPUT def parse_duration"
ANSWER = "SECRET-ANSWER the test passes now"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    return data


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def stamp(seconds: float) -> str:
    return at(seconds).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Transcript:
    """A Claude Code transcript in the real record shapes."""

    session_id: str = SESSION
    records: list[dict[str, Any]] = field(default_factory=list)
    parent: str | None = None

    def _add(self, record: dict[str, Any]) -> str:
        uuid = record.setdefault("uuid", f"rec-{len(self.records):03d}")
        record.setdefault("parentUuid", self.parent)
        record.setdefault("sessionId", self.session_id)
        record.setdefault("isSidechain", False)
        self.records.append(record)
        self.parent = uuid
        return str(uuid)

    def prompt(self, seconds: float, text: str = PROMPT) -> str:
        return self._add(
            {
                "type": "user",
                "timestamp": stamp(seconds),
                "message": {"role": "user", "content": text},
            }
        )

    def tool_use(
        self, seconds: float, call_id: str = "toolu_01ReadParser", **extra: Any
    ) -> str:
        return self._add(
            {
                "type": "assistant",
                "timestamp": stamp(seconds),
                "requestId": f"req-{len(self.records)}",
                **extra,
                "message": {
                    "id": f"msg-{len(self.records)}",
                    "role": "assistant",
                    "model": "claude-demo",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": "Read",
                            "input": {"file_path": TOOL_INPUT},
                        }
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            }
        )

    def tool_result(self, seconds: float, call_id: str = "toolu_01ReadParser") -> str:
        return self._add(
            {
                "type": "user",
                "timestamp": stamp(seconds),
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": TOOL_OUTPUT,
                        }
                    ],
                },
                "toolUseResult": {"type": "text"},
            }
        )

    def answer(self, seconds: float) -> str:
        return self._add(
            {
                "type": "assistant",
                "timestamp": stamp(seconds),
                "requestId": f"req-{len(self.records)}",
                "message": {
                    "id": f"msg-{len(self.records)}",
                    "role": "assistant",
                    "model": "claude-demo",
                    "content": [{"type": "text", "text": ANSWER}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 12, "output_tokens": 6},
                },
            }
        )

    def hook_success(self, seconds: float) -> str:
        return self._add(
            {
                "type": "attachment",
                "timestamp": stamp(seconds),
                "attachment": {"type": "hook_success", "hookName": "Stop"},
            }
        )

    def stop_summary(self, seconds: float, **extra: Any) -> str:
        return self._add(
            {
                "type": "system",
                "subtype": "stop_hook_summary",
                "timestamp": stamp(seconds),
                "hookCount": 1,
                "hookInfos": [{"command": "python -m ter hook", "durationMs": 40}],
                "hookErrors": [],
                "preventedContinuation": False,
                "stopReason": "",
                "hasOutput": False,
                "level": "suggestion",
                "toolUseID": "a89c740a-0000-4000-8000-000000000000",
                **extra,
            }
        )

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(r) + "\n" for r in self.records), encoding="utf-8"
        )
        return path


class Recorder:
    """Writes recordings as ``ter hook --record DIR`` does."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._ns = 1_000

    def record(self, name: str, seconds: float, **fields: Any) -> Path:
        payload = fixture(name) | fields
        self._ns += 1
        ns = self._ns
        return record_payload(
            json.dumps(payload),
            self.directory,
            received_at=at(seconds),
            now_ns=lambda: ns,
        )


def projects(tmp_path: Path) -> Path:
    return tmp_path / "projects"


def transcript_file(tmp_path: Path, session: str = SESSION) -> Path:
    return projects(tmp_path) / "-home-dev-app" / f"{session}.jsonl"


def full_turn(transcript: Transcript) -> dict[str, str]:
    """A prompt, a Read, its result, an answer and the stop's records."""
    return {
        "prompt": transcript.prompt(0),
        "tool_use": transcript.tool_use(2),
        "tool_result": transcript.tool_result(3),
        "answer": transcript.answer(5),
        "attachment": transcript.hook_success(5.3),
        "summary": transcript.stop_summary(5.4),
    }


def record_full_turn(recorder: Recorder, **fields: Any) -> None:
    recorder.record("session_start", -1, **fields)
    recorder.record("user_prompt_submit", 0.5, prompt=PROMPT, **fields)
    tool = {"tool_input": {"file_path": TOOL_INPUT}}
    recorder.record("pre_tool_use_read", 2.5, **tool, **fields)
    recorder.record(
        "post_tool_use_read",
        3,
        **tool,
        tool_response={"type": "text", "file": {"content": TOOL_OUTPUT}},
        **fields,
    )
    recorder.record("stop", 5.1, **fields)


def run_check(tmp_path: Path) -> HookCheck:
    return check_recordings(
        tmp_path / "rec", projects(tmp_path), ClaudeCodeJsonlSource().read
    )


def services() -> CliServices:
    def unused(*_: object) -> Any:
        raise AssertionError("not used by hooks check")

    return CliServices(
        analyse_transcript=unused,
        log_sessions=unused,
        analyse_log=unused,
        hook_ingest=unused,
        default_log_dir=Path("unused"),
        hooks_check=lambda r, t: check_recordings(r, t, ClaudeCodeJsonlSource().read),
    )


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), services(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


# --- the shared stop id rule (TER-OBS-005's mechanism) -------------------------


class TestStopRule:
    def test_session_source_derives_one_task_completed_per_stop_summary(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        ids = full_turn(transcript)
        trace = ClaudeCodeJsonlSource().read(transcript.write(tmp_path / "t.jsonl"))
        [stop] = [e for e in trace.events if e.kind is EventKind.TASK_COMPLETED]
        assert stop.id == stop_event_id(SESSION, ids["answer"])
        assert stop.provenance.record_id == ids["summary"]
        assert stop.timestamp == at(5.4)
        # In order, after the answer, with the sequence and parent chain intact.
        assert trace.events[-1] is stop
        assert [e.sequence for e in trace.events] == list(range(len(trace.events)))
        assert stop.parent_id == trace.events[-2].id

    def test_two_summaries_for_one_turn_are_one_stop(self, tmp_path: Path) -> None:
        transcript = Transcript()
        full_turn(transcript)
        transcript.stop_summary(5.6)
        trace = ClaudeCodeJsonlSource().read(transcript.write(tmp_path / "t.jsonl"))
        assert sum(e.kind is EventKind.TASK_COMPLETED for e in trace.events) == 1

    def test_a_sidechain_turn_is_not_the_turn_a_stop_closes(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        transcript.prompt(0)
        main_turn = transcript.answer(1)
        transcript.tool_use(2, call_id="toolu_side", isSidechain=True)
        transcript.stop_summary(3)
        trace = ClaudeCodeJsonlSource().read(transcript.write(tmp_path / "t.jsonl"))
        [stop] = [e for e in trace.events if e.kind is EventKind.TASK_COMPLETED]
        assert stop.id == stop_event_id(SESSION, main_turn)

    def test_a_transcript_without_summaries_derives_no_stop(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        transcript.stop_summary(0)  # before any turn: closes nothing
        transcript.prompt(1)
        transcript.answer(2)
        trace = ClaudeCodeJsonlSource().read(transcript.write(tmp_path / "t.jsonl"))
        assert not any(e.kind is EventKind.TASK_COMPLETED for e in trace.events)

    def test_the_live_stop_hook_uses_the_same_id(self, tmp_path: Path) -> None:
        transcript = Transcript()
        ids = full_turn(transcript)
        path = transcript.write(tmp_path / "t.jsonl")
        payload = fixture("stop") | {"transcript_path": str(path)}
        log = InMemoryEventLog()
        result = handle_hook(
            payload, ObserveEvent(RegexTokenizer(), log), clock=FixedClock(at(5.1))
        )
        assert result.appended == 1
        [event] = log.events(SESSION)
        assert event.id == stop_event_id(SESSION, ids["answer"])
        source = ClaudeCodeJsonlSource().read(path)
        assert event.id in {e.id for e in source.events}

    def test_turns_written_after_the_stop_arrived_are_not_its_turn(self) -> None:
        records = [
            {"type": "assistant", "uuid": "a1", "timestamp": stamp(1)},
            {"type": "assistant", "uuid": "a2", "timestamp": stamp(9)},
        ]
        assert last_turn(records, at(5)) == "a1"
        assert last_turn(records, None) == "a2"
        assert last_turn(records, at(5).replace(tzinfo=None)) == "a1"
        assert last_turn([], at(5)) is None

    def test_the_tail_reader_reaches_past_its_first_window(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "long.jsonl"
        filler = json.dumps({"type": "attachment", "pad": "x" * 1000}) + "\n"
        turn = json.dumps({"type": "assistant", "uuid": "a1", "timestamp": stamp(1)})
        path.write_text(
            turn + "\n" + filler * (2 * TAIL_START // len(filler)), encoding="utf-8"
        )
        assert transcript_turn(path, at(5)) == "a1"
        assert transcript_turn(tmp_path / "missing.jsonl", at(5)) is None

    def test_without_a_turn_the_stop_keeps_the_receive_time_key(self) -> None:
        keyed = translate(fixture("stop"), received_at=T0, turn="a1").events[0]
        unkeyed = translate(fixture("stop"), received_at=T0).events[0]
        assert keyed.id == stop_event_id(SESSION, "a1") != unkeyed.id


# --- the check (TER-OBS-012) ----------------------------------------------------


@pytest.mark.req("TER-OBS-012")
class TestHookCheck:
    def test_a_stop_only_session_matches_in_full(self, tmp_path: Path) -> None:
        transcript = Transcript()
        transcript.prompt(0)
        transcript.answer(1)
        transcript.stop_summary(1.2)
        transcript.write(transcript_file(tmp_path))
        recorder = Recorder(tmp_path / "rec")
        recorder.record("session_start", -1)
        recorder.record("stop", 1.1)
        recorder.record("session_end", 2)

        check = run_check(tmp_path)

        [session] = check.sessions
        assert session.transcript == "transcripts-dir"
        assert (session.matched, session.total, session.share) == (1, 1, 1.0)
        [stop] = session.stops
        assert stop.matched and stop.hook_id == stop.source_id
        assert dict(session.payloads) == {"SessionEnd": 1, "SessionStart": 1, "Stop": 1}
        assert check.share == 1.0

    def test_a_full_turn_reports_matches_and_why_the_rest_differ(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        full_turn(transcript)
        path = transcript.write(transcript_file(tmp_path))
        record_full_turn(Recorder(tmp_path / "rec"), transcript_path=str(path))

        [session] = run_check(tmp_path).sessions

        assert session.transcript == "transcript_path"
        by_kind = {c.kind: c for c in session.correlation}
        assert set(by_kind) == {
            "intent.stated",
            "tool.requested",
            "tool.completed",
            "task.completed",
        }
        assert len(by_kind["task.completed"].matched) == 1
        # Tool and prompt ids follow different rules on the two sides today:
        # the check finds the same record and says the id rule differs.
        assert dict(by_kind["tool.requested"].reasons) == {Reason.SAME_TOOL_CALL: 1}
        assert dict(by_kind["tool.completed"].reasons) == {Reason.SAME_TOOL_CALL: 1}
        assert dict(by_kind["intent.stated"].reasons) == {Reason.SAME_PROMPT: 1}
        assert (session.matched, session.total) == (1, 4)
        # PreToolUse and PostToolUse requests are one event.
        assert dict(session.hook_events)["tool.requested"] == 1
        assert dict(session.source_events)["task.completed"] == 1
        assert session.stops[0].matched
        assert "subagent.completed" in " ".join(session.notes)

    def test_payload_field_names_are_reported_never_values(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        full_turn(transcript)
        transcript.write(transcript_file(tmp_path))
        record_full_turn(Recorder(tmp_path / "rec"))

        check = run_check(tmp_path)
        fields = dict(check.sessions[0].fields)
        assert "tool_use_id" in fields["PostToolUse"]
        assert "prompt" in fields["UserPromptSubmit"]
        rendered = format_hook_check(check) + json.dumps(check.to_dict())
        for private in (PROMPT, TOOL_INPUT, TOOL_OUTPUT, ANSWER, "/home/dev/app"):
            assert private not in rendered

    def test_a_stop_whose_turn_the_transcript_did_not_close_is_a_mismatch(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        full_turn(transcript)
        transcript.write(transcript_file(tmp_path))
        recorder = Recorder(tmp_path / "rec")
        # Received before the answer was written: the hook saw the tool turn.
        recorder.record("stop", 2.5)

        [session] = run_check(tmp_path).sessions
        [stop] = session.stops
        assert not stop.matched
        assert stop.reason == Reason.STOP_NOT_RECORDED
        assert stop.source_id is not None and stop.source_id != stop.hook_id
        assert session.share == 0.0

    def test_a_missing_transcript_is_reported_per_session(self, tmp_path: Path) -> None:
        projects(tmp_path).mkdir()
        record_full_turn(Recorder(tmp_path / "rec"))

        [session] = run_check(tmp_path).sessions
        assert session.transcript == "missing"
        assert session.transcript_error == Reason.NO_TRANSCRIPT
        assert session.matched == 0 and session.total == 4
        assert {r for c in session.correlation for r, _ in c.reasons} == {
            Reason.NO_TRANSCRIPT
        }
        assert session.stops[0].reason == Reason.NO_TRANSCRIPT

    def test_a_transcript_without_stop_records_says_so(self, tmp_path: Path) -> None:
        transcript = Transcript()
        transcript.prompt(0)
        transcript.answer(1)
        transcript.write(transcript_file(tmp_path))
        recorder = Recorder(tmp_path / "rec")
        recorder.record("stop", 1.1)
        recorder.record("subagent_stop", 1.0)

        [session] = run_check(tmp_path).sessions
        by_kind = {c.kind: dict(c.reasons) for c in session.correlation}
        assert by_kind["task.completed"] == {Reason.STOP_NOT_RECORDED: 1}
        assert by_kind["subagent.completed"] == {Reason.KIND_NOT_DERIVED: 1}
        notes = " ".join(session.notes)
        assert "no task.completed" in notes and "no subagent.completed" in notes

    def test_empty_recordings_check_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "rec").mkdir()
        projects(tmp_path).mkdir()
        check = run_check(tmp_path)
        assert check.sessions == () and check.share is None
        assert "0 session(s)" in format_hook_check(check)

    def test_a_lifecycle_only_session_has_no_events_to_match(
        self, tmp_path: Path
    ) -> None:
        projects(tmp_path).mkdir()
        recorder = Recorder(tmp_path / "rec")
        recorder.record("session_start", 0)
        recorder.record("pre_compact", 1)
        recorder.record("session_end", 2)

        [session] = run_check(tmp_path).sessions
        assert session.total == 0 and session.share is None
        assert session.correlation == () and session.stops == ()
        assert dict(session.payloads) == {
            "PreCompact": 1,
            "SessionEnd": 1,
            "SessionStart": 1,
        }

    def test_invalid_and_unsupported_payloads_are_counted_as_ignored(
        self, tmp_path: Path
    ) -> None:
        projects(tmp_path).mkdir()
        directory = tmp_path / "rec"
        record_payload("{not json " + PROMPT, directory, received_at=T0)
        Recorder(directory).record("stop", 1, hook_event_name="Mystery")
        check = run_check(tmp_path)
        ignored = {k: n for s in check.sessions for k, n in s.ignored}
        assert ignored == {"invalid JSON": 1, "unsupported hook Mystery": 1}
        assert PROMPT not in format_hook_check(check)

    def test_transcripts_are_found_by_session_id_and_the_shallowest_wins(
        self, tmp_path: Path
    ) -> None:
        main_file = Transcript().write(transcript_file(tmp_path))
        Transcript().write(
            projects(tmp_path) / "-home-dev-app" / "deep" / "x" / f"{SESSION}.jsonl"
        )
        assert find_transcripts(projects(tmp_path))[SESSION] == main_file
        assert find_transcripts(main_file) == {SESSION: main_file}


@pytest.mark.req("TER-OBS-012")
class TestHooksCheckCommand:
    def _session(self, tmp_path: Path) -> None:
        transcript = Transcript()
        full_turn(transcript)
        transcript.write(transcript_file(tmp_path))
        record_full_turn(Recorder(tmp_path / "rec"))

    def test_text_and_json_reports(self, tmp_path: Path) -> None:
        self._session(tmp_path)
        out_json = tmp_path / "out" / "check.json"
        code, out, err = cli(
            "hooks",
            "check",
            str(tmp_path / "rec"),
            str(projects(tmp_path)),
            "--json",
            str(out_json),
        )
        assert (code, err) == (0, "")
        assert "1/4 (25.0%)" in out and "1/1 (100.0%)" in out
        assert Reason.SAME_TOOL_CALL in out
        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert data["schema"] == "ter.hook-check/1"
        assert data["stops"] == {"total": 1, "matched": 1}
        [session] = data["by_session"]
        assert session["by_kind"]["task.completed"]["matched"] == 1
        for private in (PROMPT, TOOL_INPUT, TOOL_OUTPUT, ANSWER):
            assert private not in out + out_json.read_text(encoding="utf-8")

    @pytest.mark.parametrize("missing", ["rec", "projects"])
    def test_unreadable_input_exits_2(self, tmp_path: Path, missing: str) -> None:
        self._session(tmp_path)
        args = {"rec": tmp_path / "rec", "projects": projects(tmp_path)}
        args[missing] = tmp_path / "nowhere"
        code, out, err = cli("hooks", "check", *map(str, args.values()))
        assert code == 2 and out == "" and "ter hooks check" in err

    def test_a_corrupt_recording_exits_2_without_quoting_it(
        self, tmp_path: Path
    ) -> None:
        self._session(tmp_path)
        bad = tmp_path / "rec" / SESSION / "00000000000000000001-Stop.json"
        bad.write_text(PROMPT, encoding="utf-8")
        code, out, err = cli(
            "hooks", "check", str(tmp_path / "rec"), str(projects(tmp_path))
        )
        assert code == 2 and PROMPT not in out + err

    def test_a_missing_transcript_still_exits_0(self, tmp_path: Path) -> None:
        record_full_turn(Recorder(tmp_path / "rec"))
        projects(tmp_path).mkdir()
        code, out, _ = cli(
            "hooks", "check", str(tmp_path / "rec"), str(projects(tmp_path))
        )
        assert code == 0 and Reason.NO_TRANSCRIPT in out
