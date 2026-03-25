"""Integration tests for the index build command."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RG_PATH = "/Users/kjackowski/.factory/bin/rg"


def _run_build(cwd: str, *extra_args: str, env_override: dict | None = None) -> subprocess.CompletedProcess:
    """Run `index build` in the given directory, capturing stdout/stderr separately."""
    env = os.environ.copy()
    # Ensure rg is on PATH
    env["PATH"] = f"/Users/kjackowski/.factory/bin:{env['PATH']}"
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, "-m", "indexer", "build", *extra_args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def fixture_repo(tmp_path):
    """Create a temporary repo with fixture files for integration testing."""
    # Copy fixture files
    for f in FIXTURES_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, tmp_path / f.name)
    return tmp_path


class TestBuildAutoBootstrap:
    """VAL-BUILD-001: Auto-bootstrap on build."""

    def test_creates_db_if_missing(self, fixture_repo):
        (fixture_repo / "syntax_error.py").unlink(missing_ok=True)
        db_dir = fixture_repo / ".codeindex"
        assert not db_dir.exists()
        result = _run_build(str(fixture_repo))
        assert result.returncode == 0
        assert (db_dir / "codeindex.db").exists()

    def test_db_has_schema(self, fixture_repo):
        (fixture_repo / "syntax_error.py").unlink(missing_ok=True)
        _run_build(str(fixture_repo))
        db_path = fixture_repo / ".codeindex" / "codeindex.db"
        conn = sqlite3.connect(str(db_path))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        for t in ("nodes", "edges", "files", "index_meta"):
            assert t in tables


class TestBuildLockFile:
    """VAL-BUILD-002, VAL-BUILD-003, VAL-BUILD-011: Lock file behavior."""

    def test_lock_created_and_removed(self, fixture_repo):
        (fixture_repo / "syntax_error.py").unlink(missing_ok=True)
        lock_path = fixture_repo / ".codeindex" / "build.lock"
        result = _run_build(str(fixture_repo))
        assert result.returncode == 0
        # Lock should be removed after build
        assert not lock_path.exists()

    def test_stale_lock_removed(self, fixture_repo):
        """Stale lock (>10 min old) is removed with warning."""
        db_dir = fixture_repo / ".codeindex"
        db_dir.mkdir(exist_ok=True)
        lock_path = db_dir / "build.lock"
        stale_time = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        lock_path.write_text(json.dumps({"pid": 99999, "started": stale_time}))
        (fixture_repo / "syntax_error.py").unlink(missing_ok=True)

        result = _run_build(str(fixture_repo))
        assert result.returncode == 0
        assert "[WARNING] Stale lock file removed" in result.stderr
        assert not lock_path.exists()

    def test_fresh_lock_blocks(self, fixture_repo):
        """Fresh lock (<10 min) causes exit 2."""
        db_dir = fixture_repo / ".codeindex"
        db_dir.mkdir(exist_ok=True)
        lock_path = db_dir / "build.lock"
        now = datetime.now(timezone.utc).isoformat()
        lock_path.write_text(json.dumps({"pid": 12345, "started": now}))

        result = _run_build(str(fixture_repo))
        assert result.returncode == 2
        assert "[ERROR] Another build is running (PID: 12345)" in result.stderr

    def test_naive_stale_lock_removed(self, fixture_repo):
        """VAL-BUILD-011: Naive (no tz) stale lock timestamp is handled correctly."""
        db_dir = fixture_repo / ".codeindex"
        db_dir.mkdir(exist_ok=True)
        lock_path = db_dir / "build.lock"
        naive_time = "2020-01-01T00:00:00"  # no timezone info
        lock_path.write_text(json.dumps({"pid": 99999, "started": naive_time}))
        (fixture_repo / "syntax_error.py").unlink(missing_ok=True)

        result = _run_build(str(fixture_repo))
        assert result.returncode == 0
        assert "[WARNING] Stale lock file removed" in result.stderr

    def test_naive_fresh_lock_blocks(self, fixture_repo):
        """VAL-BUILD-011: Naive (no tz) fresh lock timestamp is handled correctly."""
        db_dir = fixture_repo / ".codeindex"
        db_dir.mkdir(exist_ok=True)
        lock_path = db_dir / "build.lock"
        # Use current UTC time but without timezone info (naive)
        now_naive = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        lock_path.write_text(json.dumps({"pid": 12345, "started": now_naive}))

        result = _run_build(str(fixture_repo))
        assert result.returncode == 2
        assert "[ERROR] Another build is running (PID: 12345)" in result.stderr


class TestBuildPhases:
    """VAL-BUILD-004: Phase 1 then Phase 2 sequential with banners."""

    def test_phase_banners_in_order(self, fixture_repo):
        (fixture_repo / "syntax_error.py").unlink(missing_ok=True)
        result = _run_build(str(fixture_repo))
        assert result.returncode == 0
        stderr = result.stderr
        p1_pos = stderr.find("[PHASE 1]")
        p2_pos = stderr.find("[PHASE 2]")
        assert p1_pos >= 0, "Phase 1 banner not found"
        assert p2_pos >= 0, "Phase 2 banner not found"
        assert p1_pos < p2_pos, "Phase 1 must come before Phase 2"


class TestBuildExitCodes:
    """VAL-BUILD-005, VAL-BUILD-006, VAL-BUILD-007: Exit codes."""

    def test_exit_0_clean_build(self, fixture_repo):
        # Remove syntax_error.py so build is clean
        syntax_err = fixture_repo / "syntax_error.py"
        if syntax_err.exists():
            syntax_err.unlink()
        result = _run_build(str(fixture_repo))
        assert result.returncode == 0

    def test_exit_1_on_warnings(self, fixture_repo):
        """Build with syntax error file exits 1 (warnings)."""
        # Ensure syntax_error.py exists
        assert (fixture_repo / "syntax_error.py").exists()
        result = _run_build(str(fixture_repo))
        # parse_directory returns warnings list but the current impl may
        # have already printed the warning via parse_file. Check that
        # stderr has a WARNING about skipped file.
        assert "[WARNING]" in result.stderr
        assert result.returncode == 1

    def test_exit_2_ripgrep_not_found(self, fixture_repo):
        """When rg is not on PATH, exit 2."""
        env = {"PATH": "/usr/bin:/bin"}  # no rg
        result = _run_build(str(fixture_repo), env_override=env)
        assert result.returncode == 2
        assert "ripgrep not found" in result.stderr


class TestBuildStdoutEmpty:
    """VAL-BUILD-008: Nothing to stdout during build."""

    def test_stdout_empty(self, fixture_repo):
        # Remove syntax error file for clean build
        syntax_err = fixture_repo / "syntax_error.py"
        if syntax_err.exists():
            syntax_err.unlink()
        result = _run_build(str(fixture_repo))
        assert result.stdout == "", f"Unexpected stdout: {result.stdout!r}"


class TestBuildIndexMeta:
    """VAL-BUILD-009, VAL-BUILD-010: index_meta updates."""

    def test_meta_updated(self, fixture_repo):
        # Remove syntax error file
        syntax_err = fixture_repo / "syntax_error.py"
        if syntax_err.exists():
            syntax_err.unlink()
        _run_build(str(fixture_repo))
        db_path = fixture_repo / ".codeindex" / "codeindex.db"
        conn = sqlite3.connect(str(db_path))
        meta = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM index_meta").fetchall()}
        conn.close()
        assert "last_full_build" in meta
        assert "total_nodes" in meta
        assert "total_edges" in meta
        assert int(meta["total_nodes"]) > 0

    def test_phase_boundary_stored(self, fixture_repo):
        syntax_err = fixture_repo / "syntax_error.py"
        if syntax_err.exists():
            syntax_err.unlink()
        _run_build(str(fixture_repo), "--phase", "PREPARE")
        db_path = fixture_repo / ".codeindex" / "codeindex.db"
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'last_phase_boundary'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "PREPARE"

    def test_phase_deploy(self, fixture_repo):
        syntax_err = fixture_repo / "syntax_error.py"
        if syntax_err.exists():
            syntax_err.unlink()
        _run_build(str(fixture_repo), "--phase", "DEPLOY")
        db_path = fixture_repo / ".codeindex" / "codeindex.db"
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'last_phase_boundary'"
        ).fetchone()
        conn.close()
        assert row[0] == "DEPLOY"


class TestBuildIntegration:
    """Full integration: build on fixture repo produces correct DB contents."""

    def test_nodes_and_edges_exist(self, fixture_repo):
        # Remove syntax error file
        syntax_err = fixture_repo / "syntax_error.py"
        if syntax_err.exists():
            syntax_err.unlink()
        result = _run_build(str(fixture_repo))
        assert result.returncode == 0

        db_path = fixture_repo / ".codeindex" / "codeindex.db"
        conn = sqlite3.connect(str(db_path))
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        files_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        assert node_count > 0, "Expected nodes after build"
        assert files_count > 0, "Expected files entries after build"

    def test_idempotent_rebuild(self, fixture_repo):
        """Running build twice produces same results."""
        syntax_err = fixture_repo / "syntax_error.py"
        if syntax_err.exists():
            syntax_err.unlink()
        _run_build(str(fixture_repo))
        db_path = fixture_repo / ".codeindex" / "codeindex.db"
        conn = sqlite3.connect(str(db_path))
        count1 = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        conn.close()

        result = _run_build(str(fixture_repo))
        assert result.returncode == 0
        conn = sqlite3.connect(str(db_path))
        count2 = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        conn.close()
        assert count1 == count2
