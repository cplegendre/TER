"""Composition of the L3 context commands (``python -m ter context``)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..adapters.driving.context_cli import BundleRequest, ContextServices, ReportRequest
from ..application.context import MeasureContext, SuppliedContext, SupplyContext
from ..domain.context_metrics import ContextMeasures
from ..domain.events import Event
from ..ports.driven import SessionSource, Tokenizer
from .capabilities import repository_evidence

__all__ = ["context_services"]


def context_services(
    session_source_for: Callable[[Path], SessionSource],
    make_tokenizer: Callable[[str], Tokenizer],
) -> ContextServices:
    """Wire the context use cases. Heavy adapters are imported on first use."""

    def session_events(
        path: Path | None, log_dir: Path | None, session_id: str | None
    ) -> tuple[str, tuple[Event, ...]]:
        if path is not None:
            trace = session_source_for(path).read(path)
            return session_id or trace.session_id, trace.events
        if log_dir is None or session_id is None:
            raise ValueError("give a session transcript, or --event-log and --session")
        from ..adapters.driven.event_log import JsonlEventLog

        return session_id, JsonlEventLog(log_dir).events(session_id)

    def bundle(request: BundleRequest) -> SuppliedContext:
        from ..adapters.driven.event_log import JsonlEventLog
        from ..adapters.driven.in_memory import SystemClock

        log = JsonlEventLog(request.event_log) if request.event_log else None
        events: tuple[Event, ...] | None = None
        if request.session_path is not None:
            session_id, events = session_events(
                request.session_path, None, request.session_id
            )
        else:
            assert request.session_id is not None  # checked by the CLI
            session_id = request.session_id
            if log is not None and not request.record:
                events = log.events(session_id)  # history only, nothing appended
        use_case = SupplyContext(
            repository_evidence(request.repo, request.engine),
            make_tokenizer(request.tokenizer),
            log if request.record else None,
            SystemClock(),
        )
        return use_case(
            session_id=session_id,
            budget=request.budget,
            prompt_text=request.prompt,
            events=events,
        )

    def report(request: ReportRequest) -> ContextMeasures:
        from ..adapters.driven.critical_evidence import read_critical_evidence
        from ..adapters.driven.pricing import default_price_book

        session_id, events = session_events(
            request.session_path, request.event_log, request.session_id
        )
        critical = (
            read_critical_evidence(request.critical, session_id)
            if request.critical is not None
            else None
        )
        measures = MeasureContext(
            repository_evidence(request.repo, request.engine),
            make_tokenizer(request.tokenizer),
            default_price_book(),
        )(events, budget=request.budget, critical=critical)
        if not measures.session_id:
            # A session with no events still names itself in the report.
            from dataclasses import replace

            measures = replace(measures, session_id=session_id)
        return measures

    return ContextServices(bundle=bundle, report=report)
