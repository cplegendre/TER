"""The TER 4 vision points: definition of done, enforcing rules, verification.

Leigh's 200-point vision list is the root of TER 4's requirements. Each point
carries a definition of done, the EARS requirements (rules) that enforce it,
and how it is verified. This module holds the model and the lint that keeps
points and requirements linked in both directions. It is pure: whether a
named test or CI check exists is decided by a callable the caller supplies.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from .maturity import Maturity
from .requirements import VISION_POINTS, LintIssue, Requirement

MAX_DONE_STATEMENTS = 3
_POINT_ID = re.compile(r"^P(\d{3})$")


class PointKind(str, Enum):
    """What sort of point this is, which decides what kind of rule fits it."""

    CAPABILITY = "capability"
    PRINCIPLE = "principle"
    RESEARCH = "research"


class PointStatus(str, Enum):
    """Delivery status of a point."""

    DONE = "done"
    PARTIAL = "partial"
    NOT_STARTED = "not-started"


class VerificationKind(str, Enum):
    """How a verification entry proves a point.

    * ``test``: a pytest node id or test file on this branch.
    * ``ci``: the name of a CI workflow step.
    * ``branch``: a check that exists on another branch, pending merge.
    * ``planned``: what will verify the point once it is built.
    """

    TEST = "test"
    CI = "ci"
    BRANCH = "branch"
    PLANNED = "planned"


class PointError(ValueError):
    """A points catalogue entry is malformed."""


@dataclass(frozen=True)
class Verification:
    """One verification entry, written ``"<kind>: <target>"``."""

    kind: VerificationKind
    target: str

    @property
    def is_check(self) -> bool:
        """True for entries that name a check that must exist on this branch."""
        return self.kind in (VerificationKind.TEST, VerificationKind.CI)

    @classmethod
    def parse(cls, text: str) -> Verification:
        """Parse ``"test: tests/x.py::test_y"`` and similar entries.

        Raises:
            PointError: If the kind is unknown or the target is empty.
        """
        kind, sep, target = text.partition(":")
        target = target.strip()
        try:
            parsed = VerificationKind(kind.strip())
        except ValueError as exc:
            kinds = ", ".join(k.value for k in VerificationKind)
            raise PointError(
                f"verification {text!r} must start with one of {kinds}"
            ) from exc
        if not sep or not target:
            raise PointError(f"verification {text!r} names nothing")
        return cls(parsed, target)

    def __str__(self) -> str:
        return f"{self.kind.value}: {self.target}"


@dataclass(frozen=True)
class VisionPoint:
    """One of the 200 vision points."""

    number: int
    text: str
    level: Maturity
    kind: PointKind
    status: PointStatus
    definition_of_done: tuple[str, ...]
    rules: tuple[str, ...]
    verification: tuple[Verification, ...]
    issue: int | None = None
    real_data: bool = False
    real_data_verified: bool = False

    @property
    def id(self) -> str:
        return f"P{self.number:03d}"

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> VisionPoint:
        """Build a point from a parsed catalogue entry.

        Raises:
            PointError: If a field is missing or has the wrong shape.
        """
        ident = data.get("id")
        match = _POINT_ID.fullmatch(ident) if isinstance(ident, str) else None
        if match is None:
            raise PointError(
                f"point id {ident!r} does not match P001..P{VISION_POINTS}"
            )
        number = int(match.group(1))
        ident = match.group(0)
        if not 1 <= number <= VISION_POINTS:
            raise PointError(f"{ident}: outside P001..P{VISION_POINTS}")
        known = {
            "id",
            "text",
            "level",
            "kind",
            "status",
            "definition_of_done",
            "rules",
            "verification",
            "issue",
            "real_data",
            "real_data_verified",
        }
        unknown = sorted(set(data) - known)
        if unknown:
            raise PointError(f"{ident}: unknown fields {', '.join(unknown)}")
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise PointError(f"{ident}: text is required")
        level_raw = data.get("level")
        if not isinstance(level_raw, (str, int)) or isinstance(level_raw, bool):
            raise PointError(f"{ident}: level is required")
        try:
            level = Maturity.parse(level_raw)
            kind = PointKind(str(data.get("kind")))
            status = PointStatus(str(data.get("status")))
        except ValueError as exc:
            raise PointError(f"{ident}: {exc}") from exc
        done = _string_list(data, "definition_of_done", ident)
        rules = _string_list(data, "rules", ident)
        verification = tuple(
            Verification.parse(v) for v in _string_list(data, "verification", ident)
        )
        issue = data.get("issue")
        if issue is not None and (
            not isinstance(issue, int) or isinstance(issue, bool) or issue < 1
        ):
            raise PointError(f"{ident}: issue must be a positive issue number")
        flags = {}
        for key in ("real_data", "real_data_verified"):
            value = data.get(key, False)
            if not isinstance(value, bool):
                raise PointError(f"{ident}: {key} must be true or false")
            flags[key] = value
        return cls(
            number=number,
            text=" ".join(text.split()),
            level=level,
            kind=kind,
            status=status,
            definition_of_done=tuple(" ".join(d.split()) for d in done),
            rules=tuple(rules),
            verification=verification,
            issue=issue,
            real_data=flags["real_data"],
            real_data_verified=flags["real_data_verified"],
        )


def _string_list(data: Mapping[str, object], key: str, ident: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PointError(f"{ident}: {key} must be a list of strings")
    return [str(v) for v in value]


def lint_points(
    points: Sequence[VisionPoint],
    requirements: Sequence[Requirement],
    check_exists: Callable[[Verification], bool],
    total: int = VISION_POINTS,
) -> list[LintIssue]:
    """Check points and requirements against each other.

    Rules enforced:

    * every point 1..total appears exactly once;
    * each point has 1..3 definition-of-done statements, at least one rule
      and at least one verification entry;
    * every rule id exists, and links go both ways (a point lists a rule iff
      that rule lists the point in ``source_points``);
    * every requirement traces to at least one point;
    * every ``test:`` and ``ci:`` entry names a check that exists;
    * a done point has all its rules verified, or a verification entry that
      names an existing check. A done point whose only proof is on another
      branch gets a warning (pending merge), not an error;
    * a point with a real-data issue carries ``real_data: true``, and a
      ``real_data`` point is done only with ``real_data_verified: true``.
    """
    issues: list[LintIssue] = []
    by_req = {r.id: r for r in requirements}
    seen: dict[int, int] = {}
    for point in points:
        seen[point.number] = seen.get(point.number, 0) + 1
    for number in range(1, total + 1):
        if number not in seen:
            issues.append(
                LintIssue(f"P{number:03d}", "POINT-MISSING", "point is not catalogued")
            )
        elif seen[number] > 1:
            issues.append(
                LintIssue(
                    f"P{number:03d}", "POINT-DUPLICATE", "point is declared twice"
                )
            )

    for point in points:
        issues.extend(_lint_point(point, by_req, check_exists))

    listed = {(rid, p.number) for p in points for rid in p.rules}
    for requirement in requirements:
        if not requirement.source_points:
            issues.append(
                LintIssue(
                    requirement.id,
                    "REQ-ORPHAN",
                    "requirement traces to no vision point",
                )
            )
        for number in requirement.source_points:
            if (requirement.id, number) not in listed:
                issues.append(
                    LintIssue(
                        requirement.id,
                        "POINT-LINK",
                        f"cites P{number:03d} but P{number:03d} does not list it in rules",
                    )
                )
    return issues


def _lint_real_data(point: VisionPoint) -> list[LintIssue]:
    """Points that need real session data cannot be done on synthetic tests alone.

    Whether the tracking issue is closed cannot be checked offline, so the
    catalogue records it: ``real_data: true`` blocks ``status: done`` until
    ``real_data_verified: true`` is set (by the change that closes the issue).
    """
    pid = point.id
    issues: list[LintIssue] = []
    if point.issue is not None and not point.real_data:
        issues.append(
            LintIssue(
                pid,
                "POINT-REAL-DATA-FLAG",
                f"has issue #{point.issue} for real session data but not real_data: true",
            )
        )
    if point.real_data_verified and not point.real_data:
        issues.append(
            LintIssue(
                pid,
                "POINT-REAL-DATA-FLAG",
                "real_data_verified is set on a point without real_data: true",
            )
        )
    if (
        point.status is PointStatus.DONE
        and point.real_data
        and not point.real_data_verified
    ):
        tracked = f" (issue #{point.issue})" if point.issue is not None else ""
        issues.append(
            LintIssue(
                pid,
                "POINT-REAL-DATA",
                f"needs real session data{tracked}; synthetic tests alone cannot make "
                "it done until real_data_verified: true",
            )
        )
    return issues


def _lint_point(
    point: VisionPoint,
    by_req: Mapping[str, Requirement],
    check_exists: Callable[[Verification], bool],
) -> list[LintIssue]:
    pid = point.id
    issues: list[LintIssue] = []
    done = [d for d in point.definition_of_done if d]
    if (
        not done
        or len(point.definition_of_done) > MAX_DONE_STATEMENTS
        or len(done) != len(point.definition_of_done)
    ):
        issues.append(
            LintIssue(
                pid,
                "POINT-DOD",
                f"needs 1 to {MAX_DONE_STATEMENTS} non-empty definition-of-done statements",
            )
        )
    if not point.rules:
        issues.append(LintIssue(pid, "POINT-RULES", "no enforcing rule"))
    if not point.verification:
        issues.append(LintIssue(pid, "POINT-VERIFY", "no verification entry"))
    linked: list[Requirement] = []
    for rid in point.rules:
        requirement = by_req.get(rid)
        if requirement is None:
            issues.append(
                LintIssue(
                    pid, "POINT-RULE-UNKNOWN", f"rule {rid} is not in the catalogue"
                )
            )
        elif point.number not in requirement.source_points:
            issues.append(
                LintIssue(
                    pid,
                    "POINT-LINK",
                    f"lists {rid} but {rid} does not cite {pid} in source_points",
                )
            )
            linked.append(requirement)
        else:
            linked.append(requirement)
    missing = [v for v in point.verification if v.is_check and not check_exists(v)]
    for entry in missing:
        issues.append(LintIssue(pid, "POINT-CHECK-MISSING", f"{entry} does not exist"))
    issues.extend(_lint_real_data(point))
    if point.status is PointStatus.DONE:
        all_verified = bool(linked) and all(r.verified for r in linked)
        has_check = any(v.is_check and v not in missing for v in point.verification)
        pending = any(v.kind is VerificationKind.BRANCH for v in point.verification)
        if not (all_verified or has_check):
            if pending:
                issues.append(
                    LintIssue(
                        pid,
                        "POINT-PENDING",
                        "done on another branch; verified here once that branch merges",
                        warning=True,
                    )
                )
            else:
                issues.append(
                    LintIssue(
                        pid,
                        "POINT-DONE",
                        "marked done but no rule is verified and no existing check is named",
                    )
                )
    return issues


@dataclass(frozen=True)
class PointSummary:
    """Counts of points by status, overall and per level."""

    by_status: dict[PointStatus, int]
    by_level: dict[Maturity, dict[PointStatus, int]]


def summarise(points: Iterable[VisionPoint]) -> PointSummary:
    """Count points by status and by level."""
    by_status = {status: 0 for status in PointStatus}
    by_level = {level: {status: 0 for status in PointStatus} for level in Maturity}
    for point in points:
        by_status[point.status] += 1
        by_level[point.level][point.status] += 1
    return PointSummary(by_status, by_level)
