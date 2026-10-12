"""The control loop's stages: Observe, Analyse, Decide, Intervene, Measure, Learn.

TER-INT-013, points 171 and 172. Every turn of the loop runs these stages in
this order (:data:`ORDER`); ``ter.application.loop`` is where they run. What
each stage does at L4 Advisory:

* **Observe**: read the session's recorded events;
* **Analyse**: fold them into the Lean analysis, whose findings become
  detector signals;
* **Decide**: the Intervention Engine judges each signal under the approved
  policy file, and every verdict is a ledger record;
* **Intervene**: deliver the new advice to the developer (skipped in shadow
  mode, where nothing is delivered);
* **Measure**: record the outcome of earlier advice once its window has
  passed (skipped in shadow mode, where nothing was delivered);
* **Learn**: score each policy from the outcomes, flagging the ones that do
  not improve them. At L4 that is a report for a person: TER never changes a
  policy itself (point 173).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ORDER", "Stage"]


class Stage(StrEnum):
    """One stage of the control loop."""

    OBSERVE = "observe"
    ANALYSE = "analyse"
    DECIDE = "decide"
    INTERVENE = "intervene"
    MEASURE = "measure"
    LEARN = "learn"


#: The stages of one turn, in the order they run.
ORDER: tuple[Stage, ...] = tuple(Stage)
