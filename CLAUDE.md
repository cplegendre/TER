# TER Development Guidelines

Last updated: 2026-05-15

## Active Technologies

- Python 3.11+ + sentence-transformers (embeddings), numpy (similarity computation), rich (terminal formatting), sqlite3 (fragment storage)

## Project Structure

```text
src/ter/               # TER 4 hexagon: domain/ ports/ application/ adapters/ bootstrap/
src/ter_calculator/    # TER 3 modules (wrapped by ter adapters during the rebuild)
tests/unit/            # Unit tests
tests/features/        # BDD feature files and step definitions
tests/integration/     # Integration tests
tests/golden/          # Golden snapshots freezing TER 3 scores (TER_UPDATE_GOLDEN=1 to regenerate)
tests/contract/        # One suite per port; real adapters and fakes must both pass
tests/architecture/    # Import-contract fitness tests
tests/requirements/    # EARS catalogue lint, trace and ter-req tooling tests
requirements/          # EARS requirement catalogue (YAML per maturity level) + points.yaml (200 vision points)
docs/                  # Architecture, user guide, context orchestrator reference
sample_sessions/       # Sample JSONL files for testing
```

## Commands

```bash
pytest                                    # Run all tests
pytest tests/unit/test_fragment_store.py  # Run specific module tests
ruff check src/                           # Lint
lint-imports                              # Hexagon dependency rules
pytest tests/golden tests/contract tests/architecture  # L0 gate
ter-req lint --tests tests                # EARS grammar + every req marker cites a known id
pytest --req-trace=req-trace.json && ter-req trace --results req-trace.json --gate L0  # traceability gate
ter-req report --results req-trace.json   # Markdown coverage per level
ter-req points                            # regenerate docs/ter4/points.md (CI runs --check)
```

## Code Style

Python 3.11+: Follow standard conventions. Dataclasses for models, enums for domain constants, lazy imports in CLI handlers.

## TER 4 rules

- Dependencies point inward: bootstrap → adapters → application → ports → domain. `ter.domain` never imports `ter_calculator`, vendor SDKs or IO modules.
- TER scoring arithmetic lives in `ter.domain.scoring`; `ter_calculator.compute` delegates to it.
- Model prices are data in `src/ter/data/price_book.json`, read through the `PriceBook` port (ADR 0003). Never hard-code rates.
- Scoring changes must show up as a golden snapshot diff, committed on purpose.
- Tag tests with `@pytest.mark.req("TER-XXX-NNN")` for the requirement they verify. Every behaviour is an EARS requirement in `requirements/*.yaml` (see `docs/ter4/requirements.md`); new ones start `status: planned` and become `verified` once a passing test cites them.
- Every TER 4 change names the vision point ids it advances (`P044`, …) in its commit message and PR body, and updates those points' `status` and `verification` in `requirements/points.yaml` in the same PR (then `ter-req points`). A point is `done` only when its rules are verified by tests. Index: `docs/ter4/points.md`.
- New `ter` code is strictly typed (mypy overrides in pyproject.toml).
- See `docs/ter4/architecture.md` and `docs/decisions/`.

## Key Modules

### Core Pipeline
`models.py` `loader.py` `intent.py` `classifier.py` `compute.py` `waste.py` `economics.py` `formatter.py` `cli.py` `analyze_pipeline.py`

### Context Orchestrator
`fragment_store.py` `context_graph.py` `budget_optimizer.py` `delta_composer.py` `consistency.py`

### Real-Time & Adaptive
`real_time.py` `adaptive_budget.py` `cost_model.py` `overthinking.py`

## CLI Subcommands

`ter analyze` `ter report` `ter compare` `ter list` `ter watch` `ter budget` `ter context {store|graph|optimize|delta|check}`
