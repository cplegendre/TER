"""TER-INT-001 and TER-INT-002: the maturity ceiling bounds what a hook may say.

Claude Code takes interventions from a hook's output (a decision, extra
context, a system message, ``continue: false``) or its exit code (2 blocks).
Below L4 Advisory (``ter hook`` without ``--advise``) every hook run answers
``{}`` with exit code 0. At L4 Advisory (``--advise`` with an approved policy
file) the only field a hook may write is ``systemMessage``, which Claude Code
shows the developer and never adds to the model's context: advice that
changes no agent input. The source scan keeps it that way: ``systemMessage``
may be named only by the hook entry point, and no other response field
anywhere.
"""

from __future__ import annotations

import io
import json
import re
import shutil
from pathlib import Path

import pytest

from ter import bootstrap
from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log import JsonlEventLog
from ter.adapters.driving.cli import main

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / "tests" / "fixtures" / "hooks"
SOURCE = REPO / "src" / "ter"

#: Hook output fields through which Claude Code accepts an intervention.
INTERVENTION_FIELDS = (
    "additionalContext",
    "decision",
    "hookSpecificOutput",
    "permissionDecision",
    "suppressOutput",
    "systemMessage",
    "stopReason",
    "continue",
)

#: The one field TER writes at L4 Advisory, and the one module that writes it.
ADVICE_FIELD = "systemMessage"
ADVICE_WRITER = SOURCE / "adapters" / "driving" / "claude_hooks" / "entry.py"

PAYLOADS = sorted(HOOKS.glob("*.json"))
SESSION = REPO / "sample_sessions" / "example_session.jsonl"
EXAMPLE = REPO / "docs" / "examples" / "policies" / "l4-advisory.json"


def _hook(stdin: str, log: Path, *extra: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["hook", "--event-log", str(log), *extra],
        bootstrap.cli_services(),
        stdin=io.StringIO(stdin),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def _policies(tmp_path: Path, *, approve: bool) -> Path:
    policies = tmp_path / "policies.json"
    shutil.copy(EXAMPLE, policies)
    if approve:
        out = io.StringIO()
        code = main(
            ["policy", "approve", str(policies), "--by", "Test"],
            bootstrap.cli_services(),
            stdout=out,
            stderr=io.StringIO(),
        )
        assert code == 0, out.getvalue()
    return policies


def _stop(log: Path) -> str:
    """A live log of the sample session (one reuse-reads advice due), and the
    Stop payload that ends its turn."""
    trace = ClaudeCodeJsonlSource().read(SESSION)
    events = JsonlEventLog(log)
    for event in trace.events:
        events.append(event)
    payload = json.loads((HOOKS / "stop.json").read_text(encoding="utf-8"))
    payload["session_id"] = trace.session_id
    payload["transcript_path"] = str(log / "missing.jsonl")
    return json.dumps(payload)


@pytest.mark.req("TER-INT-001")
@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda p: p.stem)
def test_every_hook_event_gets_the_empty_response(
    payload: Path, tmp_path: Path
) -> None:
    code, out, _ = _hook(payload.read_text(encoding="utf-8"), tmp_path)
    assert code == 0
    assert json.loads(out) == {}


@pytest.mark.req("TER-INT-001")
@pytest.mark.parametrize(
    "stdin",
    ["", "{oops", "[]", json.dumps({"hook_event_name": "Unknown"})],
    ids=["empty", "invalid-json", "not-an-object", "unknown-event"],
)
def test_bad_input_gets_the_empty_response(stdin: str, tmp_path: Path) -> None:
    code, out, _ = _hook(stdin, tmp_path)
    assert code == 0
    assert json.loads(out) == {}


@pytest.mark.req("TER-INT-001", "TER-INT-002")
def test_only_the_hook_entry_point_names_the_advice_field() -> None:
    pattern = re.compile(
        r"""["'](%s)["']""" % "|".join(map(re.escape, INTERVENTION_FIELDS))
    )
    offenders = [
        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
        for path in sorted(SOURCE.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in pattern.finditer(line)
        if not (match.group(1) == ADVICE_FIELD and path == ADVICE_WRITER)
    ]
    assert offenders == []


@pytest.mark.req("TER-INT-002")
def test_at_the_advisory_ceiling_a_stop_shows_advice_and_changes_no_input(
    tmp_path: Path,
) -> None:
    log = tmp_path / "events"
    policies = _policies(tmp_path, approve=True)
    stop = _stop(log)
    code, out, err = _hook(stop, log, "--advise", str(policies))
    assert (code, err) == (0, "")
    answer = json.loads(out)
    assert set(answer) == {ADVICE_FIELD}
    assert answer[ADVICE_FIELD].startswith("TER warning (reuse-reads): ")
    # Decisions go to the ledger beside the event log; the same advice is
    # never given twice.
    assert list((log / "ledger").glob("*.ledger.jsonl"))
    code, out, err = _hook(stop, log, "--advise", str(policies))
    assert (code, json.loads(out), err) == (0, {}, "")


@pytest.mark.req("TER-INT-001", "TER-INT-015")
def test_an_unapproved_policy_file_gives_no_advice(tmp_path: Path) -> None:
    log = tmp_path / "events"
    policies = _policies(tmp_path, approve=False)
    code, out, err = _hook(_stop(log), log, "--advise", str(policies))
    assert (code, json.loads(out)) == (0, {})
    assert "no advice given: UnapprovedPolicyError" in err
    assert not (log / "ledger").exists()


@pytest.mark.req("TER-INT-002")
@pytest.mark.parametrize(
    "payload",
    [p for p in PAYLOADS if p.stem != "stop"],
    ids=lambda p: p.stem,
)
def test_advice_waits_for_the_end_of_a_turn(payload: Path, tmp_path: Path) -> None:
    log = tmp_path / "events"
    policies = _policies(tmp_path, approve=True)
    _stop(log)
    code, out, _ = _hook(
        payload.read_text(encoding="utf-8"), log, "--advise", str(policies)
    )
    assert (code, json.loads(out)) == (0, {})


@pytest.mark.req("TER-INT-001")
def test_the_fixtures_cover_every_hook_event_ter_records() -> None:
    events = {
        json.loads(p.read_text(encoding="utf-8"))["hook_event_name"] for p in PAYLOADS
    }
    assert {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "SubagentStop",
        "PreCompact",
        "SessionEnd",
    } <= events


@pytest.mark.req("TER-INT-003")
def test_the_ledger_goes_where_it_is_asked(tmp_path: Path) -> None:
    log, ledger = tmp_path / "events", tmp_path / "decisions"
    policies = _policies(tmp_path, approve=True)
    code, out, _ = _hook(
        _stop(log), log, "--advise", str(policies), "--ledger", str(ledger)
    )
    assert code == 0 and set(json.loads(out)) == {ADVICE_FIELD}
    assert list(ledger.glob("*.ledger.jsonl")) and not (log / "ledger").exists()
