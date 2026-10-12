# L4 Advisory: policies and approval

L4 turns confident, judged detector findings into advice for the developer.
The advice will appear as a message from the hook TER already runs, and it
never changes what the agent sees. That hook delivery is a later step (step
5): today the hook still prints nothing, and approving a policy file lets an
engine be built from it, nothing more. This page covers the parts that exist
so far: the Intervention Engine (step 1), policy files with human approval
(step 2), the ledger (step 3) and shadow mode (step 4). The build order and the remaining steps are in
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
more repeated 3 of 3 ([l2-explained.md](l2-explained.md)). The
`regeneration` rows come from one session, and the 70% cut was set from
those same rows (7 unsure rows sat at 52% to 64%), so that policy is a
proposal until another session confirms it. Shadow mode (step 4) is where
that check happens, before any advice reaches a developer.

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

## The ledger

Every decision the engine makes about a signal under a policy is written to
the intervention ledger (`ter.domain.ledger`, TER-INT-009) as one record:

- the triggering signal: its id, detector, waste, confidence, published rule
  and position;
- the evidence: the event ids the signal cites;
- the action taken: `advise`, or none when a guard suppressed it, with the
  outcome (`cooldown`, `ineligible`, `low_confidence` and so on) and the
  reason;
- the observed outcome, attached once to a delivered intervention: whether
  the developer accepted, ignored or rejected it, and flow measures before
  and after. Taking that measurement 10 tool events later is step 6
  (TER-INT-011).

Suppressions are recorded too, so the ledger shows advice delivered, held
back by cooldown and held back for ineligible detectors, which is what the
L4 exit gate asks to see. A record cites event ids, the detector, its rule
and the policy, never prompt text, file content or a finding's title, so a
ledger can be shared like the corpus reports. A record's id is a digest of
the decision, so replaying the same signals writes the same ids, and a
ledger keeps the first record for each id.

The `InterventionLedger.jsonl` capability keeps one append-only
`<session>.ledger.jsonl` file per session, private to its owner. Recording
appends a line without reading the file, so a hook's cost does not grow with
the session. A line cut short by a crash is skipped, never raised.

## Shadow mode

Shadow mode replays recorded sessions through an approved policy file and
writes every decision to a ledger, delivering nothing (TER-INT-017). Each
session is explained as `python -m ter explain` does, its findings become
detector signals, and a fresh engine judges them, so cooldowns count within
a session. An unapproved or changed file replays nothing.

```bash
python -m ter policy shadow docs/examples/policies/l4-advisory.json ~/.claude/projects --ledger shadow-ledger --judge shadow-judge.csv
```

Sessions are found as `control measure` finds them: files, folders, or a
corpus, with a GARE run export folder replayed as one session.

The summary gives each policy's decisions by outcome: advised, cooldown,
below hysteresis, low confidence and so on. `--judge` writes the advice TER
would have given to a CSV with an empty `verdict` column (waste, not waste
or unsure) and the finding's title and subject, so a person can judge it.
The CSV names files and session ids, so it stays on the developer's
machine, created private to its owner. Each record carries the digest of
the policy file that made it, so a run under a revised file writes new
records beside the old ones, and a second run under the same file writes
nothing new.

Judging that CSV is how a policy earns live advice (step 5). It is also how
the `regeneration` 70% cut gets the second session it needs.
