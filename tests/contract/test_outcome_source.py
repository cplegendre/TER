"""Contract suite for the ``OutcomeSource`` port.

The JUnit XML reference adapter and the in-memory fake are given the same
recorded runs and must answer alike: the obligations in the port's
docstring, one test each. A GARE adapter (issue #52) joins the fixture
when it lands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from ter.adapters.driven.in_memory import InMemoryOutcomeSource
from ter.adapters.driven.junit import JUnitOutcomeSource
from ter.domain.outcome import (
    CheckEvidence,
    CheckStatus,
    OutcomeEvidence,
    OutcomeFormatError,
)
from ter.ports import OutcomeSource

#: Each recorded run: (check id, status, detail), in record order.
RUNS: dict[str, tuple[tuple[str, CheckStatus, str], ...]] = {
    "green": (
        ("suite.a::test_one", CheckStatus.PASSED, ""),
        ("suite.a::test_two", CheckStatus.PASSED, ""),
    ),
    "red": (
        ("suite.a::test_ok", CheckStatus.PASSED, ""),
        ("suite.a::test_bad", CheckStatus.FAILED, "assert 1 == 2"),
        ("suite.a::test_later", CheckStatus.SKIPPED, "not yet"),
        ("suite.a::test_setup", CheckStatus.ERROR, "fixture exploded"),
    ),
    "empty": (),
}

_TAG = {
    CheckStatus.FAILED: "failure",
    CheckStatus.ERROR: "error",
    CheckStatus.SKIPPED: "skipped",
}


@dataclass(frozen=True)
class Subject:
    source: OutcomeSource
    refs: dict[str, str]  # run name (and "absent", "malformed") -> reference


def _junit_xml(run: tuple[tuple[str, CheckStatus, str], ...]) -> str:
    cases = []
    for check, status, detail in run:
        classname, _, name = check.partition("::")
        child = (
            f'<{_TAG[status]} message="{detail}">trace</{_TAG[status]}>'
            if status in _TAG
            else ""
        )
        cases.append(
            f'<testcase classname="{classname}" name="{name}" time="0.5">{child}</testcase>'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="s" tests="{len(run)}">{"".join(cases)}'
        "</testsuite></testsuites>"
    )


def _junit(tmp: Path) -> Subject:
    refs = {}
    for name, run in RUNS.items():
        path = tmp / f"{name}.xml"
        path.write_text(_junit_xml(run), encoding="utf-8")
        refs[name] = str(path)
    (tmp / "malformed.xml").write_text("<testsuites><testcase", encoding="utf-8")
    refs["malformed"] = str(tmp / "malformed.xml")
    refs["absent"] = str(tmp / "absent.xml")
    return Subject(JUnitOutcomeSource(), refs)


def _memory(tmp: Path) -> Subject:
    records = {
        name: OutcomeEvidence(
            run_ref=name,
            source="in-memory",
            checks=tuple(
                CheckEvidence(check, status, f"{name}#{i}", detail, 0.5)
                for i, (check, status, detail) in enumerate(run, start=1)
            ),
        )
        for name, run in RUNS.items()
    }
    refs = {name: name for name in RUNS} | {
        "absent": "absent",
        "malformed": "malformed",
    }
    return Subject(InMemoryOutcomeSource(records, malformed=["malformed"]), refs)


Factory = Callable[[Path], Subject]


@pytest.fixture(params=[_junit, _memory], ids=["junit", "in-memory"])
def subject(request: pytest.FixtureRequest, tmp_path: Path) -> Subject:
    factory: Factory = request.param
    return factory(tmp_path)


def _evidence(subject: Subject, run: str) -> OutcomeEvidence:
    evidence = subject.source.outcome(subject.refs[run])
    assert evidence is not None
    return evidence


@pytest.mark.req("TER-OUT-001")
def test_satisfies_the_port_protocol(subject: Subject) -> None:
    assert isinstance(subject.source, OutcomeSource)
    assert subject.source.name


@pytest.mark.req("TER-OUT-002")
def test_no_recorded_outcome_is_none_not_a_failure(subject: Subject) -> None:
    assert subject.source.outcome(subject.refs["absent"]) is None


@pytest.mark.req("TER-OUT-001")
def test_same_reference_yields_equal_evidence(subject: Subject) -> None:
    for run in RUNS:
        assert _evidence(subject, run) == _evidence(subject, run)


@pytest.mark.req("TER-OUT-001")
def test_evidence_names_its_run_and_keeps_every_result_in_order(
    subject: Subject,
) -> None:
    for run, expected in RUNS.items():
        evidence = _evidence(subject, run)
        assert evidence.run_ref == subject.refs[run]
        assert evidence.source == subject.source.name
        assert [(c.check_id, c.status) for c in evidence.checks] == [
            (check, status) for check, status, _ in expected
        ]
        assert all(c.check_id.strip() and c.source for c in evidence.checks)


@pytest.mark.req("TER-OUT-001")
def test_failing_results_keep_their_status_and_detail(subject: Subject) -> None:
    red = {c.check_id: c for c in _evidence(subject, "red").checks}
    assert red["suite.a::test_bad"].status is CheckStatus.FAILED
    assert red["suite.a::test_bad"].detail == "assert 1 == 2"
    assert red["suite.a::test_setup"].status is CheckStatus.ERROR
    assert red["suite.a::test_later"].status is CheckStatus.SKIPPED
    assert red["suite.a::test_ok"].seconds == 0.5


@pytest.mark.req("TER-OUT-001")
def test_a_run_with_no_checks_is_evidence_not_absence(subject: Subject) -> None:
    assert _evidence(subject, "empty").checks == ()


@pytest.mark.req("TER-OUT-003")
def test_unreadable_record_raises_a_format_error_naming_it(subject: Subject) -> None:
    with pytest.raises(OutcomeFormatError, match="malformed"):
        subject.source.outcome(subject.refs["malformed"])
