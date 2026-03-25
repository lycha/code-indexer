"""CLI entry point for the code indexer."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from indexer.db import bootstrap, get_connection, resolve_db_path


@click.group()
@click.option("--db", "db_path", type=click.Path(), default=None, help="Path to the SQLite database file.")
@click.pass_context
def cli(ctx: click.Context, db_path: str | None) -> None:
    """Hybrid code indexing system — build, enrich, and query a code index."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path


def _update_gitignore() -> None:
    """Append .codeindex/ to .gitignore if not already present."""
    gitignore_path = Path(".gitignore")
    entry = ".codeindex/"

    if gitignore_path.exists():
        content = gitignore_path.read_text()
        # Check if already present (exact line match)
        lines = content.splitlines()
        if any(line.strip() == entry for line in lines):
            return
        # Append with newline separator if file doesn't end with one
        if content and not content.endswith("\n"):
            gitignore_path.write_text(content + "\n" + entry + "\n")
        else:
            with gitignore_path.open("a") as f:
                f.write(entry + "\n")
    else:
        gitignore_path.write_text(entry + "\n")

    click.echo(f"[SETUP] Added {entry} to .gitignore", err=True)


@cli.command()
@click.option("--no-gitignore-update", is_flag=True, default=False, help="Skip automatic .gitignore update.")
@click.pass_context
def init(ctx: click.Context, no_gitignore_update: bool) -> None:
    """Initialise the code index database."""
    db_path = resolve_db_path(ctx.obj.get("db_path"))
    bootstrap(db_path)
    if not no_gitignore_update:
        _update_gitignore()


def _acquire_lock(lock_path: Path) -> None:
    """Acquire build lock file. Exits 2 if concurrent build detected."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        # Check if stale (>10 minutes)
        try:
            lock_data = json.loads(lock_path.read_text())
            started = lock_data.get("started", "")
            lock_time = datetime.fromisoformat(started)
            if lock_time.tzinfo is None:
                lock_time = lock_time.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - lock_time).total_seconds()
            if age_seconds > 600:
                lock_path.unlink()
                click.echo("[WARNING] Stale lock file removed", err=True)
            else:
                pid = lock_data.get("pid", "unknown")
                click.echo(
                    f"[ERROR] Another build is running (PID: {pid}). Exiting.",
                    err=True,
                )
                sys.exit(2)
        except (json.JSONDecodeError, ValueError, OSError):
            # Corrupt lock file — treat as stale
            lock_path.unlink(missing_ok=True)
            click.echo("[WARNING] Stale lock file removed", err=True)

    # Create lock file exclusively
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        lock_data = {
            "pid": os.getpid(),
            "started": datetime.now(timezone.utc).isoformat(),
        }
        os.write(fd, json.dumps(lock_data).encode())
        os.close(fd)
    except FileExistsError:
        click.echo(
            "[ERROR] Another build is running. Exiting.",
            err=True,
        )
        sys.exit(2)


def _release_lock(lock_path: Path) -> None:
    """Release build lock file."""
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


@cli.command()
@click.option("--phase", type=click.Choice(["PREPARE", "DEPLOY"]), default=None, help="Phase boundary tag.")
@click.option("--token-limit", type=int, default=512, help="Token limit for cAST chunking.")
@click.option("--exclude", multiple=True, help="Glob patterns to exclude from parsing.")
@click.option("--no-gitignore-update", is_flag=True, default=False, help="Skip automatic .gitignore update.")
@click.pass_context
def build(ctx: click.Context, phase: str | None, token_limit: int, exclude: tuple[str, ...], no_gitignore_update: bool) -> None:
    """Parse source files and map dependencies."""
    from indexer.mapper import map_dependencies
    from indexer.parser import parse_directory

    db_path = resolve_db_path(ctx.obj.get("db_path"))
    lock_path = Path(db_path).parent / "build.lock"

    # Auto-bootstrap DB
    bootstrap(db_path)
    if not no_gitignore_update:
        _update_gitignore()

    _acquire_lock(lock_path)
    exit_code = 0
    conn = None
    try:
        conn = get_connection(db_path)
        repo_root = Path.cwd()

        # Phase 1: Parse
        click.echo("[PHASE 1] Parsing source files...", err=True)
        warnings = parse_directory(repo_root, conn, token_limit=token_limit, exclude_patterns=list(exclude) if exclude else None)
        if warnings:
            for w in warnings:
                click.echo(f"[WARNING] {w}", err=True)
            exit_code = 1

        # Collect all node IDs for Phase 2
        all_node_ids = [
            r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()
        ]

        # Phase 2: Map dependencies
        click.echo("[PHASE 2] Mapping dependencies...", err=True)
        edges_inserted = map_dependencies(all_node_ids, conn, str(repo_root))

        # Update index_meta
        now = datetime.now(timezone.utc).isoformat()
        total_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        for key, value in [
            ("last_full_build", now),
            ("total_nodes", str(total_nodes)),
            ("total_edges", str(total_edges)),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
                (key, value),
            )

        if phase:
            conn.execute(
                "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
                ("last_phase_boundary", phase),
            )

        conn.commit()
        click.echo(
            f"[BUILD] Complete — {total_nodes} nodes, {total_edges} edges",
            err=True,
        )

    except SystemExit as e:
        # Re-raise SystemExit (from mapper._find_rg etc.)
        exit_code = e.code if isinstance(e.code, int) else 2
        raise
    except Exception as e:
        click.echo(f"[ERROR] Build failed: {e}", err=True)
        exit_code = 2
    finally:
        if conn:
            conn.close()
        _release_lock(lock_path)

    if exit_code:
        sys.exit(exit_code)


@cli.command()
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be enriched without making API calls.")
@click.option("--model", type=str, default=None, help="Override the LLM model for enrichment.")
@click.pass_context
def enrich(ctx: click.Context, dry_run: bool, model: str | None) -> None:
    """Enrich code nodes with LLM-generated semantic metadata."""
    from indexer.enricher import enrich_nodes

    db_path = resolve_db_path(ctx.obj.get("db_path"))
    bootstrap(db_path)
    conn = get_connection(db_path)
    try:
        exit_code = enrich_nodes(conn, model=model, dry_run=dry_run)
    finally:
        conn.close()
    if exit_code:
        sys.exit(exit_code)


@cli.command()
@click.argument("query_text", default="")
@click.option("--type", "query_type", type=click.Choice(["lexical", "graph", "semantic"]), default=None, help="Query type.")
@click.option("--format", "output_format", type=click.Choice(["text", "json", "jsonl"]), default="text", help="Output format.")
@click.option("--with-source", is_flag=True, default=False, help="Include raw source in results.")
@click.option("--top-k", type=int, default=10, help="Maximum number of results.")
@click.option("--depth", type=int, default=2, help="Graph traversal depth.")
@click.pass_context
def query(ctx: click.Context, query_text: str, query_type: str | None, output_format: str, with_source: bool, top_k: int, depth: int) -> None:
    """Query the code index."""
    click.echo("[TODO] query not yet implemented", err=True)


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show index status and statistics."""
    click.echo("[TODO] status not yet implemented", err=True)


@cli.command()
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_context
def reset(ctx: click.Context, yes: bool) -> None:
    """Reset the code index by dropping and recreating all tables."""
    click.echo("[TODO] reset not yet implemented", err=True)
