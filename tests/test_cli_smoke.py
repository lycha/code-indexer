"""Smoke tests for the CLI entry point."""

import subprocess
import sys


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


def test_init_runs():
    """index init exits 0 (now implemented, no longer a stub)."""
    result = subprocess.run(["index", "init"], capture_output=True, text=True)
    assert result.returncode == 0


def test_build_stub():
    """index build prints [TODO] to stderr and exits 0."""
    result = subprocess.run(["index", "build"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "[TODO]" in result.stderr


def test_enrich_stub():
    """index enrich prints [TODO] to stderr and exits 0."""
    result = subprocess.run(["index", "enrich"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "[TODO]" in result.stderr


def test_query_stub():
    """index query prints [TODO] to stderr and exits 0."""
    result = subprocess.run(["index", "query", ""], capture_output=True, text=True)
    assert result.returncode == 0
    assert "[TODO]" in result.stderr


def test_status_stub():
    """index status prints [TODO] to stderr and exits 0."""
    result = subprocess.run(["index", "status"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "[TODO]" in result.stderr


def test_reset_stub():
    """index reset prints [TODO] to stderr and exits 0."""
    result = subprocess.run(["index", "reset", "--yes"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "[TODO]" in result.stderr


def test_db_option_accepted():
    """--db PATH global option is accepted by all commands."""
    result = subprocess.run(
        ["index", "--db", "/tmp/test.db", "init"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


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
