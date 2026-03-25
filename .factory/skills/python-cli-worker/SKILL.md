---
name: python-cli-worker
description: Implements Python CLI features for the code indexing tool — modules, commands, tests
---

# Python CLI Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for all features in the Hybrid Code Indexing System mission. This worker implements Python modules (parser, mapper, enricher, query, db, cli), CLI commands, database operations, and their corresponding tests.

## Required Skills

None

## Work Procedure

1. **Read the feature description and preconditions carefully.** Understand what must be implemented and what already exists. Read `AGENTS.md` for coding conventions and boundaries. Read relevant source files to understand existing patterns.

2. **Read the implementation plan and tech spec.** The feature description references specific tasks (T1-T8). Read `/Users/kjackowski/IdeaProjects/ai-os/code-indexer/docs/implementation-plan-code-indexing.md` and `/Users/kjackowski/IdeaProjects/ai-os/code-indexer/docs/tech-spec-code-indexing.md` for the full acceptance criteria and technical notes for your task. These documents are authoritative — follow them precisely.

3. **Write tests FIRST (red).** Create test files in `tests/` following the test patterns in the feature description. Use the shared `db_conn` fixture from `tests/conftest.py` when database access is needed. Tests must fail before implementation (TDD). For CLI smoke tests, use `subprocess.run()`. For unit tests, import modules directly.

4. **Implement to make tests pass (green).** Follow the coding conventions:
   - All progress/warnings to stderr via `click.echo(..., err=True)` — NEVER bare `print()`
   - Exit codes: 0=clean, 1=warnings, 2=fatal — use `sys.exit()` or `click` exit mechanisms
   - Node IDs: `{file_path}::{node_type}::{qualified_name}` with repo-relative paths
   - Migration paths: `pathlib.Path(__file__).parent / "migrations"` — NEVER CWD-relative
   - Content hashing: SHA-256 via `hashlib.sha256()`
   - Token estimate: `len(raw_source.split()) * 1.3`

5. **Run the full test suite.** Execute `python3 -m pytest tests/ -v` and ensure ALL tests pass — not just the ones you wrote. Fix any regressions.

6. **Manual verification.** After tests pass, manually verify the feature works end-to-end:
   - Install the package: `pip install -e .` (if not already installed)
   - Run the relevant CLI commands against test fixtures or the project itself
   - Verify exit codes, stdout/stderr separation, and expected output
   - For each manual check, record the exact command, what you observed, and whether it matched expectations

7. **Commit your work.** Stage all changed files, review the diff for completeness, and commit with a descriptive message referencing the task (e.g., "T1: Project scaffold and CLI skeleton").

## Example Handoff

```json
{
  "salientSummary": "Implemented index init command with full DB bootstrap, migration runner using __file__-relative paths, WAL mode, foreign keys, 4-step path resolution, .gitignore auto-append. Ran pytest (12 passing) and manually verified init creates .codeindex/, re-run is no-op, downgrade exits 2.",
  "whatWasImplemented": "db.py with bootstrap(), get_connection(), resolve_db_path(). 001_initial.sql with full DDL (nodes, edges, files, nodes_fts, index_meta + all indexes). CLI index init command wired. tests/conftest.py with db_conn fixture. tests/test_db.py with 8 test cases covering fresh create, no-op, upgrade, downgrade, gitignore append, path resolution.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "python3 -m pytest tests/ -v", "exitCode": 0, "observation": "12 tests passed, 0 failed, 0 errors"},
      {"command": "pip install -e .", "exitCode": 0, "observation": "Package installed successfully"},
      {"command": "cd /tmp/test-repo && index init", "exitCode": 0, "observation": "Created .codeindex/ directory, DB bootstrapped, .gitignore updated"},
      {"command": "cd /tmp/test-repo && index init", "exitCode": 0, "observation": "No-op, already initialized"},
      {"command": "sqlite3 /tmp/test-repo/.codeindex/codeindex.db '.tables'", "exitCode": 0, "observation": "edges  files  index_meta  nodes  nodes_fts"}
    ],
    "interactiveChecks": [
      {"action": "Ran index init on fresh directory", "observed": "Created .codeindex/, printed SETUP message to stderr, .gitignore updated"},
      {"action": "Ran index init again", "observed": "No output, exit 0 — idempotent"},
      {"action": "Checked WAL mode via PRAGMA", "observed": "journal_mode = wal, foreign_keys = 1"}
    ]
  },
  "tests": {
    "added": [
      {"file": "tests/test_db.py", "cases": [
        {"name": "test_fresh_bootstrap", "verifies": "DB created with all tables and schema_version=1"},
        {"name": "test_bootstrap_idempotent", "verifies": "Second bootstrap is no-op"},
        {"name": "test_migration_upgrade", "verifies": "Pending migrations applied"},
        {"name": "test_downgrade_detection", "verifies": "Exit 2 on schema version ahead of code"},
        {"name": "test_gitignore_append", "verifies": ".codeindex/ added to .gitignore"},
        {"name": "test_path_resolution", "verifies": "4-step resolution order"}
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Feature depends on a module or function that doesn't exist yet and isn't part of this feature's scope
- tree-sitter grammar fails to compile on this platform
- ripgrep binary not found at expected path and workaround unclear
- Requirements conflict with existing implementation
- Test suite has pre-existing failures unrelated to this feature
