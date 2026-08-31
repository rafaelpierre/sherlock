"""Deterministic result-set normalization and comparison policies."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

RowOrder = Literal["sensitive", "insensitive"]
ColumnOrder = Literal["sensitive", "insensitive"]


@dataclass(frozen=True)
class ComparisonPolicy:
    """Explicit semantics for comparing one expected result set."""

    row_order: RowOrder
    column_order: ColumnOrder
    absolute_tolerance: float
    relative_tolerance: float
    null_equivalents: tuple[str, ...]


@dataclass(frozen=True)
class ComparisonResult:
    """The deterministic outcome of one result-set comparison."""

    matches: bool
    message: str | None = None


def normalize_column(value: str) -> str:
    """Normalize harmless identifier formatting without hiding wrong columns."""

    return " ".join(value.strip().casefold().split())


def _normalize_scalar(value: Any, policy: ComparisonPolicy) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.casefold() in policy.null_equivalents:
            return None
        return stripped
    return value


def _numbers_match(expected: Any, actual: Any, policy: ComparisonPolicy) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if not isinstance(expected, (int, float)) or not isinstance(actual, (int, float)):
        return False
    expected_number = float(expected)
    actual_number = float(actual)
    return (
        math.isfinite(expected_number)
        and math.isfinite(actual_number)
        and math.isclose(
            expected_number,
            actual_number,
            rel_tol=policy.relative_tolerance,
            abs_tol=policy.absolute_tolerance,
        )
    )


def _scalars_match(expected: Any, actual: Any, policy: ComparisonPolicy) -> bool:
    expected = _normalize_scalar(expected, policy)
    actual = _normalize_scalar(actual, policy)
    if expected is None or actual is None:
        return expected is actual
    if _numbers_match(expected, actual, policy):
        return True
    return type(expected) is type(actual) and expected == actual


def _rows_match(
    expected: list[Any], actual: list[Any], policy: ComparisonPolicy
) -> bool:
    return len(expected) == len(actual) and all(
        _scalars_match(expected_value, actual_value, policy)
        for expected_value, actual_value in zip(expected, actual, strict=True)
    )


def _unordered_rows_match(
    expected_rows: list[list[Any]],
    actual_rows: list[list[Any]],
    policy: ComparisonPolicy,
) -> bool:
    """Compare row multisets using deterministic maximum bipartite matching."""

    candidates = [
        [
            actual_index
            for actual_index, actual in enumerate(actual_rows)
            if _rows_match(expected, actual, policy)
        ]
        for expected in expected_rows
    ]
    matched_expected_by_actual: dict[int, int] = {}

    def assign(expected_index: int, visited: set[int]) -> bool:
        for actual_index in candidates[expected_index]:
            if actual_index in visited:
                continue
            visited.add(actual_index)
            previous = matched_expected_by_actual.get(actual_index)
            if previous is None or assign(previous, visited):
                matched_expected_by_actual[actual_index] = expected_index
                return True
        return False

    return all(assign(index, set()) for index in range(len(expected_rows)))


def compare_result_sets(
    expected_columns: list[str],
    expected_rows: list[list[Any]],
    actual_columns: list[str],
    actual_rows: list[list[Any]],
    policy: ComparisonPolicy,
) -> ComparisonResult:
    """Compare columns and rows after policy-controlled normalization."""

    normalized_expected = [normalize_column(column) for column in expected_columns]
    normalized_actual = [normalize_column(column) for column in actual_columns]
    if len(set(normalized_expected)) != len(normalized_expected):
        return ComparisonResult(False, "oracle contains duplicate normalized columns")
    if len(set(normalized_actual)) != len(normalized_actual):
        return ComparisonResult(False, "result contains duplicate normalized columns")

    reordered_actual_rows = actual_rows
    if policy.column_order == "sensitive":
        if normalized_expected != normalized_actual:
            return ComparisonResult(
                False,
                f"columns differ: expected {expected_columns!r}, got {actual_columns!r}",
            )
    else:
        if set(normalized_expected) != set(normalized_actual):
            return ComparisonResult(
                False,
                f"columns differ: expected {expected_columns!r}, got {actual_columns!r}",
            )
        indexes = [normalized_actual.index(column) for column in normalized_expected]
        reordered_actual_rows = [
            [row[index] for index in indexes] for row in actual_rows
        ]

    expected_width = len(expected_columns)
    if any(len(row) != expected_width for row in expected_rows):
        return ComparisonResult(False, "oracle row width does not match its columns")
    if any(len(row) != len(actual_columns) for row in actual_rows):
        return ComparisonResult(False, "result row width does not match its columns")
    if len(expected_rows) != len(actual_rows):
        return ComparisonResult(
            False,
            f"row count differs: expected {len(expected_rows)}, got {len(actual_rows)}",
        )

    if policy.row_order == "sensitive":
        rows_match = all(
            _rows_match(expected, actual, policy)
            for expected, actual in zip(
                expected_rows, reordered_actual_rows, strict=True
            )
        )
    else:
        rows_match = _unordered_rows_match(expected_rows, reordered_actual_rows, policy)
    if not rows_match:
        return ComparisonResult(False, "row values or ordering differ")
    return ComparisonResult(True)
