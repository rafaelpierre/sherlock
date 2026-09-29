from __future__ import annotations

from sherlock_evals.oracles import ComparisonPolicy, RankKey, compare_result_sets


def _policy(**overrides) -> ComparisonPolicy:
    values = {
        "row_order": "sensitive",
        "column_order": "sensitive",
        "absolute_tolerance": 0.0,
        "relative_tolerance": 0.0,
        "null_equivalents": (),
    }
    values.update(overrides)
    return ComparisonPolicy(**values)


def test_comparison_normalizes_columns_and_reorders_values() -> None:
    result = compare_result_sets(
        ["merchant", "fraud_rate"],
        [["A", 0.25]],
        [" FRAUD_RATE ", "MERCHANT"],
        [[0.25001, "A"]],
        _policy(
            column_order="insensitive",
            absolute_tolerance=0.001,
        ),
    )

    assert result.matches


def test_order_insensitive_comparison_preserves_duplicate_rows() -> None:
    result = compare_result_sets(
        ["value"],
        [[1.0], [1.0], [2.0]],
        ["value"],
        [[2.0], [1.0004], [0.9996]],
        _policy(row_order="insensitive", absolute_tolerance=0.001),
    )

    assert result.matches


def test_ranked_comparison_allows_ties_in_either_order() -> None:
    rank_by = (RankKey(column="count", direction="descending"),)
    result = compare_result_sets(
        ["hour", "count"],
        [[12, 10], [13, 10], [14, 5]],
        ["count", "hour"],
        [[10, 13], [10, 12], [5, 14]],
        _policy(
            row_order="ranked",
            column_order="insensitive",
            rank_by=rank_by,
        ),
    )
    wrong_order = compare_result_sets(
        ["hour", "count"],
        [[12, 10], [13, 10], [14, 5]],
        ["hour", "count"],
        [[14, 5], [12, 10], [13, 10]],
        _policy(row_order="ranked", rank_by=rank_by),
    )

    assert result.matches
    assert not wrong_order.matches


def test_null_equivalents_are_policy_controlled() -> None:
    result = compare_result_sets(
        ["value"],
        [[None]],
        ["value"],
        [[" N/A "]],
        _policy(null_equivalents=("n/a",)),
    )

    assert result.matches


def test_relative_numeric_tolerance_is_supported() -> None:
    result = compare_result_sets(
        ["value"],
        [[1_000_000]],
        ["value"],
        [[1_000_001.0]],
        _policy(relative_tolerance=0.00001),
    )

    assert result.matches


def test_bool_does_not_compare_as_number() -> None:
    result = compare_result_sets(["value"], [[1]], ["value"], [[True]], _policy())

    assert not result.matches
    assert result.message == "row values or ordering differ"


def test_wrong_columns_rows_order_and_width_report_mismatches() -> None:
    wrong_columns = compare_result_sets(
        ["expected"], [[1]], ["actual"], [[1]], _policy()
    )
    wrong_count = compare_result_sets(
        ["value"], [[1]], ["value"], [[1], [2]], _policy()
    )
    wrong_order = compare_result_sets(
        ["value"], [[1], [2]], ["value"], [[2], [1]], _policy()
    )
    wrong_width = compare_result_sets(["value"], [[1]], ["value"], [[1, 2]], _policy())
    short_reordered_row = compare_result_sets(
        ["first", "second"],
        [[1, 2]],
        ["second", "first"],
        [[2]],
        _policy(column_order="insensitive"),
    )

    assert not wrong_columns.matches and "columns differ" in wrong_columns.message
    assert not wrong_count.matches and "row count differs" in wrong_count.message
    assert not wrong_order.matches and "ordering differ" in wrong_order.message
    assert not wrong_width.matches and "row width" in wrong_width.message
    assert (
        not short_reordered_row.matches and "row width" in short_reordered_row.message
    )


def test_duplicate_normalized_columns_are_rejected() -> None:
    oracle_duplicate = compare_result_sets(
        ["value", " VALUE "], [[1, 2]], ["a", "b"], [[1, 2]], _policy()
    )
    result_duplicate = compare_result_sets(
        ["a", "b"], [[1, 2]], ["value", " VALUE "], [[1, 2]], _policy()
    )

    assert oracle_duplicate.message == "oracle contains duplicate normalized columns"
    assert result_duplicate.message == "result contains duplicate normalized columns"
