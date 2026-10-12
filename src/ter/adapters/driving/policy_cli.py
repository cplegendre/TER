"""``python -m ter policy``: L4 intervention policy files (TER-INT-008, TER-INT-015).

Commands::

    python -m ter policy check FILE [--source NAME]
    python -m ter policy approve FILE --by NAME [--note TEXT] [--date YYYY-MM-DD]
    python -m ter policy shadow FILE SESSIONS... --ledger DIR [--judge CSV]

``check`` validates a ``ter.intervention-policies/1`` file and says whether
it may be applied: exit 0 when it is approved in its current form, 1 when it
has no approval or changed since it was approved, 2 when it is not a valid
policy file. ``approve`` is the human sign-off: it records who approved the
file, when, and the digest of its content, and rewrites the file with that
record. Run it yourself after reading the policies; TER never approves on
its own, and any later edit voids the approval.

``shadow`` replays recorded sessions through an approved file and writes
every decision to the intervention ledger in ``DIR``, delivering nothing
(TER-INT-017). ``--judge`` writes the advice it would have given to a CSV
with an empty verdict column, for a person to judge. The CSV names files
and session ids, so it is for the developer's own machine, never a commit.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO, TYPE_CHECKING

from ...domain.capabilities import CapabilityError
from ...domain.intervention import PolicyError
from ...domain.ledger import LedgerError
from ...domain.policy_set import ApprovalState, PolicySet
from ...ports.driven import InterventionPolicies

if TYPE_CHECKING:
    from ...application.shadow import ShadowRun

__all__ = [
    "PolicyServices",
    "add_policy_parser",
    "format_policies",
    "format_shadow",
    "run_policy",
]


@dataclass(frozen=True)
class PolicyServices:
    """What ``python -m ter policy`` needs, wired by the composition root."""

    #: ``source(name)``: the ``InterventionPolicies.<name>`` capability.
    source: Callable[[str], InterventionPolicies]
    today: Callable[[], date]
    #: ``shadow(sessions, policies, ledger_dir, tokenizer)``: replay sessions
    #: through approved policies into a ledger (TER-INT-017).
    shadow: Callable[[Sequence[Path], PolicySet, Path, str], "ShadowRun"] | None = None
    #: ``run_files(folder)``: the files of the one session exported to the
    #: folder (a GARE run), or none.
    run_files: Callable[[Path], Sequence[Path]] = lambda _: ()


def add_policy_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    policy = commands.add_parser(
        "policy", help="L4: check, approve and shadow-run intervention policy files"
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
    shadow = sub.add_parser(
        "shadow",
        help="replay sessions through approved policies into a ledger; deliver nothing",
    )
    shadow.add_argument("file", type=Path, help="approved policy file")
    shadow.add_argument(
        "sources",
        nargs="+",
        type=Path,
        help="session .jsonl files, folders of them, or an imported corpus",
    )
    shadow.add_argument(
        "--ledger", type=Path, required=True, metavar="DIR", help="ledger folder"
    )
    shadow.add_argument(
        "--judge",
        type=Path,
        metavar="CSV",
        help="write the advice to judge to CSV (stays on your machine)",
    )
    shadow.add_argument("--tokenizer", default="regex", help="tokenizer (regex)")


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
    if args.policy_command == "shadow" and services.shadow is None:
        err.write("shadow mode is not available in this installation\n")
        return 2
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
    if args.policy_command == "shadow":
        return _shadow(args, services, policies, out, err)
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


def _shadow(
    args: argparse.Namespace,
    services: PolicyServices,
    policies: PolicySet,
    out: IO[str],
    err: IO[str],
) -> int:
    from .control_cli import session_files

    if policies.state is not ApprovalState.APPROVED:
        err.write(
            f"Not replayed: the policy file is {policies.state.value}. Shadow "
            "mode runs only approved policies: python -m ter policy approve "
            "FILE --by NAME\n"
        )
        return 1
    missing = [s for s in args.sources if not s.exists()]
    if missing:
        err.write(f"No such session file or folder: {missing[0]}\n")
        return 2
    paths = list(session_files(args.sources, services.run_files))
    if not paths:
        err.write("No session transcripts (.jsonl) found\n")
        return 2
    assert services.shadow is not None  # checked in run_policy
    ledger = Path(args.ledger).expanduser()
    try:
        run = services.shadow(paths, policies, ledger, args.tokenizer)
    except (OSError, LedgerError, CapabilityError, PolicyError) as exc:
        err.write(f"Cannot write the ledger in {ledger}: {exc}\n")
        return 2
    out.write(format_shadow(run, ledger))
    if args.judge is not None:
        judge = Path(args.judge).expanduser()
        try:
            _write_judge_csv(judge, run)
        except OSError as exc:
            err.write(f"Cannot write {judge}: {exc}\n")
            return 2
        out.write(f"Wrote {len(run.advice)} piece(s) of advice to judge to {judge}\n")
    for ref, error in run.failures:
        err.write(f"  ! {ref}: {error}\n")
    if not run.sessions:
        err.write("No session could be replayed\n")
        return 1
    return 1 if run.failures else 0


def format_shadow(run: "ShadowRun", ledger: Path) -> str:
    """What shadow mode would have done, per policy and outcome."""
    lines = [
        f"Shadow mode: {len(run.sessions)} session(s), {len(run.records)} "
        f"decision(s), {run.written} new in {ledger}. Nothing was delivered."
    ]
    outcomes = run.outcomes()
    for policy in sorted({p for p, _ in outcomes}):
        counts = sorted(
            ((o, n) for (p, o), n in outcomes.items() if p == policy),
            key=lambda item: (item[0] != "advised", item[0]),
        )
        lines.append(f"  {policy}: " + ", ".join(f"{n} {o}" for o, n in counts))
    if not outcomes:
        lines.append("  no signal matched any policy")
    lines.append(f"Advice TER would have given: {len(run.advice)}")
    return "\n".join(lines) + "\n"


_JUDGE_COLUMNS = (
    "verdict",
    "note",
    "session",
    "policy",
    "detector",
    "confidence",
    "title",
    "subject",
    "evidence",
    "advice",
    "record",
)


def _cell(text: str) -> str:
    """A cell a spreadsheet shows as text, never runs as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _write_judge_csv(path: Path, run: "ShadowRun") -> None:
    """The advice to judge: verdict is waste, not waste or unsure.

    The file names sessions and files, so it is created private to its owner
    (0600 on POSIX), as the ledger is.
    """
    if path.parent != Path():
        path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    if os.name == "posix":
        os.fchmod(fd, 0o600)
    with open(fd, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_JUDGE_COLUMNS)
        for item in run.advice:
            r, s = item.record, item.signal
            writer.writerow(
                (
                    "",
                    "",
                    _cell(r.session_id),
                    r.policy,
                    r.detector,
                    f"{r.confidence:.2f}",
                    _cell(s.title),
                    _cell(s.subject),
                    _cell(" ".join(r.evidence)),
                    _cell(r.advice),
                    r.id,
                )
            )


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
