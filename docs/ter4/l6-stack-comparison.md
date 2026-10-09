# L6: token use and waste by language and stack

Leigh's question: is there "a correlation between token usage and delivery
mechanism"? Do sessions in one language or stack (TypeScript/Svelte in a pnpm
monorepo, say) spend more tokens, or waste more of them, than sessions in
another?

TER records each session's languages and, with `--repo`, its repository's
stack ([l3-grounded.md](l3-grounded.md#session-languages-and-stack)). This
page is the comparison built on them. The tool is verified on synthetic data;
the answer needs a real corpus with enough sessions per stack, so points P182
and P185 stay `partial`.

## Requirements

| Id | Requirement | Verified by |
|---|---|---|
| TER-STK-010 | The stack comparison shall report, for each stratum of dominant language or repository stack by task category label and outcome label, the stratum's session count and the median generated tokens, context tokens, flow efficiency, unused context share and rework, regeneration and exploration rates of its sessions. | `tests/unit/test_ter4_stack_comparison.py` (`TestStratification`, `test_session_measures_read_the_scorecard_and_the_waste_allocation`, the script smoke tests) |
| TER-STK-011 | If a stratum holds fewer sessions than the stated minimum, then the stack comparison shall mark the stratum insufficient and report no measure and no comparison for it. | `TestInsufficientStrata`, `test_compare_by_stack_counts_failures_and_stratifies` |

## Why stratify

A raw "TypeScript sessions use more tokens than Python sessions" mostly
measures *which tasks* were done in which language. Three confounders are
larger than any plausible language effect:

- **task type**: a feature spends more than a one-line bugfix in any
  language;
- **repository size**: a large monorepo means more exploration and more
  context before the first edit;
- **outcome**: an abandoned session stops early and a merged one validates.

So sessions are only compared like with like. Each session gets a group (its
dominant language, or its stack label) and the `task_category` and `outcome`
labels of the corpus (`unlabelled` when missing). A **stratum** is one
(group, task category, outcome). Every stratum reports its session count; a
stratum with fewer than `--min-sessions` sessions (default **5**) is
*insufficient* and reports no measure. Groups are compared only inside one
(task category, outcome) cell, and only when at least two of its groups are
sufficient; those cells are listed under `comparisons`. Repository size is
not a stratum yet: the stack label groups sessions of one repository
together, so read a stack comparison as a comparison of repositories as much
as of stacks.

## Measures (per session, then the stratum median)

| Measure | Definition |
|---|---|
| `generated_tokens`, `context_tokens` | the L2 scorecard's text-token estimates |
| `flow_efficiency` | Agentic Flow Efficiency by tokens (L2 scorecard) |
| `unused_context` | unused retrieved context tokens / retrieved context tokens (context inventory, TER-DET-004) |
| `rework_rate` | generated tokens the scorecard charges to confident `rework_cycle` findings / generated tokens |
| `regeneration_rate` | the same for `regeneration` |
| `exploration_rate` | the same for `repeated_exploration` |

Rates come from the scorecard's waste allocation, so each generated token is
charged to at most one finding and uncertain findings (below 0.70) are never
counted. A measure with a zero denominator is left out of that stratum's
median, and `with_value` says how many sessions had one.

## Running it

Over an imported corpus (labels from its manifest; paths are redacted but
keep their extensions, so languages are known; there is no repository, so the
stack is `unknown`):

```bash
python scripts/corpus_by_stack.py corpus ~/ter-data/corpus --out by-stack.json
```

Over raw transcripts with the D4 labels file. With `--work`, each session's
repository is exported at its start commit with `git archive` (the export
helper of `scripts/corpus_grounded.py`) and its manifests give the stack:

```bash
python scripts/corpus_by_stack.py raw labels-d4.csv ~/.claude/projects \
    --work ~/ter-data/grounded --out by-stack.json --min-sessions 5
```

The output (`ter.corpus-by-stack/1`) holds counts, medians and ratios only:
language names, stack labels (framework names), label values and numbers. No
prompt, path, code or repository name, so it can be shared from a private
corpus.

## Code

| Layer | Module |
|---|---|
| domain | `ter/domain/stack_comparison.py`: `SessionMeasures`, `Stratum`, `StackComparison`, `compare_strata` |
| application | `ter/application/compare_stacks.py`: `session_measures`, `CompareByStack`, `StackCorpusReport` |
| script | `scripts/corpus_by_stack.py` |

## Data still needed

Leigh's 52 labelled sessions are mostly TypeScript/Svelte in two
repositories (tutors-mono-repo and ESI.ts), so almost every stratum but one
or two is insufficient, and with one dominant stack there is nothing to
compare against. The comparison needs sessions from more stacks (Python,
Go, Rust, JVM, other front-end frameworks) and more than one repository per
stack, labelled with task category and outcome, at least five per stratum.
