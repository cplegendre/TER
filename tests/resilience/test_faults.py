"""Fault injection into TER's append-only files (TER-TST-013, TER-TST-014).

The live event log and the intervention ledger are written by hook
processes that Claude Code may kill, on disks that fill up. These tests
inject the faults a real machine produces (a write cut short, a full disk,
a folder that cannot be written, an unreadable policy file) and check that
the hook still answers Claude Code, that nothing written before is lost,
and that the next write lands on a line of its own.
"""

from __future__ import annotations

import errno
import io
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from golden.corpus import CORPUS, REPO_ROOT
from ter import bootstrap
from ter.adapters.driven import intervention_ledger as ledger_module
from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log import JsonlEventLog
from ter.adapters.driven.event_log import jsonl as event_log_module
from ter.adapters.driven.intervention_ledger import JsonlInterventionLedger
from ter.adapters.driving.cli import main
from ter.domain import Event
from ter.domain.ledger import LedgerRecord

HOOKS = REPO_ROOT / "tests" / "fixtures" / "hooks"
POLICIES = REPO_ROOT / "docs" / "examples" / "policies" / "l4-advisory.json"


def _events() -> tuple[Event, ...]:
    return ClaudeCodeJsonlSource().read(min(CORPUS.values())).events


def _payload(name: str, **fields: Any) -> str:
    payload = json.loads((HOOKS / f"{name}.json").read_text(encoding="utf-8"))
    return json.dumps(payload | fields)


@pytest.fixture
def write_fails(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any, int], None]:
    """Make every write of one adapter module raise ``errno`` from now on."""

    def install(module: Any, code: int) -> None:
        def broken(fd: int, data: bytes) -> None:
            raise OSError(code, os.strerror(code))

        monkeypatch.setattr(module, "_write_all", broken)

    return install


@pytest.fixture
def short_writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """``os.write`` writes at most half of what it is given, as on a disk
    filling up; yields the sizes of the writes made."""
    real = os.write
    sizes: list[int] = []

    def half(fd: int, data: Any) -> int:
        n = real(fd, bytes(data[: max(1, len(data) // 2)]))
        sizes.append(n)
        return n

    monkeypatch.setattr(os, "write", half)
    yield sizes


def _hook(argv: list[str], stdin: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["hook", *argv],
        bootstrap.cli_services(),
        stdin=io.StringIO(stdin),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


# --- a write cut short ---------------------------------------------------------


@pytest.mark.req("TER-TST-013")
def test_an_event_log_write_cut_short_loses_only_that_record(tmp_path: Path) -> None:
    log = JsonlEventLog(tmp_path)
    first, second, third = _events()[:3]
    log.append(first)
    path = log.path_for(first.session_id)
    whole = path.read_bytes()
    log.append(second)
    torn = path.read_bytes()[: len(whole) + (len(path.read_bytes()) - len(whole)) // 2]
    path.write_bytes(torn)  # the hook process was killed mid-write
    log.append(third)
    reader = JsonlEventLog(tmp_path)
    assert reader.events(first.session_id) == (first, third)
    assert reader.skipped == 1


@pytest.mark.req("TER-TST-013")
def test_a_short_write_still_writes_the_whole_record(
    tmp_path: Path, short_writes: list[int]
) -> None:
    log = JsonlEventLog(tmp_path)
    event = _events()[0]
    log.append(event)
    assert len(short_writes) > 1, "the write was not split"
    assert JsonlEventLog(tmp_path).events(event.session_id) == (event,)


@pytest.mark.req("TER-TST-013")
def test_a_write_that_makes_no_progress_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    with pytest.raises(OSError, match="no bytes written"):
        JsonlEventLog(tmp_path).append(_events()[0])


@pytest.mark.req("TER-TST-013")
def test_a_ledger_write_cut_short_loses_only_that_record(tmp_path: Path) -> None:
    ledger = JsonlInterventionLedger(tmp_path)
    records = _ledger_records(tmp_path / "source")
    first, second, third = records[:3]
    ledger.record(first)
    path = ledger.path_for(first.session_id)
    size = len(path.read_bytes())
    ledger.record(second)
    path.write_bytes(path.read_bytes()[: size + 20])
    ledger.record(third)
    reader = JsonlInterventionLedger(tmp_path)
    assert [e.id for e in reader.entries(first.session_id)] == [first.id, third.id]
    assert reader.skipped == 1


def _ledger_records(folder: Path) -> list[LedgerRecord]:
    """Three records of one session from a shadow run's ledger."""
    from datetime import UTC, date, datetime

    from ter.adapters.driven.in_memory import FixedClock
    from ter.application.shadow import shadow_sessions
    from ter.domain.policy_set import PolicySet

    draft = PolicySet.from_mapping(json.loads(POLICIES.read_text(encoding="utf-8")))
    services = bootstrap.cli_services()
    assert services.explain_transcript is not None
    explain = services.explain_transcript
    source = JsonlInterventionLedger(folder)
    shadow_sessions(
        sorted(CORPUS.values()),
        lambda path: explain(path, "regex", "off"),
        draft.approve("Faults", date(2026, 10, 14)),
        source,
        FixedClock(datetime(2026, 10, 14, tzinfo=UTC)),
    )
    first = next(e for s in source.sessions() for e in source.entries(s))
    return [replace(first, id=f"{first.id}-{n}") for n in range(3)]


# --- a full disk, a folder that cannot be written --------------------------------


@pytest.mark.req("TER-TST-014", "TER-TST-008")
@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES, errno.EROFS])
def test_the_hook_answers_when_the_event_log_cannot_be_written(
    tmp_path: Path, write_fails: Callable[[Any, int], None], code: int
) -> None:
    write_fails(event_log_module, code)
    result = _hook(["--event-log", str(tmp_path)], _payload("post_tool_use_read"))
    assert result[:2] == (0, "{}\n"), result


@pytest.mark.req("TER-TST-014", "TER-TST-008")
def test_the_hook_answers_when_the_event_log_folder_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "events"
    blocker.write_text("not a folder", encoding="utf-8")
    result = _hook(["--event-log", str(blocker)], _payload("post_tool_use_read"))
    assert result[:2] == (0, "{}\n"), result


def _approved(tmp_path: Path) -> Path:
    policies = tmp_path / "policies.json"
    policies.write_bytes(POLICIES.read_bytes())
    err = io.StringIO()
    code = main(
        ["policy", "approve", str(policies), "--by", "Faults"],
        bootstrap.cli_services(),
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == 0, err.getvalue()
    return policies


def _session_with_advice(events: Path) -> None:
    """Reads repeated between shell calls: the reuse-reads policy advises."""
    for n in range(8):
        name = ("post_tool_use_bash", "post_tool_use_read")[n % 2]
        code, out, _ = _hook(
            ["--event-log", str(events)], _payload(name, tool_use_id=f"t{n}")
        )
        assert (code, out) == (0, "{}\n")


@pytest.mark.req("TER-TST-014", "TER-TST-008")
def test_no_advice_is_shown_that_the_ledger_could_not_record(
    tmp_path: Path, write_fails: Callable[[Any, int], None]
) -> None:
    events, ledger = tmp_path / "events", tmp_path / "ledger"
    policies = _approved(tmp_path)
    _session_with_advice(events)
    advise = [
        "--event-log",
        str(events),
        "--advise",
        str(policies),
        "--ledger",
        str(ledger),
    ]
    write_fails(ledger_module, errno.ENOSPC)
    code, out, err = _hook(advise, _payload("stop"))
    # Advice is given at most once: what the ledger did not record is not shown.
    assert (code, out) == (0, "{}\n"), err
    assert "no advice given" in err and "No space left" in err, err
    assert "Traceback" not in err


@pytest.mark.req("TER-TST-014", "TER-TST-008")
def test_the_hook_answers_when_the_policy_file_cannot_be_read(tmp_path: Path) -> None:
    events = tmp_path / "events"
    _session_with_advice(events)
    unreadable = tmp_path / "policies.json"
    unreadable.mkdir()  # reading it raises IsADirectoryError
    advise = ["--event-log", str(events), "--advise", str(unreadable)]
    code, out, err = _hook(advise, _payload("stop"))
    assert (code, out) == (0, "{}\n"), err
    assert "no advice given" in err and "cannot read" in err, err
    assert "Traceback" not in err


@pytest.mark.req("TER-TST-014")
def test_the_same_session_gets_advice_when_the_ledger_works(tmp_path: Path) -> None:
    # The control for the test above: without the fault there is advice.
    events, ledger = tmp_path / "events", tmp_path / "ledger"
    policies = _approved(tmp_path)
    _session_with_advice(events)
    advise = [
        "--event-log",
        str(events),
        "--advise",
        str(policies),
        "--ledger",
        str(ledger),
    ]
    code, out, err = _hook(advise, _payload("stop"))
    assert code == 0, err
    assert json.loads(out)["systemMessage"].strip()
