"""TER-INT-001: below L4 Advisory, TER delivers no intervention to the session.

Claude Code takes interventions from a hook's output (a decision, extra
context, a system message, ``continue: false``) or its exit code (2 blocks).
No build below L4 has an intervention capability, so the guarantee is checked
at both places it could leak: every hook run answers ``{}`` with exit code 0,
and no TER source module names a hook response field.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from ter import bootstrap
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

PAYLOADS = sorted(HOOKS.glob("*.json"))


def _hook(stdin: str, log: Path) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["hook", "--event-log", str(log)],
        bootstrap.cli_services(),
        stdin=io.StringIO(stdin),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue()


@pytest.mark.req("TER-INT-001")
@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda p: p.stem)
def test_every_hook_event_gets_the_empty_response(
    payload: Path, tmp_path: Path
) -> None:
    code, out = _hook(payload.read_text(encoding="utf-8"), tmp_path)
    assert code == 0
    assert json.loads(out) == {}


@pytest.mark.req("TER-INT-001")
@pytest.mark.parametrize(
    "stdin",
    ["", "{oops", "[]", json.dumps({"hook_event_name": "Unknown"})],
    ids=["empty", "invalid-json", "not-an-object", "unknown-event"],
)
def test_bad_input_gets_the_empty_response(stdin: str, tmp_path: Path) -> None:
    code, out = _hook(stdin, tmp_path)
    assert code == 0
    assert json.loads(out) == {}


@pytest.mark.req("TER-INT-001")
def test_no_source_module_writes_a_hook_response_field() -> None:
    pattern = re.compile(
        r"""["'](%s)["']""" % "|".join(map(re.escape, INTERVENTION_FIELDS))
    )
    offenders = [
        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
        for path in sorted(SOURCE.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


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
