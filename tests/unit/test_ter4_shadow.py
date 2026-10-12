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
        Outcome.INELIGIBLE,
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
    assert "edit-not-rewrite: 1 ineligible" in out
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


@pytest.mark.req("TER-INT-017")
def test_a_replay_into_the_same_ledger_writes_nothing_new(tmp_path: Path) -> None:
    ledger = JsonlInterventionLedger(tmp_path)
    first = shadow_sessions([SESSION], explain, approved(), ledger, FixedClock(AT))
    lines = ledger.path_for("sample-session-001").read_text(encoding="utf-8")
    again = shadow_sessions([SESSION], explain, approved(), ledger, FixedClock(AT))
    assert (first.written, again.written) == (2, 0)
    assert len(again.records) == 2
    assert ledger.path_for("sample-session-001").read_text(encoding="utf-8") == lines


@pytest.mark.req("TER-INT-017", "TER-INT-009")
def test_a_revised_policy_file_writes_new_records_beside_the_old() -> None:
    ledger = InMemoryInterventionLedger()
    before = shadow_sessions([SESSION], explain, approved(), ledger, FixedClock(AT))
    doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    for policy in doc["policies"]:
        if policy["id"] == "reuse-reads":
            policy["advice"]["message"] = "Reuse what you already read."
    revised = PolicySet.from_mapping(doc).approve("Leigh", date(2026, 10, 13))
    after = shadow_sessions([SESSION], explain, revised, ledger, FixedClock(AT))
    assert after.written == 2
    entries = ledger.entries("sample-session-001")
    assert len(entries) == 4
    assert {e.policy_version for e in entries} == {
        approved().digest(),
        revised.digest(),
    }
    assert after.advice[0].record.advice == "Reuse what you already read."


@pytest.mark.parametrize(
    ("text", "cell"),
    [
        ("=HYPERLINK(1)", "'=HYPERLINK(1)"),
        ("+1", "'+1"),
        ("-rf", "'-rf"),
        ("@x", "'@x"),
        ("src/a.py", "src/a.py"),
        ("", ""),
    ],
)
def test_judging_cells_never_run_as_formulas(text: str, cell: str) -> None:
    from ter.adapters.driving.policy_cli import _cell

    assert _cell(text) == cell


def test_cli_shadow_writes_a_private_csv_in_a_new_folder(tmp_path: Path) -> None:
    import os
    import stat

    policies = policy_file(tmp_path, approve=True)
    judge = tmp_path / "new" / "deeper" / "judge.csv"
    code, _, err = run_cli(
        [
            "policy",
            "shadow",
            str(policies),
            str(SESSION),
            "--ledger",
            str(tmp_path / "ledger"),
            "--judge",
            str(judge),
        ]
    )
    assert code == 0, err
    assert judge.read_text(encoding="utf-8").startswith("verdict,note,session")
    if os.name == "posix":
        assert stat.S_IMODE(judge.stat().st_mode) == 0o600


def test_cli_shadow_rejects_an_unknown_tokenizer_before_replaying(
    tmp_path: Path,
) -> None:
    policies = policy_file(tmp_path, approve=True)
    code, _, err = run_cli(
        [
            "policy",
            "shadow",
            str(policies),
            str(SESSION),
            "--ledger",
            str(tmp_path / "ledger"),
            "--tokenizer",
            "nope",
        ]
    )
    assert code == 2 and "Unknown tokenizer 'nope'" in err
    assert not (tmp_path / "ledger").exists()


def test_cli_shadow_reports_a_ledger_error_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ter.domain.ledger import LedgerError

    def broken(self: object, entry: object) -> None:
        raise LedgerError("ledger is read-only")

    monkeypatch.setattr(JsonlInterventionLedger, "record", broken)
    policies = policy_file(tmp_path, approve=True)
    code, _, err = run_cli(
        ["policy", "shadow", str(policies), str(SESSION), "--ledger", str(tmp_path)]
    )
    assert code == 2 and "ledger is read-only" in err


def test_cli_shadow_replays_a_gare_run_folder_as_one_session(tmp_path: Path) -> None:
    from dataclasses import replace

    services = bootstrap.cli_services()
    assert services.policies is not None and services.policies.shadow is not None
    shadow = services.policies.shadow
    replayed: list[Path] = []

    def spy(paths: Any, policies: PolicySet, ledger: Path, tokenizer: str) -> Any:
        replayed.extend(paths)
        return shadow(paths, policies, ledger, tokenizer)

    wired = replace(services, policies=replace(services.policies, shadow=spy))
    policies = policy_file(tmp_path, approve=True)
    run_dir = REPO / "tests" / "fixtures" / "gare" / "failover-run"
    argv = ["policy", "shadow", str(policies), str(run_dir)]
    out, err = io.StringIO(), io.StringIO()
    code = main(argv + ["--ledger", str(tmp_path / "l")], wired, stdout=out, stderr=err)
    assert code == 0, err.getvalue()
    # The whole export (explain.json included), not the usage file alone.
    assert replayed == [run_dir]
