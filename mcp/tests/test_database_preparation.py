from __future__ import annotations

import sqlite3
from pathlib import Path

from db.prepare_database import EXPECTED_COLUMNS, prepare_database


def test_preparation_is_idempotent_and_preserves_grain(database_path: Path) -> None:
    report = prepare_database(database_path)

    assert report == {
        "total_transactions": 3,
        "fraud": 1,
        "non_fraud": 1,
        "unlabelled": 1,
        "negative_card_age_rows": 0,
        "negative_user_age_rows": 0,
    }

    connection = sqlite3.connect(database_path)
    columns = [
        row[1] for row in connection.execute("PRAGMA table_info(fraud_transactions)")
    ]
    row = connection.execute(
        """
        SELECT amount_usd, transaction_hour, transaction_day_of_week,
               amount_to_credit_limit_ratio, is_fraud
        FROM fraud_transactions WHERE transaction_id = 1
        """
    ).fetchone()
    connection.close()

    assert columns == EXPECTED_COLUMNS
    assert row == (123.45, 1, 2, 0.12345, 1)


def test_missing_label_remains_null(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    value = connection.execute(
        "SELECT is_fraud FROM fraud_transactions WHERE transaction_id = 3"
    ).fetchone()[0]
    connection.close()
    assert value is None
