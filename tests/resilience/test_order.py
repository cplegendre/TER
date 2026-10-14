"""Tests run in a random order (TER-TST-012).

The root ``conftest.py`` shuffles modules, then the classes in a module,
then the tests in a class, from a seed the run header names. These tests run
pytest on a small suite with that ``conftest.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT_CONFTEST = Path(__file__).resolve().parents[2] / "conftest.py"
PASSED = re.compile(r"^(\S+::\S+) PASSED", re.MULTILINE)


@pytest.fixture
def suite(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest(ROOT_CONFTEST.read_text(encoding="utf-8"))
    body = "\n".join(f"def test_{n}(): pass" for n in range(6))
    klass = "class TestGroup:\n" + "\n".join(
        f"    def test_{n}(self): pass" for n in range(4)
    )
    for name in ("alpha", "beta", "gamma", "delta"):
        pytester.makepyfile(**{f"test_{name}": f"{body}\n\n{klass}\n"})
    return pytester


def _order(suite: pytest.Pytester, *args: str) -> tuple[list[str], str]:
    result = suite.runpytest("-v", "-p", "no:cacheprovider", *args)
    result.assert_outcomes(passed=40)
    text = result.stdout.str()
    return PASSED.findall(text), text


def _grouped(order: list[str], depth: int) -> bool:
    """True when the tests sharing their first ``depth`` id parts run
    together."""
    keys = ["::".join(t.split("::")[:depth]) for t in order]
    runs = [k for i, k in enumerate(keys) if i == 0 or keys[i - 1] != k]
    return len(runs) == len(set(runs))


@pytest.mark.req("TER-TST-012")
def test_the_order_is_random_by_seed_and_named_in_the_header(
    suite: pytest.Pytester,
) -> None:
    first, header = _order(suite, "--test-order-seed=1")
    again, _ = _order(suite, "--test-order-seed=1")
    other, _ = _order(suite, "--test-order-seed=2")
    declared, _ = _order(suite, "--test-order=declared")
    assert "test order: random, seed 1 (rerun with --test-order-seed=1)" in header
    assert first == again, "the same seed must give the same order"
    assert first != other and first != declared
    assert sorted(first) == sorted(declared)


@pytest.mark.req("TER-TST-012")
def test_modules_and_classes_stay_together(suite: pytest.Pytester) -> None:
    for seed in range(5):
        order, _ = _order(suite, f"--test-order-seed={seed}")
        assert _grouped(order, 1), "a module's tests must run together"
        assert _grouped(order, 2), "a class's tests must run together"


@pytest.mark.req("TER-TST-012")
def test_the_seed_can_come_from_the_environment(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TER_TEST_ORDER_SEED", "7")
    from_env, header = _order(suite)
    assert "seed 7" in header
    assert from_env == _order(suite, "--test-order-seed=7")[0]


@pytest.mark.req("TER-TST-012")
def test_failed_first_runs_the_last_failures_first(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(ROOT_CONFTEST.read_text(encoding="utf-8"))
    body = "\n".join(f"def test_{n}(): pass" for n in range(8))
    pytester.makepyfile(
        test_a=body, test_z="import os\n\ndef test_last(): assert os.environ.get('OK')"
    )
    pytester.runpytest("--test-order-seed=1").assert_outcomes(passed=8, failed=1)
    result = pytester.runpytest("-v", "--ff", "--test-order-seed=1")
    result.assert_outcomes(passed=8, failed=1)
    text = result.stdout.str()
    assert "test order: as --ff orders it (not shuffled)" in text
    ran = re.findall(r"^(\S+::\S+) (?:PASSED|FAILED)", text, re.MULTILINE)
    assert ran[0] == "test_z.py::test_last"
