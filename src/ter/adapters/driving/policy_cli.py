"""``python -m ter policy``: L4 intervention policy files (TER-INT-008, TER-INT-015).

Commands::

    python -m ter policy check FILE [--source NAME]
    python -m ter policy approve FILE --by NAME [--note TEXT] [--date YYYY-MM-DD]

``check`` validates a ``ter.intervention-policies/1`` file and says whether
it may be applied: exit 0 when it is approved in its current form, 1 when it
has no approval or changed since it was approved, 2 when it is not a valid
policy file. ``approve`` is the human sign-off: it records who approved the
file, when, and the digest of its content, and rewrites the file with that
record. Run it yourself after reading the policies; TER never approves on
its own, and any later edit voids the approval.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO

from ...domain.capabilities import CapabilityError
from ...domain.intervention import PolicyError
from ...domain.policy_set import ApprovalState, PolicySet
from ...ports.driven import InterventionPolicies

__all__ = [
    "PolicyServices",
    "add_policy_parser",
    "format_policies",
    "run_policy",
]


@dataclass(frozen=True)
class PolicyServices:
    """What ``python -m ter policy`` needs, wired by the composition root."""

    #: ``source(name)``: the ``InterventionPolicies.<name>`` capability.
    source: Callable[[str], InterventionPolicies]
    today: Callable[[], date]


def add_policy_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    policy = commands.add_parser(
        "policy", help="L4: check and approve intervention policy files"
    )
    sub = policy.add_subparsers(dest="policy_command", required=True)
    check = sub.add_parser(
        "check", help="validate a policy file and show whether it may be applied"
    )
    check.add_argument("file", type=Path, help="ter.intervention-policies/1 file")
    check.add_argument(
        "--source", default="json", help="InterventionPolicies capability (json)"
    )
    approve = sub.add_parser(
        "approve", help="record your approval of a policy file's current content"
    )
    approve.add_argument("file", type=Path, help="ter.intervention-policies/1 file")
    approve.add_argument("--by", required=True, help="who is approving (you)")
    approve.add_argument("--note", default="", help="why, or what was reviewed")
    approve.add_argument("--date", help="approval date (default: today)")


def run_policy(
    args: argparse.Namespace,
    services: PolicyServices | None,
    out: IO[str],
    err: IO[str],
) -> int:
    if services is None:
        err.write("intervention policies are not available in this installation\n")
        return 2
    source = args.source if args.policy_command == "check" else "json"
    path = Path(args.file).expanduser()
    try:
        reader = services.source(source)
    except CapabilityError as exc:
        err.write(f"Unknown policy source: {exc}\n")
        return 2
    try:
        policies = reader.policies(path)
    except PolicyError as exc:
        err.write(f"Not a valid policy file: {exc}\n")
        return 2
    if args.policy_command == "check":
        out.write(format_policies(policies))
        return 0 if policies.state is ApprovalState.APPROVED else 1
    try:
        day = date.fromisoformat(args.date) if args.date else services.today()
        approved = policies.approve(args.by, day, args.note)
    except (ValueError, PolicyError) as exc:
        err.write(f"Cannot approve: {exc}\n")
        return 2
    try:
        _replace(path, json.dumps(approved.as_dict(), indent=2) + "\n")
    except OSError as exc:
        err.write(f"Cannot write the approval to {path}: {exc}\n")
        return 2
    out.write(format_policies(approved))
    out.write(f"Wrote the approval to {path}\n")
    return 0


def _replace(path: Path, text: str) -> None:
    """Write ``text`` beside ``path``, then swap it in: never a half-written file."""
    temp = path.with_name(f".{path.name}.approve.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def format_policies(policies: PolicySet) -> str:
    """The policies, the eligibility list and the approval state, as text."""
    lines = [f"Policy file: {policies.state.value} ({policies.digest()})"]
    if policies.approval is not None:
        a = policies.approval
        lines.append(
            f"  approved by {a.approved_by} on {a.approved_on.isoformat()}"
            + (f": {a.note}" if a.note else "")
        )
    detectors = ", ".join(sorted(policies.eligibility.detectors)) or "none"
    lines.append(f"Eligible detectors: {detectors}")
    if policies.eligibility.basis:
        lines.append(f"  basis: {policies.eligibility.basis}")
    lines.append(f"Policies: {len(policies.policies)}")
    for p in policies.policies:
        lines.append(
            f"  {p.id}: {', '.join(sorted(p.detectors))} -> {p.advice.kind.value} "
            f"(evidence >= {p.min_evidence}, confidence >= "
            f"{p.confidence_floor:.2f}, {p.min_signals} signal(s), cooldown "
            f"{p.cooldown_tool_events} tool events; "
            f"{', '.join(sorted(a.value for a in p.actions))})"
        )
        lines.append(f"    {p.advice.message}")
    for pid, detector in policies.ineligible():
        lines.append(
            f"Note: {pid} answers {detector}, which is not eligible: its "
            "signals are recorded as ineligible, never advised"
        )
    if policies.state is not ApprovalState.APPROVED:
        lines.append(
            "Not applied until a person approves this content: "
            "python -m ter policy approve FILE --by NAME"
        )
    return "\n".join(lines) + "\n"
