"""Tests for index status and index reset commands."""

import subprocess
import sys
from pathlib import Path

import pytest

from indexer.db import bootstrap, get_connection


@pytest.fixture
def tmp_db(tmp_path):
    """Create a real DB file with schema and sample data."""
    db_path = str(tmp_path / ".codeindex" / "codeindex.db")
    bootstrap(db_path)
    conn = get_connection(db_path)
    # Insert sample data
    conn.execute(
        "INSERT INTO files (path, last_modified, content_hash, language, node_count, indexed_at) "
        "VALUES ('test.py', '2025-01-01T00:00:00', 'abc123', 'python', 2, '2025-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, signature, "
        "docstring, start_line, end_line, language, raw_source, content_hash) "
        "VALUES ('test.py::function::foo', 'test.py', 'function', 'foo', 'foo', "
        "'def foo()', 'A function', 1, 5, 'python', 'def foo(): pass', 'hash1')"
    )
    conn.execute(
        "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, signature, "
        "docstring, start_line, end_line, language, raw_source, content_hash, enriched_at) "
        "VALUES ('test.py::function::bar', 'test.py', 'function', 'bar', 'bar', "
        "'def bar()', 'Another', 6, 10, 'python', 'def bar(): pass', 'hash2', '2025-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO edges (source_id, target_id, edge_type) "
        "VALUES ('test.py::function::foo', 'test.py::function::bar', 'calls')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('last_full_build', '2025-01-01T12:00:00+00:00')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('last_phase_boundary', 'DEPLOY')"
    )
    conn.commit()
    conn.close()
    return db_path


class TestStatusCommand:
    """Tests for the index status command."""

    def test_status_shows_node_count(self, tmp_db):
        """Status output includes node count."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Nodes:" in result.stdout
        assert "2" in result.stdout

    def test_status_shows_edge_count(self, tmp_db):
        """Status output includes edge count."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert "Edges:" in result.stdout
        assert "1" in result.stdout

    def test_status_shows_unenriched_count(self, tmp_db):
        """Status output includes unenriched node count."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert "Unenriched:" in result.stdout
        assert "1" in result.stdout

    def test_status_shows_last_build(self, tmp_db):
        """Status output includes last build timestamp."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert "Last build:" in result.stdout
        assert "2025-01-01" in result.stdout

    def test_status_shows_phase_boundary(self, tmp_db):
        """Status output includes last phase boundary."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert "Phase boundary:" in result.stdout
        assert "DEPLOY" in result.stdout

    def test_status_shows_schema_version(self, tmp_db):
        """Status output includes schema version."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert "Schema version:" in result.stdout

    def test_status_shows_db_path(self, tmp_db):
        """Status output includes database path."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert "DB path:" in result.stdout
        assert tmp_db in result.stdout

    def test_status_exits_1_if_db_missing(self, tmp_path):
        """Status exits 1 with helpful message if DB doesn't exist."""
        missing_db = str(tmp_path / "nonexistent" / "codeindex.db")
        result = subprocess.run(
            ["index", "--db", missing_db, "status"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "Index not initialised" in result.stdout
        assert "index build" in result.stdout

    def test_status_output_to_stdout(self, tmp_db):
        """Status output goes to stdout, not stderr."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Nodes:" in result.stdout
        # stderr should not contain the status info
        assert "Nodes:" not in result.stderr

    def test_status_schema_mismatch_warning(self, tmp_db):
        """Status shows warning if schema version is stale."""
        # Set schema version higher than code supports to simulate mismatch
        conn = get_connection(tmp_db)
        conn.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('schema_version', '999')"
        )
        conn.commit()
        conn.close()
        result = subprocess.run(
            ["index", "--db", tmp_db, "status"],
            capture_output=True, text=True,
        )
        assert "WARNING" in result.stdout
        assert "Schema version mismatch" in result.stdout


class TestResetCommand:
    """Tests for the index reset command."""

    def test_reset_with_yes_flag(self, tmp_db):
        """Reset with --yes drops and recreates tables."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "reset", "--yes"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        # Verify tables are empty but exist
        conn = get_connection(tmp_db)
        nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        assert nodes == 0
        assert edges == 0
        assert files == 0
        conn.close()

    def test_reset_with_y_flag(self, tmp_db):
        """Reset with -y also works."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "reset", "-y"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_reset_requires_yes_in_non_tty(self, tmp_db):
        """Reset exits 2 without --yes when stdin is not a TTY."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "reset"],
            capture_output=True, text=True,
            input="",  # Force non-TTY by providing stdin
        )
        assert result.returncode == 2
        assert "ERROR" in result.stderr

    def test_reset_recreates_schema(self, tmp_db):
        """After reset, tables exist with fresh schema."""
        subprocess.run(
            ["index", "--db", tmp_db, "reset", "--yes"],
            capture_output=True, text=True,
        )
        conn = get_connection(tmp_db)
        # Check tables exist
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        assert "nodes" in tables
        assert "edges" in tables
        assert "files" in tables
        assert "index_meta" in tables
        conn.close()

    def test_reset_confirmation_to_stderr(self, tmp_db):
        """Reset confirmation messages go to stderr."""
        result = subprocess.run(
            ["index", "--db", tmp_db, "reset", "--yes"],
            capture_output=True, text=True,
        )
        # Progress should be on stderr
        assert result.returncode == 0
        # The reset message should be on stderr
        assert "reset" in result.stderr.lower() or "Reset" in result.stderr or "RESET" in result.stderr

    def test_reset_drops_all_data(self, tmp_db):
        """Reset actually removes all data before recreating."""
        # Verify data exists before reset
        conn = get_connection(tmp_db)
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] > 0
        conn.close()

        subprocess.run(
            ["index", "--db", tmp_db, "reset", "--yes"],
            capture_output=True, text=True,
        )

        conn = get_connection(tmp_db)
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        # index_meta should have schema_version from bootstrap
        version = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert version is not None
        conn.close()


class TestResetInteractiveTTY:
    """Tests for reset TTY interactive prompt behavior."""

    def test_reset_prompt_decline(self, tmp_db):
        """Reset in interactive mode with 'n' answer cancels."""
        # Simulate TTY with 'n' response - use a script that patches isatty
        script = f"""
import sys
import unittest.mock
with unittest.mock.patch('sys.stdin') as mock_stdin:
    mock_stdin.isatty.return_value = True
    mock_stdin.readline.return_value = 'n\\n'
    # Re-import to pick up patched stdin
    from indexer.cli import cli
    from click.testing import CliRunner
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, ['--db', '{tmp_db}', 'reset'])
    sys.exit(result.exit_code)
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
        )
        # Should exit without resetting (cancelled)
        # Data should still exist
        conn = get_connection(tmp_db)
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] > 0
        conn.close()
