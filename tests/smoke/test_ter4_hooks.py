"""The Claude Code hook as Claude Code runs it: a payload on stdin, events
appended to the live log, advice on Stop, and the hook never failing the
agent (TER-TST-006, TER-TST-007)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from schema.conftest import assert_conforms

from .conftest import HOOKS, POLICIES, Cli

#: The session every payload in tests/fixtures/hooks belongs to.
SESSION = "3f0c9a1e-hook-demo"

pytestmark = pytest.mark.smoke


def _payload(name: str) -> str:
    return (HOOKS / f"{name}.json").read_text(encoding="utf-8")


class Transcript:
    """A Claude Code transcript, written record by record with the time it is
    written, in the record shapes tests/unit/test_ter4_hook_check.py uses."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: list[dict[str, Any]] = []

    def add(self, record: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        parent = self.records[-1]["uuid"] if self.records else None
        record |= {
            "uuid": f"rec-{len(self.records):03d}",
            "parentUuid": parent,
            "sessionId": SESSION,
            "isSidechain": False,
            "timestamp": now.replace("+00:00", "Z"),
        }
        self.records.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = "".join(json.dumps(r) + "\n" for r in self.records)
        self.path.write_text(lines, encoding="utf-8")

    def user(self, content: Any, **extra: Any) -> None:
        self.add(
            {"type": "user", "message": {"role": "user", "content": content}} | extra
        )

    def assistant(self, block: dict[str, Any], stop_reason: str) -> None:
        n = len(self.records)
        message = {
            "id": f"msg-{n}",
            "role": "assistant",
            "model": "claude-demo",
            "content": [block],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        self.add({"type": "assistant", "requestId": f"req-{n}", "message": message})


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


def _hook(cli: Cli, *args: str, payload: dict[str, Any]) -> str:
    return cli.ter4("hook", *args, stdin=json.dumps(payload)).ok().out


@pytest.mark.req("TER-TST-006")
def test_advice_on_stop_is_a_system_message(cli: Cli, workdir: Path) -> None:
    policies = workdir / "hook-policies.json"
    shutil.copy(POLICIES, policies)
    cli.ter4("policy", "approve", str(policies), "--by", "Smoke").ok()
    # The same file read again and again between shell calls: repeated
    # exploration, which the shipped reuse-reads policy advises on.
    calls = [
        json.loads(_payload(n)) for n in ("post_tool_use_bash", "post_tool_use_read")
    ]
    for n in range(8):
        _hook(
            cli,
            "--event-log",
            "advice-events",
            payload=calls[n % 2] | {"tool_use_id": f"t{n}"},
        )
    advise = (
        "--event-log",
        "advice-events",
        "--advise",
        str(policies),
        "--ledger",
        "hook-ledger",
    )
    reply = json.loads(_hook(cli, *advise, payload=json.loads(_payload("stop"))))
    assert set(reply) == {"systemMessage"}, reply
    assert reply["systemMessage"].strip()
    [ledger] = (workdir / "hook-ledger").glob("*.jsonl")
    for line in ledger.read_text(encoding="utf-8").splitlines():
        assert_conforms("ter.intervention-ledger/1", json.loads(line))
    # Advice is given once: the next Stop has nothing new to say.
    assert (
        json.loads(_hook(cli, *advise, payload=json.loads(_payload("stop"))) or "{}")
        == {}
    )


@pytest.mark.req("TER-TST-006")
def test_hooks_check_correlates_recordings(cli: Cli, workdir: Path) -> None:
    """A turn recorded by the hook while its transcript is written, as Claude
    Code does: every hook event and the stop match the transcript."""
    transcript = Transcript(workdir / "projects" / "-home-dev-app" / f"{SESSION}.jsonl")

    def record(name: str) -> None:
        payload = json.loads(_payload(name)) | {"transcript_path": str(transcript.path)}
        _hook(cli, "--record", "check-recordings", payload=payload)

    read = json.loads(_payload("post_tool_use_read"))
    record("session_start")
    transcript.user(json.loads(_payload("user_prompt_submit"))["prompt"])
    record("user_prompt_submit")
    transcript.assistant(
        {
            "type": "tool_use",
            "id": read["tool_use_id"],
            "name": "Read",
            "input": read["tool_input"],
        },
        stop_reason="tool_use",
    )
    record("pre_tool_use_read")
    transcript.user(
        [
            {
                "type": "tool_result",
                "tool_use_id": read["tool_use_id"],
                "content": "def parse",
            }
        ],
        toolUseResult={"type": "text"},
    )
    record("post_tool_use_read")
    transcript.assistant({"type": "text", "text": "Fixed."}, stop_reason="end_turn")
    record("stop")
    transcript.add(
        {
            "type": "attachment",
            "attachment": {"type": "hook_success", "hookName": "Stop"},
        }
    )
    transcript.add(
        {
            "type": "system",
            "subtype": "stop_hook_summary",
            "hookCount": 1,
            "hookInfos": [{"command": "python -m ter hook", "durationMs": 40}],
            "hookErrors": [],
            "preventedContinuation": False,
            "stopReason": "",
            "hasOutput": False,
            "level": "suggestion",
            "toolUseID": "a89c740a-0000-4000-8000-000000000000",
        }
    )
    cli.ter4(
        "hooks", "check", "check-recordings", "projects", "--json", "check.json"
    ).ok()
    report = json.loads((workdir / "check.json").read_text(encoding="utf-8"))
    assert report["schema"] == "ter.hook-check/1"
    assert report["sessions"] == 1
    assert report["hook_events"] == 4 and report["matched"] == 4, report["by_session"]
    assert report["stops"] == {"total": 1, "matched": 1}


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
