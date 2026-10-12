# L4 Advisory: policies, approval and live advice

L4 turns confident, judged detector findings into advice for the developer.
The advice appears as a message from the hook TER already runs, at the end
of a turn, and it never changes what the agent sees. This page covers the
parts that exist so far: the Intervention Engine (step 1), policy files with
human approval (step 2), the ledger (step 3), shadow mode (step 4) and live
advice through the hook (step 5). The build order and the remaining steps
are in [strategy.md](strategy.md#l4-advisory).

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

The draft is
[docs/examples/policies/l4-advisory.json](../examples/policies/l4-advisory.json).
Two of its detectors are eligible:

| Policy | Detector | Fires on |
|---|---|---|
| `batch-edits` | `fragmented_edits` | many small edits to one file in separate round trips |
| `reuse-reads` | `repeated_exploration` | re-reading a file, or re-running a search, with no edit in between |
| `edit-not-rewrite` | `regeneration` (not eligible) | a whole-file rewrite repeating at least 70% of the old file; recorded as ineligible, never advised |

Eligibility rests on the two judged samples and the first shadow run (12 Oct
2026, 64 sessions, 12 pieces of advice judged by the owner):

| Detector | Judged samples | Shadow run | Now |
|---|---|---|---|
| `fragmented_edits` | 17 of 17 waste | 5 waste, 0 not waste, 1 unsure | eligible |
| `repeated_exploration` | 10 of 10 waste | 0 waste, 3 not waste | eligible; the 3 were re-read images a render had redrawn, which are now uncertain |
| `regeneration` | 3 of 3 at 70% or more repeated, one session | 0 waste, 2 not waste, 1 unsure | not eligible |

The two false `regeneration` rows rewrote files under 1 KB, where a rewrite
costs no more than an edit. That is two rows, not a rule, so the detector is
unchanged and only its eligibility is withdrawn.

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

## Live advice

With `--advise` and an approved policy file, the hook gives advice at the
end of each turn (the `Stop` hook):

```bash
python -m ter hook --event-log ~/.ter/events --advise docs/examples/policies/l4-advisory.json --ledger ~/.ter/ledger
```

`--ledger` defaults to `ledger` inside the event log directory. On `Stop`
the hook replays the session's event log, explains it, and decides on its
signals exactly as shadow mode does (`ter.application.advise`). Every
decision is written to the ledger; the advice not given before is printed
as the hook's `systemMessage`, which Claude Code shows the developer and
never adds to the model's context (TER-INT-002). The same advice is never
given twice, because a decision the ledger already holds is not written or
delivered again (TER-INT-003), and a finding that gains evidence between
two turns (more small edits of one file) is not advised again by the policy
that already advised it under the same file. Every other hook event, and
every hook without `--advise`, still prints `{}`.

The Stop hook replays the whole session, so its cost grows with the session,
once per turn: about a second for 3,000 events (a benchmark in
`tests/unit/test_ter4_advise.py`), against Claude Code's default hook
timeout of 60 seconds. Advice past Claude Code's 10,000-character hook
output limit is counted at the end of the message instead of being cut; all
of it is in the ledger.

Each message shows the advice with the signal and evidence behind it
(TER-INT-014):

```text
TER warning (reuse-reads): Claude re-read a file, or re-ran a search, with no edit in between. The answer is already in its context; a note in CLAUDE.md to reuse earlier reads and search results avoids the repeat.
Why: Re-read src/app.py (repeated_exploration, confidence 0.85: 0.85 when the same read or search (same arguments) returned identical output and the file was not edited in between; ...)
Evidence: 1b87a23bd9056e34, 2b5db1c78a6dc335, 524b6ec125634e52, c30726a9604bad2f
```

That is the advice the sample session `sample_sessions/example_session.jsonl`
gets (the confidence rule is shortened here). At most four evidence event
ids are shown, then a count of the rest; the ledger keeps them all.

The hook fails open. An unapproved or changed policy file, an unreadable
log or a ledger that cannot be written gives no advice: the hook prints
`{}`, exits 0, and leaves a `ter hook: no advice given: ...` line on stderr.
Advice is delivered through the `InterventionChannel` port
(`MessageChannel` renders it as text for the hook), with a contract suite in
`tests/contract/test_intervention_channel.py`.
`tests/architecture/test_no_intervention.py` keeps the ceiling: without
`--advise` every hook answers `{}`, and `systemMessage` is the only hook
response field any TER module names, in the hook entry point alone.

Only detectors judged eligible are advised on (`fragmented_edits` and
`repeated_exploration` today), so warnings on context growth, drift,
repeated failures, untested edits, WIP and context bloat (points 125 to
130) wait for a detector judged eligible for them.
