"""L4 Advisory: shadow mode (TER-INT-017).

Recorded sessions are replayed through approved policies, and every decision
TER would have made is written to the ledger. Nothing is delivered.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from ter import bootstrap
from ter.adapters.driven.in_memory import FixedClock, InMemoryInterventionLedger
from ter.adapters.driven.intervention_ledger import JsonlInterventionLedger
from ter.adapters.driving.cli import main
from ter.application.explain import ExplainedSession
from ter.application.shadow import shadow_sessions
from ter.domain.intervention import Outcome
from ter.domain.policy_set import PolicySet, UnapprovedPolicyError

REPO = Path(__file__).resolve().parents[2]
SESSION = REPO / "sample_sessions" / "example_session.jsonl"
EXAMPLE = REPO / "docs" / "examples" / "policies" / "l4-advisory.json"
AT = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)


def approved() -> PolicySet:
    draft = PolicySet.from_mapping(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    return draft.approve("Leigh", date(2026, 10, 12))


def explain(path: Path) -> ExplainedSession:
    services = bootstrap.cli_services()
    assert services.explain_transcript is not None
    return services.explain_transcript(path, "regex", "off")


@pytest.mark.req("TER-INT-017")
def test_every_decision_is_ledgered_and_nothing_is_delivered() -> None:
    ledger = InMemoryInterventionLedger()
    run = shadow_sessions([SESSION], explain, approved(), ledger, FixedClock(AT))
    assert run.sessions == ("sample-session-001",)
    assert ledger.entries("sample-session-001") == run.records
    # The suppression is recorded with its reason, not only the advice.
    assert {r.outcome for r in run.records} == {
        Outcome.ADVISED,
        Outcome.LOW_CONFIDENCE,
    }
    [advice] = run.advice
    assert advice.record.policy == "reuse-reads"
    assert advice.signal.subject == "src/app.py"  # for the judge, not the ledger
    assert all(r.observation is None for r in run.records)
    assert run.outcomes()[("reuse-reads", "advised")] == 1


@pytest.mark.req("TER-INT-017", "TER-INT-015")
def test_unapproved_policies_replay_nothing() -> None:
    draft = PolicySet.from_mapping(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    calls: list[Path] = []

    def spy(path: Path) -> ExplainedSession:
        calls.append(path)
        return explain(path)

    with pytest.raises(UnapprovedPolicyError):
        shadow_sessions(
            [SESSION], spy, draft, InMemoryInterventionLedger(), FixedClock(AT)
        )
    assert calls == []


@pytest.mark.req("TER-INT-017")
def test_a_replay_writes_the_same_records() -> None:
    first, second = InMemoryInterventionLedger(), InMemoryInterventionLedger()
    a = shadow_sessions([SESSION], explain, approved(), first, FixedClock(AT))
    b = shadow_sessions([SESSION], explain, approved(), second, FixedClock(AT))
    assert [r.id for r in a.records] == [r.id for r in b.records]


def test_a_session_that_cannot_be_explained_does_not_stop_the_rest(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken.jsonl"

    def flaky(path: Path) -> ExplainedSession:
        if path == broken:
            raise ValueError("not a transcript")
        return explain(path)

    run = shadow_sessions(
        [broken, SESSION],
        flaky,
        approved(),
        InMemoryInterventionLedger(),
        FixedClock(AT),
    )
    assert run.sessions == ("sample-session-001",)
    assert run.failures == ((str(broken), "ValueError: not a transcript"),)


# --- python -m ter policy shadow ------------------------------------------------


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, bootstrap.cli_services(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def policy_file(tmp_path: Path, *, approve: bool) -> Path:
    path = tmp_path / "policies.json"
    shutil.copy(EXAMPLE, path)
    if approve:
        code, _, _ = run_cli(["policy", "approve", str(path), "--by", "Leigh"])
        assert code == 0
    return path


@pytest.mark.req("TER-INT-017")
def test_cli_shadow_writes_the_ledger_and_a_judging_csv(tmp_path: Path) -> None:
    policies = policy_file(tmp_path, approve=True)
    ledger, judge = tmp_path / "ledger", tmp_path / "judge.csv"
    code, out, err = run_cli(
        [
            "policy",
            "shadow",
            str(policies),
            str(SESSION),
            "--ledger",
            str(ledger),
            "--judge",
            str(judge),
        ]
    )
    assert code == 0, err
    assert "Nothing was delivered" in out
    assert "reuse-reads: 1 advised" in out
    assert "edit-not-rewrite: 1 low_confidence" in out
    entries = JsonlInterventionLedger(ledger).entries("sample-session-001")
    assert len(entries) == 2
    with open(judge, encoding="utf-8", newline="") as handle:
        rows: list[dict[str, Any]] = list(csv.DictReader(handle))
    [row] = rows
    assert row["verdict"] == "" and row["policy"] == "reuse-reads"
    assert row["subject"] == "src/app.py"
    assert row["record"] == entries[[e.delivered for e in entries].index(True)].id


@pytest.mark.req("TER-INT-017", "TER-INT-015")
def test_cli_shadow_refuses_an_unapproved_file(tmp_path: Path) -> None:
    policies = policy_file(tmp_path, approve=False)
    code, _, err = run_cli(
        ["policy", "shadow", str(policies), str(SESSION), "--ledger", str(tmp_path)]
    )
    assert code == 1 and "unapproved" in err and "policy approve" in err
    assert not list(tmp_path.glob("*.ledger.jsonl"))


def test_cli_shadow_reports_missing_sessions(tmp_path: Path) -> None:
    policies = policy_file(tmp_path, approve=True)
    code, _, err = run_cli(
        ["policy", "shadow", str(policies), str(tmp_path / "none"), "--ledger", "x"]
    )
    assert code == 2 and "No such session" in err
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, err = run_cli(
        ["policy", "shadow", str(policies), str(empty), "--ledger", "x"]
    )
    assert code == 2 and "No session transcripts" in err
