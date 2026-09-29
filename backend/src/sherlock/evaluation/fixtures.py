"""Loading and selection for versioned evaluation suite fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from sherlock.evaluation.models import EvaluationSuite


class FixtureError(ValueError):
    """Evaluation fixtures are missing, malformed, or ambiguous."""


def load_suites(directory: Path) -> dict[str, EvaluationSuite]:
    """Load every JSON suite in a directory with contextual diagnostics."""

    if not directory.is_dir():
        raise FixtureError(f"Fixture directory does not exist: {directory}")
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise FixtureError(f"No JSON suite fixtures found in: {directory}")

    suites: dict[str, EvaluationSuite] = {}
    for path in paths:
        suite = _load_suite(path)
        if suite.name in suites:
            raise FixtureError(f"Duplicate suite name: {suite.name}")
        suites[suite.name] = suite
    return suites


def select_suites(
    available: dict[str, EvaluationSuite], selected: list[str] | None
) -> list[EvaluationSuite]:
    """Return all suites or the requested subset in explicit request order."""

    if not selected:
        return list(available.values())
    unknown = [name for name in selected if name not in available]
    if unknown:
        names = ", ".join(sorted(set(unknown)))
        raise FixtureError(f"Unknown suite(s): {names}")
    if len(selected) != len(set(selected)):
        raise FixtureError("A suite cannot be selected more than once")
    return [available[name] for name in selected]


def _load_suite(path: Path) -> EvaluationSuite:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EvaluationSuite.model_validate(payload)
    except OSError as exc:
        raise FixtureError(f"Could not read fixture {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise FixtureError(
            f"Malformed JSON in {path} at line {exc.lineno}, column {exc.colno}"
        ) from exc
    except ValidationError as exc:
        raise FixtureError(f"Invalid fixture {path}: {exc}") from exc
