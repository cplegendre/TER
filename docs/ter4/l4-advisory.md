# L4 Advisory: policies and approval

L4 turns confident, judged detector findings into advice for the developer.
The advice will appear as a message from the hook TER already runs, and it
never changes what the agent sees. That hook delivery is a later step (step
5): today the hook still prints nothing, and approving a policy file lets an
engine be built from it, nothing more. This page covers the parts that exist
so far: the Intervention Engine (step 1) and policy files with human approval
(step 2). The build order and the remaining steps are in
[strategy.md](strategy.md#l4-advisory).

## The Intervention Engine

`ter.domain.intervention` reads only structured detector signals
(`ter.domain.signal.Signal`), never events or findings (TER-INT-006). For
each signal and each policy that answers its detector, it records a verdict
with its reason. The guards run in a fixed order:

1. **Ceiling:** below L4 nothing is advised (TER-INT-002).
2. **Evidence:** a signal that cites no event, nothing beyond a token count,
   is never advised (TER-INT-007).
3. **Eligibility:** only detectors on the calibration policy's eligibility
   list are advised (TER-INT-010).
4. **Confidence:** a signal below 0.70 is uncertain. It counts as waste
   until verified (ADR 0006) but never triggers advice.
5. **Cooldown and hysteresis:** a policy fires once `min_signals` matching
   signals have arrived, then stays silent for `cooldown_tool_events` tool
   events (TER-INT-016).

## Policy files

A policy file (`ter.intervention-policies/1`, TER-INT-008) declares:

- the eligibility list, with its judged basis;
- each policy's detectors, evidence requirement, confidence threshold,
  hysteresis, cooldown and permitted actions;
- the advice each policy gives.

Only `advise` is a permitted action at L4. A corrective action is refused
with a pointer to L5.

The draft for the three eligible detectors is
[docs/examples/policies/l4-advisory.json](../examples/policies/l4-advisory.json):

| Policy | Detector | Fires on |
|---|---|---|
| `batch-edits` | `fragmented_edits` | many small edits to one file in separate round trips |
| `reuse-reads` | `repeated_exploration` | re-reading a file, or re-running a search, with no edit in between |
| `edit-not-rewrite` | `regeneration` | a whole-file rewrite repeating at least 70% of the old file (confidence 0.80) |

Their eligibility rests on the two judged samples: `fragmented_edits` was 17
of 17 waste, `repeated_exploration` 10 of 10, and `regeneration` at 70% or
more repeated 3 of 3 ([l2-explained.md](l2-explained.md)).

Policies load through the capability registry as `InterventionPolicies.json`
(TER-ARC-008), so a capability pack can add another source.

## Approval

A policy applies only from a file a person has approved (TER-INT-015). The
approval record names who approved it, when, and the sha256 digest of the
file's content. If the file has no record, or has changed since it was
approved, `PolicySet.engine` refuses to build an engine from it.
Reformatting the file keeps the approval; changing any value voids it.

TER ships the draft unapproved and never approves on its own. To review and
approve a policy file:

```bash
python -m ter policy check docs/examples/policies/l4-advisory.json
python -m ter policy approve docs/examples/policies/l4-advisory.json --by "Your Name" --note "read all three"
```

`check` exits 0 when the file is approved in its current form. It exits 1
when the file is unapproved or changed, and 2 when it is not a valid policy
file. It also lists any policy that names a detector outside the eligibility
list; such a policy's signals are recorded as ineligible and never advised.
