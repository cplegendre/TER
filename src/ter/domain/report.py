"""Report view-model: what a TER session report shows, independent of format.

A ``SessionReport`` is a frozen, presentation-neutral projection of one
analysed session. Renderers (SVG charts, the HTML report, later slide decks
and A3 sheets) read only this model, so they never depend on how the numbers
were computed, and a new analysis engine only needs a new mapper.

The model holds raw values (token counts, ratios, machine labels). Display
names, colours and number formats belong to the renderers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Span labels TER counts as aligned work. Every other label is waste.
ALIGNED_SPAN_LABELS: frozenset[str] = frozenset(
    {"aligned_reasoning", "aligned_tool_call", "aligned_response"}
)

#: Canonical order of span labels: aligned first, then waste, each by phase.
SPAN_LABEL_ORDER: tuple[str, ...] = (
    "aligned_reasoning",
    "aligned_tool_call",
    "aligned_response",
    "redundant_reasoning",
    "unnecessary_tool_call",
    "over_explanation",
)

#: Canonical order of the three span phases.
PHASE_ORDER: tuple[str, ...] = ("reasoning", "tool_use", "generation")


class Reliability(StrEnum):
    """How much weight a reader can put on the headline TER."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: str) -> Reliability:
        """Map a free-form reliability string onto the enum."""
        try:
            return cls(value.lower())
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class KeyMetrics:
    """Headline numbers for the session."""

    ter: float
    raw_ratio: float
    total_tokens: int
    aligned_tokens: int
    waste_tokens: int

    @property
    def waste_share(self) -> float:
        """Waste tokens as a share of scored tokens (0 when nothing was scored)."""
        return self.waste_tokens / self.total_tokens if self.total_tokens else 0.0


@dataclass(frozen=True)
class LabelTokens:
    """Scored tokens that received one classification label."""

    label: str
    tokens: int

    @property
    def aligned(self) -> bool:
        return self.label in ALIGNED_SPAN_LABELS


@dataclass(frozen=True)
class PhaseScore:
    """TER for one phase, with the tokens it covers."""

    phase: str
    score: float
    tokens: int = 0


@dataclass(frozen=True)
class SpanCell:
    """One classified span, reduced to what a timeline needs."""

    position: int
    phase: str
    label: str
    tokens: int
    confidence: float

    @property
    def aligned(self) -> bool:
        return self.label in ALIGNED_SPAN_LABELS


@dataclass(frozen=True)
class WasteEntry:
    """One detected waste pattern."""

    pattern_type: str
    description: str
    start_position: int
    end_position: int
    spans_involved: int
    tokens_wasted: int


@dataclass(frozen=True)
class WasteByType:
    """Waste tokens summed over every pattern of one type."""

    pattern_type: str
    tokens: int
    occurrences: int


@dataclass(frozen=True)
class PositionalTer:
    """TER over the early, middle and late thirds of the session."""

    early: float
    mid: float
    late: float
    early_spans: int = 0
    mid_spans: int = 0
    late_spans: int = 0

    @property
    def values(self) -> tuple[float, float, float]:
        return (self.early, self.mid, self.late)


@dataclass(frozen=True)
class EconomicsSummary:
    """Provider-reported token volumes and their estimated cost."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cache_hit_rate: float
    cost_usd: float
    waste_cost_usd: float


@dataclass(frozen=True)
class UncertaintySummary:
    """How precisely the headline TER is known."""

    lower: float
    upper: float
    confidence_level: float
    reliability: Reliability
    mean_confidence: float
    low_confidence_tokens: int
    low_confidence_share: float
    method: str

    @property
    def width(self) -> float:
        return self.upper - self.lower


@dataclass(frozen=True)
class ReportSection:
    """A titled block of prose, for content such as a later A3 sheet.

    ``paragraphs`` are plain text; renderers escape them.
    """

    key: str
    title: str
    paragraphs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionReport:
    """Everything a TER report shows about one session."""

    session_id: str
    classifier_version: str
    metrics: KeyMetrics
    composition: tuple[LabelTokens, ...] = ()
    phases: tuple[PhaseScore, ...] = ()
    spans: tuple[SpanCell, ...] = ()
    waste_patterns: tuple[WasteEntry, ...] = ()
    positional: PositionalTer | None = None
    economics: EconomicsSummary | None = None
    uncertainty: UncertaintySummary | None = None
    sections: tuple[ReportSection, ...] = field(default=())

    def waste_by_type(self) -> tuple[WasteByType, ...]:
        """Waste patterns grouped by type, largest first (ties by name)."""
        tokens: dict[str, int] = {}
        counts: dict[str, int] = {}
        for entry in self.waste_patterns:
            tokens[entry.pattern_type] = (
                tokens.get(entry.pattern_type, 0) + entry.tokens_wasted
            )
            counts[entry.pattern_type] = counts.get(entry.pattern_type, 0) + 1
        return tuple(
            WasteByType(name, tokens[name], counts[name])
            for name in sorted(tokens, key=lambda n: (-tokens[n], n))
        )

    @property
    def pattern_waste_tokens(self) -> int:
        """Tokens attributed to detected waste patterns (may overlap span waste)."""
        return sum(entry.tokens_wasted for entry in self.waste_patterns)
