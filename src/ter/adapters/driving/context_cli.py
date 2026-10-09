"""``python -m ter context``: build, supply and measure context bundles (L3).

Commands::

    python -m ter context bundle [SESSION.jsonl] [--prompt TEXT] [--session ID]
                                 --repo DIR [--repo-engine NAME] [--budget N]
                                 [--event-log DIR | --no-record] [--out FILE]
                                 [--json] [--tokenizer NAME]
    python -m ter context report [SESSION.jsonl] [--event-log DIR --session ID]
                                 --repo DIR [--repo-engine NAME] [--budget N]
                                 [--critical FILE] [--json] [--tokenizer NAME]

``bundle`` prints the bundle (Markdown, or JSON with ``--json``) for the
session's next decision and appends one ``context.supplied`` event per
fragment to the event log (TER-EVD-004). It is a command, not a hook: the
bundle goes to whoever ran it, never into a hook response (TER-INT-001).
``report`` prints each bundle's precision, recall, unused context and
missing context (TER-CTX-003, TER-CTX-004) and, with ``--critical``, the
critical recall (TER-EVD-005).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING

from ...domain.capabilities import CapabilityError
from ...domain.repository import RepositoryEvidenceError

if TYPE_CHECKING:
    from ...application.context import SuppliedContext
    from ...domain.context_metrics import ContextMeasures

__all__ = [
    "BundleRequest",
    "ContextServices",
    "ReportRequest",
    "add_context_parser",
    "format_measures",
    "run_context",
]

DEFAULT_BUDGET = 8000


@dataclass(frozen=True)
class BundleRequest:
    repo: Path
    engine: str
    tokenizer: str
    budget: int
    session_path: Path | None
    session_id: str | None
    prompt: str | None
    #: Where the ``context.supplied`` events go (``None``: not recorded).
    event_log: Path | None


@dataclass(frozen=True)
class ReportRequest:
    repo: Path
    engine: str
    tokenizer: str
    budget: int
    session_path: Path | None
    session_id: str | None
    event_log: Path | None
    critical: Path | None


@dataclass(frozen=True)
class ContextServices:
    """The context use cases, wired by the composition root. Both raise
    ``ValueError`` for input they cannot use (the message says why)."""

    bundle: Callable[[BundleRequest], "SuppliedContext"]
    report: Callable[[ReportRequest], "ContextMeasures"]


def add_context_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
    default_log_dir: Path,
    tokenizer_help: str,
    repo_engine_help: str,
) -> None:
    context = commands.add_parser(
        "context", help="L3: context bundles for the next decision, and their measures"
    )
    sub = context.add_subparsers(dest="context_command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "session",
            nargs="?",
            type=Path,
            help="session transcript (.jsonl); else --session in --event-log",
        )
        p.add_argument("--session", dest="session_id", help="session id")
        p.add_argument(
            "--repo",
            type=Path,
            required=True,
            metavar="DIR",
            help="the repository, at the commit the session started from",
        )
        p.add_argument("--repo-engine", default="syntax", help=repo_engine_help)
        p.add_argument(
            "--budget",
            type=int,
            default=DEFAULT_BUDGET,
            help=f"token budget per bundle (default {DEFAULT_BUDGET})",
        )
        p.add_argument("--tokenizer", default="regex", help=tokenizer_help)
        p.add_argument("--json", action="store_true", help="print JSON")

    bundle = sub.add_parser(
        "bundle",
        help="build the bundle for the session's next decision and record "
        "its supply as context.supplied events",
    )
    common(bundle)
    bundle.add_argument(
        "--prompt", help="the prompt to build for (default: the session's last)"
    )
    bundle.add_argument(
        "--event-log",
        type=Path,
        default=default_log_dir,
        metavar="DIR",
        help=f"where context.supplied events are appended (default {default_log_dir})",
    )
    bundle.add_argument(
        "--no-record",
        action="store_true",
        help="print the bundle without recording its supply",
    )
    bundle.add_argument(
        "--out", type=Path, metavar="FILE", help="also write the bundle (.json or .md)"
    )

    report = sub.add_parser(
        "report",
        help="precision, recall, unused and missing context of each bundle",
    )
    common(report)
    report.add_argument(
        "--event-log",
        type=Path,
        default=None,
        metavar="DIR",
        help="read the session from DIR (with --session) instead of a transcript",
    )
    report.add_argument(
        "--critical",
        type=Path,
        metavar="FILE",
        help="critical evidence list (JSON or CSV, docs/ter4/l3-grounded.md)",
    )


def run_context(
    args: argparse.Namespace,
    services: ContextServices | None,
    out: IO[str],
    err: IO[str],
) -> int:
    if services is None:
        err.write("context is not available in this installation\n")
        return 2
    repo: Path = args.repo
    if not repo.is_dir():
        err.write(f"No such repository directory: {repo}\n")
        return 2
    session: Path | None = args.session
    if session is not None and not session.exists():
        err.write(f"No such session file: {session}\n")
        return 2
    if args.budget < 0:
        err.write("--budget must be 0 or more\n")
        return 2
    try:
        if args.context_command == "bundle":
            return _bundle(args, services, out, err)
        return _report(args, services, out, err)
    except (RepositoryEvidenceError, CapabilityError) as exc:
        err.write(f"Cannot read repository {repo}: {exc}\n")
        return 2
    except (OSError, ValueError) as exc:
        err.write(f"ter context: {exc}\n")
        return 2


def _bundle(
    args: argparse.Namespace, services: ContextServices, out: IO[str], err: IO[str]
) -> int:
    if args.session is None and args.session_id is None:
        err.write("ter context bundle: give a session transcript or --session\n")
        return 2
    log: Path | None = None if args.no_record else args.event_log
    supplied = services.bundle(
        BundleRequest(
            repo=args.repo,
            engine=args.repo_engine,
            tokenizer=args.tokenizer,
            budget=args.budget,
            session_path=args.session,
            session_id=args.session_id,
            prompt=args.prompt,
            event_log=log,
        )
    )
    bundle = supplied.bundle
    out.write(bundle.to_json() if args.json else bundle.render())
    target: Path | None = args.out
    if target is not None:
        if target.parent != Path():
            target.parent.mkdir(parents=True, exist_ok=True)
        text = bundle.to_json() if target.suffix == ".json" else bundle.render()
        target.write_text(text, encoding="utf-8")
        err.write(f"Wrote {target}\n")
    if log is not None:
        err.write(
            f"Recorded {len(supplied.events)} context.supplied event(s) for "
            f"session {bundle.session_id} in {log}\n"
        )
    if bundle.insufficient:
        err.write("Bundle is insufficient: see its seeds and omissions\n")
    return 0


def _report(
    args: argparse.Namespace, services: ContextServices, out: IO[str], err: IO[str]
) -> int:
    if args.session is None and (args.event_log is None or args.session_id is None):
        err.write(
            "ter context report: give a session transcript, or --event-log and "
            "--session\n"
        )
        return 2
    critical: Path | None = args.critical
    if critical is not None and not critical.is_file():
        err.write(f"No such critical evidence file: {critical}\n")
        return 2
    measures = services.report(
        ReportRequest(
            repo=args.repo,
            engine=args.repo_engine,
            tokenizer=args.tokenizer,
            budget=args.budget,
            session_path=args.session,
            session_id=args.session_id,
            event_log=args.event_log,
            critical=critical,
        )
    )
    if critical is not None and measures.critical is None:
        err.write(f"{critical} lists nothing for session {measures.session_id}\n")
    if args.json:
        out.write(json.dumps(measures.as_dict(), indent=2, ensure_ascii=False) + "\n")
    else:
        out.write(format_measures(measures))
    return 0


def _share(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def format_measures(m: "ContextMeasures") -> str:
    """A plain-text summary of a session's context measures."""
    how = "rebuilt for each prompt" if m.simulated else "as supplied"
    lines = [
        f"TER context · session {m.session_id or '-'} · {len(m.bundles)} "
        f"bundle(s), {how}"
    ]
    for b in m.bundles:
        used = sum(f.used for f in b.fragments)
        cost = "" if b.unused_usd is None else f", ${b.unused_usd:.4f}"
        lines.append(
            f"  {b.bundle}  {len(b.fragments)} fragment(s), {b.tokens} tok · "
            f"precision {_share(b.precision)} ({used}/{len(b.fragments)}) · "
            f"recall {_share(b.recall)} ({len(b.held)}/{len(b.needed)} "
            f"{b.needed_basis}) · unused {b.unused_tokens} tok over "
            f"{b.carried_turns} turn(s){cost}"
        )
        for miss in b.missing:
            what = miss.path + (f" ({miss.symbol})" if miss.symbol else "")
            read = (
                f"; read later by {', '.join(miss.read_by[:3])}"
                if miss.read_by
                else "; never read"
            )
            lines.append(
                f"    missing {what}: depended on by "
                f"{', '.join(miss.depended_by[:4]) or '-'}{read}"
            )
    if m.critical is not None:
        c = m.critical
        held = sum(i.in_context for i in c.judged)
        skipped = len(c.items) - len(c.judged)
        lines.append(
            f"  critical recall  {_share(c.recall)} ({held}/{len(c.judged)} in "
            f"context before the first dependent edit) from {c.source}"
            + (f"; {skipped} item(s) no edit depended on" if skipped else "")
        )
    usd = m.unused_usd
    lines.append(
        f"  unused context   {m.unused_tokens} tok"
        + (
            ""
            if usd is None
            else f", ${usd:.4f} carrying cost ({m.price_book}"
            + (f", priced on {m.priced_on}" if m.priced_on else "")
            + (f", {m.unpriced_turns} unpriced turn(s)" if m.unpriced_turns else "")
            + ")"
        )
    )
    return "\n".join(lines) + "\n"
