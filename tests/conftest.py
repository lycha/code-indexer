"""Shared test fixtures for the code indexer test suite."""

import pytest

from indexer.db import bootstrap, get_connection


@pytest.fixture
def db_conn(tmp_path):
    """File-backed temp database with full schema via bootstrap()."""
    db_path = str(tmp_path / "test.db")
    bootstrap(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()
