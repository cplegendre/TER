"""Composition root: the only place that chooses concrete adapters.

Entry points (CLI, hooks, CI gate) ask this package for wired use cases, and
it applies the installation's maturity ceiling while doing so. No other
module decides which capabilities are switched on.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ..adapters.driving.cli import CliServices
from ..adapters.driving.cli import main as cli_main
from ..application.observe import AnalyseEventLog, AnalyseTrace, ObserveEvent
from ..domain.stream import StreamReport
from ..ports.driven import Tokenizer
from ..ports.driving import EventIngest

__all__ = ["cli_services", "default_event_log_dir", "main", "make_tokenizer"]

#: Environment variable that relocates the live event log.
EVENT_LOG_ENV = "TER_EVENT_LOG_DIR"


def default_event_log_dir() -> Path:
    configured = os.environ.get(EVENT_LOG_ENV)
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "ter-events"


def make_tokenizer(name: str = "regex") -> Tokenizer:
    """``regex`` is offline and deterministic; ``tiktoken`` needs its encoding."""
    from ..adapters.driven.tokenizers import RegexTokenizer, TiktokenTokenizer

    if name == "tiktoken":
        return TiktokenTokenizer()
    if name == "regex":
        return RegexTokenizer()
    raise ValueError(f"Unknown tokenizer {name!r}")


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

        return ObserveEvent(make_tokenizer("regex"), JsonlEventLog(directory))

    return CliServices(
        analyse_transcript=analyse_transcript,
        log_sessions=log_sessions,
        analyse_log=analyse_log,
        hook_ingest=hook_ingest,
        default_log_dir=default_event_log_dir(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    return cli_main(argv, cli_services())
