"""Deterministic result-set normalization and comparison policies."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal

RowOrder = Literal["sensitive", "insensitive", "ranked"]
ColumnOrder = Literal["sensitive", "insensitive"]


@dataclass(frozen=True)
class RankKey:
    """One expected output column and direction used to validate ranking."""

    column: str
    direction: Literal["ascending", "descending"]


@dataclass(frozen=True)
class ComparisonPolicy:
    """Explicit semantics for comparing one expected result set."""

    row_order: RowOrder
    column_order: ColumnOrder
    absolute_tolerance: float
    relative_tolerance: float
    null_equivalents: tuple[str, ...]
    rank_by: tuple[RankKey, ...] = ()


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


def _compare_rank_values(
    left: Any,
    right: Any,
    direction: Literal["ascending", "descending"],
    policy: ComparisonPolicy,
) -> int | None:
    """Return ordering for comparable rank values, treating close numbers as tied."""

    left = _normalize_scalar(left, policy)
    right = _normalize_scalar(right, policy)
    if _scalars_match(left, right, policy):
        return 0
    if isinstance(left, bool) or isinstance(right, bool):
        return None
    both_numbers = isinstance(left, (int, float)) and isinstance(right, (int, float))
    both_strings = isinstance(left, str) and isinstance(right, str)
    if both_numbers or both_strings:
        comparison = -1 if left < right else 1
    else:
        return None
    return comparison if direction == "ascending" else -comparison


def _rows_are_ranked(
    rows: list[list[Any]],
    columns: list[str],
    policy: ComparisonPolicy,
) -> bool:
    indexes = [columns.index(normalize_column(key.column)) for key in policy.rank_by]
    for left, right in pairwise(rows):
        for index, key in zip(indexes, policy.rank_by, strict=True):
            comparison = _compare_rank_values(
                left[index], right[index], key.direction, policy
            )
            if comparison is None:
                return False
            if comparison < 0:
                break
            if comparison > 0:
                return False
    return True


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

    expected_width = len(expected_columns)
    if any(len(row) != expected_width for row in expected_rows):
        return ComparisonResult(False, "oracle row width does not match its columns")
    if any(len(row) != len(actual_columns) for row in actual_rows):
        return ComparisonResult(False, "result row width does not match its columns")

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
    elif policy.row_order == "insensitive":
        rows_match = _unordered_rows_match(expected_rows, reordered_actual_rows, policy)
    else:
        normalized_rank_columns = {
            normalize_column(key.column) for key in policy.rank_by
        }
        if not policy.rank_by or not normalized_rank_columns.issubset(
            normalized_expected
        ):
            return ComparisonResult(False, "oracle has invalid ranked columns")
        rows_match = _unordered_rows_match(
            expected_rows, reordered_actual_rows, policy
        ) and _rows_are_ranked(reordered_actual_rows, normalized_expected, policy)
    if not rows_match:
        return ComparisonResult(False, "row values or ordering differ")
    return ComparisonResult(True)
