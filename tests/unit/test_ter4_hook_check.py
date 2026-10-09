"""``python -m ter hooks check``: recorded hook payloads against transcripts.

Hermetic: every recording set and transcript is built here, in the shapes of
``tests/fixtures/hooks/*.json`` and of real Claude Code transcript records
(``user``, ``assistant``, ``attachment``, ``system``/``stop_hook_summary``).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.claude_code_ids import (
    prompt_event_id,
    record_event_id,
    subagent_event_id,
    tool_event_id,
)
from ter.adapters.claude_code_turns import (
    PROMPT_TAIL,
    TAIL_START,
    PromptRecord,
    last_turn,
    prompt_record,
    stop_event_id,
    transcript_prompt,
    transcript_turn,
)
from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.claude_code.redaction import RedactionPolicy, Redactor
from ter.adapters.driven.in_memory import FixedClock, InMemoryEventLog
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.claude_hooks import (
    derive,
    handle_hook,
    record_payload,
    run_hook,
    translate,
)
from ter.adapters.driving.claude_hooks.check import (
    HookCheck,
    Reason,
    check_recordings,
    find_transcripts,
    format_hook_check,
)
from ter.adapters.driving.cli import CliServices, main
from ter.application import ObserveEvent
from ter.domain import EventKind, SessionTrace

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

    def prompt_blocks(self, seconds: float, *blocks: Any, **extra: Any) -> str:
        """A user record whose content is a list of blocks (an image, a text)."""
        return self._add(
            {
                "type": "user",
                "timestamp": stamp(seconds),
                **extra,
                "message": {"role": "user", "content": list(blocks)},
            }
        )

    def queued(self, seconds: float, text: str = PROMPT) -> str:
        """A prompt typed while the agent worked (TER-SRC-024)."""
        return self._add(
            {
                "type": "attachment",
                "timestamp": stamp(seconds),
                "attachment": {
                    "type": "queued_command",
                    "commandMode": "prompt",
                    "prompt": text,
                    "origin": {"kind": "human"},
                },
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

    def test_a_full_turn_reports_every_kind_matched(self, tmp_path: Path) -> None:
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
        # One id rule per kind on both sides: every hook event matches.
        assert all(not c.unmatched and c.source_only == 0 for c in by_kind.values())
        assert (session.matched, session.total) == (4, 4)
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
        # The fixture SubagentStop names no agent: it cannot correlate.
        assert by_kind["subagent.completed"] == {Reason.SUBAGENT_UNKEYED: 1}
        notes = " ".join(session.notes)
        assert "no task.completed" in notes and "no subagent.completed" in notes
        assert "no <session>/subagents folder" in notes

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
        assert "4/4 (100.0%)" in out and "1/1 (100.0%)" in out
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


# --- one id rule per kind on both sides (TER-OBS-007, TER-OBS-005) -------------
#
# These tests prove the mechanism on synthetic transcripts. Both requirements
# stay planned until `python -m ter hooks check` shows it on real recordings
# (issue #35): Claude Code 2.1 appears to write a prompt's record only after
# the UserPromptSubmit hooks ran, which the fallback cases below cover.


def read_source(path: Path) -> SessionTrace:
    return ClaudeCodeJsonlSource().read(path)


class LiveSession:
    """Drives the live hook as Claude Code would while the transcript grows.

    Before each hook the transcript file holds only the records written so
    far; each payload is recorded (``ter hook --record``) and applied to an
    event log.
    """

    def __init__(self, tmp_path: Path, transcript: Transcript) -> None:
        self.transcript = transcript
        self.path = transcript_file(tmp_path)
        self.recordings = tmp_path / "rec"
        self.log = InMemoryEventLog()

    def written(self, upto: int) -> None:
        Transcript(records=self.transcript.records[:upto]).write(self.path)

    def hook(
        self, name: str, seconds: float, upto: int | None = None, **fields: Any
    ) -> None:
        if upto is not None:
            self.written(upto)
        payload = fixture(name) | {"transcript_path": str(self.path)} | fields
        out = io.StringIO()
        result = run_hook(
            io.StringIO(json.dumps(payload)),
            out,
            ObserveEvent(RegexTokenizer(), self.log),
            clock=FixedClock(at(seconds)),
            record_to=self.recordings,
        )
        assert out.getvalue() == "{}\n" and result.record_error == ""

    def finish(self) -> SessionTrace:
        self.written(len(self.transcript.records))
        return read_source(self.path)


def live_turn(tmp_path: Path, *, prompt_first: bool = True) -> LiveSession:
    """Prompt, PreToolUse, PostToolUse and Stop through the live hook.

    With ``prompt_first`` the prompt's record (stamped 0) is in the transcript
    when UserPromptSubmit fires; without it the hook fires first and the
    record is written after, as Claude Code 2.1 appears to do.
    """
    transcript = Transcript()
    full_turn(transcript)
    live = LiveSession(tmp_path, transcript)
    tool = {"tool_input": {"file_path": TOOL_INPUT}}
    live.hook("session_start", -1, upto=0)
    if prompt_first:
        live.hook("user_prompt_submit", 0.5, upto=1, prompt=PROMPT)
    else:
        live.hook("user_prompt_submit", -0.5, upto=0, prompt=PROMPT)
    live.hook("pre_tool_use_read", 2.5, upto=2, **tool)
    live.hook(
        "post_tool_use_read",
        3.5,
        upto=3,
        **tool,
        tool_response={"type": "text", "file": {"content": TOOL_OUTPUT}},
    )
    live.hook("stop", 5.1, upto=5)
    return live


@pytest.mark.req("TER-OBS-007", "TER-OBS-005")
class TestSharedIdRules:
    def test_a_live_turn_matches_its_transcript_by_id_in_full(
        self, tmp_path: Path
    ) -> None:
        live = live_turn(tmp_path)
        source = live.finish()

        hook_events = live.log.events(SESSION)
        assert sorted(e.kind.value for e in hook_events) == [
            "intent.stated",
            "task.completed",
            "tool.completed",
            "tool.requested",  # PreToolUse and PostToolUse: one request
        ]
        by_id = {e.id: e for e in source.events}
        for event in hook_events:
            assert by_id[event.id].kind is event.kind
        # Every source event of a kind the hooks observe has its hook event.
        observed = {e.kind for e in hook_events}
        assert {e.id for e in source.events if e.kind in observed} == {
            e.id for e in hook_events
        }

        # The recordings replay to the same verdict.
        check = check_recordings(live.recordings, projects(tmp_path), read_source)
        [session] = check.sessions
        assert (session.matched, session.total, session.share) == (4, 4, 1.0)
        assert [s.matched for s in session.stops] == [True]

    def test_tool_events_are_keyed_by_session_tool_use_id_and_kind(
        self, tmp_path: Path
    ) -> None:
        live = live_turn(tmp_path)
        source = live.finish()
        call = "toolu_01ReadParser"
        for kind in (EventKind.TOOL_REQUESTED, EventKind.TOOL_COMPLETED):
            [hook] = [e for e in live.log.events(SESSION) if e.kind is kind]
            [recorded] = [e for e in source.events if e.kind is kind]
            assert hook.id == recorded.id == tool_event_id(SESSION, call, kind)
        pre = translate(fixture("pre_tool_use_read")).events
        post = translate(fixture("post_tool_use_read")).events
        assert pre[0].id == post[0].id  # one request, de-duplicated (TER-OBS-004)
        assert post[1].parent_id == post[0].id

    def test_a_prompt_written_after_the_hook_ran_stays_unkeyed(
        self, tmp_path: Path
    ) -> None:
        live = live_turn(tmp_path, prompt_first=False)
        source = live.finish()
        [prompt] = [e for e in live.log.events(SESSION) if e.kind is EventKind.PROMPT]
        assert prompt.provenance.record_id.startswith("prompt:")  # the old key
        assert prompt.id not in {e.id for e in source.events}

        [session] = check_recordings(
            live.recordings, projects(tmp_path), read_source
        ).sessions
        by_kind = {c.kind: c for c in session.correlation}
        assert dict(by_kind["intent.stated"].reasons) == {
            Reason.PROMPT_WRITTEN_LATER: 1
        }
        assert (session.matched, session.total) == (3, 4)

    def test_without_transcript_path_prompts_and_stops_fall_back(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        full_turn(transcript)
        transcript.write(transcript_file(tmp_path))
        record_full_turn(Recorder(tmp_path / "rec"), transcript_path=None)

        [session] = run_check(tmp_path).sessions
        assert session.transcript == "transcripts-dir"
        by_kind = {c.kind: c for c in session.correlation}
        # Tool ids need no transcript; the prompt and the stop do. The check
        # finds the transcript by session id and says why they differ.
        assert len(by_kind["tool.requested"].matched) == 1
        assert len(by_kind["tool.completed"].matched) == 1
        assert dict(by_kind["intent.stated"].reasons) == {
            Reason.PROMPT_WRITTEN_LATER: 1
        }
        assert dict(by_kind["task.completed"].reasons) == {Reason.STOP_UNKEYED: 1}
        unrooted = {"transcript_path": None}
        prompt = derive(fixture("user_prompt_submit") | unrooted, T0).events[0]
        assert prompt.provenance.record_id.startswith("prompt:")
        stop = derive(fixture("stop") | unrooted, T0).events[0]
        assert stop.provenance.record_id.startswith("stop:")
        assert not stop.provenance.record_id.startswith("stop:turn:")

    def test_a_tool_block_without_an_id_keeps_the_record_rule(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        transcript.prompt(0)
        use = transcript.tool_use(1)
        transcript.records[-1]["message"]["content"][0].pop("id")
        trace = read_source(transcript.write(tmp_path / "t.jsonl"))
        [request] = [e for e in trace.events if e.kind is EventKind.TOOL_REQUESTED]
        assert request.id == record_event_id(SESSION, use, 0, EventKind.TOOL_REQUESTED)

    def test_a_repeated_tool_use_id_falls_back_so_ids_stay_unique(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        transcript.tool_use(1)
        copy = transcript.tool_use(2)  # the same tool_use_id, as a copied record
        trace = read_source(transcript.write(tmp_path / "t.jsonl"))
        kind = EventKind.TOOL_REQUESTED
        ids = [e.id for e in trace.events if e.kind is kind]
        assert ids == [
            tool_event_id(SESSION, "toolu_01ReadParser", kind),
            record_event_id(SESSION, copy, 0, kind),
        ]

    def test_a_hook_tool_call_without_tool_use_id_is_reported_unkeyed(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        full_turn(transcript)
        path = transcript.write(transcript_file(tmp_path))
        recorder = Recorder(tmp_path / "rec")
        recorder.record(
            "pre_tool_use_read",
            2.5,
            tool_input={"file_path": TOOL_INPUT},
            tool_use_id=None,
            transcript_path=str(path),
        )
        [session] = run_check(tmp_path).sessions
        [requested] = session.correlation
        assert dict(requested.reasons) == {Reason.TOOL_UNKEYED: 1}

    def test_a_queued_prompt_is_keyed_by_its_attachment(self, tmp_path: Path) -> None:
        transcript = Transcript()
        transcript.prompt(0, "first")
        transcript.tool_use(1)
        queued = transcript.queued(1.5, PROMPT)
        path = transcript.write(tmp_path / "t.jsonl")
        payload = fixture("user_prompt_submit") | {
            "prompt": PROMPT,
            "transcript_path": str(path),
        }
        [event] = derive(payload, at(2)).events
        assert event.id == prompt_event_id(SESSION, queued)
        source = read_source(path)
        [recorded] = [e for e in source.events if e.provenance.record_id == queued]
        assert recorded.kind is EventKind.PROMPT and recorded.id == event.id

    def test_an_attachment_written_after_a_later_record_is_not_seen_early(
        self,
    ) -> None:
        # Stamped when queued (1.5) but written after a record stamped 3: a
        # hook received at 2 could not have read it.
        records: list[dict[str, Any]] = [
            {"type": "assistant", "uuid": "a1", "timestamp": stamp(1)},
            {"type": "user", "uuid": "u1", "timestamp": stamp(3), "message": {}},
            {
                "type": "attachment",
                "uuid": "q1",
                "timestamp": stamp(1.5),
                "attachment": {
                    "type": "queued_command",
                    "commandMode": "prompt",
                    "prompt": PROMPT,
                },
            },
        ]
        assert prompt_record(records, PROMPT, at(2)) is None
        assert prompt_record(records, PROMPT, at(3)) == PromptRecord("q1", 0)
        assert prompt_record(records, PROMPT, None) == PromptRecord("q1", 0)

    def test_a_prompt_beside_an_image_keeps_its_block_index(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        image = {"type": "image", "source": {"type": "base64", "data": "AAAA"}}
        uuid = transcript.prompt_blocks(0, image, {"type": "text", "text": PROMPT})
        path = transcript.write(tmp_path / "t.jsonl")
        payload = fixture("user_prompt_submit") | {
            "prompt": PROMPT,
            "transcript_path": str(path),
        }
        [event] = derive(payload, at(1)).events
        assert event.id == prompt_event_id(SESSION, uuid, 1)
        assert event.id in {e.id for e in read_source(path).events}

    def test_the_same_prompt_twice_keys_each_submission_to_its_record(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        first = transcript.prompt(0, "continue")
        transcript.answer(1)
        second = transcript.prompt(5, "continue")
        transcript.prompt_blocks(
            6, {"type": "text", "text": "side note"}, isSidechain=True
        )
        path = transcript.write(tmp_path / "t.jsonl")
        assert transcript_prompt(path, "continue", at(2)) == PromptRecord(first, 0)
        assert transcript_prompt(path, "continue", at(5)) == PromptRecord(second, 0)
        assert transcript_prompt(path, " continue\n", None) == PromptRecord(second, 0)
        assert transcript_prompt(path, "side note", None) is None  # a sidechain
        assert transcript_prompt(path, "never typed", None) is None

    def test_the_prompt_lookup_reads_a_bounded_tail(self, tmp_path: Path) -> None:
        path = tmp_path / "long.jsonl"
        early = json.dumps(
            {"type": "user", "uuid": "u1", "message": {"content": PROMPT}}
        )
        filler = json.dumps({"type": "attachment", "pad": "x" * 1000}) + "\n"
        path.write_text(
            early + "\n" + filler * (PROMPT_TAIL // len(filler) + 1), encoding="utf-8"
        )
        assert transcript_prompt(path, PROMPT, None) is None  # beyond the tail
        path.write_text(early + "\n" + filler * 10, encoding="utf-8")
        assert transcript_prompt(path, PROMPT, None) == PromptRecord("u1", 0)
        assert transcript_prompt(tmp_path / "missing.jsonl", PROMPT, None) is None

    def test_a_failing_lookup_leaves_the_hook_open(self) -> None:
        def broken(*_: object) -> None:
            raise RuntimeError("disk gone")

        [prompt] = derive(fixture("user_prompt_submit"), T0, prompts=broken).events
        assert prompt.provenance.record_id.startswith("prompt:")
        [stop] = derive(fixture("stop"), T0, turns=broken).events
        assert not stop.provenance.record_id.startswith("stop:turn:")


# --- subagents: SubagentStop against the subagent's transcript (TER-OBS-007) ---

AGENT = "a1b2c3d4e5f6a7b8c"


def subagent_file(tmp_path: Path, agent: str = AGENT) -> Path:
    """Where Claude Code writes a subagent's transcript."""
    folder = transcript_file(tmp_path).with_suffix("") / "subagents"
    return folder / f"agent-{agent}.jsonl"


def subagent_run(
    tmp_path: Path,
    agent: str = AGENT,
    *,
    finished: bool = True,
    agent_type: str | None = "general-purpose",
) -> Path:
    """A subagent's own transcript: an instruction, a Read, and (when
    ``finished``) an answer that ends its turn. Unfinished, it stops after
    the tool result, as a subagent still running when copied does."""
    run = Transcript()
    run.prompt(10, "SECRET-INSTRUCTION read the parser")
    run.tool_use(11, call_id="toolu_sub_read")
    run.tool_result(12, call_id="toolu_sub_read")
    if finished:
        run.answer(14)
    for record in run.records:
        record["agentId"] = agent
        record["isSidechain"] = True
    path = run.write(subagent_file(tmp_path, agent))
    if agent_type is not None:
        path.with_name(f"agent-{agent}.meta.json").write_text(
            json.dumps({"agentType": agent_type, "description": "SECRET-DESC"}),
            encoding="utf-8",
        )
    return path


def notification(agent: str, status: str = "completed") -> dict[str, Any]:
    """A background agent's notification, as Claude Code 2.1 queues it."""
    return {
        "type": "queued_command",
        "commandMode": "task-notification",
        "prompt": (
            "<task-notification>\n"
            f"<task-id>{agent}</task-id>\n<tool-use-id>toolu_agent</tool-use-id>\n"
            f"<status>{status}</status>\n<summary>SECRET-SUMMARY</summary>\n"
            "</task-notification>"
        ),
        "origin": {"kind": "task-notification"},
    }


def parent_with_agent(
    transcript: Transcript,
    *,
    notify: tuple[float, ...] = (),
    status: str = "completed",
) -> None:
    """A parent turn that starts a subagent (an Agent call) and answers after it.

    ``notify`` adds a background agent's notification at each time.
    """
    transcript.prompt(0)
    transcript.tool_use(2, call_id="toolu_agent")
    transcript.tool_result(3, call_id="toolu_agent")
    for seconds in notify:
        transcript._add(
            {
                "type": "attachment",
                "timestamp": stamp(seconds),
                "attachment": notification(AGENT, status),
            }
        )
    transcript.answer(20)


def subagents_of(trace: SessionTrace) -> list[Any]:
    return [e for e in trace.events if e.kind is EventKind.SUBAGENT_COMPLETED]


def subagent_stop_id(agent: str, seconds: float = 15) -> str:
    payload = fixture("subagent_stop") | {
        "agent_id": agent,
        "agent_type": "general-purpose",
    }
    [event] = translate(payload, received_at=at(seconds)).events
    return event.id


@pytest.mark.req("TER-OBS-007")
class TestSubagentRule:
    def test_a_finished_subagent_gets_the_subagent_stop_hooks_id(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        parent_with_agent(transcript)
        path = transcript.write(transcript_file(tmp_path))
        bare = read_source(transcript.write(tmp_path / "bare.jsonl"))
        subagent_run(tmp_path)

        trace = read_source(path)
        [sub] = subagents_of(trace)
        assert sub.id == subagent_event_id(SESSION, AGENT) == subagent_stop_id(AGENT)
        assert sub.text == "general-purpose"  # as SubagentStop's agent_type
        # The final-turn marker: the subagent's answer, cited in its own file.
        assert sub.timestamp == at(14)
        assert sub.provenance.source == f"subagents/agent-{AGENT}.jsonl"
        assert sub.provenance.lines == (4,)
        # Placed by time: after the parent's tool result, before its answer;
        # every other event is as without the subagent.
        assert trace.events[-2] is sub
        others = [e.id for e in trace.events if e is not sub]
        assert others == [e.id for e in bare.events]
        assert [e.sequence for e in trace.events] == list(range(len(trace.events)))
        assert trace.events[-1].parent_id == sub.id
        assert sub.parent_id == trace.events[-3].id

    def test_a_session_without_a_subagents_folder_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        parent_with_agent(transcript, notify=(15,))
        path = transcript.write(transcript_file(tmp_path))
        before = read_source(path)
        assert subagents_of(before) == []
        # An empty folder, or another session's subagents, change nothing.
        subagent_file(tmp_path).parent.mkdir(parents=True)
        other = transcript_file(tmp_path, "other-session").with_suffix("")
        (other / "subagents").mkdir(parents=True)
        run = subagent_run(tmp_path)
        run.rename(other / "subagents" / run.name)
        assert read_source(path).events == before.events

    def test_a_subagent_without_a_finish_marker_derives_nothing(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        parent_with_agent(transcript)
        path = transcript.write(transcript_file(tmp_path))
        subagent_run(tmp_path, finished=False)
        assert subagents_of(read_source(path)) == []

        # A turn that ended, then was resumed with a new instruction.
        resumed = subagent_run(tmp_path)
        with resumed.open("a", encoding="utf-8") as handle:
            record = {
                "type": "user",
                "uuid": "resume-1",
                "agentId": AGENT,
                "timestamp": stamp(16),
                "message": {"role": "user", "content": "keep going"},
            }
            handle.write(json.dumps(record) + "\n")
        assert subagents_of(read_source(path)) == []

    def test_a_background_subagent_is_finished_by_its_notification(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        # Resumed once: notified twice; one event, at the last notification.
        parent_with_agent(transcript, notify=(15, 18))
        path = transcript.write(transcript_file(tmp_path))
        subagent_run(tmp_path, finished=False, agent_type=None)

        [sub] = subagents_of(read_source(path))
        assert sub.id == subagent_stop_id(AGENT)
        assert sub.timestamp == at(18)
        assert sub.provenance.source == path.name and sub.text == ""

        # Another status is no finish.
        transcript = Transcript()
        parent_with_agent(transcript, notify=(15,), status="running")
        assert subagents_of(read_source(transcript.write(path))) == []

    def test_a_foreground_agent_result_finishes_its_subagent(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        parent_with_agent(transcript)
        transcript.records[2]["toolUseResult"] = {
            "status": "completed",
            "agentId": AGENT,
            "content": [{"type": "text", "text": "SECRET-OUTPUT"}],
        }
        path = transcript.write(transcript_file(tmp_path))
        subagent_run(tmp_path, finished=False)

        [sub] = subagents_of(read_source(path))
        assert sub.id == subagent_stop_id(AGENT)
        assert sub.timestamp == at(3) and sub.provenance.lines == (3,)

    def test_a_parent_marker_without_a_subagent_file_derives_nothing(
        self, tmp_path: Path
    ) -> None:
        # Claude Code releases that write no subagent files read as before.
        transcript = Transcript()
        parent_with_agent(transcript, notify=(15,))
        transcript.records[2]["toolUseResult"] = {
            "status": "completed",
            "agentId": AGENT,
        }
        path = transcript.write(transcript_file(tmp_path))
        assert subagents_of(read_source(path)) == []

    def test_a_redacted_session_derives_the_same_subagent_ids(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        parent_with_agent(transcript, notify=(15,))
        path = transcript.write(transcript_file(tmp_path))
        files = [
            path,
            subagent_run(tmp_path, finished=False),  # the notification marks it
            subagent_run(tmp_path, "f00dfeedf00dfeed1"),  # its final turn does
        ]
        raw = [e.id for e in subagents_of(read_source(path))]
        assert len(raw) == 2

        redacted = tmp_path / "redacted"
        for source in files:
            records = [json.loads(line) for line in source.read_text().splitlines()]
            clean = Redactor(RedactionPolicy(salt="s")).redact_session(records)
            Transcript(records=clean).write(redacted / source.relative_to(path.parent))
        assert [e.id for e in subagents_of(read_source(redacted / path.name))] == raw

    def test_the_hooks_check_matches_a_subagent_stop(self, tmp_path: Path) -> None:
        transcript = Transcript()
        parent_with_agent(transcript)
        transcript.write(transcript_file(tmp_path))
        subagent_run(tmp_path)
        subagent_run(tmp_path, "0123456789abcdef0", finished=False)
        recorder = Recorder(tmp_path / "rec")
        recorder.record("subagent_stop", 14.1, agent_id=AGENT)
        recorder.record("subagent_stop", 14.2, agent_id=AGENT)  # resumed: one id
        recorder.record("subagent_stop", 15, agent_id="0123456789abcdef0")
        recorder.record("subagent_stop", 16, agent_id="feedfeedfeedfeed0")
        recorder.record("subagent_stop", 17)  # no agent_id: the fallback key

        [session] = run_check(tmp_path).sessions
        [sub] = session.correlation
        assert sub.kind == "subagent.completed"
        assert sub.matched == (subagent_event_id(SESSION, AGENT),)
        assert dict(sub.reasons) == {
            Reason.SUBAGENT_NOT_FINISHED: 1,
            Reason.SUBAGENT_NO_TRANSCRIPT: 1,
            Reason.SUBAGENT_UNKEYED: 1,
        }
        assert sub.source_only == 0
        assert not any("subagent.completed" in note for note in session.notes)

    def test_the_hooks_check_notes_subagents_without_a_finish(
        self, tmp_path: Path
    ) -> None:
        transcript = Transcript()
        parent_with_agent(transcript)
        transcript.write(transcript_file(tmp_path))
        subagent_run(tmp_path, finished=False)
        Recorder(tmp_path / "rec").record("subagent_stop", 15, agent_id=AGENT)

        [session] = run_check(tmp_path).sessions
        [sub] = session.correlation
        assert dict(sub.reasons) == {Reason.SUBAGENT_NOT_FINISHED: 1}
        notes = " ".join(session.notes)
        assert "no subagent transcript holds a finish marker" in notes


# --- the owner's real run (issue #35) --------------------------------------------

REAL_CHECK = FIXTURES / "real-check-2026-10-09.md"


@pytest.mark.req("TER-OBS-005", "TER-OBS-012")
def test_the_real_hooks_check_summary_records_every_stop_matched() -> None:
    """The real evidence for TER-OBS-005: 10 of 10 real Stop payloads matched.

    The summary is hand-copied from the owner's report; this test keeps its
    arithmetic honest and its content free of anything the report omits.
    """
    text = REAL_CHECK.read_text(encoding="utf-8")
    rows = {
        match[0]: (int(match[1]), int(match[2]))
        for match in re.findall(r"^\| `([a-z.]+)` \| (\d+) \| (\d+) \|", text, re.M)
    }
    assert rows == {
        "intent.stated": (11, 0),
        "tool.requested": (34, 0),
        "tool.completed": (32, 0),
        "task.completed": (10, 0),
        "subagent.completed": (0, 7),
    }
    matched = sum(m for m, _ in rows.values())
    total = sum(m + u for m, u in rows.values())
    assert (matched, total) == (87, 94)
    assert f"{matched} of {total} hook events ({matched / total:.1%})" in text
    assert "10 of 10 real Stop payloads" in text
    # Content-free: no event id (16+ hex digits), path or session id.
    assert not re.search(r"[0-9a-f]{16}|[A-Za-z]:\\|/home/|/Users/", text)
