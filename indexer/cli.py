"""CLI entry point for the code indexer."""

import json
import os
import platform
import shutil
import subprocess
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


@cli.command()
@click.pass_context
def install(ctx: click.Context) -> None:
    """Install external dependencies (ripgrep)."""
    if shutil.which("rg"):
        click.echo("[OK] ripgrep is already installed.", err=True)
        return

    system = platform.system()
    click.echo("[INSTALL] ripgrep not found. Installing...", err=True)

    commands: list[tuple[str, list[str]]] = []
    if system == "Darwin":
        if shutil.which("brew"):
            commands.append(("Homebrew", ["brew", "install", "ripgrep"]))
        else:
            click.echo("[ERROR] Homebrew not found. Install ripgrep manually: https://github.com/BurntSushi/ripgrep#installation", err=True)
            sys.exit(2)
    elif system == "Linux":
        if shutil.which("apt-get"):
            commands.append(("apt", ["sudo", "apt-get", "install", "-y", "ripgrep"]))
        elif shutil.which("dnf"):
            commands.append(("dnf", ["sudo", "dnf", "install", "-y", "ripgrep"]))
        elif shutil.which("pacman"):
            commands.append(("pacman", ["sudo", "pacman", "-S", "--noconfirm", "ripgrep"]))
        else:
            click.echo("[ERROR] No supported package manager found. Install ripgrep manually: https://github.com/BurntSushi/ripgrep#installation", err=True)
            sys.exit(2)
    elif system == "Windows":
        if shutil.which("choco"):
            commands.append(("Chocolatey", ["choco", "install", "-y", "ripgrep"]))
        elif shutil.which("scoop"):
            commands.append(("Scoop", ["scoop", "install", "ripgrep"]))
        else:
            click.echo("[ERROR] No supported package manager found. Install ripgrep manually: https://github.com/BurntSushi/ripgrep#installation", err=True)
            sys.exit(2)
    else:
        click.echo(f"[ERROR] Unsupported platform: {system}. Install ripgrep manually: https://github.com/BurntSushi/ripgrep#installation", err=True)
        sys.exit(2)

    for label, cmd in commands:
        click.echo(f"[INSTALL] Using {label}: {' '.join(cmd)}", err=True)
        result = subprocess.run(cmd)
        if result.returncode != 0:
            click.echo(f"[ERROR] {label} install failed (exit {result.returncode}).", err=True)
            sys.exit(2)

    if shutil.which("rg"):
        click.echo("[OK] ripgrep installed successfully.", err=True)
    else:
        click.echo("[ERROR] ripgrep installation did not place 'rg' on PATH.", err=True)
        sys.exit(2)


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
        t0 = time.monotonic()
        warnings = parse_directory(repo_root, conn, token_limit=token_limit, exclude_patterns=list(exclude) if exclude else None)
        phase1_elapsed = time.monotonic() - t0
        click.echo(f"[PHASE 1] Done in {phase1_elapsed:.1f}s", err=True)
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
        t0 = time.monotonic()
        edges_inserted = map_dependencies(all_node_ids, conn, str(repo_root))
        phase2_elapsed = time.monotonic() - t0
        click.echo(f"[PHASE 2] Done in {phase2_elapsed:.1f}s", err=True)

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
@click.option("--provider", type=click.Choice(["anthropic", "openai", "openrouter", "litellm"]), default=None, help="LLM provider (auto-detected from env vars if omitted).")
@click.pass_context
def enrich(ctx: click.Context, dry_run: bool, model: str | None, provider: str | None) -> None:
    """Enrich code nodes with LLM-generated semantic metadata."""
    from indexer.enricher import enrich_nodes

    db_path = resolve_db_path(ctx.obj.get("db_path"))
    bootstrap(db_path)
    conn = get_connection(db_path)
    try:
        t0 = time.monotonic()
        exit_code = enrich_nodes(conn, model=model, dry_run=dry_run, provider=provider)
        elapsed = time.monotonic() - t0
        click.echo(f"[PHASE 3] Done in {elapsed:.1f}s", err=True)
    finally:
        conn.close()
    if exit_code:
        sys.exit(exit_code)


@cli.command()
@click.argument("query_text", default="")
@click.option("--type", "query_type", type=click.Choice(["lexical", "graph", "semantic"]), default=None, help="Query type.")
@click.option("--format", "output_format", type=click.Choice(["text", "json", "jsonl"]), default=None, help="Output format.")
@click.option("--with-source", is_flag=True, default=False, help="Include raw source in results.")
@click.option("--top-k", type=int, default=10, help="Maximum number of results.")
@click.option("--depth", type=int, default=2, help="Graph traversal depth.")
@click.pass_context
def query(ctx: click.Context, query_text: str, query_type: str | None, output_format: str | None, with_source: bool, top_k: int, depth: int) -> None:
    """Query the code index."""
    from indexer.query import (
        format_results,
        graph_search,
        lexical_search,
        route_query,
        semantic_search,
    )

    if not query_text:
        click.echo("[ERROR] No query text provided.", err=True)
        sys.exit(2)

    # Auto-detect output format based on TTY
    if output_format is None:
        output_format = "text" if sys.stdout.isatty() else "json"

    db_path = resolve_db_path(ctx.obj.get("db_path"))

    # Check DB exists
    if db_path != ":memory:" and not Path(db_path).exists():
        click.echo("[ERROR] Index not found. Run 'index init' or 'index build' first.", err=True)
        sys.exit(1)

    try:
        conn = get_connection(db_path)
    except Exception as e:
        click.echo(f"[ERROR] Database error: {e}", err=True)
        sys.exit(2)

    try:
        strategy = route_query(query_text, query_type)
        click.echo(f"[QUERY] Strategy: {strategy}", err=True)

        results = None

        if strategy == "graph":
            results = graph_search(
                node_id=query_text, conn=conn, depth=depth, with_source=with_source,
            )
            if results is None:
                click.echo("[WARNING] Node not found for graph search.", err=True)
        elif strategy == "lexical":
            repo_root = str(Path.cwd())
            results = lexical_search(
                identifier=query_text, conn=conn, repo_root=repo_root,
                top_k=top_k, with_source=with_source,
            )
            # Fallback: lexical → semantic
            if not results:
                click.echo("[QUERY] Lexical returned empty, falling back to semantic.", err=True)
                results = semantic_search(
                    query=query_text, conn=conn, top_k=top_k, with_source=with_source,
                )
        elif strategy == "semantic":
            # Check for enrichment
            enriched = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NOT NULL"
            ).fetchone()[0]
            if enriched == 0:
                total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                if total == 0:
                    click.echo("[ERROR] Index is empty. Run 'index build' first.", err=True)
                    sys.exit(1)
                click.echo("[WARNING] No enriched nodes. Semantic results may be poor. Run 'index enrich'.", err=True)

            results = semantic_search(
                query=query_text, conn=conn, top_k=top_k, with_source=with_source,
            )
            # Fallback: semantic → lexical
            if not results:
                click.echo("[QUERY] Semantic returned empty, falling back to lexical.", err=True)
                repo_root = str(Path.cwd())
                results = lexical_search(
                    identifier=query_text, conn=conn, repo_root=repo_root,
                    top_k=top_k, with_source=with_source,
                )

        output = format_results(results, output_format)
        if output:
            click.echo(output)

    except Exception as e:
        click.echo(f"[ERROR] Query failed: {e}", err=True)
        sys.exit(2)
    finally:
        conn.close()


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show index status and statistics."""
    from indexer.db import _get_migration_files, _get_schema_version

    db_path = resolve_db_path(ctx.obj.get("db_path"))

    # Check if DB exists
    if db_path != ":memory:" and not Path(db_path).exists():
        click.echo("[INFO] Index not initialised. Run: index build", nl=True)
        sys.exit(1)

    conn = get_connection(db_path)
    try:
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        unenriched = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NULL"
        ).fetchone()[0]

        # Metadata lookups
        def _meta(key: str) -> str:
            row = conn.execute(
                "SELECT value FROM index_meta WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else "N/A"

        last_build = _meta("last_full_build")
        phase_boundary = _meta("last_phase_boundary")
        schema_version = _get_schema_version(conn)
        migrations = _get_migration_files()
        max_version = migrations[-1][0] if migrations else 0

        click.echo(f"Nodes:            {node_count}")
        click.echo(f"Edges:            {edge_count}")
        click.echo(f"Unenriched:       {unenriched}")
        click.echo(f"Last build:       {last_build}")
        click.echo(f"Phase boundary:   {phase_boundary}")
        click.echo(f"Schema version:   {schema_version}")
        click.echo(f"DB path:          {db_path}")

        if schema_version > max_version:
            click.echo(
                f"[WARNING] Schema version mismatch: DB is v{schema_version} "
                f"but code supports v{max_version}. "
                f"Upgrade the tool or run 'index reset --yes'."
            )
    finally:
        conn.close()


@cli.command()
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_context
def reset(ctx: click.Context, yes: bool) -> None:
    """Reset the code index by dropping and recreating all tables."""
    db_path = resolve_db_path(ctx.obj.get("db_path"))

    # Check if DB exists
    if db_path != ":memory:" and not Path(db_path).exists():
        click.echo("[INFO] Index not initialised. Nothing to reset.", err=True)
        return

    # Confirmation logic
    if not yes:
        if not sys.stdin.isatty():
            click.echo(
                "[ERROR] Non-interactive context requires --yes/-y flag to confirm reset.",
                err=True,
            )
            sys.exit(2)
        # Interactive TTY prompt
        click.echo(
            "This will delete all indexed data. Continue? [y/N] ",
            err=True,
            nl=False,
        )
        answer = input().strip().lower()
        if answer != "y":
            click.echo("[INFO] Reset cancelled.", err=True)
            return

    conn = get_connection(db_path)
    try:
        # Drop tables in reverse dependency order
        for table in ["edges", "nodes_fts", "nodes", "files", "index_meta"]:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()

    # Recreate via bootstrap
    bootstrap(db_path)
    click.echo("[RESET] Index has been reset successfully.", err=True)
