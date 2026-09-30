# Contributing to TER Calculator

Thanks for your interest in contributing! This page is the short version.
For TER 4 work, read the [contributing guide](docs/guides/contributing.md):
it covers the hexagon's rules, the gates to run before pushing, and the
commit and PR conventions (vision point ids, requirement ids, golden
snapshot diffs on purpose, strict typing for `ter` code). The other
[guides](docs/guides/README.md) cover testing, EARS requirements, the
definition of done, Lean, hooks and the A3.

## Getting Started

```bash
# Fork and clone the repo
git clone https://github.com/<your-username>/TER.git
cd TER

# Install in development mode
python -m pip install -c constraints/dev.txt -e ".[dev,embeddings]"

# Install pre-commit hooks
pip install pre-commit
pre-commit install
```

Supported Python versions: 3.11, 3.12, and 3.13.

## Development Workflow

### Running Tests

```bash
python -m pytest                                    # All tests
python -m pytest tests/unit/test_classifier.py -v   # Specific module
python -m pytest --cov=ter_calculator --cov-branch  # With coverage
```

### Linting and Type Checking

```bash
ruff check src/                           # Lint
ruff format src/ tests/                   # Format
mypy src/                                 # Type check
```

Pre-commit hooks run ruff automatically on staged files.

### Branch Naming

- `feature/<description>` -- new functionality
- `fix/<description>` -- bug fixes
- `docs/<description>` -- documentation changes
- `refactor/<description>` -- code restructuring
- `test/<description>` -- test additions or fixes

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add rolling window size option to watch command
fix: correct token count for merged reasoning spans
docs: add context orchestrator usage examples
test: add unit tests for waste_detectors module
refactor: extract shared CLI argument definitions
```

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes with tests
3. Ensure all checks pass (the full list is in the
   [contributing guide](docs/guides/contributing.md#gates-to-run-before-pushing))
4. Open a PR against `main` with a clear description
5. One approval required for merge

### PR Guidelines

- One feature or fix per PR
- Include tests for new functionality
- Update documentation if behavior changes
- Keep PRs focused -- separate unrelated changes into different PRs

## Code Style

- Python 3.11+ -- use modern syntax (type unions with `|`, match statements where appropriate)
- Dataclasses for models (see `models.py`)
- Lazy imports in CLI handlers for fast startup
- Ruff handles formatting and linting -- don't fight the formatter

## Project Structure

```
src/ter/               # TER 4 hexagon: domain/ ports/ application/ adapters/ bootstrap/
src/ter_calculator/    # TER 3 modules
requirements/          # EARS requirements (one file per level) and points.yaml
tests/unit/            # Unit tests
tests/features/        # BDD feature files
tests/integration/     # Integration tests
tests/golden/          # Golden snapshots (TER_UPDATE_GOLDEN=1 to regenerate)
tests/contract/        # One suite per port
tests/architecture/    # Import-contract fitness tests
tests/docs/            # Link, command-example and points-index checks
docs/                  # Architecture and user documentation
sample_sessions/       # Sample JSONL files for testing
```

## Reporting Bugs

Open a [GitHub Issue](https://github.com/lgriffin/TER/issues) with:

- Steps to reproduce
- Expected vs actual behavior
- Python version and OS
- Sample session file (if applicable, redact sensitive content)

## Requesting Features

Open a [GitHub Issue](https://github.com/lgriffin/TER/issues) with the `enhancement` label describing:

- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
