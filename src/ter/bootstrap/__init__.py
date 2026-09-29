"""Composition root: the only place that chooses concrete adapters.

Entry points (CLI, hooks, CI gate) ask this package for wired use cases, and
it applies the installation's maturity ceiling while doing so. No other
module decides which capabilities are switched on.
"""
