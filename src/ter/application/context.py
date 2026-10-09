"""L3 use cases: build, supply and measure context bundles.

* :class:`ContextBuilder` selects the evidence for a prompt from the
  repository (through the ``RepositoryEvidence`` port) and packs it into a
  :class:`~ter.domain.context_bundle.ContextBundle` within a token budget
  (TER-CTX-001).
* :class:`SupplyContext` builds the bundle for a session's next decision and
  appends one ``context.supplied`` event per fragment to the session's event
  log (TER-EVD-004). It hands the bundle back to its caller, the command
  line; nothing is written into a hook response, so TER still delivers no
  intervention through hooks below L4 (TER-INT-001).
* :class:`MeasureContext` reports each bundle's precision, recall, unused
  context carrying cost and missing context (TER-CTX-003, TER-CTX-004) and,
  given a critical evidence list, the session's critical recall
  (TER-EVD-005). A recorded session that holds no ``context.supplied``
  events is measured against the bundles TER would have supplied: one per
  prompt, built from the repository at the session's start commit and placed
  right after the prompt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.context_bundle import (
    Candidate,
    ContextBundle,
    assemble_bundle,
    outline_of,
    select_evidence,
    supplied_events,
)
from ..domain.context_metrics import (
    ContextMeasures,
    CriticalEvidence,
    measure_context,
)
from ..domain.events import Event, EventId, EventKind
from ..domain.lean.facts import content_words
from ..domain.lean.grounding import RepositoryGrounding
from ..domain.lean.surface import files_named
from ..domain.repository import RepositoryEvidenceError
from ..ports.driven import Clock, EventLog, PriceBook, RepositoryEvidence, Tokenizer
from .ground import ground_session

__all__ = [
    "DEFAULT_BUDGET",
    "ContextBuilder",
    "MeasureContext",
    "SuppliedContext",
    "SupplyContext",
    "inherited_seeds",
]

#: Default token budget of a bundle.
DEFAULT_BUDGET = 8000


class ContextBuilder:
    """Builds bundles for one repository at one commit.

    Texts and outlines are read once per file and cached, so building a
    bundle for every prompt of a session reads each selected file once.
    """

    def __init__(
        self,
        evidence: RepositoryEvidence,
        tokenizer: Tokenizer,
        grounding: RepositoryGrounding,
    ) -> None:
        self._evidence = evidence
        self._tokenizer = tokenizer
        self.grounding = grounding
        self._texts: dict[str, str | None] = {}
        self._outlines: dict[str, str | None] = {}

    def _text(self, path: str) -> str | None:
        if path not in self._texts:
            try:
                self._texts[path] = self._evidence.text(path)
            except RepositoryEvidenceError:
                self._texts[path] = None
        return self._texts[path]

    def _outline(self, path: str) -> str | None:
        if path not in self._outlines:
            try:
                structure = self._evidence.structure(path)
            except RepositoryEvidenceError:
                structure = None
            self._outlines[path] = None if structure is None else outline_of(structure)
        return self._outlines[path]

    def build(
        self,
        prompt_text: str,
        *,
        session_id: str,
        budget: int,
        prompt: EventId | None = None,
        inherited: Sequence[str] = (),
    ) -> ContextBundle:
        basis, seeds, selection = select_evidence(
            self.grounding, content_words(prompt_text), inherited
        )
        candidates = [
            Candidate(s, self._text(s.path), self._outline(s.path)) for s in selection
        ]
        return assemble_bundle(
            session_id=session_id,
            prompt_text=prompt_text,
            candidates=candidates,
            budget=budget,
            count=self._tokenizer.count,
            basis=basis,
            seeds=seeds,
            prompt=prompt,
            tokenizer=self._tokenizer.name,
            engine=self._evidence.name,
        )


def inherited_seeds(
    g: RepositoryGrounding, prompts: Sequence[Event]
) -> tuple[str, ...]:
    """The files the last of ``prompts`` that names repository files names."""
    for event in reversed(prompts):
        named = files_named(g, content_words(event.text))
        if named:
            return named
    return ()


@dataclass(frozen=True)
class SuppliedContext:
    bundle: ContextBundle
    #: The ``context.supplied`` events appended, one per fragment.
    events: tuple[Event, ...]


class SupplyContext:
    """Build the bundle for a session's next decision and record its supply.

    The prompt is ``prompt_text`` when given, else the session's last
    prompt. ``events`` are the session's events so far (from its transcript
    or the event log); without them the log's events for ``session_id`` are
    read.
    """

    def __init__(
        self,
        evidence: RepositoryEvidence,
        tokenizer: Tokenizer,
        log: EventLog | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._evidence = evidence
        self._tokenizer = tokenizer
        self._log = log
        self._clock = clock

    def __call__(
        self,
        *,
        session_id: str,
        budget: int = DEFAULT_BUDGET,
        prompt_text: str | None = None,
        events: Sequence[Event] | None = None,
    ) -> SuppliedContext:
        logged = self._log.events(session_id) if self._log is not None else ()
        history = tuple(events) if events is not None else logged
        g = ground_session(history, self._evidence)
        prompts = [e for e in history if e.kind is EventKind.PROMPT]
        prompt: EventId | None = None
        earlier: Sequence[Event] = prompts
        if prompt_text is None:
            if not prompts:
                raise ValueError(
                    f"session {session_id!r} has no prompt; pass the prompt text"
                )
            prompt_text, prompt = prompts[-1].text, prompts[-1].id
            earlier = prompts[:-1]
        builder = ContextBuilder(self._evidence, self._tokenizer, g)
        bundle = builder.build(
            prompt_text,
            session_id=session_id,
            budget=budget,
            prompt=prompt,
            inherited=inherited_seeds(g, earlier),
        )
        supplied = supplied_events(
            bundle,
            start_sequence=len(logged),
            timestamp=self._clock.now() if self._clock is not None else None,
        )
        if self._log is not None:
            for event in supplied:
                self._log.append(event)
        return SuppliedContext(bundle, supplied)


class MeasureContext:
    """Context measures of a session (TER-CTX-003, TER-CTX-004, TER-EVD-005)."""

    def __init__(
        self,
        evidence: RepositoryEvidence,
        tokenizer: Tokenizer,
        prices: PriceBook | None = None,
    ) -> None:
        self._evidence = evidence
        self._tokenizer = tokenizer
        self._prices = prices

    def __call__(
        self,
        events: Sequence[Event],
        *,
        budget: int = DEFAULT_BUDGET,
        critical: CriticalEvidence | None = None,
    ) -> ContextMeasures:
        g = ground_session(events, self._evidence)
        simulated = not any(e.kind is EventKind.CONTEXT_SUPPLIED for e in events)
        stream: Sequence[Event] = events
        if simulated:
            stream = self._with_bundles(events, g, budget)
        return measure_context(
            stream, g, critical=critical, prices=self._prices, simulated=simulated
        )

    def _with_bundles(
        self, events: Sequence[Event], g: RepositoryGrounding, budget: int
    ) -> tuple[Event, ...]:
        """The session with the bundle TER would have supplied placed right
        after each prompt."""
        builder = ContextBuilder(self._evidence, self._tokenizer, g)
        out: list[Event] = []
        prompts: list[Event] = []
        for event in events:
            out.append(event)
            if event.kind is not EventKind.PROMPT:
                continue
            bundle = builder.build(
                event.text,
                session_id=event.session_id,
                budget=budget,
                prompt=event.id,
                inherited=inherited_seeds(g, prompts),
            )
            prompts.append(event)
            out.extend(
                supplied_events(
                    bundle, start_sequence=event.sequence, timestamp=event.timestamp
                )
            )
        return tuple(out)
