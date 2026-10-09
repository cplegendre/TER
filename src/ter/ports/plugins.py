"""Analysis plugins: protocols for code that runs inside the core's analysis.

A driven port is how the core reaches the outside world; a plugin is a piece
of analysis the core runs. Both are loaded through the same capability
registry (``ter.capabilities`` entry points, ADR 0005), keyed
``<Kind>.<name>`` (TER-ARC-002).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.lean.detectors import WasteDetector

__all__ = ["WasteDetectorPlugin"]


@runtime_checkable
class WasteDetectorPlugin(WasteDetector, Protocol):
    """A :class:`~ter.domain.lean.detectors.WasteDetector`, checkable at load time.

    Registered as ``WasteDetector.<id>``. The capability name should be the
    detector's own ``id``; every finding it returns must cite evidence event
    ids and its published ``confidence_rule`` (see ``docs/ter4/l2-explained.md``).
    """
