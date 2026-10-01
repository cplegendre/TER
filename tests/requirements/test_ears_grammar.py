"""EARS grammar lint and the Requirement model."""

from __future__ import annotations

from typing import Any

import pytest

from ter.domain import Maturity
from ter.domain.requirements import (
    EarsPattern,
    LintIssue,
    Requirement,
    RequirementError,
    RequirementStatus,
    banned_words_in,
    lint_catalogue,
    lint_requirement,
    parse_ears,
)


def _req(text: str, pattern: str, rid: str = "TER-TST-001") -> Requirement:
    return Requirement.from_mapping(
        {
            "id": rid,
            "pattern": pattern,
            "text": text,
            "level": "L0",
            "status": "planned",
            "rationale": "Because.",
        }
    )


def _codes(
    requirement: Requirement, vocabulary: dict[str, str] | None = None
) -> set[str]:
    return {issue.code for issue in lint_requirement(requirement, vocabulary)}


GOOD = [
    ("TER shall count tokens.", "ubiquitous"),
    ("The session source shall emit one event per record.", "ubiquitous"),
    ("When a hook fires, the hook adapter shall append one event.", "event-driven"),
    ("While a session is live, TER shall refresh the score every 5 s.", "state-driven"),
    ("If a record is malformed, then TER shall count it as unrecognised.", "unwanted"),
    ("Where embeddings are installed, TER shall use them for alignment.", "optional"),
    (
        "Where hooks are enabled, while a session is live, when a tool completes, "
        "TER shall append one event.",
        "complex",
    ),
    (
        "While a session is live, if the store is full, then TER shall drop no event.",
        "complex",
    ),
    (
        "When a tool call completes, and the hook is enabled, TER shall append one event.",
        "event-driven",
    ),
]


@pytest.mark.req("TER-REQ-001")
@pytest.mark.parametrize(("text", "pattern"), GOOD)
def test_well_formed_requirements_pass(text: str, pattern: str) -> None:
    requirement = _req(text, pattern)
    assert lint_requirement(requirement) == []
    parsed, problems = parse_ears(text)
    assert parsed is not None and not problems
    assert parsed.pattern is EarsPattern(pattern)


BAD = [
    ("TER shall count tokens", "ubiquitous", "EARS-END"),
    ("ter shall count tokens.", "ubiquitous", "EARS-CASE"),
    ("TER counts tokens.", "ubiquitous", "EARS-SHALL"),
    ("TER shall count tokens and shall log them.", "ubiquitous", "EARS-SHALL"),
    ("TER shall.", "ubiquitous", "EARS-RESPONSE"),
    ("When a hook fires, TER shall append one event.", "ubiquitous", "EARS-PATTERN"),
    ("TER shall count tokens.", "event-driven", "EARS-PATTERN"),
    ("If a record is malformed, TER shall count it.", "unwanted", "EARS-THEN"),
    (
        "When a hook fires, then TER shall append one event.",
        "event-driven",
        "EARS-THEN",
    ),
    ("When , TER shall append one event.", "event-driven", "EARS-CLAUSE"),
    ("Because hooks fire, TER shall append one event.", "ubiquitous", "EARS-CLAUSE"),
    ("When a hook fires TER shall append one event.", "event-driven", "EARS-CLAUSE"),
    ("When a hook fires, when it fires, TER shall append.", "complex", "EARS-ORDER"),
    (
        "When a hook fires, while live, TER shall append one event.",
        "complex",
        "EARS-ORDER",
    ),
    (
        "When a hook fires, if it fails, then TER shall append one event.",
        "complex",
        "EARS-ORDER",
    ),
    (", TER shall count.", "ubiquitous", "EARS-CLAUSE"),
    ("When a hook fires, shall append one event.", "event-driven", "EARS-SUBJECT"),
    (
        "The session source adapter for the claude code harness shall emit events.",
        "ubiquitous",
        "EARS-SUBJECT",
    ),
    ("TER shall respond quickly.", "ubiquitous", "EARS-VAGUE"),
    ("TER should count tokens.", "ubiquitous", "EARS-SHALL"),
    ("TER shall count tokens as appropriate.", "ubiquitous", "EARS-VAGUE"),
]


@pytest.mark.req("TER-REQ-001")
@pytest.mark.parametrize(("text", "pattern", "code"), BAD)
def test_malformed_requirements_are_rejected(
    text: str, pattern: str, code: str
) -> None:
    assert code in _codes(_req(text, pattern))


def test_banned_words_match_whole_words_only() -> None:
    assert banned_words_in("TER shall run fast.") == ["fast"]
    assert banned_words_in("TER shall use the fastText model and mayfly.") == []
    assert banned_words_in("TER shall read x and/or y.") == ["and/or"]


def test_controlled_vocabulary_suggests_preferred_term() -> None:
    requirement = _req("TER shall read the transcript.", "ubiquitous")
    issues = lint_requirement(requirement, {"transcript": "session"})
    assert [i.code for i in issues] == ["EARS-VOCAB"]
    assert "session" in str(issues[0])


def test_catalogue_rejects_duplicate_ids() -> None:
    a = _req("TER shall count tokens.", "ubiquitous")
    issues = lint_catalogue([a, a])
    assert [i.code for i in issues] == ["CAT-DUPLICATE"]
    assert str(issues[0]) == "TER-TST-001: [CAT-DUPLICATE] id is declared twice"


def test_lint_issue_is_readable() -> None:
    assert str(LintIssue("TER-X", "C", "m")) == "TER-X: [C] m"


BASE: dict[str, Any] = {
    "id": "TER-TST-001",
    "pattern": "ubiquitous",
    "text": "TER  shall\n count tokens.",
    "level": "L2",
    "status": "verified",
    "rationale": " Why. ",
    "source_points": [7, 3, 7],
    "port": "Tokenizer",
}


def test_from_mapping_normalises_fields() -> None:
    requirement = Requirement.from_mapping(BASE)
    assert requirement.text == "TER shall count tokens."
    assert requirement.rationale == "Why."
    assert requirement.source_points == (3, 7)
    assert requirement.level is Maturity.EXPLAINED
    assert requirement.status is RequirementStatus.VERIFIED and requirement.verified
    assert requirement.port == "Tokenizer"
    assert Requirement.from_mapping({**BASE, "level": 1}).level is Maturity.OBSERVED


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"id": "ANL-1"}, "does not match"),
        ({"id": None}, "does not match"),
        ({"id": "TER-ANL-001\n"}, "does not match"),
        ({"extra": 1}, "unknown fields extra"),
        ({"text": ""}, "text is required"),
        ({"rationale": None}, "rationale is required"),
        ({"pattern": "narrative"}, "narrative"),
        ({"status": "done"}, "done"),
        ({"level": None}, "level is required"),
        ({"level": True}, "level is required"),
        ({"level": "L9"}, "Unknown maturity level"),
        ({"source_points": "1"}, "list of integers"),
        ({"source_points": [True]}, "list of integers"),
        ({"source_points": [0, 10000]}, "outside 1..9999"),
        ({"port": 3}, "port must be a string"),
    ],
)
def test_from_mapping_rejects_malformed_entries(
    change: dict[str, Any], message: str
) -> None:
    with pytest.raises(RequirementError, match=message):
        Requirement.from_mapping({**BASE, **change})
