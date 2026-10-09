"""The Lean model of an agentic software session (maturity level L2, Explained).

* :mod:`.model`: activity classes, Lean wastes, value-stream stages, findings.
* :mod:`.facts`: what single events show (shell intent, validation outcome).
* :mod:`.steps`: the incremental fold from events to steps.
* :mod:`.detectors`: the ``WasteDetector`` protocol, registry and L2 detectors.
* :mod:`.graph`: the session Evidence Graph.
* :mod:`.analysis`: classification, value stream, scorecard.
* :mod:`.countermeasures` and :mod:`.a3`: what to change, as an A3 view-model.
"""

from __future__ import annotations

from .a3 import A3_SCHEMA, A3Report, ParetoBar, build_a3
from .analysis import (
    Classification,
    Composite,
    LeanAnalyser,
    LeanAnalysis,
    Scorecard,
    StageSummary,
    TerMeasure,
    analyse_steps,
    explain,
)
from .countermeasures import (
    Action,
    ActionKind,
    Countermeasure,
    FollowUp,
    build_countermeasures,
)
from .detectors import (
    DEFAULT_REGISTRY,
    DetectorRegistry,
    SessionView,
    WasteDetector,
    validation_cycles,
)
from .graph import EdgeType, EvidenceEdge, EvidenceGraph, EvidenceNode
from .model import (
    STAGE_ORDER,
    UNCERTAIN_BELOW,
    ActivityClass,
    CycleVerdict,
    ExplorationDriver,
    ExplorationLabel,
    Finding,
    FindingKind,
    FlowState,
    LeanWaste,
    Outcome,
    ShellIntent,
    Stage,
    Step,
    ValidationCycle,
)
from .steps import StepLog

__all__ = [
    "A3_SCHEMA",
    "A3Report",
    "Action",
    "ActionKind",
    "Countermeasure",
    "FollowUp",
    "ParetoBar",
    "build_a3",
    "build_countermeasures",
    "DEFAULT_REGISTRY",
    "STAGE_ORDER",
    "UNCERTAIN_BELOW",
    "ActivityClass",
    "Classification",
    "Composite",
    "CycleVerdict",
    "DetectorRegistry",
    "EdgeType",
    "EvidenceEdge",
    "EvidenceGraph",
    "EvidenceNode",
    "ExplorationDriver",
    "ExplorationLabel",
    "Finding",
    "FindingKind",
    "FlowState",
    "LeanAnalyser",
    "LeanAnalysis",
    "LeanWaste",
    "Outcome",
    "Scorecard",
    "SessionView",
    "ShellIntent",
    "Stage",
    "StageSummary",
    "Step",
    "StepLog",
    "TerMeasure",
    "ValidationCycle",
    "WasteDetector",
    "analyse_steps",
    "explain",
    "validation_cycles",
]
