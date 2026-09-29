from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.prepare_database import prepare_database
from fraud_mcp.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "fraud.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            birth_date DATE,
            gender TEXT,
            address TEXT,
            latitude REAL,
            longitude REAL,
            per_capita_income_usd_cents INTEGER,
            yearly_income_usd_cents INTEGER,
            total_debt_usd_cents INTEGER,
            credit_score INTEGER
        );
        CREATE TABLE mcc_codes (mcc INTEGER PRIMARY KEY, description TEXT);
        CREATE TABLE merchants (
            id INTEGER PRIMARY KEY,
            name TEXT,
            mcc INTEGER REFERENCES mcc_codes(mcc)
        );
        CREATE TABLE merchant_locations (
            id INTEGER PRIMARY KEY,
            merchant_id INTEGER REFERENCES merchants(id),
            city TEXT,
            state TEXT,
            zip INTEGER
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            card_brand TEXT,
            card_type TEXT,
            expires DATE,
            has_chip BOOLEAN,
            credit_limit_usd_cents INTEGER,
            acct_open_date DATE,
            year_pin_last_changed INTEGER,
            card_on_dark_web BOOLEAN
        );
        CREATE TABLE fraud_labels (
            transaction_id TEXT PRIMARY KEY,
            is_fraud BOOLEAN
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            date DATETIME,
            card_id INTEGER REFERENCES cards(id),
            amount_usd_cents INTEGER,
            transaction_type TEXT,
            merchant_id INTEGER REFERENCES merchants(id),
            merchant_location_id INTEGER REFERENCES merchant_locations(id),
            errors TEXT
        );

        INSERT INTO users VALUES
            (1, '1980-06-15', 'Female', '1 Main St', 0, 0,
             3000000, 6000000, 1000000, 720),
            (2, '1995-01-01', 'Male', '2 Main St', 0, 0,
             2000000, 4000000, 500000, 650);
        INSERT INTO mcc_codes VALUES (5411, 'Grocery Stores');
        INSERT INTO merchants VALUES (10, 'Local Grocer', 5411);
        INSERT INTO merchant_locations VALUES
            (100, 10, 'London', 'London', 10001);
        INSERT INTO cards VALUES
            (20, 1, 'Visa', 'Credit', '2025-01-01', 1, 100000,
             '2018-01-01', 2018, 0),
            (21, 2, 'Mastercard', 'Debit', '2025-01-01', 1, 50000,
             '2018-06-01', 2019, 1);
        INSERT INTO transactions VALUES
            (1, '2019-01-01 01:30:00', 20, 12345, 'Online Transaction',
             10, 100, NULL),
            (2, '2019-01-02 13:15:00', 21, 5000, 'Chip Transaction',
             10, 100, 'Bad PIN'),
            (3, '2019-01-03 23:45:00', 20, 1000, 'Swipe Transaction',
             10, 100, NULL);
        INSERT INTO fraud_labels VALUES ('1', 1), ('2', 0);
        """
    )
    connection.close()
    prepare_database(path)
    return path


@pytest.fixture
def settings(database_path: Path) -> Settings:
    return Settings(
        database_path=database_path,
        max_query_rows=2,
        hard_max_query_rows=5,
        query_timeout_seconds=1,
        mcp_host="127.0.0.1",
        mcp_port=8000,
        mcp_path="/mcp",
    )
