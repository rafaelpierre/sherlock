#!/usr/bin/env python3
"""Create and validate the canonical fraud analytics view."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

DB_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = DB_ROOT / "data" / "data.db"
VIEW_SQL = DB_ROOT / "views" / "fraud_transactions.sql"

EXPECTED_COLUMNS = [
    "transaction_id",
    "transaction_datetime",
    "transaction_date",
    "transaction_hour",
    "transaction_day_of_week",
    "amount_usd_cents",
    "amount_usd",
    "transaction_type",
    "errors",
    "card_id",
    "card_brand",
    "card_type",
    "has_chip",
    "credit_limit_usd_cents",
    "credit_limit_usd",
    "acct_open_date",
    "card_age_days",
    "year_pin_last_changed",
    "card_on_dark_web",
    "user_id",
    "birth_date",
    "user_age_years",
    "gender",
    "per_capita_income_usd_cents",
    "per_capita_income_usd",
    "yearly_income_usd_cents",
    "yearly_income_usd",
    "total_debt_usd_cents",
    "total_debt_usd",
    "credit_score",
    "amount_to_credit_limit_ratio",
    "merchant_id",
    "merchant_name",
    "mcc",
    "merchant_category",
    "merchant_location_id",
    "merchant_city",
    "merchant_state",
    "merchant_zip",
    "is_fraud",
]


class PreparationError(RuntimeError):
    """The prepared database does not satisfy its analytical contract."""


def scalar(connection: sqlite3.Connection, sql: str) -> Any:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise PreparationError("validation query unexpectedly returned no rows")
    return row[0]


def validate_database(connection: sqlite3.Connection) -> dict[str, int]:
    view_exists = scalar(
        connection,
        """
        SELECT COUNT(*) FROM sqlite_schema
        WHERE type = 'view' AND name = 'fraud_transactions'
        """,
    )
    if view_exists != 1:
        raise PreparationError("fraud_transactions view was not created")

    actual_columns = [
        row[1] for row in connection.execute("PRAGMA table_info(fraud_transactions)")
    ]
    if actual_columns != EXPECTED_COLUMNS:
        missing = sorted(set(EXPECTED_COLUMNS) - set(actual_columns))
        extra = sorted(set(actual_columns) - set(EXPECTED_COLUMNS))
        raise PreparationError(
            f"canonical columns differ; missing={missing}, extra={extra}"
        )

    transaction_count = scalar(connection, "SELECT COUNT(*) FROM transactions")
    view_count = scalar(connection, "SELECT COUNT(*) FROM fraud_transactions")
    if view_count != transaction_count:
        raise PreparationError(
            f"view grain changed: transactions={transaction_count}, view={view_count}"
        )

    distinct_ids = scalar(
        connection,
        "SELECT COUNT(DISTINCT transaction_id) FROM fraud_transactions",
    )
    if distinct_ids != transaction_count:
        raise PreparationError(
            f"transaction IDs are not unique: distinct={distinct_ids}, "
            f"transactions={transaction_count}"
        )

    invalid_conversions = scalar(
        connection,
        """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM fraud_transactions
            WHERE ABS(amount_usd - amount_usd_cents / 100.0) > 0.0000001
               OR ABS(credit_limit_usd - credit_limit_usd_cents / 100.0)
                    > 0.0000001
               OR ABS(per_capita_income_usd -
                      per_capita_income_usd_cents / 100.0) > 0.0000001
               OR ABS(yearly_income_usd - yearly_income_usd_cents / 100.0)
                    > 0.0000001
               OR ABS(total_debt_usd - total_debt_usd_cents / 100.0)
                    > 0.0000001
            LIMIT 1
        )
        """,
    )
    if invalid_conversions:
        raise PreparationError("one or more USD conversions are invalid")

    invalid_derived_values = scalar(
        connection,
        """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM fraud_transactions
            WHERE transaction_date IS NULL
               OR transaction_hour NOT BETWEEN 0 AND 23
               OR transaction_day_of_week NOT BETWEEN 0 AND 6
               OR (
                    credit_limit_usd_cents > 0
                    AND ABS(
                        amount_to_credit_limit_ratio -
                        CAST(amount_usd_cents AS REAL) /
                            credit_limit_usd_cents
                    ) > 0.0000001
               )
            LIMIT 1
        )
        """,
    )
    if invalid_derived_values:
        raise PreparationError("one or more normalized or derived values are invalid")

    # These are reported as source-data quality signals rather than preparation
    # failures. The canonical view must preserve legitimate source anomalies.
    negative_card_age_count = scalar(
        connection,
        "SELECT COUNT(*) FROM fraud_transactions WHERE card_age_days < 0",
    )
    negative_user_age_count = scalar(
        connection,
        "SELECT COUNT(*) FROM fraud_transactions WHERE user_age_years < 0",
    )

    counts_row = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(is_fraud = 1) AS fraud,
            SUM(is_fraud = 0) AS non_fraud,
            SUM(is_fraud IS NULL) AS unlabelled
        FROM fraud_transactions
        """
    ).fetchone()
    if counts_row is None:
        raise PreparationError("could not calculate fraud-label counts")

    return {
        "total_transactions": int(counts_row[0]),
        "fraud": int(counts_row[1] or 0),
        "non_fraud": int(counts_row[2] or 0),
        "unlabelled": int(counts_row[3] or 0),
        "negative_card_age_rows": int(negative_card_age_count),
        "negative_user_age_rows": int(negative_user_age_count),
    }


def prepare_database(database_path: Path) -> dict[str, int]:
    path = database_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")

    view_sql = VIEW_SQL.read_text(encoding="utf-8")
    connection = sqlite3.connect(path)
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + view_sql)
        report = validate_database(connection)
        connection.commit()
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and validate the fraud_transactions view."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite database path (default: {DEFAULT_DATABASE})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = prepare_database(args.database)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
