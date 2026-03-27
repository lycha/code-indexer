"""Smoke tests for the CLI entry point."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _env_with_rg() -> dict:
    """Return env dict with ripgrep's directory on PATH."""
    env = os.environ.copy()
    rg = shutil.which("rg")
    if rg:
        rg_dir = str(Path(rg).parent)
        if rg_dir not in env.get("PATH", ""):
            env["PATH"] = f"{rg_dir}:{env['PATH']}"
    return env


def _bootstrap_index(tmp_path, env=None):
    """Create a minimal source file and run init+build in *tmp_path*."""
    (tmp_path / "hello.py").write_text("def hello():\n    return 'world'\n")
    if env is None:
        env = _env_with_rg()
    subprocess.run(
        ["index", "init"], cwd=str(tmp_path), capture_output=True, text=True, env=env,
    )
    subprocess.run(
        ["index", "build"], cwd=str(tmp_path), capture_output=True, text=True, env=env,
    )


# ── tests that need no isolation (read-only / error paths) ──────────


def test_help_lists_all_subcommands():
    """index --help lists all 6 subcommands."""
    result = subprocess.run(
        [sys.executable, "-m", "indexer.cli"],
        capture_output=True,
        text=True,
    )
    # click group with no subcommand shows help by default — but let's use --help explicitly
    result = subprocess.run(
        ["index", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for cmd in ("init", "build", "enrich", "query", "status", "reset"):
        assert cmd in result.stdout, f"Subcommand '{cmd}' not found in --help output"


def test_enrich_missing_api_key():
    """index enrich without ANTHROPIC_API_KEY exits 2."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    result = subprocess.run(["index", "enrich"], capture_output=True, text=True, env=env)
    assert result.returncode == 2
    assert "ANTHROPIC_API_KEY" in result.stderr


def test_query_no_input_exits_2():
    """index query with empty input exits 2."""
    result = subprocess.run(["index", "query"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "ERROR" in result.stderr


def test_build_help_shows_options():
    """index build --help shows command-specific options."""
    result = subprocess.run(
        ["index", "build", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--phase" in result.stdout
    assert "--token-limit" in result.stdout
    assert "--exclude" in result.stdout


# ── tests isolated to a temp directory ──────────────────────────────


def test_init_runs(tmp_path):
    """index init exits 0 (now implemented, no longer a stub)."""
    result = subprocess.run(
        ["index", "init"], capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 0


def test_build_runs(tmp_path):
    """index build runs (may exit 0 or 1 depending on warnings, never 2 if rg available)."""
    (tmp_path / "hello.py").write_text("def hello():\n    return 'world'\n")
    env = _env_with_rg()
    result = subprocess.run(
        ["index", "build"], capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert result.returncode in (0, 1)
    assert "[PHASE 1]" in result.stderr


def test_enrich_dry_run(tmp_path):
    """index enrich --dry-run exits 0 without API key."""
    _bootstrap_index(tmp_path)
    result = subprocess.run(
        ["index", "enrich", "--dry-run"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "nodes to enrich" in result.stderr


def test_status_shows_output(tmp_path):
    """index status prints status info to stdout and exits 0."""
    _bootstrap_index(tmp_path)
    result = subprocess.run(
        ["index", "status"], capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "Nodes:" in result.stdout


def test_reset_with_yes(tmp_path):
    """index reset --yes resets and exits 0."""
    _bootstrap_index(tmp_path)
    result = subprocess.run(
        ["index", "reset", "--yes"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "[RESET]" in result.stderr


def test_db_option_accepted(tmp_path):
    """--db PATH global option is accepted by all commands."""
    db_file = str(tmp_path / "test.db")
    result = subprocess.run(
        ["index", "--db", db_file, "init"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 0
