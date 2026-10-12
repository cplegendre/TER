"""Contract suite for the ``InterventionPolicies`` port (TER-INT-008, TER-INT-015).

The JSON file adapter and the in-memory fake run the same assertions, so the
fake cannot drift from the obligations the real adapter meets.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from ter.adapters.driven.in_memory import InMemoryInterventionPolicies
from ter.adapters.driven.intervention_policies import JsonInterventionPolicies
from ter.domain.intervention import Advice, AdviceKind, Eligibility, Policy, PolicyError
from ter.domain.policy_set import ApprovalState, PolicySet, UnapprovedPolicyError
from ter.ports import InterventionPolicies


def _policies(approved: bool) -> PolicySet:
    policies = PolicySet(
        (
            Policy(
                "batch-edits",
                frozenset({"fragmented_edits"}),
                Advice(AdviceKind.RECOMMENDATION, "Make the change in one edit."),
                min_evidence=2,
                cooldown_tool_events=20,
            ),
        ),
        Eligibility(frozenset({"fragmented_edits"}), "judge-l4"),
    )
    return policies.approve("Leigh", date(2026, 10, 12)) if approved else policies


Factory = Callable[[Path, bool], tuple[InterventionPolicies, str]]


def _json(tmp: Path, approved: bool) -> tuple[InterventionPolicies, str]:
    path = tmp / "policies.json"
    path.write_text(json.dumps(_policies(approved).as_dict()), encoding="utf-8")
    return JsonInterventionPolicies(), str(path)


def _memory(tmp: Path, approved: bool) -> tuple[InterventionPolicies, str]:
    return InMemoryInterventionPolicies({"p": _policies(approved)}), "p"


@pytest.fixture(params=[_json, _memory], ids=["json", "in-memory"])
def factory(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Callable[[bool], tuple[InterventionPolicies, str]]:
    make: Factory = request.param
    return lambda approved: make(tmp_path, approved)


@pytest.mark.req("TER-INT-008")
def test_satisfies_the_port_protocol(
    factory: Callable[[bool], tuple[InterventionPolicies, str]],
) -> None:
    port, _ = factory(True)
    assert isinstance(port, InterventionPolicies) and port.name


@pytest.mark.req("TER-INT-008")
def test_policies_are_deterministic_and_complete(
    factory: Callable[[bool], tuple[InterventionPolicies, str]],
) -> None:
    port, ref = factory(True)
    first = port.policies(ref)
    assert first == port.policies(ref) == _policies(True)
    assert first.state is ApprovalState.APPROVED


@pytest.mark.req("TER-INT-008")
def test_unknown_ref_raises(
    factory: Callable[[bool], tuple[InterventionPolicies, str]],
) -> None:
    port, _ = factory(True)
    with pytest.raises(PolicyError, match="no such policy file"):
        port.policies("missing.json")


@pytest.mark.req("TER-INT-015")
def test_never_approves_on_its_own(
    factory: Callable[[bool], tuple[InterventionPolicies, str]],
) -> None:
    port, ref = factory(False)
    policies = port.policies(ref)
    assert policies.state is ApprovalState.UNAPPROVED
    with pytest.raises(UnapprovedPolicyError):
        policies.engine()


@pytest.mark.req("TER-INT-008")
@pytest.mark.parametrize("text", ["{", "[]", '{"schema": "other"}'])
def test_json_adapter_rejects_invalid_files(tmp_path: Path, text: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PolicyError, match="bad.json"):
        JsonInterventionPolicies().policies(path)


@pytest.mark.req("TER-INT-008")
def test_json_adapter_reads_a_file_saved_with_a_bom(tmp_path: Path) -> None:
    # Windows editors and PowerShell 5's Set-Content write UTF-8 with a BOM.
    path = tmp_path / "policies.json"
    text = json.dumps(_policies(True).as_dict())
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    assert JsonInterventionPolicies().policies(path) == _policies(True)
