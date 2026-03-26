"""Shared test fixtures for the code indexer test suite."""

import sqlite3

import pytest

from indexer.db import bootstrap, get_connection


@pytest.fixture
def db_conn():
    """In-memory SQLite database with full schema applied.

    Yields a connection, closes it after the test.
    """
    bootstrap(":memory:")
    conn = get_connection(":memory:")
    # For in-memory DBs, bootstrap and get_connection create separate databases.
    # We need to bootstrap on the same connection. Re-do it inline.
    conn.close()

    # Create a single in-memory connection and apply schema directly
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    from pathlib import Path
    migrations_dir = Path(__file__).parent.parent / "indexer" / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        conn.executescript(sql_file.read_text())

    conn.execute(
        "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('schema_version', '2')"
    )
    conn.commit()
    yield conn
    conn.close()
