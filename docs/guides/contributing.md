# Contributing guide

The short version is in [CONTRIBUTING.md](../../CONTRIBUTING.md). This guide
is the full workflow for a TER 4 change: setup, the rules the code follows,
the gates to run before pushing, and what a commit and PR must say.

## Setup

```bash
git clone https://github.com/lgriffin/TER.git
cd TER
python -m venv .venv && source .venv/bin/activate
python -m pip install -c constraints/dev.txt -e ".[dev]"
pre-commit install
```

Add `embeddings` to the extras (`".[dev,embeddings]"`) to run TER 3's
semantic analysis with the real sentence-transformers model; CI installs it
for the test job. Supported Python versions are 3.11, 3.12 and 3.13.

Check the install:

```bash
ter --version
python -m ter --help
ter-req lint --tests tests
python -m pytest tests/golden tests/contract tests/architecture -q
```

On a machine that cannot reach the tiktoken and Hugging Face download hosts,
about 39 TER 3 tests fail with download errors (see the
[testing guide](testing.md#tests-that-need-the-network)). Everything else,
including all golden tests, runs offline.

## The rules the code follows

| Rule | Enforced by |
|---|---|
| New behaviour goes in `src/ter` (TER 4). TER 3 changes only to delegate inward | review; the strangler plan in the [architecture guide](architecture.md#strangler-fig-over-ter-3) |
| Dependencies point inward: bootstrap → adapters → application → ports → domain | `lint-imports`, `tests/architecture` |
| `ter.domain` imports no TER 3, vendor SDK or IO module | `lint-imports` (`pure-domain`) |
| TER scoring arithmetic lives in `ter.domain.scoring` | golden snapshots |
| Model prices are data in `src/ter/data/price_book.json`, never literals | review, `tests/contract/test_price_book.py` ([ADR 0003](../decisions/0003-price-book-as-data.md)) |
| Every `ter` module is strictly typed | `mypy src/` with the overrides in `pyproject.toml` |
| Every behaviour is an EARS requirement cited by a passing test | `ter-req lint`, `ter-req trace` ([EARS guide](ears.md)) |
| Scoring changes appear as a golden snapshot diff, committed on purpose | `tests/golden` ([testing guide](testing.md#changing-a-snapshot-on-purpose)) |
| Every change names the vision points it advances and updates them | review, `ter-req lint`, `ter-req points --check` ([definition of done](definition-of-done.md)) |
| Real-data points are never done on synthetic tests | `ter-req lint` (`POINT-REAL-DATA`) |
| Documented commands exist and links resolve | `tests/docs` |

Code style: Python 3.11+, dataclasses (frozen for values) for models, enums
for domain constants, `typing.Protocol` for ports, lazy imports in CLI
handlers and in the composition root so `ter` and the hook start quickly.
Ruff formats and lints; do not fight the formatter.

### Strict typing, in practice

`pyproject.toml` applies these to `ter` and `ter.*`: `disallow_untyped_defs`,
`disallow_incomplete_defs`, `disallow_untyped_calls`,
`disallow_any_generics`, `disallow_untyped_decorators`,
`check_untyped_defs`, `no_implicit_reexport`, `strict_equality`,
`warn_unused_ignores`. So:

- annotate every parameter and return, including `-> None`;
- parametrise generics (`dict[str, int]`, not `dict`);
- export names explicitly with `__all__` in package `__init__.py` files;
- do not leave a `# type: ignore` that is no longer needed.

## Workflow for a change

1. **Branch** from the integration branch you are targeting:
   `feature/…`, `fix/…`, `docs/…`, `refactor/…` or `test/…`.
2. **Find the points** the change serves in
   [docs/ter4/points.md](../ter4/points.md).
3. **Write or update the requirements** (`status: planned`) and link them to
   the points both ways.
4. **Write the tests** tagged with `@pytest.mark.req(...)`: unit and property
   tests for logic, a contract test for any new adapter, a golden session if
   the behaviour shows in reports.
5. **Implement** in the right layer.
6. **Promote** the requirements to `verified`; update the points' `status`
   and `verification`; run `ter-req points`.
7. **Update the docs**: the relevant `docs/ter4` page, a guide if usage
   changed, an ADR if a design decision changed.
8. **Run the gates** below.
9. **Commit and open the PR** with the ids named.

## Gates to run before pushing

These are the CI jobs, run locally:

```bash
ruff format --check src tests
ruff check src tests
mypy src/
lint-imports
ter-req lint --strict --tests tests
ter-req points --check
python -m pytest tests/golden tests/contract tests/architecture -q
python -m pytest --cov=ter_calculator --cov=ter --cov-branch --req-trace=req-trace.json
ter-req trace --results req-trace.json --gate L0
ter-req trace --results req-trace.json --gate L1
ter-req trace --results req-trace.json --gate L2
```

If a formatter check fails, run `ruff format src tests` and review the diff.
If `ter-req points --check` fails, run `ter-req points` and commit the
regenerated `docs/ter4/points.md`.

## Commits and pull requests

A TER 4 commit message says what changed and why, then names:

- the **vision point ids** it advances, and how their status changed
  (`P020 done`, `P035 stays partial: issue #40`);
- the **requirement ids** it adds, verifies or changes;
- any **golden snapshot diff** and why the numbers moved, or that snapshots
  are unchanged.

```text
Add deleted-work detector (overproduction)

- regeneration's sibling: edits to a file later deleted in the same prompt
  are overproduction; confidence 0.80, structural rule published.
- New golden session deleted_work; lean_mix and rework_loop snapshots
  unchanged.

Requirements: TER-DET-011 verified.
Points: P020 verification adds TestDeletedWork (stays done).
```

The PR body repeats the point and requirement ids, lists the gates you ran,
and calls out any snapshot diff for the reviewer. Keep one feature or fix per
PR.

## Documentation changes

- Guides live in `docs/guides/`; reference pages in `docs/ter4/`; decisions
  in `docs/decisions/` (`NNNN-title.md`, with status, date, context,
  decision, consequences).
- Put commands in `bash` blocks and output in `text` blocks:
  `tests/docs` checks every `ter`, `ter-req` and `python -m ter` command in
  bash blocks against the real CLI, and every relative link and anchor.
- Link to reference pages instead of copying their tables.
- `docs/ter4/points.md` is generated: edit `requirements/points.yaml` and run
  `ter-req points`.

## Reporting bugs and requesting features

Open a [GitHub issue](https://github.com/lgriffin/TER/issues) with steps to
reproduce, expected and actual behaviour, Python version and OS, and a
redacted session file if one is involved. Work that needs real session data
belongs under the tracker issue #47.

By contributing you agree your contributions are licensed under the
[Apache License 2.0](../../LICENSE).
