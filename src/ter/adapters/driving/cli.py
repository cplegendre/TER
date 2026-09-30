"""The ``python -m ter`` command line: a thin driving adapter.

It parses arguments, asks the composition root (through :class:`CliServices`)
for wired use cases, and renders :class:`~ter.domain.stream.StreamReport`
values as plain text or JSON. It holds no analysis logic.

Commands::

    python -m ter observe SESSION.jsonl [--timeline] [--json]
    python -m ter observe --event-log DIR [--session ID] [--timeline] [--json]
    python -m ter hook [--event-log DIR]      # reads one hook payload on stdin
    python -m ter explain SESSION.jsonl [--json] [--graph FILE]
    python -m ter a3 SESSION.jsonl [--html FILE] [--json [FILE]] [--graph FILE]
                                   [--ter offline|model|off]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ...application.explain import ExplainedSession
from ...domain.lean import LeanAnalysis
from ...domain.stream import StreamReport
from ...ports.driven import Clock
from ...ports.driving import EventIngest
from .claude_hooks import HookStatus, run_hook

__all__ = ["CliServices", "format_findings", "format_report", "format_timeline", "main"]

TOKENIZERS = ("regex", "tiktoken")
#: How the A3 obtains TER: offline (deterministic, lexical embedder), with the
#: TER 3 sentence-transformers model, or not at all.
TER_MODES = ("offline", "model", "off")


@dataclass(frozen=True)
class CliServices:
    """Use cases the composition root hands to the CLI."""

    analyse_transcript: Callable[[Path, str], StreamReport]
    log_sessions: Callable[[Path], tuple[str, ...]]
    analyse_log: Callable[[Path, str, str], StreamReport]
    hook_ingest: Callable[[Path], EventIngest]
    default_log_dir: Path
    hook_clock: Clock | None = None
    explain_transcript: Callable[[Path, str, str], ExplainedSession] | None = None


def main(
    argv: Sequence[str] | None,
    services: CliServices,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser(services.default_log_dir).parse_args(argv)
    if args.command == "hook":
        log_dir: Path = args.event_log
        result = run_hook(
            stdin or sys.stdin,
            out,
            lambda: services.hook_ingest(log_dir),
            clock=services.hook_clock,
        )
        if result.status is HookStatus.IGNORED and result.reason:
            # Still exit 0 so the agent carries on; stderr leaves a trace
            # (Claude Code shows it in verbose mode and debug logs).
            err.write(f"ter hook: event not recorded: {result.reason}\n")
        return 0
    if args.command in ("explain", "a3"):
        return _explain(args, services, out, err)
    return _observe(args, services, out, err)


def _write(path: Path, text: str) -> None:
    if path.parent != Path():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _explain(
    args: argparse.Namespace, services: CliServices, out: IO[str], err: IO[str]
) -> int:
    if services.explain_transcript is None:
        err.write("explain is not available in this installation\n")
        return 2
    if not args.path.exists():
        err.write(f"No such session file: {args.path}\n")
        return 2
    ter_mode = getattr(args, "ter", "off")
    explained = services.explain_transcript(args.path, args.tokenizer, ter_mode)
    if args.graph is not None:
        _write(args.graph, _json(explained.analysis.graph.as_dict()))
        err.write(f"Wrote {args.graph}\n")
    if args.command == "explain":
        if args.json:
            out.write(_json(explained.analysis.as_dict()))
        else:
            out.write(format_findings(explained.analysis))
        return 0

    from .reports.a3 import render_a3_html

    wrote = False
    if args.html is not None:
        _write(args.html, render_a3_html(explained.a3))
        err.write(f"Wrote {args.html}\n")
        wrote = True
    if args.json is not None:
        payload = _json(explained.a3.as_dict())
        if args.json == "-":
            out.write(payload)
        else:
            _write(Path(args.json), payload)
            err.write(f"Wrote {args.json}\n")
        wrote = True
    if not wrote:
        out.write(format_findings(explained.analysis))
    return 0


def _observe(
    args: argparse.Namespace, services: CliServices, out: IO[str], err: IO[str]
) -> int:
    if args.event_log is not None:
        sessions = services.log_sessions(args.event_log)
        session = args.session
        if session is None:
            if len(sessions) != 1:
                listing = "\n".join(f"  {s}" for s in sessions) or "  (none)"
                err.write(
                    f"{len(sessions)} sessions in {args.event_log}; "
                    f"choose one with --session:\n{listing}\n"
                )
                return 2
            session = sessions[0]
        report = services.analyse_log(args.event_log, session, args.tokenizer)
    elif args.path is not None:
        if not args.path.exists():
            err.write(f"No such session file: {args.path}\n")
            return 2
        report = services.analyse_transcript(args.path, args.tokenizer)
    else:
        err.write("observe needs a session file or --event-log DIR\n")
        return 2

    if args.json:
        out.write(json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n")
        return 0
    out.write(format_report(report))
    if args.timeline:
        out.write("\n" + format_timeline(report, limit=args.limit))
    return 0


def _parser(default_log_dir: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ter", description="TER 4: Lean analysis of agent sessions."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    observe = commands.add_parser(
        "observe", help="L1 observables of a session, from its event stream"
    )
    observe.add_argument(
        "path", nargs="?", type=Path, help="Claude Code session .jsonl"
    )
    observe.add_argument(
        "--event-log",
        type=Path,
        default=None,
        metavar="DIR",
        help="analyse what `ter hook` recorded in DIR instead of a transcript",
    )
    observe.add_argument("--session", help="session id within --event-log")
    observe.add_argument("--timeline", action="store_true", help="print every event")
    observe.add_argument(
        "--limit", type=int, default=None, help="timeline rows to print"
    )
    observe.add_argument("--json", action="store_true", help="print the report as JSON")
    observe.add_argument("--tokenizer", choices=TOKENIZERS, default="regex")

    explain = commands.add_parser(
        "explain", help="L2: Lean findings, value stream and scorecard of a session"
    )
    explain.add_argument("path", type=Path, help="Claude Code session .jsonl")
    explain.add_argument(
        "--json", action="store_true", help="print the analysis as JSON"
    )
    explain.add_argument(
        "--graph", type=Path, metavar="FILE", help="write the evidence graph as JSON"
    )
    explain.add_argument("--tokenizer", choices=TOKENIZERS, default="regex")

    a3 = commands.add_parser("a3", help="L2: a one-page Lean A3 report of a session")
    a3.add_argument("path", type=Path, help="Claude Code session .jsonl")
    a3.add_argument("--html", type=Path, metavar="FILE", help="write the A3 as HTML")
    a3.add_argument(
        "--json",
        nargs="?",
        const="-",
        default=None,
        metavar="FILE",
        help="write the A3 as JSON to FILE (stdout when FILE is omitted)",
    )
    a3.add_argument(
        "--graph", type=Path, metavar="FILE", help="write the evidence graph as JSON"
    )
    a3.add_argument(
        "--ter",
        choices=TER_MODES,
        default="offline",
        help="how to compute TER: offline (deterministic, default), model "
        "(TER 3 sentence-transformers, may download) or off",
    )
    a3.add_argument("--tokenizer", choices=TOKENIZERS, default="regex")

    hook = commands.add_parser(
        "hook", help="Claude Code hook: record one hook payload read from stdin"
    )
    hook.add_argument(
        "--event-log",
        type=Path,
        default=default_log_dir,
        metavar="DIR",
        help=f"where live events are appended (default {default_log_dir})",
    )
    return parser


def _pairs(pairs: Sequence[tuple[object, int]]) -> str:
    return " · ".join(f"{_value(k)} {n:,}" for k, n in pairs) or "-"


def _value(key: object) -> str:
    return str(getattr(key, "value", key))


def format_report(report: StreamReport) -> str:
    """A readable plain-text summary of a report."""
    usage = report.usage
    trust = "exact" if report.tokens_exact else "estimate"
    reads = (
        ", ".join(f"{path} ×{n}" for path, n in report.repeated_reads)
        if report.repeated_reads
        else "-"
    )
    rows = [
        ("events", f"{report.total_events:,}  ({_pairs(report.by_class)})"),
        ("by kind", _pairs(report.by_kind)),
        ("by tool", _pairs(report.by_tool)),
        (
            "text tokens",
            f"{_pairs(report.tokens_by_class)}  [{report.tokenizer}, {trust}]",
        ),
        (
            "usage",
            f"input {usage.input_tokens:,} · output {usage.output_tokens:,} · "
            f"cache write {usage.cache_creation_tokens:,} · "
            f"cache read {usage.cache_read_tokens:,}",
        ),
        ("duplicate calls", f"{len(report.duplicate_tool_calls)}"),
        ("repeated reads", f"{report.repeated_read_count}  {reads}"),
        ("orphan results", f"{len(report.orphan_results)}"),
        ("open requests", f"{len(report.open_requests)}"),
        (
            "unvalidated edits",
            f"{report.edits_since_validation} since last shell "
            f"(peak {report.peak_edits_without_validation})",
        ),
    ]
    width = max(len(label) for label, _ in rows)
    lines = [f"TER observe · session {report.session_id or '-'}"]
    lines += [f"  {label:<{width}}  {value}" for label, value in rows]
    return "\n".join(lines) + "\n"


def format_timeline(report: StreamReport, *, limit: int | None = None) -> str:
    """One line per accepted event, with the signals it raised."""
    rows = report.timeline if limit is None else report.timeline[:limit]
    header = (
        f"  {'#':>4}  {'kind':<15} {'actor':<9} {'tool':<13} {'tokens':>7}  signals"
    )
    lines = ["Timeline", header]
    for row in rows:
        tool = row.tool_kind.value if row.tool_kind else ""
        signals = ", ".join(s.value for s in row.signals)
        lines.append(
            f"  {row.index:>4}  {row.kind.value:<15} {row.actor.value:<9} "
            f"{tool:<13} {row.tokens:>7}  {signals}".rstrip()
        )
    hidden = len(report.timeline) - len(rows)
    if hidden > 0:
        lines.append(f"  … {hidden} more")
    return "\n".join(lines) + "\n"


def format_findings(analysis: LeanAnalysis) -> str:
    """A readable plain-text summary of an L2 analysis."""
    sc = analysis.scorecard
    lines = [f"TER explain · session {analysis.session_id or '-'}"]
    eff = sc.flow_efficiency_tokens
    lines.append(
        f"  flow efficiency  {'-' if eff is None else f'{eff:.0%}'} of generated tokens"
        + (
            ""
            if sc.flow_efficiency_time is None
            else f", {sc.flow_efficiency_time:.0%} of agent time"
        )
    )
    lines.append(
        "  activity         " + " · ".join(f"{k} {n:,}" for k, n in sc.activity_tokens)
    )
    if sc.ter is not None:
        lines.append(f"  TER              {sc.ter.value:.3f} ({sc.ter.method})")
    lines.append(
        f"  findings         {sc.findings} confident, {sc.uncertain_findings} uncertain, "
        f"{sc.risks} risk(s)"
    )
    for f in analysis.findings:
        flag = " (uncertain)" if f.uncertain else ""
        cost = (
            "risk" if f.kind.value == "risk" else f"{f.tokens + f.context_tokens} tok"
        )
        lines.append(
            f"  - [{f.confidence:.2f}{flag}] {f.waste.value}: {f.title} · {cost} · "
            f"evidence {', '.join(f.evidence[:4])}{' …' if len(f.evidence) > 4 else ''}"
        )
    return "\n".join(lines) + "\n"
