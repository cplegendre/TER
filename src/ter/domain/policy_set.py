"""L4 Advisory: intervention policies as a declarative, human-approved document.

A policy file (``ter.intervention-policies/1``, TER-INT-008, points 163 and
164) states each policy's detectors, evidence requirement, confidence
threshold, hysteresis, cooldown, permitted actions and advice, plus the
calibration policy's eligibility list (TER-INT-010) with its judged basis::

    {
      "schema": "ter.intervention-policies/1",
      "eligibility": {"detectors": ["fragmented_edits"], "basis": "judge-l4"},
      "policies": [
        {"id": "batch-edits", "detectors": ["fragmented_edits"],
         "advice": {"kind": "recommendation", "message": "Batch the edits."},
         "min_evidence": 2, "min_confidence": 0.7, "min_signals": 1,
         "cooldown_tool_events": 10, "actions": ["advise"]}
      ],
      "approval": {"approved_by": "Leigh", "approved_on": "2026-10-12",
                   "digest": "sha256:…"}
    }

The ``approval`` record is the human's sign-off (TER-INT-015, point 173): it
names who approved, when, and the digest of everything else in the file. A
file with no approval, or whose content changed since it was approved (its
digest no longer matches), is :attr:`ApprovalState.UNAPPROVED` or
:attr:`ApprovalState.CHANGED`, and :meth:`PolicySet.engine` refuses to build
an Intervention Engine from it. Only a person running ``python -m ter policy
approve`` writes the record; nothing in TER approves on its own.

Pure: parsing, digests and approval checks read no files.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from .intervention import (
    Action,
    Advice,
    AdviceKind,
    Eligibility,
    InterventionEngine,
    Policy,
    PolicyError,
)
from .maturity import Maturity

__all__ = [
    "POLICY_SCHEMA",
    "Approval",
    "ApprovalState",
    "PolicySet",
    "UnapprovedPolicyError",
]

POLICY_SCHEMA = "ter.intervention-policies/1"

#: Policy keys and their defaults, in the order a file lists them.
_POLICY_KEYS = (
    "id",
    "detectors",
    "advice",
    "min_evidence",
    "min_confidence",
    "min_signals",
    "cooldown_tool_events",
    "actions",
)


class UnapprovedPolicyError(PolicyError):
    """A policy file no human has approved in its current form (TER-INT-015)."""


class ApprovalState(StrEnum):
    """Whether a policy file may be applied."""

    APPROVED = "approved"
    UNAPPROVED = "unapproved"
    CHANGED = "changed since approval"


@dataclass(frozen=True)
class Approval:
    """A person's sign-off on one exact policy content (its digest)."""

    approved_by: str
    approved_on: date
    digest: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.approved_by.strip():
            raise PolicyError("an approval names the person who approved")
        if not self.digest.startswith("sha256:"):
            raise PolicyError("an approval records the sha256 digest it approved")

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "approved_by": self.approved_by,
            "approved_on": self.approved_on.isoformat(),
            "digest": self.digest,
        }
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class PolicySet:
    """The policies and eligibility list of one policy file, with its approval."""

    policies: tuple[Policy, ...]
    eligibility: Eligibility
    approval: Approval | None = None

    def __post_init__(self) -> None:
        ids = [p.id for p in self.policies]
        if len(ids) != len(set(ids)):
            raise PolicyError("policy ids must be unique")

    # --- approval ---------------------------------------------------------

    def digest(self) -> str:
        """The sha256 of everything but the approval, in canonical JSON."""
        content = {k: v for k, v in self.as_dict().items() if k != "approval"}
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def state(self) -> ApprovalState:
        if self.approval is None:
            return ApprovalState.UNAPPROVED
        if self.approval.digest != self.digest():
            return ApprovalState.CHANGED
        return ApprovalState.APPROVED

    def approve(self, by: str, on: date, note: str = "") -> PolicySet:
        """This content, signed off by ``by`` on ``on``."""
        return replace(self, approval=Approval(by, on, self.digest(), note))

    def require_approved(self) -> PolicySet:
        state = self.state
        if state is ApprovalState.UNAPPROVED:
            raise UnapprovedPolicyError(
                "the policy file has no approval: a person must run "
                "`python -m ter policy approve` before it applies (TER-INT-015)"
            )
        if state is ApprovalState.CHANGED:
            assert self.approval is not None
            raise UnapprovedPolicyError(
                f"the policy file changed after {self.approval.approved_by} "
                f"approved it on {self.approval.approved_on.isoformat()}; "
                "approve the new content before it applies (TER-INT-015)"
            )
        return self

    def engine(self, ceiling: Maturity = Maturity.ADVISORY) -> InterventionEngine:
        """An Intervention Engine for these policies, only once approved."""
        self.require_approved()
        return InterventionEngine(self.policies, self.eligibility, ceiling)

    def ineligible(self) -> tuple[tuple[str, str], ...]:
        """(policy, detector) pairs the eligibility list does not allow."""
        return tuple(
            (p.id, d)
            for p in self.policies
            for d in sorted(p.detectors)
            if not self.eligibility.allows(d)
        )

    # --- the document -----------------------------------------------------

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "schema": POLICY_SCHEMA,
            "eligibility": {
                "detectors": sorted(self.eligibility.detectors),
                "basis": self.eligibility.basis,
            },
            "policies": [_policy_dict(p) for p in self.policies],
        }
        if self.approval is not None:
            out["approval"] = self.approval.as_dict()
        return out

    @classmethod
    def from_mapping(cls, document: object) -> PolicySet:
        """Parse a ``ter.intervention-policies/1`` document.

        Raises:
            PolicyError: If the document is malformed, names an unknown key,
                or holds a policy that could fire on a token count alone or
                take an action other than advice.
        """
        doc = _mapping(document, "the policy file")
        _known(doc, ("schema", "eligibility", "policies", "approval"), "the file")
        if doc.get("schema") != POLICY_SCHEMA:
            raise PolicyError(
                f"schema must be {POLICY_SCHEMA!r}, not {doc.get('schema')!r}"
            )
        elig = _mapping(doc.get("eligibility", {}), "eligibility")
        _known(elig, ("detectors", "basis"), "eligibility")
        eligibility = Eligibility(
            frozenset(_strings(elig.get("detectors", []), "eligibility.detectors")),
            _string(elig.get("basis", ""), "eligibility.basis", empty=True),
        )
        raw = doc.get("policies", [])
        if not isinstance(raw, list):
            raise PolicyError("policies must be a list")
        policies = tuple(
            _policy(_mapping(p, f"policies[{i}]")) for i, p in enumerate(raw)
        )
        approval = None
        if "approval" in doc:
            approval = _approval(_mapping(doc["approval"], "approval"))
        return cls(policies, eligibility, approval)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _policy_dict(p: Policy) -> dict[str, object]:
    return {
        "id": p.id,
        "detectors": sorted(p.detectors),
        "advice": {"kind": p.advice.kind.value, "message": p.advice.message},
        "min_evidence": p.min_evidence,
        "min_confidence": p.min_confidence,
        "min_signals": p.min_signals,
        "cooldown_tool_events": p.cooldown_tool_events,
        "actions": sorted(a.value for a in p.actions),
    }


def _policy(raw: Mapping[str, object]) -> Policy:
    pid = _string(raw.get("id", ""), "a policy's id", empty=True)
    where = f"policy {pid or '?'}"
    _known(raw, _POLICY_KEYS, where)
    advice = _mapping(raw.get("advice"), f"{where}: advice")
    _known(advice, ("kind", "message"), f"{where}: advice")
    try:
        kind = AdviceKind(_string(advice.get("kind"), f"{where}: advice.kind"))
    except ValueError:
        kinds = ", ".join(k.value for k in AdviceKind)
        raise PolicyError(f"{where}: advice.kind must be one of {kinds}") from None
    actions = frozenset(
        _action(a, where) for a in _strings(raw.get("actions", ["advise"]), where)
    )
    defaults = Policy("x", frozenset({"x"}), Advice(kind, "x"))
    return Policy(
        id=pid,
        detectors=frozenset(_strings(raw.get("detectors", []), f"{where}: detectors")),
        advice=Advice(kind, _string(advice.get("message"), f"{where}: advice.message")),
        min_evidence=_int(raw.get("min_evidence", defaults.min_evidence), where),
        min_confidence=_number(
            raw.get("min_confidence", defaults.min_confidence), where
        ),
        min_signals=_int(raw.get("min_signals", defaults.min_signals), where),
        cooldown_tool_events=_int(
            raw.get("cooldown_tool_events", defaults.cooldown_tool_events), where
        ),
        actions=actions,
    )


def _action(name: str, where: str) -> Action:
    try:
        return Action(name)
    except ValueError:
        raise PolicyError(
            f"{where}: action {name!r} is not permitted at L4 Advisory, which "
            "only advises; corrective actions belong to L5"
        ) from None


def _approval(raw: Mapping[str, object]) -> Approval:
    _known(raw, ("approved_by", "approved_on", "digest", "note"), "approval")
    on = _string(raw.get("approved_on"), "approval.approved_on")
    try:
        day = date.fromisoformat(on)
    except ValueError:
        raise PolicyError(f"approval.approved_on {on!r} is not a date") from None
    return Approval(
        _string(raw.get("approved_by"), "approval.approved_by"),
        day,
        _string(raw.get("digest"), "approval.digest"),
        _string(raw.get("note", ""), "approval.note", empty=True),
    )


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{what} must be an object")
    return value


def _known(raw: Mapping[str, object], keys: Sequence[str], where: str) -> None:
    unknown = sorted(set(raw) - set(keys))
    if unknown:
        raise PolicyError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _string(value: object, what: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise PolicyError(f"{what} must be a non-empty string")
    return value


def _strings(value: object, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"{what} must be a list of strings")
    return [str(v) for v in value]


def _int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(f"{where}: counts must be whole numbers, not {value!r}")
    return value


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"{where}: min_confidence must be a number, not {value!r}")
    return float(value)
