"""L4 Advisory: declarative policy files and the human approval record.

TER-INT-008 (policies are declarative files stating evidence, confidence,
cooldowns and permitted actions), TER-INT-015 (a changed policy applies only
from a file a person approved) and TER-ARC-008 (policies load through the
capability registry).
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ter import bootstrap
from ter.adapters.driven.intervention_policies import JsonInterventionPolicies
from ter.adapters.driving.cli import main
from ter.domain.intervention import (
    Action,
    AdviceKind,
    Outcome,
    PolicyError,
    Signal,
)
from ter.domain.maturity import Maturity
from ter.domain.policy_set import (
    POLICY_SCHEMA,
    ApprovalState,
    PolicySet,
    UnapprovedPolicyError,
)
from ter.ports import InterventionPolicies

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "docs" / "examples" / "policies" / "l4-advisory.json"
DAY = date(2026, 10, 12)


def document(**policy: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "reuse-reads",
        "detectors": ["repeated_exploration"],
        "advice": {"kind": "warning", "message": "Reuse the earlier read."},
        "min_evidence": 2,
        "min_confidence": 0.75,
        "min_signals": 2,
        "cooldown_tool_events": 5,
        "actions": ["advise"],
    }
    base.update(policy)
    return {
        "schema": POLICY_SCHEMA,
        "eligibility": {"detectors": ["repeated_exploration"], "basis": "judge-l4"},
        "policies": [base],
    }


def signal(n: int, *, confidence: float = 0.85) -> Signal:
    return Signal(
        id=f"repeated_exploration:{n}",
        detector="repeated_exploration",
        waste="motion",
        kind="waste",
        confidence=confidence,
        title="Re-read src/a.py",
        subject="src/a.py",
        evidence=("e1", "e2"),
        rule="0.85 when the same read returned identical output",
        position=n,
    )


# --- TER-INT-008: declarative policies ---------------------------------------


@pytest.mark.req("TER-INT-008")
def test_a_policy_file_states_evidence_confidence_cooldown_and_actions() -> None:
    policies = PolicySet.from_mapping(document())
    [p] = policies.policies
    assert p.id == "reuse-reads" and p.detectors == {"repeated_exploration"}
    assert p.advice.kind is AdviceKind.WARNING
    assert (p.min_evidence, p.min_confidence) == (2, 0.75)
    assert (p.min_signals, p.cooldown_tool_events) == (2, 5)
    assert p.actions == {Action.ADVISE}
    assert policies.eligibility.basis == "judge-l4"


@pytest.mark.req("TER-INT-008")
def test_the_file_round_trips() -> None:
    policies = PolicySet.from_mapping(document())
    assert PolicySet.from_mapping(policies.as_dict()) == policies
    approved = policies.approve("Leigh", DAY, "read all three")
    assert PolicySet.from_mapping(approved.as_dict()) == approved


@pytest.mark.req("TER-INT-008")
def test_boundary_omitted_fields_take_the_engine_defaults() -> None:
    doc = document()
    for key in ("min_evidence", "min_confidence", "min_signals", "actions"):
        del doc["policies"][0][key]
    [p] = PolicySet.from_mapping(doc).policies
    assert (p.min_evidence, p.min_confidence, p.min_signals) == (1, 0.70, 1)
    assert p.actions == {Action.ADVISE}


@pytest.mark.req("TER-INT-008")
@pytest.mark.parametrize("action", ["correct", "block", "reroute"])
def test_a_corrective_action_is_refused_at_l4(action: str) -> None:
    with pytest.raises(PolicyError, match="L5"):
        PolicySet.from_mapping(document(actions=["advise", action]))


@pytest.mark.req("TER-INT-007")
def test_a_file_cannot_fire_on_a_token_count_alone() -> None:
    with pytest.raises(PolicyError, match="token count"):
        PolicySet.from_mapping(document(min_evidence=0))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": "ter.intervention-policies/2"}, "schema"),
        ({"surprise": 1}, "unknown key"),
        ({"policies": {}}, "list"),
    ],
)
def test_malformed_files_are_rejected(change: dict[str, Any], message: str) -> None:
    doc = {**document(), **change}
    with pytest.raises(PolicyError, match=message):
        PolicySet.from_mapping(doc)


@pytest.mark.parametrize(
    "policy",
    [
        {"advice": {"kind": "shout", "message": "x"}},
        {"advice": {"kind": "warning", "message": " "}},
        {"min_signals": 1.5},
        {"min_confidence": "high"},
        {"cooldown_tool_events": True},
        {"detectors": "repeated_exploration"},
        {"typo_cooldown": 3},
        {"actions": []},
    ],
)
def test_malformed_policies_are_rejected(policy: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        PolicySet.from_mapping(document(**policy))


def test_policy_ids_are_unique_in_a_file() -> None:
    doc = document()
    doc["policies"] *= 2
    with pytest.raises(PolicyError, match="unique"):
        PolicySet.from_mapping(doc)


def test_policies_naming_ineligible_detectors_are_listed() -> None:
    policies = PolicySet.from_mapping(
        document(detectors=["repeated_exploration", "unused_context"])
    )
    assert policies.ineligible() == (("reuse-reads", "unused_context"),)


# --- TER-INT-015: only an approved file applies -----------------------------


@pytest.mark.req("TER-INT-015")
def test_an_unapproved_file_builds_no_engine() -> None:
    policies = PolicySet.from_mapping(document())
    assert policies.state is ApprovalState.UNAPPROVED
    with pytest.raises(UnapprovedPolicyError, match="policy approve"):
        policies.engine()


@pytest.mark.req("TER-INT-015")
def test_an_approved_file_builds_the_engine_it_states() -> None:
    policies = PolicySet.from_mapping(document()).approve("Leigh", DAY)
    assert policies.state is ApprovalState.APPROVED
    engine = policies.engine()
    got = [v.outcome for n in (1, 2) for v in engine.apply(signal(n))]
    assert got == [Outcome.BELOW_HYSTERESIS, Outcome.ADVISED]  # min_signals 2


@pytest.mark.req("TER-INT-015")
@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["policies"][0].update(cooldown_tool_events=0),
        lambda d: d["policies"][0]["advice"].update(message="Something else."),
        lambda d: d["eligibility"]["detectors"].append("unused_context"),
        lambda d: d["policies"].append({**d["policies"][0], "id": "second"}),
    ],
)
def test_a_changed_policy_does_not_apply_until_approved_again(edit: Any) -> None:
    approved = PolicySet.from_mapping(document()).approve("Leigh", DAY)
    doc = json.loads(json.dumps(approved.as_dict()))
    edit(doc)
    changed = PolicySet.from_mapping(doc)
    assert changed.state is ApprovalState.CHANGED
    with pytest.raises(UnapprovedPolicyError, match="changed after Leigh"):
        changed.engine()
    assert changed.approve("Leigh", DAY).state is ApprovalState.APPROVED


@pytest.mark.req("TER-INT-015")
def test_boundary_reformatting_the_file_keeps_its_approval() -> None:
    approved = PolicySet.from_mapping(document()).approve("Leigh", DAY)
    doc = approved.as_dict()
    reordered = json.loads(json.dumps(doc, indent=4, sort_keys=True))
    assert PolicySet.from_mapping(reordered).state is ApprovalState.APPROVED


@pytest.mark.req("TER-INT-015")
@pytest.mark.parametrize(
    "approval",
    [
        {"approved_by": " ", "approved_on": "2026-10-12", "digest": "sha256:x"},
        {"approved_by": "Leigh", "approved_on": "today", "digest": "sha256:x"},
        {"approved_by": "Leigh", "approved_on": "2026-10-12", "digest": "md5:x"},
    ],
)
def test_an_approval_names_a_person_a_date_and_a_digest(
    approval: dict[str, str],
) -> None:
    with pytest.raises(PolicyError):
        PolicySet.from_mapping({**document(), "approval": approval})


@pytest.mark.req("TER-INT-002")
def test_an_approved_file_still_advises_nothing_below_l4() -> None:
    engine = (
        PolicySet.from_mapping(document(min_signals=1))
        .approve("Leigh", DAY)
        .engine(Maturity.GROUNDED)
    )
    [verdict] = engine.apply(signal(1))
    assert verdict.outcome is Outcome.BELOW_CEILING


# --- TER-ARC-008: policies load through the capability registry ------------


@pytest.mark.req("TER-ARC-008")
def test_policy_files_load_through_the_capability_registry(tmp_path: Path) -> None:
    source = bootstrap.make_policies("json")
    assert isinstance(source, InterventionPolicies)
    assert isinstance(source, JsonInterventionPolicies)
    assert "json" in bootstrap.default_registry().names("InterventionPolicies")
    path = tmp_path / "p.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    assert source.policies(path) == PolicySet.from_mapping(document())


# --- the shipped draft --------------------------------------------------------


@pytest.mark.req("TER-INT-008", "TER-INT-010")
def test_the_example_policy_file_answers_only_the_eligible_detectors() -> None:
    policies = JsonInterventionPolicies().policies(EXAMPLE)
    assert policies.eligibility.detectors == {
        "fragmented_edits",
        "repeated_exploration",
        "regeneration",
    }
    assert "judge-l4" in policies.eligibility.basis
    assert policies.ineligible() == ()
    # Shipped unapproved: the developer approves it, TER never does.
    assert policies.state is ApprovalState.UNAPPROVED
    # Regeneration advises only at 0.80: rewrites repeating 70% or more.
    regen = next(p for p in policies.policies if "regeneration" in p.detectors)
    assert regen.confidence_floor == 0.8


# --- python -m ter policy -------------------------------------------------------


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, bootstrap.cli_services(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.req("TER-INT-015")
def test_cli_check_then_approve_then_check(tmp_path: Path) -> None:
    path = tmp_path / "policies.json"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    code, out, _ = run(["policy", "check", str(path)])
    assert code == 1 and "unapproved" in out and "policy approve" in out
    assert "batch-edits" in out and "Eligible detectors: fragmented_edits" in out
    code, out, _ = run(
        ["policy", "approve", str(path), "--by", "Leigh", "--date", "2026-10-12"]
    )
    assert code == 0 and "approved by Leigh on 2026-10-12" in out
    code, out, _ = run(["policy", "check", str(path)])
    assert code == 0 and out.startswith("Policy file: approved")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["policies"][0]["cooldown_tool_events"] = 0
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, out, _ = run(["policy", "check", str(path)])
    assert code == 1 and "changed since approval" in out


def test_cli_rejects_an_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "policies.json"
    path.write_text(json.dumps(document(actions=["block"])), encoding="utf-8")
    code, _, err = run(["policy", "check", str(path)])
    assert code == 2 and "L5" in err
    code, _, err = run(["policy", "check", str(tmp_path / "missing.json")])
    assert code == 2 and "no such policy file" in err
