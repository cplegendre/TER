"""The Claude Code hook as Claude Code runs it: a payload on stdin, events
appended to the live log, advice on Stop, and the hook never failing the
agent (TER-TST-006, TER-TST-007)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from schema.conftest import assert_conforms

from .conftest import HOOKS, POLICIES, SAMPLE, Cli

pytestmark = pytest.mark.smoke


def _payload(name: str) -> str:
    return (HOOKS / f"{name}.json").read_text(encoding="utf-8")


@pytest.mark.req("TER-TST-006")
def test_hook_records_a_payload_into_the_event_log(cli: Cli, workdir: Path) -> None:
    for name in ("session_start", "post_tool_use_read", "post_tool_use_bash"):
        result = cli.ter4(
            "hook",
            "--event-log",
            "events",
            "--record",
            "recordings",
            stdin=_payload(name),
        ).ok()
        assert json.loads(result.out or "{}") == {}
    [log] = (workdir / "events").glob("*.events.jsonl")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        assert_conforms("ter.event/0.5", json.loads(line))
    assert any((workdir / "recordings").iterdir())


@pytest.mark.req("TER-TST-006")
def test_observe_reads_the_live_event_log(cli: Cli) -> None:
    cli.ter4("hook", "--event-log", "events", stdin=_payload("post_tool_use_read")).ok()
    report = json.loads(cli.ter4("observe", "--event-log", "events", "--json").ok().out)
    assert report["total_events"] >= 1


@pytest.mark.req("TER-TST-006")
def test_advice_on_stop_is_a_system_message(cli: Cli, workdir: Path) -> None:
    policies = workdir / "hook-policies.json"
    shutil.copy(POLICIES, policies)
    cli.ter4("policy", "approve", str(policies), "--by", "Smoke").ok()
    result = cli.ter4(
        "hook",
        "--event-log",
        "events",
        "--advise",
        str(policies),
        "--ledger",
        "hook-ledger",
        stdin=_payload("stop"),
    ).ok()
    reply = json.loads(result.out)
    assert set(reply) <= {"systemMessage"}


@pytest.mark.req("TER-TST-006")
def test_hooks_check_correlates_recordings(cli: Cli, workdir: Path) -> None:
    transcripts = workdir / "transcripts"
    transcripts.mkdir(exist_ok=True)
    shutil.copy(SAMPLE, transcripts)
    cli.ter4("hook", "--record", "recordings", stdin=_payload("stop")).ok()
    cli.ter4(
        "hooks", "check", "recordings", str(transcripts), "--json", "check.json"
    ).ok()
    report = json.loads((workdir / "check.json").read_text(encoding="utf-8"))
    assert report["schema"] == "ter.hook-check/1"


@pytest.mark.req("TER-TST-008")
@pytest.mark.parametrize(
    "stdin",
    ["", "{not json", "[]", '{"hook_event_name": 7}', "\x00" * 64, '"text"'],
    ids=["empty", "torn", "list", "wrong-type", "binary", "string"],
)
def test_a_bad_payload_never_fails_the_agent(cli: Cli, stdin: str) -> None:
    result = cli.ter4("hook", "--event-log", "events", stdin=stdin)
    assert result.code == 0, result.err
    assert json.loads(result.out or "{}") == {}
    assert "Traceback" not in result.err, result.err


@pytest.mark.req("TER-TST-008")
def test_an_unwritable_event_log_never_fails_the_agent(cli: Cli, workdir: Path) -> None:
    blocker = workdir / "a-file"
    blocker.write_text("not a folder")
    result = cli.ter4(
        "hook",
        "--event-log",
        str(blocker / "events"),
        stdin=_payload("post_tool_use_read"),
    )
    assert result.code == 0, result.err
    assert "Traceback" not in result.err, result.err
