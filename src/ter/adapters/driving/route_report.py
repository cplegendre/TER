"""Text rendering of a routing plan for ``python -m ter route`` (L3).

Reads only :class:`~ter.domain.routing.RoutingPlan` values; the routing
itself is the domain's (``ter.domain.routing``).
"""

from __future__ import annotations

from ...domain.routing import Dimension, RoutingPlan

__all__ = ["format_routing"]

_SHORT = {
    Dimension.COMPLEXITY: "complexity",
    Dimension.AMBIGUITY: "ambiguity",
    Dimension.RISK: "risk",
    Dimension.SCOPE: "scope",
    Dimension.VALIDATION: "validation",
}


def format_routing(plan: RoutingPlan, grounded: bool) -> str:
    profile = plan.profile
    lines = [
        f"TER route · session {plan.session_id or '-'} · profile {profile.name}"
        + (" · grounded" if grounded else ""),
        "  advisory: decisions and route.escalated events for analysis; "
        "nothing is sent to the session",
        "  roles     "
        + " · ".join(
            f"{role} {b.provider}/{b.model}" for role, b in profile.roles.items()
        ),
        "",
    ]
    escalated = sum(1 for d in plan.decisions if d.escalated)
    lines.append(
        f"Tasks {len(plan.decisions)} · escalated {escalated} · "
        f"kept {len(plan.decisions) - escalated}"
    )
    for d in plan.decisions:
        t = d.task
        role = d.role if not d.escalated else f"{d.role} -> {d.final_role}"
        lines.append("")
        lines.append(
            f"  task {t.task}  steps {t.first}-{t.last}  {t.kind.value}  role {role}"
        )
        for e in t.evidence:
            cited = f"  [{', '.join(e.events[:3])}{' …' if len(e.events) > 3 else ''}]"
            lines.append(
                f"    {_SHORT[e.dimension]:<11}{e.value:<11}{e.reason}"
                + (cited if e.events else "")
            )
        lines.append(f"    decision   {d.reason}")
        if d.event is not None:
            lines.append(f"    event      route.escalated {d.event.id}: {d.event.text}")
    return "\n".join(lines) + "\n"
