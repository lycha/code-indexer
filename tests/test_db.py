"""Unit tests for database bootstrap, connection, and path resolution."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from indexer.db import bootstrap, get_connection, resolve_db_path


class TestBootstrap:
    """Tests for bootstrap() function."""

    def test_fresh_bootstrap_creates_tables(self, tmp_path):
        """Bootstrap on a fresh DB creates all tables and sets schema_version=4."""
        db_path = str(tmp_path / ".codeindex" / "codeindex.db")
        bootstrap(db_path)

        conn = get_connection(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'table')"
            ).fetchall()
        }
        # nodes_fts creates shadow tables; check the main ones
        assert "nodes" in tables
        assert "edges" in tables
        assert "files" in tables
        assert "index_meta" in tables

        # Check FTS5 virtual table
        vtables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'nodes_fts'"
            ).fetchall()
        }
        # FTS5 virtual tables don't appear as type='table', check via pragma or shadow tables
        # Instead, test that we can query it
        conn.execute("SELECT * FROM nodes_fts LIMIT 0")

        # Check schema_version
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None
        assert row[0] == "4"
        conn.close()

    def test_bootstrap_creates_directory(self, tmp_path):
        """Bootstrap creates .codeindex/ directory if it doesn't exist."""
        db_path = str(tmp_path / ".codeindex" / "codeindex.db")
        assert not (tmp_path / ".codeindex").exists()
        bootstrap(db_path)
        assert (tmp_path / ".codeindex").exists()

    def test_bootstrap_idempotent(self, tmp_path):
        """Running bootstrap twice is a no-op the second time."""
        db_path = str(tmp_path / ".codeindex" / "codeindex.db")
        bootstrap(db_path)
        # Second run should not raise
        bootstrap(db_path)

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row[0] == "4"
        conn.close()

    def test_downgrade_detection(self, tmp_path):
        """Bootstrap exits 2 when DB schema version is ahead of code."""
        db_path = str(tmp_path / ".codeindex" / "codeindex.db")
        bootstrap(db_path)

        # Manually bump schema_version to simulate a newer DB
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE index_meta SET value = '999' WHERE key = 'schema_version'"
        )
        conn.commit()
        conn.close()

        with pytest.raises(SystemExit) as exc_info:
            bootstrap(db_path)
        assert exc_info.value.code == 2

    def test_migration_upgrade(self, tmp_path):
        """Bootstrap applies pending migrations when schema_version is stale."""
        db_path = str(tmp_path / ".codeindex" / "codeindex.db")
        bootstrap(db_path)

        # Set schema_version to 0 to simulate stale
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE index_meta SET value = '0' WHERE key = 'schema_version'"
        )
        conn.commit()
        conn.close()

        # Re-bootstrap should re-apply migration as a safe no-op
        # (all CREATE statements use IF NOT EXISTS so existing tables don't crash)
        bootstrap(db_path)

        # Verify schema_version is updated back to 4
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row[0] == "4"

        # Verify all tables still exist and are queryable
        conn.execute("SELECT * FROM nodes LIMIT 0")
        conn.execute("SELECT * FROM edges LIMIT 0")
        conn.execute("SELECT * FROM files LIMIT 0")
        conn.execute("SELECT * FROM nodes_fts LIMIT 0")
        conn.execute("SELECT * FROM index_meta LIMIT 0")
        conn.close()

    def test_migration_replay_via_init_command(self, tmp_path):
        """Setting schema_version to 0 and running index init succeeds."""
        # First create a normal DB via CLI
        result = subprocess.run(
            ["index", "init"], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 0

        # Set schema_version to 0
        db_path = str(tmp_path / ".codeindex" / "codeindex.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE index_meta SET value = '0' WHERE key = 'schema_version'"
        )
        conn.commit()
        conn.close()

        # Re-run init — should succeed (idempotent DDL)
        result = subprocess.run(
            ["index", "init"], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 0

        # Verify schema_version restored
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row[0] == "4"
        conn.close()


class TestGetConnection:
    """Tests for get_connection() function."""

    def test_wal_mode(self, tmp_path):
        """get_connection sets WAL journal mode."""
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_foreign_keys_enabled(self, tmp_path):
        """get_connection enables foreign keys."""
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        conn.close()


class TestResolveDbPath:
    """Tests for resolve_db_path() function."""

    def test_explicit_db_arg(self):
        """--db argument takes priority."""
        assert resolve_db_path("/tmp/custom.db") == "/tmp/custom.db"

    def test_env_var(self):
        """CODEINDEX_DB env var is used when no --db arg."""
        with patch.dict(os.environ, {"CODEINDEX_DB": "/tmp/env.db"}):
            assert resolve_db_path(None) == "/tmp/env.db"

    def test_default_path(self):
        """Default path is .codeindex/codeindex.db."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove CODEINDEX_DB if present
            env = os.environ.copy()
            env.pop("CODEINDEX_DB", None)
            with patch.dict(os.environ, env, clear=True):
                result = resolve_db_path(None)
                assert result == str(Path(".codeindex") / "codeindex.db")

    def test_db_arg_overrides_env(self):
        """--db arg takes priority over CODEINDEX_DB env var."""
        with patch.dict(os.environ, {"CODEINDEX_DB": "/tmp/env.db"}):
            assert resolve_db_path("/tmp/explicit.db") == "/tmp/explicit.db"


class TestDbConnFixture:
    """Tests using the shared db_conn fixture."""

    def test_all_tables_exist(self, db_conn):
        """db_conn fixture provides a connection with all tables."""
        # Verify we can query each table
        db_conn.execute("SELECT * FROM nodes LIMIT 0")
        db_conn.execute("SELECT * FROM edges LIMIT 0")
        db_conn.execute("SELECT * FROM files LIMIT 0")
        db_conn.execute("SELECT * FROM nodes_fts LIMIT 0")
        db_conn.execute("SELECT * FROM index_meta LIMIT 0")

    def test_schema_version_set(self, db_conn):
        """db_conn fixture has schema_version=4."""
        row = db_conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row[0] == "4"

    def test_node_type_check_constraint(self, db_conn):
        """nodes table enforces CHECK constraint on node_type."""
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, "
                "start_line, end_line, language, content_hash) "
                "VALUES ('test', 'test.py', 'invalid_type', 'test', 'test', 1, 10, 'python', 'abc')"
            )

    def test_edge_type_check_constraint(self, db_conn):
        """edges table enforces CHECK constraint on edge_type."""
        # First insert valid nodes
        db_conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, "
            "start_line, end_line, language, content_hash) "
            "VALUES ('n1', 'test.py', 'function', 'foo', 'foo', 1, 10, 'python', 'abc')"
        )
        db_conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, "
            "start_line, end_line, language, content_hash) "
            "VALUES ('n2', 'test.py', 'function', 'bar', 'bar', 11, 20, 'python', 'def')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO edges (source_id, target_id, edge_type) "
                "VALUES ('n1', 'n2', 'invalid_edge')"
            )

    def test_valid_node_types(self, db_conn):
        """All valid node_type values are accepted."""
        for i, ntype in enumerate(('file', 'class', 'function', 'method', 'interface', 'object', 'module')):
            db_conn.execute(
                "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, "
                "start_line, end_line, language, content_hash) "
                f"VALUES ('n{i}', 'test.py', ?, 'test', 'test{i}', 1, 10, 'python', 'hash{i}')",
                (ntype,),
            )

    def test_valid_edge_types(self, db_conn):
        """All valid edge_type values are accepted."""
        # Create source and target nodes
        for i in range(12):
            db_conn.execute(
                "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, "
                "start_line, end_line, language, content_hash) "
                f"VALUES ('e{i}', 'test.py', 'function', 'f{i}', 'f{i}', 1, 10, 'python', 'h{i}')"
            )
        for i, etype in enumerate(('calls', 'imports', 'inherits', 'overrides', 'references', 'instantiates')):
            db_conn.execute(
                "INSERT INTO edges (source_id, target_id, edge_type) VALUES (?, ?, ?)",
                (f"e{i*2}", f"e{i*2+1}", etype),
            )

    def test_fts5_external_content(self, db_conn):
        """nodes_fts is an external content FTS5 table tied to nodes."""
        # Insert a node and rebuild FTS
        db_conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, "
            "start_line, end_line, language, content_hash, semantic_summary) "
            "VALUES ('fts_test', 'test.py', 'function', 'test', 'test_func', "
            "1, 10, 'python', 'abc', 'This is a test summary')"
        )
        db_conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
        # Query FTS
        rows = db_conn.execute(
            "SELECT id FROM nodes_fts WHERE nodes_fts MATCH 'test summary'"
        ).fetchall()
        assert len(rows) > 0

    def test_foreign_key_cascade(self, db_conn):
        """Deleting a node cascades to edges."""
        db_conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, "
            "start_line, end_line, language, content_hash) "
            "VALUES ('fk1', 'test.py', 'function', 'a', 'a', 1, 10, 'python', 'x')"
        )
        db_conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, name, qualified_name, "
            "start_line, end_line, language, content_hash) "
            "VALUES ('fk2', 'test.py', 'function', 'b', 'b', 11, 20, 'python', 'y')"
        )
        db_conn.execute(
            "INSERT INTO edges (source_id, target_id, edge_type) VALUES ('fk1', 'fk2', 'calls')"
        )
        db_conn.commit()
        db_conn.execute("DELETE FROM nodes WHERE id = 'fk1'")
        edges = db_conn.execute("SELECT * FROM edges WHERE source_id = 'fk1'").fetchall()
        assert len(edges) == 0


class TestInitCommand:
    """Integration tests for the index init CLI command."""

    def test_init_creates_db(self, tmp_path):
        """index init creates .codeindex/ directory and database."""
        result = subprocess.run(
            ["index", "init"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert (tmp_path / ".codeindex" / "codeindex.db").exists()

    def test_init_idempotent(self, tmp_path):
        """Running index init twice is a no-op."""
        subprocess.run(["index", "init"], capture_output=True, text=True, cwd=str(tmp_path))
        result = subprocess.run(
            ["index", "init"], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 0

    def test_init_gitignore_append(self, tmp_path):
        """index init appends .codeindex/ to .gitignore."""
        result = subprocess.run(
            ["index", "init"], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 0
        gitignore = (tmp_path / ".gitignore").read_text()
        assert ".codeindex/" in gitignore

    def test_init_gitignore_idempotent(self, tmp_path):
        """Running index init twice doesn't duplicate .gitignore entry."""
        subprocess.run(["index", "init"], capture_output=True, text=True, cwd=str(tmp_path))
        subprocess.run(["index", "init"], capture_output=True, text=True, cwd=str(tmp_path))
        gitignore = (tmp_path / ".gitignore").read_text()
        assert gitignore.count(".codeindex/") == 1

    def test_init_no_gitignore_update(self, tmp_path):
        """--no-gitignore-update suppresses .gitignore modification."""
        result = subprocess.run(
            ["index", "init", "--no-gitignore-update"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert not (tmp_path / ".gitignore").exists()

    def test_init_downgrade_exits_2(self, tmp_path):
        """index init exits 2 when DB has higher schema version."""
        # First create a normal DB
        subprocess.run(["index", "init"], capture_output=True, text=True, cwd=str(tmp_path))
        # Bump schema_version
        import sqlite3
        db_path = str(tmp_path / ".codeindex" / "codeindex.db")
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE index_meta SET value = '999' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        # Now init should fail
        result = subprocess.run(
            ["index", "init"], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 2
        assert "Schema version mismatch" in result.stderr

    def test_init_with_custom_db(self, tmp_path):
        """--db PATH creates the database at the specified path."""
        db_path = str(tmp_path / "custom" / "my.db")
        result = subprocess.run(
            ["index", "--db", db_path, "init"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert Path(db_path).exists()

    def test_init_schema_completeness(self, tmp_path):
        """After init, DB has all 5 tables with correct structure."""
        subprocess.run(["index", "init"], capture_output=True, text=True, cwd=str(tmp_path))
        db_path = str(tmp_path / ".codeindex" / "codeindex.db")

        conn = sqlite3.connect(db_path)
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
        schema_text = "\n".join(row[0] for row in schema)

        # Verify tables
        assert "CREATE TABLE nodes" in schema_text
        assert "CREATE TABLE edges" in schema_text
        assert "CREATE TABLE files" in schema_text
        assert "CREATE TABLE index_meta" in schema_text
        # FTS5 virtual table
        assert "nodes_fts" in schema_text

        # Verify CHECK constraints
        assert "node_type IN" in schema_text
        assert "edge_type IN" in schema_text

        # Verify indexes
        assert "idx_nodes_file_path" in schema_text
        assert "idx_edges_source" in schema_text
        conn.close()
