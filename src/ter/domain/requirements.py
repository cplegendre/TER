"""EARS requirements: the catalogue model, grammar lint and traceability.

Every TER 4 behaviour is a requirement written in EARS (Easy Approach to
Requirements Syntax). This module is pure: it validates requirement text
against the six EARS templates, checks catalogue invariants and computes
forward, backward and point traces from data handed to it. Loading YAML and
reading test results is the job of adapters.

The six templates:

* Ubiquitous: ``The <system> shall <response>.``
* Event-driven: ``When <trigger>, the <system> shall <response>.``
* State-driven: ``While <state>, the <system> shall <response>.``
* Unwanted: ``If <condition>, then the <system> shall <response>.``
* Optional: ``Where <feature>, the <system> shall <response>.``
* Complex: two or more of the clauses above, in the order
  Where, While, When or If.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from .maturity import Maturity

VISION_POINTS = 200
"""Size of the owner's numbered vision list that ``source_points`` refer to."""

REQUIREMENT_ID = re.compile(r"^TER-[A-Z]{3}-\d{3}$")

BANNED_WORDS: tuple[str, ...] = (
    "should",
    "may",
    "might",
    "could",
    "must",
    "fast",
    "quickly",
    "slow",
    "appropriate",
    "appropriately",
    "efficient",
    "efficiently",
    "user-friendly",
    "easy",
    "easily",
    "adequate",
    "sufficient",
    "robust",
    "seamless",
    "seamlessly",
    "optimal",
    "etc",
    "and/or",
    "tbd",
)
"""Weak modals and unbounded adjectives an EARS requirement must not use."""

_CLAUSE_RANK: dict[str, int] = {"where": 0, "while": 1, "when": 2, "if": 2}
_MAX_SUBJECT_WORDS = 6


class EarsPattern(str, Enum):
    """The six EARS templates."""

    UBIQUITOUS = "ubiquitous"
    EVENT_DRIVEN = "event-driven"
    STATE_DRIVEN = "state-driven"
    UNWANTED = "unwanted"
    OPTIONAL = "optional"
    COMPLEX = "complex"


class RequirementStatus(str, Enum):
    """Lifecycle of a requirement. The CI gate enforces only verified ones."""

    PLANNED = "planned"
    VERIFIED = "verified"


_SINGLE_CLAUSE_PATTERN: dict[str, EarsPattern] = {
    "where": EarsPattern.OPTIONAL,
    "while": EarsPattern.STATE_DRIVEN,
    "when": EarsPattern.EVENT_DRIVEN,
    "if": EarsPattern.UNWANTED,
}


class RequirementError(ValueError):
    """A catalogue entry is malformed (missing field, bad id, bad level...)."""


@dataclass(frozen=True)
class Requirement:
    """One EARS requirement from the catalogue."""

    id: str
    pattern: EarsPattern
    text: str
    level: Maturity
    status: RequirementStatus
    rationale: str
    source_points: tuple[int, ...] = ()
    port: str | None = None

    @property
    def verified(self) -> bool:
        return self.status is RequirementStatus.VERIFIED

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Requirement:
        """Build a requirement from a parsed catalogue entry.

        Raises:
            RequirementError: If a field is missing or has the wrong shape.
        """
        known = {
            "id",
            "pattern",
            "text",
            "level",
            "status",
            "rationale",
            "source_points",
            "port",
        }
        unknown = sorted(set(data) - known)
        ident = data.get("id")
        if not isinstance(ident, str) or not REQUIREMENT_ID.fullmatch(ident):
            raise RequirementError(f"id {ident!r} does not match TER-<AREA>-NNN")
        if unknown:
            raise RequirementError(f"{ident}: unknown fields {', '.join(unknown)}")
        text = _required_str(data, "text", ident)
        rationale = _required_str(data, "rationale", ident)
        try:
            pattern = EarsPattern(_required_str(data, "pattern", ident))
            status = RequirementStatus(_required_str(data, "status", ident))
        except ValueError as exc:
            raise RequirementError(f"{ident}: {exc}") from exc
        level_raw = data.get("level")
        if not isinstance(level_raw, (str, int)) or isinstance(level_raw, bool):
            raise RequirementError(f"{ident}: level is required")
        try:
            level = Maturity.parse(level_raw)
        except ValueError as exc:
            raise RequirementError(f"{ident}: {exc}") from exc
        points_raw = data.get("source_points", [])
        if not isinstance(points_raw, list) or not all(
            isinstance(p, int) and not isinstance(p, bool) for p in points_raw
        ):
            raise RequirementError(f"{ident}: source_points must be a list of integers")
        bad = [p for p in points_raw if not 1 <= p <= VISION_POINTS]
        if bad:
            raise RequirementError(
                f"{ident}: source_points {bad} outside 1..{VISION_POINTS}"
            )
        port = data.get("port")
        if port is not None and not isinstance(port, str):
            raise RequirementError(f"{ident}: port must be a string")
        return cls(
            id=ident,
            pattern=pattern,
            text=" ".join(text.split()),
            level=level,
            status=status,
            rationale=rationale.strip(),
            source_points=tuple(sorted(set(points_raw))),
            port=port,
        )


def _required_str(data: Mapping[str, object], key: str, ident: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RequirementError(f"{ident}: {key} is required")
    return value


# --------------------------------------------------------------------------
# Grammar lint
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LintIssue:
    """One problem found in a requirement, a vision point or the catalogue.

    ``requirement_id`` names the subject: a requirement id or a point id.
    Warnings are reported but do not fail the lint.
    """

    requirement_id: str
    code: str
    message: str
    warning: bool = False

    def __str__(self) -> str:
        prefix = "warning: " if self.warning else ""
        return f"{prefix}{self.requirement_id}: [{self.code}] {self.message}"


@dataclass(frozen=True)
class ParsedEars:
    """The structure recovered from an EARS sentence."""

    clauses: tuple[tuple[str, str], ...]
    subject: str
    response: str

    @property
    def pattern(self) -> EarsPattern:
        kinds = [keyword for keyword, _ in self.clauses]
        if not kinds:
            return EarsPattern.UBIQUITOUS
        if len(kinds) == 1:
            return _SINGLE_CLAUSE_PATTERN[kinds[0]]
        return EarsPattern.COMPLEX


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z/\-]*", text)


def parse_ears(text: str) -> tuple[ParsedEars | None, list[tuple[str, str]]]:
    """Parse EARS text into clauses, subject and response.

    Returns the parse (or None when the sentence cannot be parsed) and a list
    of ``(code, message)`` problems found on the way.
    """
    problems: list[tuple[str, str]] = []
    sentence = " ".join(text.split())
    if not sentence.endswith("."):
        problems.append(("EARS-END", "text must end with a full stop"))
    if sentence[:1] and not sentence[0].isupper():
        problems.append(("EARS-CASE", "text must start with a capital letter"))
    shalls = [
        m.start() for m in re.finditer(r"\bshall\b", sentence, flags=re.IGNORECASE)
    ]
    if len(shalls) != 1:
        problems.append(
            (
                "EARS-SHALL",
                f"text must contain exactly one 'shall' (found {len(shalls)})",
            )
        )
        if not shalls:
            return None, problems
    head = sentence[: shalls[0]].strip()
    response = sentence[shalls[0] + len("shall") :].strip().rstrip(".").strip()
    if not response:
        problems.append(("EARS-RESPONSE", "no system response after 'shall'"))

    segments = [s.strip() for s in head.split(",")] if head else [""]
    subject = segments[-1]
    clauses: list[list[str]] = []
    for segment in segments[:-1]:
        first = segment.split(" ", 1)[0].lower()
        if first in _CLAUSE_RANK:
            body = segment[len(first) :].strip()
            clauses.append([first, body])
        elif clauses:
            clauses[-1][1] = f"{clauses[-1][1]}, {segment}".strip(", ")
        else:
            problems.append(
                (
                    "EARS-CLAUSE",
                    f"clause {segment!r} must start with Where, While, When or If",
                )
            )

    has_then = subject.lower().startswith("then ")
    if has_then:
        subject = subject[len("then ") :].strip()
    kinds = [k for k, _ in clauses]
    if "if" in kinds and not has_then:
        problems.append(("EARS-THEN", "an If clause needs 'then' before the system"))
    if has_then and "if" not in kinds:
        problems.append(("EARS-THEN", "'then' is only used after an If clause"))
    for keyword, body in clauses:
        if not body:
            problems.append(("EARS-CLAUSE", f"empty {keyword.capitalize()} clause"))
    if len(set(kinds)) != len(kinds):
        problems.append(
            ("EARS-ORDER", "each of Where, While, When, If may appear once")
        )
    ranks = [_CLAUSE_RANK[k] for k in kinds]
    if ranks != sorted(ranks):
        problems.append(
            ("EARS-ORDER", "clauses must appear in the order Where, While, When/If")
        )
    if "when" in kinds and "if" in kinds:
        problems.append(
            ("EARS-ORDER", "a requirement is either event-driven or unwanted, not both")
        )

    subject_words = _words(subject)
    if not subject_words:
        problems.append(("EARS-SUBJECT", "no system named before 'shall'"))
    elif len(subject_words) > _MAX_SUBJECT_WORDS:
        problems.append(
            (
                "EARS-SUBJECT",
                f"system name {subject!r} is longer than {_MAX_SUBJECT_WORDS} words; "
                "is a comma missing after a clause?",
            )
        )
    elif subject_words[0].lower() in _CLAUSE_RANK:
        problems.append(("EARS-CLAUSE", "a clause must be followed by a comma"))
    parsed = ParsedEars(
        clauses=tuple((k, b) for k, b in clauses), subject=subject, response=response
    )
    return parsed, problems


def banned_words_in(text: str, banned: Iterable[str] = BANNED_WORDS) -> list[str]:
    """Return banned words found in ``text``, in catalogue order."""
    lowered = text.lower()
    found = []
    for word in banned:
        if re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", lowered):
            found.append(word)
    return found


def lint_requirement(
    requirement: Requirement,
    vocabulary: Mapping[str, str] | None = None,
) -> list[LintIssue]:
    """Check one requirement against the EARS grammar and house rules.

    Args:
        requirement: The requirement to check.
        vocabulary: Optional controlled vocabulary mapping a discouraged term
            to the preferred one (``{"transcript": "session trace"}``).
    """
    rid = requirement.id
    issues: list[LintIssue] = []
    parsed, problems = parse_ears(requirement.text)
    issues.extend(LintIssue(rid, code, msg) for code, msg in problems)
    if (
        parsed is not None
        and not problems
        and parsed.pattern is not requirement.pattern
    ):
        issues.append(
            LintIssue(
                rid,
                "EARS-PATTERN",
                f"declared {requirement.pattern.value} but text reads as {parsed.pattern.value}",
            )
        )
    for word in banned_words_in(requirement.text):
        issues.append(LintIssue(rid, "EARS-VAGUE", f"banned word {word!r}"))
    for term, preferred in (vocabulary or {}).items():
        if re.search(rf"\b{re.escape(term.lower())}\b", requirement.text.lower()):
            issues.append(
                LintIssue(rid, "EARS-VOCAB", f"use {preferred!r} instead of {term!r}")
            )
    return issues


def lint_catalogue(
    requirements: Sequence[Requirement],
    vocabulary: Mapping[str, str] | None = None,
) -> list[LintIssue]:
    """Lint every requirement and check catalogue-wide invariants."""
    issues: list[LintIssue] = []
    seen: set[str] = set()
    for requirement in requirements:
        if requirement.id in seen:
            issues.append(
                LintIssue(requirement.id, "CAT-DUPLICATE", "id is declared twice")
            )
        seen.add(requirement.id)
        issues.extend(lint_requirement(requirement, vocabulary))
    return issues


# --------------------------------------------------------------------------
# Traceability
# --------------------------------------------------------------------------

PASSED = "passed"


@dataclass(frozen=True)
class TestOutcome:
    """The result of one test that cites a requirement."""

    __test__ = False  # not a pytest test class

    nodeid: str
    outcome: str

    @property
    def passed(self) -> bool:
        return self.outcome == PASSED


@dataclass(frozen=True)
class ForwardGap:
    """A verified requirement at or below the gate without a passing test."""

    requirement: Requirement
    outcomes: tuple[TestOutcome, ...]

    @property
    def reason(self) -> str:
        if not self.outcomes:
            return "no test cites it"
        summary = ", ".join(sorted({o.outcome for o in self.outcomes}))
        return f"{len(self.outcomes)} citing test(s), none passed ({summary})"


@dataclass(frozen=True)
class LevelCoverage:
    """Requirement counts for one maturity level."""

    level: Maturity
    total: int
    verified: int
    traced: int

    @property
    def planned(self) -> int:
        return self.total - self.verified

    @property
    def ratio(self) -> float:
        """Share of this level's requirements that are verified and traced."""
        return self.traced / self.total if self.total else 0.0


@dataclass(frozen=True)
class TraceReport:
    """Everything the traceability gate and the coverage report need."""

    gate: Maturity
    forward_gaps: tuple[ForwardGap, ...]
    unknown_ids: tuple[tuple[str, tuple[str, ...]], ...]
    uncovered_points: tuple[int, ...]
    levels: tuple[LevelCoverage, ...]
    promotable: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """True when the gate passes: no forward gaps and no unknown ids."""
        return not self.forward_gaps and not self.unknown_ids


def forward_trace(
    requirements: Iterable[Requirement],
    results: Mapping[str, Sequence[TestOutcome]],
    gate: Maturity,
) -> list[ForwardGap]:
    """Verified requirements at or below ``gate`` that no passing test covers."""
    gaps = []
    for requirement in requirements:
        if not requirement.verified or not gate.permits(requirement.level):
            continue
        outcomes = tuple(results.get(requirement.id, ()))
        if not any(o.passed for o in outcomes):
            gaps.append(ForwardGap(requirement, outcomes))
    return gaps


def backward_trace(
    requirements: Iterable[Requirement],
    citations: Mapping[str, Sequence[str]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Cited ids that are not in the catalogue, with the tests citing them."""
    known = {r.id for r in requirements}
    return [
        (rid, tuple(where))
        for rid, where in sorted(citations.items())
        if rid not in known
    ]


def point_trace(
    requirements: Iterable[Requirement], total: int = VISION_POINTS
) -> list[int]:
    """Vision points (1..total) that no requirement cites."""
    covered = {p for r in requirements for p in r.source_points}
    return [p for p in range(1, total + 1) if p not in covered]


def level_coverage(
    requirements: Iterable[Requirement],
    results: Mapping[str, Sequence[TestOutcome]],
) -> list[LevelCoverage]:
    """Per-level totals, one entry for every maturity level."""
    by_level: dict[Maturity, list[Requirement]] = {level: [] for level in Maturity}
    for requirement in requirements:
        by_level[requirement.level].append(requirement)
    rows = []
    for level, members in by_level.items():
        verified = [r for r in members if r.verified]
        traced = [r for r in verified if any(o.passed for o in results.get(r.id, ()))]
        rows.append(LevelCoverage(level, len(members), len(verified), len(traced)))
    return rows


def trace(
    requirements: Sequence[Requirement],
    results: Mapping[str, Sequence[TestOutcome]],
    gate: Maturity,
) -> TraceReport:
    """Run the forward, backward and point traces in one pass."""
    citations = {rid: [o.nodeid for o in outcomes] for rid, outcomes in results.items()}
    promotable = tuple(
        r.id
        for r in requirements
        if not r.verified and any(o.passed for o in results.get(r.id, ()))
    )
    return TraceReport(
        gate=gate,
        forward_gaps=tuple(forward_trace(requirements, results, gate)),
        unknown_ids=tuple(backward_trace(requirements, citations)),
        uncovered_points=tuple(point_trace(requirements)),
        levels=tuple(level_coverage(requirements, results)),
        promotable=promotable,
    )
