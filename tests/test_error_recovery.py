"""Tests for error and crash recovery paths.

Covers: mid-build crash recovery, mid-enrichment crash recovery,
corrupt DB handling, missing DB file, schema version mismatch,
concurrent write safety (WAL mode), and parse recovery for bad files.
"""

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from indexer.db import bootstrap, get_connection
from indexer.enricher import enrich_nodes
from indexer.parser import parse_directory, parse_file


# ---------------------------------------------------------------------------
# Helpers (mirrors test_enricher.py patterns)
# ---------------------------------------------------------------------------


def _insert_node(conn, node_id="test.py::function::foo", enriched_at=None, **kwargs):
    """Helper to insert a node for testing."""
    defaults = {
        "file_path": "test.py",
        "node_type": "function",
        "name": "foo",
        "qualified_name": "foo",
        "signature": "def foo(x)",
        "docstring": "Does stuff.",
        "start_line": 1,
        "end_line": 5,
        "language": "python",
        "raw_source": "def foo(x):\n    return x + 1",
        "content_hash": "abc123",
    }
    defaults.update(kwargs)
    conn.execute(
        """INSERT INTO nodes (id, file_path, node_type, name, qualified_name, signature,
           docstring, start_line, end_line, language, raw_source, content_hash, enriched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            node_id,
            defaults["file_path"],
            defaults["node_type"],
            defaults["name"],
            defaults["qualified_name"],
            defaults["signature"],
            defaults["docstring"],
            defaults["start_line"],
            defaults["end_line"],
            defaults["language"],
            defaults["raw_source"],
            defaults["content_hash"],
            enriched_at,
        ),
    )
    conn.commit()


def _make_llm_response(summary="Does stuff", tags=None, responsibility="Handles stuff"):
    """Create a valid LLM JSON response."""
    if tags is None:
        tags = ["utility", "math"]
    return json.dumps({
        "semantic_summary": summary,
        "domain_tags": tags,
        "inferred_responsibility": responsibility,
    })


# ---------------------------------------------------------------------------
# 1. Build recovery after crash
# ---------------------------------------------------------------------------


class TestBuildRecoveryAfterCrash:
    """Verify that a fresh parse_directory recovers after partial state."""

    def test_new_file_indexed_alongside_existing(self, tmp_path):
        """After indexing files, adding a new file and re-running parse_directory
        indexes the new file correctly alongside existing files."""
        # Set up DB
        db_path = str(tmp_path / "test.db")
        bootstrap(db_path)
        conn = get_connection(db_path)

        # Create initial Python file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "alpha.py").write_text("def alpha():\n    return 1\n")

        # First build
        warnings1, changed1 = parse_directory(src_dir, conn)
        conn.commit()
        count1 = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert count1 > 0
        assert "src/alpha.py" not in [w for w in warnings1]

        # Add a new file
        (src_dir / "beta.py").write_text("def beta():\n    return 2\n")

        # Second build
        warnings2, changed2 = parse_directory(src_dir, conn)
        conn.commit()
        count2 = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert count2 > count1, "New file should add nodes"

        # Verify both files are in the DB
        files = {r[0] for r in conn.execute("SELECT path FROM files").fetchall()}
        assert "alpha.py" in files
        assert "beta.py" in files
        conn.close()

    def test_modified_file_re_indexed(self, tmp_path):
        """Modifying a file and re-running parse_directory re-indexes it."""
        db_path = str(tmp_path / "test.db")
        bootstrap(db_path)
        conn = get_connection(db_path)

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "mod.py").write_text("def original():\n    pass\n")

        parse_directory(src_dir, conn)
        conn.commit()

        # Verify original function is indexed
        names = {r[0] for r in conn.execute("SELECT name FROM nodes WHERE node_type != 'file'").fetchall()}
        assert "original" in names

        # Modify the file
        (src_dir / "mod.py").write_text("def modified():\n    pass\n")

        parse_directory(src_dir, conn)
        conn.commit()

        # Verify old function is gone, new function is indexed
        names = {r[0] for r in conn.execute("SELECT name FROM nodes WHERE node_type != 'file'").fetchall()}
        assert "modified" in names
        assert "original" not in names
        conn.close()


# ---------------------------------------------------------------------------
# 2. Enrichment interruption recovery
# ---------------------------------------------------------------------------


class TestEnrichmentInterruptionRecovery:
    """Verify that enrichment resumes correctly after a partial failure."""

    @patch("indexer.enricher.call_llm")
    def test_partial_enrichment_then_resume(self, mock_llm, db_conn):
        """Enrichment that fails partway through can be resumed;
        already-enriched nodes are not re-enriched."""
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            # Insert 3 non-file nodes
            _insert_node(db_conn, "a.py::function::fn_a", name="fn_a", qualified_name="fn_a",
                         file_path="a.py", content_hash="hash_a")
            _insert_node(db_conn, "b.py::function::fn_b", name="fn_b", qualified_name="fn_b",
                         file_path="b.py", content_hash="hash_b")
            _insert_node(db_conn, "c.py::function::fn_c", name="fn_c", qualified_name="fn_c",
                         file_path="c.py", content_hash="hash_c")

            # First run: succeed for first 2, raise API error for the 3rd
            import anthropic as anthropic_mod
            mock_llm.side_effect = [
                _make_llm_response(summary="Summary A"),
                _make_llm_response(summary="Summary B"),
                anthropic_mod.APIError(
                    message="Service unavailable",
                    request=MagicMock(),
                    body=None,
                ),
            ]

            exit_code1 = enrich_nodes(db_conn, model="claude-sonnet-4-6", provider="anthropic")
            assert exit_code1 == 1  # some nodes remain unenriched

            enriched1 = db_conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NOT NULL"
            ).fetchone()[0]
            assert enriched1 == 2

            # Second run: only the remaining unenriched node should be processed
            mock_llm.reset_mock()
            mock_llm.side_effect = [_make_llm_response(summary="Summary C")]

            exit_code2 = enrich_nodes(db_conn, model="claude-sonnet-4-6", provider="anthropic")
            assert exit_code2 == 0  # all enriched now

            # call_llm should have been called exactly once (for the remaining node)
            assert mock_llm.call_count == 1

            enriched2 = db_conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NOT NULL"
            ).fetchone()[0]
            assert enriched2 == 3
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    @patch("indexer.enricher.call_llm")
    def test_enriched_count_increases_across_runs(self, mock_llm, db_conn):
        """Verify enriched_count increases across interrupted runs."""
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "x.py::function::fn_x", name="fn_x", qualified_name="fn_x",
                         file_path="x.py", content_hash="hash_x")
            _insert_node(db_conn, "y.py::function::fn_y", name="fn_y", qualified_name="fn_y",
                         file_path="y.py", content_hash="hash_y")

            # First run: succeed for 1, fail for the other
            import anthropic as anthropic_mod
            mock_llm.side_effect = [
                _make_llm_response(),
                anthropic_mod.APIError(
                    message="Timeout",
                    request=MagicMock(),
                    body=None,
                ),
            ]
            enrich_nodes(db_conn, model="claude-sonnet-4-6", provider="anthropic")

            enriched_after_first = db_conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NOT NULL"
            ).fetchone()[0]
            assert enriched_after_first == 1

            # Second run
            mock_llm.reset_mock()
            mock_llm.side_effect = [_make_llm_response()]
            enrich_nodes(db_conn, model="claude-sonnet-4-6", provider="anthropic")

            enriched_after_second = db_conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NOT NULL"
            ).fetchone()[0]
            assert enriched_after_second > enriched_after_first
            assert enriched_after_second == 2
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)


# ---------------------------------------------------------------------------
# 3. Corrupt DB handling
# ---------------------------------------------------------------------------


class TestCorruptDbHandling:
    """Verify behavior when the DB file contains garbage data."""

    def test_corrupt_db_get_connection_raises(self, tmp_path):
        """get_connection on a corrupt file raises sqlite3.DatabaseError
        because it immediately executes PRAGMA statements."""
        db_path = str(tmp_path / "corrupt.db")
        Path(db_path).write_bytes(b"THIS IS NOT A VALID SQLITE DATABASE FILE AT ALL")

        with pytest.raises(sqlite3.DatabaseError):
            get_connection(db_path)

    def test_bootstrap_on_corrupt_file_raises(self, tmp_path):
        """bootstrap on a corrupt file raises sqlite3.DatabaseError."""
        db_path = str(tmp_path / "corrupt.db")
        Path(db_path).write_bytes(b"GARBAGE CONTENT NOT SQLITE")

        with pytest.raises(sqlite3.DatabaseError):
            bootstrap(db_path)


# ---------------------------------------------------------------------------
# 4. Missing DB file
# ---------------------------------------------------------------------------


class TestMissingDbFile:
    """Verify behavior when the DB file does not exist."""

    def test_get_connection_creates_file(self, tmp_path):
        """get_connection on a non-existent path creates the file (SQLite behavior)."""
        db_path = str(tmp_path / "new.db")
        assert not Path(db_path).exists()

        conn = get_connection(db_path)
        assert Path(db_path).exists()

        # Connection should be usable
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_bootstrap_on_new_path_creates_schema(self, tmp_path):
        """bootstrap on a new path creates the DB with the full schema."""
        db_path = str(tmp_path / "fresh" / "index.db")
        assert not Path(db_path).exists()

        bootstrap(db_path)
        assert Path(db_path).exists()

        conn = get_connection(db_path)
        # All main tables should exist
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table')"
            ).fetchall()
        }
        assert "nodes" in tables
        assert "edges" in tables
        assert "files" in tables
        assert "index_meta" in tables

        # Schema version should be set
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None
        assert int(row[0]) > 0
        conn.close()


# ---------------------------------------------------------------------------
# 5. Schema version mismatch
# ---------------------------------------------------------------------------


class TestSchemaVersionMismatch:
    """Verify downgrade detection when schema_version exceeds supported version."""

    def test_higher_schema_version_exits_2(self, tmp_path):
        """bootstrap with schema_version higher than code supports raises SystemExit(2)."""
        db_path = str(tmp_path / "test.db")
        bootstrap(db_path)

        # Manually set schema_version to a very high value
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE index_meta SET value = '999' WHERE key = 'schema_version'"
        )
        conn.commit()
        conn.close()

        with pytest.raises(SystemExit) as exc_info:
            bootstrap(db_path)
        assert exc_info.value.code == 2

    def test_current_version_is_noop(self, tmp_path):
        """bootstrap with current schema_version is a no-op (no error)."""
        db_path = str(tmp_path / "test.db")
        bootstrap(db_path)

        # Second bootstrap should be a no-op
        bootstrap(db_path)

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None
        assert int(row[0]) > 0
        conn.close()


# ---------------------------------------------------------------------------
# 6. Concurrent write safety (WAL mode)
# ---------------------------------------------------------------------------


class TestConcurrentWriteSafety:
    """Verify the connection is configured for concurrent access."""

    def test_wal_mode_enabled(self, db_conn):
        """Connection uses WAL journal mode."""
        mode = db_conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_busy_timeout_set(self, db_conn):
        """Connection has busy_timeout configured."""
        timeout = db_conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000

    def test_foreign_keys_enabled(self, db_conn):
        """Connection has foreign keys enabled."""
        fk = db_conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


# ---------------------------------------------------------------------------
# 7. Parse recovery for bad files
# ---------------------------------------------------------------------------


class TestParseRecoveryForBadFiles:
    """Verify parse_directory handles a mix of valid and invalid files."""

    def test_valid_files_indexed_despite_syntax_error(self, tmp_path):
        """Valid Python files are indexed even when a syntax-error file is present."""
        db_path = str(tmp_path / "test.db")
        bootstrap(db_path)
        conn = get_connection(db_path)

        src_dir = tmp_path / "project"
        src_dir.mkdir()

        # Valid file 1
        (src_dir / "good1.py").write_text("def hello():\n    return 'hello'\n")
        # Valid file 2
        (src_dir / "good2.py").write_text("class MyClass:\n    def method(self):\n        pass\n")
        # Invalid file (syntax error)
        (src_dir / "bad.py").write_text("def broken(\n    # missing closing paren\n    x, y\n")

        warnings, changed = parse_directory(src_dir, conn)
        conn.commit()

        # Valid files should be indexed
        files = {r[0] for r in conn.execute("SELECT path FROM files").fetchall()}
        assert "good1.py" in files
        assert "good2.py" in files

        # Nodes should exist for valid files
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert node_count > 0

        # The bad file should produce a warning
        assert any("bad.py" in w for w in warnings)

        # The bad file should NOT be in the files table (it was skipped)
        assert "bad.py" not in files
        conn.close()

    def test_syntax_error_does_not_stop_build(self, tmp_path):
        """A syntax-error file does not prevent other files from being indexed."""
        db_path = str(tmp_path / "test.db")
        bootstrap(db_path)
        conn = get_connection(db_path)

        src_dir = tmp_path / "project"
        src_dir.mkdir()

        # Create many valid files plus one bad one
        for i in range(5):
            (src_dir / f"module_{i}.py").write_text(f"def func_{i}():\n    return {i}\n")
        (src_dir / "broken.py").write_text("class Incomplete(:\n    pass\n")

        warnings, changed = parse_directory(src_dir, conn)
        conn.commit()

        # All 5 valid files should be in the files table
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        assert file_count == 5

        # Functions from valid files should exist as nodes
        func_nodes = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE node_type = 'function'"
        ).fetchone()[0]
        assert func_nodes == 5

        # Warning should mention the broken file
        assert any("broken.py" in w for w in warnings)
        conn.close()

    def test_single_file_parse_with_syntax_error(self, tmp_path):
        """parse_file on a file with a syntax error returns empty list (no crash)."""
        src_dir = tmp_path / "project"
        src_dir.mkdir()
        bad_file = src_dir / "bad.py"
        bad_file.write_text("def broken(\n    x, y\n")

        db_path = str(tmp_path / "test.db")
        bootstrap(db_path)
        conn = get_connection(db_path)

        nodes = parse_file(bad_file, conn, src_dir)
        assert nodes == []
        conn.close()
