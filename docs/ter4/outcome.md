# Outcome and acceptance

Point 5 separates two questions: how did the agent behave, and is the
resulting software correct? TER measures the first from the session's
events. The second is a **verdict** judged from outcome evidence (test
results, acceptance checks) that TER reads through the `OutcomeSource` port.
The verdict is shown beside the behaviour measures and never feeds them.

This is capability 1 of the TER × GARE integration plan, built as a
capability pack ([ADR 0005](../decisions/0005-admitting-external-capabilities.md)):
a port, a contract suite, EARS requirements (`TER-OUT-001` to `TER-OUT-008`,
`TER-SCR-004` to `TER-SCR-006`), an in-memory fake and a reference adapter
that reads JUnit XML. A GARE adapter that reads GARE's recorded runs by
schema name is the next pack (issue #52).

## Model (`ter.domain.outcome`)

| Type | Meaning |
|---|---|
| `Check` | One condition of acceptance, by id (`tests.test_parser::test_round_trip`); `required` or optional |
| `AcceptanceContract` | The checks a run must pass. Default: every check the run recorded (`AcceptanceContract.every_check_in`) |
| `CheckEvidence` | One recorded result for one check: `passed`, `failed`, `error` or `skipped`, with where it was recorded, a one-line detail and its duration |
| `OutcomeEvidence` | Everything a source recorded for one run, in record order |
| `OutcomeVerdict` | `accepted`, `rejected` or `incomplete`, the contract, every check's evidence, evidence for checks the contract does not name, and the reasons |

`judge(evidence, contract)` decides:

- **rejected** when any required check has failing (failed or error)
  evidence (TER-OUT-005). A check recorded more than once (a rerun) counts as
  failed if any record failed;
- otherwise **incomplete** when a required check was skipped, has no
  evidence, or the contract requires no check (TER-OUT-006);
- otherwise **accepted**.

Optional checks are reported but never decide. There is no weighted outcome
score (TER-OUT-004): hand-set weights would hide which check decided the
result, so TER keeps the evidence per check and a three-valued verdict.

## The port and its adapters

`OutcomeSource.outcome(ref)` returns the evidence recorded for a run, or
`None` when none is recorded; absence is unknown, never a failure
(TER-OUT-002). The same reference gives equal evidence every time, and
failing, erroring and skipped results are kept (TER-OUT-001). A record that
exists but cannot be read raises `OutcomeFormatError` naming it
(TER-OUT-003). `tests/contract/test_outcome_source.py` runs these
obligations against every adapter and the fake.

| Adapter | Capability | Reads |
|---|---|---|
| `JUnitOutcomeSource` | `OutcomeSource.junit` | One JUnit XML file per run, as written by `pytest --junitxml`, Surefire, Gradle, `jest-junit`, `go-junit-report` and most CI runners (TER-OUT-007) |
| `InMemoryOutcomeSource` | (fake) | Evidence built in code |

Each `<testcase>` becomes one check, `<classname>::<name>`. Test results come
from outside TER and `defusedxml` is not a dependency, so the JUnit adapter
rejects any document that declares a DOCTYPE or an entity before parsing it:
no entity is expanded and nothing external is fetched (TER-OUT-008).

The adapter is registered as a capability in `pyproject.toml`:

```toml
[project.entry-points."ter.capabilities"]
"OutcomeSource.junit" = "ter.adapters.driven.junit:JUnitOutcomeSource"
```

## Beside the scorecard, never inside it

The modules that compute behaviour measures (events, pricing, scoring, the
L1 engine, the Lean analysis, detectors and countermeasures) may not import
`ter.domain.outcome`; the `behaviour-blind-to-outcome` import contract
enforces it (TER-SCR-005). The A3 view-model shows the verdict in its own
Outcome box beside the scorecard, and every measure is identical with or
without it (TER-SCR-004). The one joint figure is **generated tokens per
verified outcome**, the generated tokens divided by the accepted outcomes, and
no figure when none was accepted (TER-SCR-006).

```bash
pytest --junitxml=results.xml
python -m ter a3 session.jsonl --outcome results.xml --html a3.html --json a3.json
python -m ter explain session.jsonl --outcome results.xml
```

The A3 JSON carries an `outcome` object (verdict, reasons, contract, each
check with its evidence, unlisted evidence and
`generated_tokens_per_verified_outcome`) only when `--outcome` is given, so
reports without one are unchanged.

## The verdict as an event (TER-EXP-002)

`verdict_event(session, verdict)` records a verdict as a `verdict.recorded`
event of its session: the run, its source, the acceptance contract and every
piece of evidence, with the verdict as a cross-check. `recorded_verdict(events)`
judges it again from that record, so a report rebuilt from the event log alone
shows the same verdict (and refuses a record whose evidence judges otherwise).
The behaviour fold never reads it. See
[l3-grounded.md](l3-grounded.md#recorded-measures-ter-exp-002).

## Not yet

- Software Value Efficiency (TER-SCR-003) reads the verdict the same way:
  it sits next to TER and is *unknown* without outcome evidence (see
  [l2-explained.md](l2-explained.md#software-value-efficiency)).
- An acceptance contract other than "every recorded check passes" is
  available in the API (the `contract` argument of `ExplainSession`) but not yet on
  the command line.
- Cost per verified outcome waits for cost on the L2 scorecard.
- The GARE outcome adapter (issue #52) needs access to the GARE repository and
  a recorded run to pin its schema.
