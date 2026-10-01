"""Composition root: the only place that chooses concrete adapters.

Entry points (CLI, hooks, CI gate) ask this package for wired use cases, and
it applies the installation's maturity ceiling while doing so. No other
module decides which capabilities are switched on.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from ..adapters.driving.cli import CliServices
from ..adapters.driving.cli import main as cli_main
from ..adapters.driven.in_memory import SystemClock
from ..application.explain import ExplainedSession, ExplainSession
from ..application.observe import AnalyseEventLog, AnalyseTrace, RecordEvent
from ..domain.stream import StreamReport
from ..ports.driven import TerScorer, Tokenizer
from ..ports.driving import EventIngest

__all__ = [
    "cli_services",
    "default_event_log_dir",
    "main",
    "make_ter_scorer",
    "make_tokenizer",
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
    """``regex`` is offline and deterministic; ``tiktoken`` needs its encoding."""
    from ..adapters.driven.tokenizers import RegexTokenizer, TiktokenTokenizer

    if name == "tiktoken":
        return TiktokenTokenizer()
    if name == "regex":
        return RegexTokenizer()
    raise ValueError(f"Unknown tokenizer {name!r}")


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


def cli_services() -> CliServices:
    """Wire the CLI's use cases. Heavy adapters are imported on first use."""

    def analyse_transcript(path: Path, tokenizer: str) -> StreamReport:
        from ..adapters.driven.claude_code import ClaudeCodeJsonlSource

        return AnalyseTrace(ClaudeCodeJsonlSource(), make_tokenizer(tokenizer))(path)

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

    def explain_transcript(path: Path, tokenizer: str, ter: str) -> ExplainedSession:
        from ..adapters.driven.claude_code import ClaudeCodeJsonlSource

        use_case = ExplainSession(
            ClaudeCodeJsonlSource(), make_tokenizer(tokenizer), make_ter_scorer(ter)
        )
        return use_case(path)

    return CliServices(
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
