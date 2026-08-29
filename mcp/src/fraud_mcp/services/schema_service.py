"""SQLite schema introspection for Text2SQL clients."""

from __future__ import annotations

import sqlite3

from fraud_mcp.models import (
    ColumnInfo,
    ForeignKeyInfo,
    RelationInfo,
    SchemaResponse,
)
from fraud_mcp.services.database import Database, readonly_connection

CANONICAL_RELATION = "fraud_transactions"


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def relation_names(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT name, type
        FROM sqlite_schema
        WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
        ORDER BY CASE WHEN name = 'fraud_transactions' THEN 0 ELSE 1 END, name
        """
    )
    return {str(row["name"]): str(row["type"]) for row in rows}


def column_names(connection: sqlite3.Connection, relation: str) -> list[str]:
    pragma = f"PRAGMA table_xinfo({quote_identifier(relation)})"
    return [str(row["name"]) for row in connection.execute(pragma)]


def get_schema(database: Database) -> SchemaResponse:
    with readonly_connection(database) as connection:
        names = relation_names(connection)
        relations: list[RelationInfo] = []
        for name, relation_type in names.items():
            quoted = quote_identifier(name)
            columns = []
            for row in connection.execute(f"PRAGMA table_xinfo({quoted})"):
                primary_key = bool(row["pk"])
                columns.append(
                    ColumnInfo(
                        name=str(row["name"]),
                        type=str(row["type"] or "UNKNOWN"),
                        nullable=not bool(row["notnull"]) and not primary_key,
                        primary_key=primary_key,
                    )
                )

            foreign_keys = [
                ForeignKeyInfo(
                    column=str(row["from"]),
                    referenced_relation=str(row["table"]),
                    referenced_column=str(row["to"]),
                )
                for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")
            ]
            relations.append(
                RelationInfo(
                    name=name,
                    type=relation_type,
                    columns=columns,
                    foreign_keys=foreign_keys,
                    grain=(
                        "one row per transaction"
                        if name == CANONICAL_RELATION
                        else None
                    ),
                )
            )

    return SchemaResponse(
        recommended_relation=CANONICAL_RELATION,
        relations=relations,
    )
