"""Reports: a driving-side presentation adapter.

Renders a :class:`ter.domain.report.SessionReport` as SVG charts
(:mod:`.svg`) and a self-contained HTML page (:mod:`.html`). Colours live in
:mod:`.palette`. :mod:`.ter3` builds the view-model from a TER 3 result and is
imported on demand, so the renderers stay free of TER 3 types.
"""

from __future__ import annotations

from .html import render_report_html, uncertainty_note
from .svg import report_charts

__all__ = ["render_report_html", "report_charts", "uncertainty_note"]
