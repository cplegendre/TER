"""Composition root: the only place that chooses concrete adapters.

Entry points (CLI, hooks, CI gate) ask this package for wired use cases, and
it applies the installation's maturity ceiling while doing so. No other
module decides which capabilities are switched on.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ..adapters.driving.cli import CliServices
from ..adapters.driving.cli import main as cli_main
from ..adapters.driven.in_memory import SystemClock
from ..application.explain import ExplainedSession, ExplainSession
from .capabilities import CapabilityRegistry, default_registry
from ..application.observe import AnalyseEventLog, AnalyseTrace, RecordEvent
from ..domain.capabilities import Capability, CapabilityProblem, UnknownCapabilityError
from ..domain.stream import StreamReport
from ..ports.driven import OutcomeSource, SessionSource, TerScorer, Tokenizer
from ..ports.driving import EventIngest

if TYPE_CHECKING:
    from ..adapters.driven.claude_code.corpus import CorpusImport

__all__ = [
    "CapabilityRegistry",
    "cli_services",
    "default_registry",
    "default_event_log_dir",
    "main",
    "make_outcome_source",
    "make_ter_scorer",
    "make_tokenizer",
    "session_source_for",
]

#: Environment variable that relocates the live event log.
EVENT_LOG_ENV = "TER_EVENT_LOG_DIR"


def default_event_log_dir() -> Path:
    """``$TER_EVENT_LOG_DIR``, else ``ter/events`` in the user's cache directory.

    The log holds prompts and tool output, so the default lives under the
    user's home (``$XDG_CACHE_HOME`` or ``~/.cache``), not a shared temporary
    directory.
    """
    configured = os.environ.get(EVENT_LOG_ENV)
    if configured:
        return Path(configured)
    cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache) if cache else Path.home() / ".cache"
    return base / "ter" / "events"


def make_tokenizer(name: str = "regex") -> Tokenizer:
    """``regex`` is offline and deterministic; ``tiktoken`` needs its encoding.

    Any ``Tokenizer.<name>`` capability works; built-ins resolve without
    scanning installed packages, so hooks stay cheap.
    """
    try:
        tokenizer = default_registry().create("Tokenizer", name)
    except UnknownCapabilityError:
        raise ValueError(f"Unknown tokenizer {name!r}") from None
    assert isinstance(tokenizer, Tokenizer)  # checked by the registry
    return tokenizer


def make_outcome_source(name: str = "junit") -> OutcomeSource:
    """An ``OutcomeSource.<name>`` capability; ``junit`` reads JUnit XML results."""
    source = default_registry().create("OutcomeSource", name)
    assert isinstance(source, OutcomeSource)  # checked by the registry
    return source


def make_ter_scorer(mode: str) -> TerScorer | None:
    """``offline`` pins TER 3 to the deterministic adapters; ``model`` uses its
    sentence-transformers model; ``off`` skips TER."""
    if mode == "off":
        return None
    from ..adapters.driven.ter3 import Ter3Scorer

    if mode == "model":
        return Ter3Scorer()
    if mode == "offline":
        from ..adapters.driven.embedders import HashingEmbedder
        from ..adapters.driven.tokenizers import RegexTokenizer

        return Ter3Scorer(RegexTokenizer(), HashingEmbedder())
    raise ValueError(f"Unknown TER mode {mode!r}")


def session_source_for(path: Path) -> SessionSource:
    """The session source for a reference: a GARE export, else Claude Code."""
    from ..adapters.driven.gare import GareRunSource

    if GareRunSource.accepts(path):
        return GareRunSource()
    from ..adapters.driven.claude_code import ClaudeCodeJsonlSource

    return ClaudeCodeJsonlSource()


def cli_services() -> CliServices:
    """Wire the CLI's use cases. Heavy adapters are imported on first use."""

    def analyse_transcript(path: Path, tokenizer: str) -> StreamReport:
        return AnalyseTrace(session_source_for(path), make_tokenizer(tokenizer))(path)

    def log_sessions(directory: Path) -> tuple[str, ...]:
        from ..adapters.driven.event_log import JsonlEventLog

        return JsonlEventLog(directory).sessions()

    def analyse_log(directory: Path, session_id: str, tokenizer: str) -> StreamReport:
        from ..adapters.driven.event_log import JsonlEventLog

        log = JsonlEventLog(directory)
        return AnalyseEventLog(log, make_tokenizer(tokenizer))(session_id)

    def hook_ingest(directory: Path) -> EventIngest:
        from ..adapters.driven.event_log import JsonlEventLog

        # Append-only: a hook's cost must not grow with the session.
        return RecordEvent(make_tokenizer("regex"), JsonlEventLog(directory))

    def explain_transcript(
        path: Path, tokenizer: str, ter: str, outcome: Path | None = None
    ) -> ExplainedSession:
        from ..adapters.driven.claude_code import ClaudeCodeJsonlSource
        from ..adapters.driven.pricing import default_price_book

        source = session_source_for(path)
        # TER 3 scores Claude Code transcripts only; other sources get no TER
        # rather than a meaningless one (TER-SRC-017).
        scores = isinstance(source, ClaudeCodeJsonlSource)
        use_case = ExplainSession(
            source,
            make_tokenizer(tokenizer),
            make_ter_scorer(ter if scores else "off"),
            make_outcome_source() if outcome is not None else None,
            default_price_book(),
        )
        return use_case(path, outcome)

    def capabilities() -> tuple[tuple[Capability, ...], tuple[CapabilityProblem, ...]]:
        registry = default_registry()
        problems = registry.check()
        return registry.capabilities(), problems

    def tokenizers() -> tuple[str, ...]:
        # Only tokenizers that load and fit the port: a broken plugin is
        # reported by `capabilities`, not crashed into by a report command.
        registry = default_registry()
        broken = {(p.key, p.target) for p in registry.check("Tokenizer")}
        return tuple(
            c.name
            for c in registry.capabilities("Tokenizer")
            if (c.key, c.target) not in broken
        )

    def import_corpus(
        sources: Sequence[Path],
        out: Path,
        labels: Path | None,
        max_tool_output: int,
        keep_tools: frozenset[str],
        quote_files: bool,
    ) -> CorpusImport:
        from ..adapters.driven.claude_code.redaction import RedactionPolicy
        from ..adapters.driven.claude_code.corpus import (
            import_corpus as run,
            read_labels,
        )

        policy = RedactionPolicy(
            max_tool_output=max_tool_output,
            keep_tools=keep_tools,
            quote_file_contents=quote_files,
        )
        return run(
            sources,
            out,
            policy=policy,
            labels=read_labels(labels) if labels is not None else None,
        )

    return CliServices(
        capabilities=capabilities,
        import_corpus=import_corpus,
        tokenizers=tokenizers,
        analyse_transcript=analyse_transcript,
        log_sessions=log_sessions,
        analyse_log=analyse_log,
        hook_ingest=hook_ingest,
        default_log_dir=default_event_log_dir(),
        hook_clock=SystemClock(),
        explain_transcript=explain_transcript,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return cli_main(argv, cli_services())
