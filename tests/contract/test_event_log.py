"""Contract suite for the ``EventLog`` port: JSONL file and in-memory adapters."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log import JsonlEventLog
from ter.adapters.driven.event_log.codec import event_to_record
from ter.adapters.driven.in_memory import InMemoryEventLog
from ter.domain import Actor, Event, EventKind, Provenance, make_event_id
from ter.ports import EventLog

from golden.corpus import CORPUS

Factory = Callable[[Path], EventLog]


@pytest.fixture(
    params=[lambda p: JsonlEventLog(p / "log"), lambda p: InMemoryEventLog()],
    ids=["jsonl", "in-memory"],
)
def log(request: pytest.FixtureRequest, tmp_path: Path) -> EventLog:
    factory: Factory = request.param
    return factory(tmp_path)


def prompt(session: str, n: int) -> Event:
    return Event(
        id=make_event_id(session, n),
        session_id=session,
        sequence=n,
        kind=EventKind.PROMPT,
        actor=Actor.USER,
        text=f"prompt {n} ✓",
        provenance=Provenance("t", str(n)),
        timestamp=datetime(2026, 9, 29, 12, n, tzinfo=UTC),
    )


def test_satisfies_the_port_protocol(log: EventLog) -> None:
    assert isinstance(log, EventLog)


def test_empty_log(log: EventLog) -> None:
    assert log.sessions() == ()
    assert log.events("nobody") == ()


def test_round_trips_every_corpus_event_in_order(log: EventLog) -> None:
    for path in CORPUS.values():
        trace = ClaudeCodeJsonlSource().read(path)
        for event in trace.events:
            log.append(event)
        assert log.events(trace.session_id) == trace.events


def test_sessions_are_separate_and_listed_sorted(log: EventLog) -> None:
    for n in range(3):
        log.append(prompt("b-session", n))
        log.append(prompt("a/unsafe session", n))
    assert log.sessions() == ("a/unsafe session", "b-session")
    assert [e.sequence for e in log.events("b-session")] == [0, 1, 2]
    assert {e.session_id for e in log.events("a/unsafe session")} == {
        "a/unsafe session"
    }


def test_lifecycle_events_round_trip(log: EventLog) -> None:
    events = [
        Event(
            id=make_event_id("s", kind.value),
            session_id="s",
            sequence=n,
            kind=kind,
            actor=Actor.SYSTEM,
            text="",
            provenance=Provenance("t", kind.value),
        )
        for n, kind in enumerate(
            (EventKind.TASK_COMPLETED, EventKind.SUBAGENT_COMPLETED)
        )
    ]
    for event in events:
        log.append(event)
    assert log.events("s") == tuple(events)


def test_duplicate_appends_are_kept(log: EventLog) -> None:
    event = prompt("s", 0)
    log.append(event)
    log.append(event)
    assert log.events("s") == (event, event)


class TestJsonlSpecifics:
    def test_torn_and_foreign_lines_are_skipped_and_counted(
        self, tmp_path: Path
    ) -> None:
        log = JsonlEventLog(tmp_path)
        log.append(prompt("s", 0))
        with open(log.path_for("s"), "a", encoding="utf-8") as handle:
            handle.write('{"schema": "ter.event/0.1", "id": \n')
            handle.write('{"schema": "other"}\n\n')
            handle.write('["not", "an", "object"]\n')
        log.append(prompt("s", 1))
        assert [e.sequence for e in log.events("s")] == [0, 1]
        assert log.skipped == 3

    @pytest.mark.req("TER-OBS-011")
    def test_records_of_an_earlier_schema_version_still_decode(
        self, tmp_path: Path
    ) -> None:
        log = JsonlEventLog(tmp_path)
        old = prompt("s", 0)
        record = event_to_record(old) | {"schema": "ter.event/0.1"}
        log.path_for("s").write_text(json.dumps(record) + "\n", encoding="utf-8")
        log.append(prompt("s", 1))
        assert log.events("s") == (old, prompt("s", 1))
        assert log.skipped == 0

    def test_unsafe_session_ids_never_escape_the_directory(
        self, tmp_path: Path
    ) -> None:
        log = JsonlEventLog(tmp_path)
        for session in ("../../etc/passwd", "..", ".hidden", "a" * 300):
            assert log.path_for(session).parent == tmp_path
        assert log.path_for("abc-123").name == "abc-123.events.jsonl"

    def test_an_append_after_a_torn_last_line_starts_a_new_line(
        self, tmp_path: Path
    ) -> None:
        log = JsonlEventLog(tmp_path)
        log.append(prompt("s", 0))
        with open(log.path_for("s"), "a", encoding="utf-8") as handle:
            handle.write('{"schema": "ter.event/0.1", "id": ')  # crash mid-write
        log.append(prompt("s", 1))
        log.append(prompt("s", 2))
        assert [e.sequence for e in log.events("s")] == [0, 1, 2]
        assert log.skipped == 1

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
    def test_the_log_is_private_to_its_owner(self, tmp_path: Path) -> None:
        old = os.umask(0o022)
        try:
            log = JsonlEventLog(tmp_path / "events")
            log.append(prompt("s", 0))
        finally:
            os.umask(old)
        assert stat.S_IMODE(log.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(log.path_for("s").stat().st_mode) == 0o600

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
    def test_an_existing_open_log_is_tightened(self, tmp_path: Path) -> None:
        directory = tmp_path / "events"
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
        log = JsonlEventLog(directory)
        path = log.path_for("s")
        path.write_text("", encoding="utf-8")
        path.chmod(0o644)
        log.append(prompt("s", 0))
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_missing_directory_lists_no_sessions(self, tmp_path: Path) -> None:
        assert JsonlEventLog(tmp_path / "absent").sessions() == ()
