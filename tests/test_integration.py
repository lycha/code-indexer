"""Cross-area integration tests.

Verifies end-to-end flows spanning init, build, enrich, query, status,
and reset commands working together.
"""

import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _env_with_rg() -> dict:
    """Return env dict with ripgrep's directory on PATH."""
    env = os.environ.copy()
    rg = shutil.which("rg")
    if rg:
        rg_dir = str(Path(rg).parent)
        if rg_dir not in env.get("PATH", ""):
            env["PATH"] = f"{rg_dir}:{env['PATH']}"
    return env


def _run_cmd(*args: str, cwd: str, env_override: dict | None = None) -> subprocess.CompletedProcess:
    """Run an ``index`` sub-command via ``python -m indexer``."""
    env = _env_with_rg()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, "-m", "indexer", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def repo(tmp_path):
    """Copy fixture files into a temporary repo directory."""
    for f in FIXTURES_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, tmp_path / f.name)
    # Remove syntax_error.py so builds are clean by default
    (tmp_path / "syntax_error.py").unlink(missing_ok=True)
    return tmp_path


def _db_path(repo_dir: Path) -> Path:
    return repo_dir / ".codeindex" / "codeindex.db"


def _connect(repo_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(_db_path(repo_dir)))


# ── (1) First-time user flow ─────────────────────────────────────────


class TestFirstTimeBuild:
    """index build on a fresh repo auto-bootstraps and produces a queryable index."""

    def test_build_creates_queryable_index(self, repo):
        assert not _db_path(repo).exists()

        r = _run_cmd("build", cwd=str(repo))
        assert r.returncode == 0, r.stderr

        conn = _connect(repo)
        nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()

        assert nodes > 0
        assert files > 0
        # FTS5 should be queryable
        conn = _connect(repo)
        fts = conn.execute(
            "SELECT COUNT(*) FROM nodes_fts WHERE nodes_fts MATCH 'function OR class'"
        ).fetchone()[0]
        conn.close()
        assert fts >= 0  # FTS5 table is accessible

    def test_status_after_build(self, repo):
        _run_cmd("build", cwd=str(repo))
        r = _run_cmd("status", cwd=str(repo))
        assert r.returncode == 0
        assert "Nodes:" in r.stdout
        assert "Edges:" in r.stdout


# ── (2) Incremental build ────────────────────────────────────────────


class TestIncrementalBuild:
    """Modify a file, rebuild; only changed file re-parsed."""

    def test_only_changed_file_reparsed(self, repo):
        _run_cmd("build", cwd=str(repo))
        conn = _connect(repo)
        original_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        original_files = {
            r[0]: r[1]
            for r in conn.execute("SELECT path, content_hash FROM files").fetchall()
        }
        conn.close()

        # Modify sample.py — append a new function
        sample = repo / "sample.py"
        sample.write_text(
            sample.read_text() + "\ndef new_func():\n    return 999\n"
        )

        r = _run_cmd("build", cwd=str(repo))
        assert r.returncode == 0, r.stderr

        conn = _connect(repo)
        new_files = {
            r[0]: r[1]
            for r in conn.execute("SELECT path, content_hash FROM files").fetchall()
        }
        conn.close()

        # sample.py hash should have changed; others should not
        assert new_files["sample.py"] != original_files["sample.py"]
        for fp, h in original_files.items():
            if fp != "sample.py":
                assert new_files[fp] == h, f"{fp} hash changed unexpectedly"


# ── (3) Hash-gating enrichment ───────────────────────────────────────


class TestHashGatedEnrichment:
    """Build+enrich, modify, rebuild → only changed nodes need re-enrichment."""

    def test_changed_nodes_cleared_for_reenrichment(self, repo):
        _run_cmd("build", cwd=str(repo))

        # Simulate enrichment by setting enriched_at on all nodes
        enriched_ts = datetime.now(timezone.utc).isoformat()
        conn = _connect(repo)
        conn.execute(
            "UPDATE nodes SET enriched_at = ?",
            (enriched_ts,),
        )
        conn.commit()

        # Record node IDs and their enriched_at per file before modification
        rows_before = conn.execute(
            "SELECT id, file_path, enriched_at FROM nodes"
        ).fetchall()
        enriched_before = {r[0]: (r[1], r[2]) for r in rows_before}
        assert all(ts is not None for _, ts in enriched_before.values())
        conn.close()

        # Modify sample.py
        sample = repo / "sample.py"
        sample.write_text(
            sample.read_text() + "\ndef another_func():\n    return 1\n"
        )

        _run_cmd("build", cwd=str(repo))

        conn = _connect(repo)
        rows_after = conn.execute(
            "SELECT id, file_path, enriched_at FROM nodes"
        ).fetchall()
        conn.close()

        # Nodes from sample.py should have enriched_at cleared (NULL)
        changed_nodes = [r for r in rows_after if r[1] == "sample.py"]
        assert len(changed_nodes) > 0, "Expected nodes from sample.py"
        for node_id, fp, enriched_at in changed_nodes:
            assert enriched_at is None, (
                f"Node {node_id} in changed file sample.py should have enriched_at=NULL"
            )

        # Unchanged nodes should retain their original enriched_at
        unchanged_nodes = [r for r in rows_after if r[1] != "sample.py"]
        for node_id, fp, enriched_at in unchanged_nodes:
            assert enriched_at == enriched_ts, (
                f"Node {node_id} in unchanged file {fp} should retain enriched_at"
            )


# ── (4) Reset + rebuild equivalence ──────────────────────────────────


class TestResetRebuildEquivalence:
    """Reset then rebuild should produce equivalent index to a fresh build."""

    def test_reset_rebuild_matches_fresh(self, repo):
        _run_cmd("build", cwd=str(repo))
        conn = _connect(repo)
        nodes1 = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        conn.close()

        _run_cmd("reset", "--yes", cwd=str(repo))
        _run_cmd("build", cwd=str(repo))

        conn = _connect(repo)
        nodes2 = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        conn.close()

        assert nodes1 == nodes2


# ── (5) DB path consistency ──────────────────────────────────────────


class TestDbPathConsistency:
    """--db flag respected across all commands."""

    def test_custom_db_path(self, repo, tmp_path):
        custom_db = str(tmp_path / "custom.db")

        r = _run_cmd("--db", custom_db, "init", cwd=str(repo))
        assert r.returncode == 0
        assert Path(custom_db).exists()

        r = _run_cmd("--db", custom_db, "build", cwd=str(repo))
        assert r.returncode == 0

        r = _run_cmd("--db", custom_db, "status", cwd=str(repo))
        assert r.returncode == 0
        assert "Nodes:" in r.stdout

        r = _run_cmd("--db", custom_db, "query", "Calculator", "--format", "json", cwd=str(repo))
        # Should not error out with exit 2 (db exists)
        assert r.returncode in (0, 1)

        r = _run_cmd("--db", custom_db, "enrich", "--dry-run", cwd=str(repo))
        # dry-run should succeed (exit 0 or 1) without needing API key
        assert r.returncode in (0, 1), f"enrich --dry-run failed: {r.stderr}"

        r = _run_cmd("--db", custom_db, "reset", "--yes", cwd=str(repo))
        assert r.returncode == 0

    def test_env_var_db_path(self, repo, tmp_path):
        custom_db = str(tmp_path / "envdb.db")
        env = {"CODEINDEX_DB": custom_db}

        r = _run_cmd("build", cwd=str(repo), env_override=env)
        assert r.returncode == 0
        assert Path(custom_db).exists()

        r = _run_cmd("status", cwd=str(repo), env_override=env)
        assert r.returncode == 0
        assert "Nodes:" in r.stdout


# ── (6) Status reflects pipeline state ───────────────────────────────


class TestStatusReflectsPipelineState:
    """Status output accurately reflects the current pipeline state."""

    def test_status_before_build(self, repo):
        """Status on uninitialised repo exits 1."""
        r = _run_cmd("status", cwd=str(repo))
        assert r.returncode == 1

    def test_status_after_build(self, repo):
        _run_cmd("build", cwd=str(repo))
        r = _run_cmd("status", cwd=str(repo))
        assert r.returncode == 0
        # Should reflect non-zero node count
        assert "Nodes:" in r.stdout
        # Unenriched should equal total nodes (no enrichment done)
        lines = r.stdout.strip().splitlines()
        node_line = [l for l in lines if l.startswith("Nodes:")][0]
        unenriched_line = [l for l in lines if l.startswith("Unenriched:")][0]
        node_count = int(node_line.split(":")[1].strip())
        unenriched_count = int(unenriched_line.split(":")[1].strip())
        assert node_count == unenriched_count
        assert node_count > 0

    def test_status_after_reset(self, repo):
        _run_cmd("build", cwd=str(repo))
        _run_cmd("reset", "--yes", cwd=str(repo))
        r = _run_cmd("status", cwd=str(repo))
        assert r.returncode == 0
        assert "Nodes:            0" in r.stdout


# ── (7) Exit code consistency ────────────────────────────────────────


class TestExitCodeConsistency:
    """All commands use consistent exit codes: 0=clean, 1=warning, 2=fatal."""

    def test_init_clean(self, repo):
        r = _run_cmd("init", cwd=str(repo))
        assert r.returncode == 0

    def test_build_clean(self, repo):
        r = _run_cmd("build", cwd=str(repo))
        assert r.returncode == 0

    def test_status_missing_db(self, repo):
        r = _run_cmd("status", cwd=str(repo))
        assert r.returncode == 1

    def test_query_no_args(self, repo):
        r = _run_cmd("query", cwd=str(repo))
        assert r.returncode == 2

    def test_reset_no_confirm(self, repo):
        _run_cmd("build", cwd=str(repo))
        r = _run_cmd("reset", cwd=str(repo))
        # Non-TTY without --yes → exit 2
        assert r.returncode == 2

    def test_build_with_warnings(self, repo):
        """Build with syntax error file produces exit 1."""
        # Re-add syntax error file
        shutil.copy2(FIXTURES_DIR / "syntax_error.py", repo / "syntax_error.py")
        r = _run_cmd("build", cwd=str(repo))
        assert r.returncode == 1


# ── (8) Build lock prevents concurrent execution ─────────────────────


class TestBuildLockPrevents:
    """An active flock blocks a second build."""

    def test_concurrent_build_blocked(self, repo):
        db_dir = repo / ".codeindex"
        db_dir.mkdir(exist_ok=True)
        lock = db_dir / "build.lock"
        # Hold an actual flock to simulate a concurrent build
        fd = open(lock, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            r = _run_cmd("build", cwd=str(repo))
            assert r.returncode == 2
            assert "Another build is running" in r.stderr
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()


# ── (9) Gitignore managed across init and build ──────────────────────


class TestGitignoreManagement:
    """.codeindex/ added to .gitignore by both init and build."""

    def test_init_adds_gitignore(self, repo):
        gi = repo / ".gitignore"
        gi.unlink(missing_ok=True)
        _run_cmd("init", cwd=str(repo))
        assert ".codeindex/" in gi.read_text()

    def test_build_adds_gitignore(self, repo):
        gi = repo / ".gitignore"
        gi.unlink(missing_ok=True)
        _run_cmd("build", cwd=str(repo))
        assert ".codeindex/" in gi.read_text()

    def test_no_duplicate_gitignore_entries(self, repo):
        _run_cmd("init", cwd=str(repo))
        _run_cmd("build", cwd=str(repo))
        _run_cmd("init", cwd=str(repo))
        gi = repo / ".gitignore"
        lines = [l.strip() for l in gi.read_text().splitlines() if l.strip() == ".codeindex/"]
        assert len(lines) == 1


# ── (10) FTS5 consistency after incremental rebuild ──────────────────


class TestFTS5Consistency:
    """FTS5 index stays consistent after incremental rebuilds."""

    def test_fts5_after_incremental(self, repo):
        _run_cmd("build", cwd=str(repo))

        conn = _connect(repo)
        fts_before = conn.execute(
            "SELECT COUNT(*) FROM nodes_fts"
        ).fetchone()[0]
        conn.close()

        # Modify and rebuild
        sample = repo / "sample.py"
        sample.write_text(
            sample.read_text() + "\ndef searchable_func():\n    '''A searchable docstring.'''\n    pass\n"
        )
        _run_cmd("build", cwd=str(repo))

        conn = _connect(repo)
        fts_after = conn.execute(
            "SELECT COUNT(*) FROM nodes_fts"
        ).fetchone()[0]
        conn.close()

        # Should have more entries after adding a function
        assert fts_after >= fts_before

    def test_fts5_integrity_check(self, repo):
        """FTS5 integrity-check passes after build."""
        _run_cmd("build", cwd=str(repo))
        conn = _connect(repo)
        # integrity-check returns 'ok' row if healthy
        result = conn.execute(
            "INSERT INTO nodes_fts(nodes_fts) VALUES('integrity-check')"
        )
        conn.close()
        # If we get here without error, FTS5 is consistent


# ── (11) Multi-language build with cross-language edges ──────────────


class TestMultiLanguageBuild:
    """Build indexes Python, Kotlin, and TypeScript with cross-language edges."""

    def test_all_languages_indexed(self, repo):
        r = _run_cmd("build", cwd=str(repo))
        assert r.returncode == 0, r.stderr

        conn = _connect(repo)
        files = [r[0] for r in conn.execute("SELECT path FROM files").fetchall()]
        conn.close()

        extensions = {Path(f).suffix for f in files}
        assert ".py" in extensions
        assert ".kt" in extensions
        assert ".ts" in extensions

    def test_nodes_from_all_languages(self, repo):
        _run_cmd("build", cwd=str(repo))
        conn = _connect(repo)
        # Check we have nodes from each language file
        py_nodes = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path LIKE '%.py'"
        ).fetchone()[0]
        kt_nodes = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path LIKE '%.kt'"
        ).fetchone()[0]
        ts_nodes = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path LIKE '%.ts'"
        ).fetchone()[0]
        conn.close()

        assert py_nodes > 0, "No Python nodes"
        assert kt_nodes > 0, "No Kotlin nodes"
        assert ts_nodes > 0, "No TypeScript nodes"

    def test_cross_language_edges(self, repo):
        """Build should create edges across language boundaries where identifiers match."""
        _run_cmd("build", cwd=str(repo))
        conn = _connect(repo)
        edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        assert edges > 0, "Expected edges from dependency mapping"

        # Verify at least one edge links nodes from different languages
        cross_lang = conn.execute(
            """
            SELECT COUNT(*)
            FROM edges e
            JOIN nodes src ON e.source_id = src.id
            JOIN nodes tgt ON e.target_id = tgt.id
            WHERE src.language != tgt.language
            """
        ).fetchone()[0]
        conn.close()
        assert cross_lang > 0, (
            "Expected at least one cross-language edge (source and target in different languages)"
        )
