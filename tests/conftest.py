"""Shared test fixtures for the code indexer test suite."""

import os

import pytest

from indexer.db import bootstrap, get_connection


@pytest.fixture(autouse=True)
def _isolate_litellm_env():
    """Prevent LITELLM_BASE_URL from causing provider auto-detection in tests."""
    saved = {k: os.environ.pop(k) for k in ("LITELLM_API_KEY", "LITELLM_BASE_URL") if k in os.environ}
    yield
    os.environ.update(saved)


@pytest.fixture
def db_conn(tmp_path):
    """File-backed temp database with full schema via bootstrap()."""
    db_path = str(tmp_path / "test.db")
    bootstrap(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()
