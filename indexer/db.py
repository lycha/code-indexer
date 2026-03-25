"""Database connection management and bootstrap logic."""

import os
import sqlite3
import sys
from pathlib import Path

import click

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return a connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_migration_files() -> list[tuple[int, Path]]:
    """Return sorted list of (version, path) for all migration SQL files."""
    migrations: list[tuple[int, Path]] = []
    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        prefix = sql_file.stem.split("_", 1)[0]
        try:
            version = int(prefix)
        except ValueError:
            continue
        migrations.append((version, sql_file))
    return migrations


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Return current schema version, or 0 if index_meta doesn't exist."""
    try:
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def bootstrap(db_path: str) -> None:
    """Create the database directory (if needed), run pending migrations, set schema_version.

    No-op if schema is already current. Exits with code 2 on downgrade detection.
    """
    # Create .codeindex/ directory for file-based databases
    if db_path != ":memory:":
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    try:
        current_version = _get_schema_version(conn)
        migrations = _get_migration_files()

        if not migrations:
            return

        max_migration_version = migrations[-1][0]

        # Downgrade detection
        if current_version > max_migration_version:
            click.echo(
                f"[ERROR] Schema version mismatch: database is v{current_version} "
                f"but code supports up to v{max_migration_version}. "
                f"This database was created by a newer version of the tool. "
                f"Please upgrade the tool or run 'index reset --yes' to rebuild.",
                err=True,
            )
            sys.exit(2)

        # No-op if already current
        if current_version >= max_migration_version:
            return

        # Apply pending migrations
        for version, sql_path in migrations:
            if version <= current_version:
                continue
            sql = sql_path.read_text()
            conn.executescript(sql)
            click.echo(
                f"[SETUP] Applied migration {sql_path.name} (v{version})",
                err=True,
            )

        # Set schema_version
        conn.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('schema_version', ?)",
            (str(max_migration_version),),
        )
        conn.commit()
    finally:
        conn.close()


def resolve_db_path(db_arg: str | None) -> str:
    """Resolve the database path using 4-step resolution.

    1. --db argument
    2. CODEINDEX_DB environment variable
    3. .codeindex/codeindex.db (default)
    4. Exit 2 with actionable message
    """
    # Step 1: explicit --db argument
    if db_arg:
        return db_arg

    # Step 2: CODEINDEX_DB environment variable
    env_path = os.environ.get("CODEINDEX_DB")
    if env_path:
        return env_path

    # Step 3: default path
    default_path = Path(".codeindex") / "codeindex.db"
    return str(default_path)
