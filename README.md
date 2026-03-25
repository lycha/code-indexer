# Hybrid Code Indexing System

A Python CLI tool that builds a structured code index through three phases: deterministic AST parsing, ripgrep-based dependency mapping, and LLM semantic enrichment. The index is stored in a local SQLite database and supports lexical, graph, and semantic queries — giving agents and developers fast, structured access to codebase knowledge without injecting raw source into context windows.

## Installation

Requires **Python ≥ 3.11** and [ripgrep](https://github.com/BurntSushi/ripgrep) on `PATH`.

```bash
pip install -e .
```

## Quick Start

```bash
# 1. Initialise the database
index init

# 2. Parse source files and map dependencies
index build

# 3. Enrich nodes with LLM-generated semantic metadata
export ANTHROPIC_API_KEY="sk-..."
index enrich

# 4. Query the index
index query "validateCartState"
```

## Commands

### `index init`

Create the `.codeindex/` directory and initialise the database schema. No-op if the DB already exists and the schema version is current. Auto-invoked by `index build` if the DB does not yet exist.

| Option | Description |
|--------|-------------|
| `--db PATH` | Path to the SQLite database file |
| `--no-gitignore-update` | Skip automatic `.gitignore` update |

### `index build`

Run Phase 1 (AST parse) and Phase 2 (dependency mapping). Bootstraps the DB automatically if not yet initialised.

| Option | Description |
|--------|-------------|
| `--db PATH` | Path to the SQLite database file |
| `--phase PREPARE\|DEPLOY` | Tag this build with a phase boundary label |
| `--token-limit N` | Max tokens per cAST chunk (default: 512) |
| `--exclude PATTERN` | Glob patterns to exclude from parsing (repeatable) |
| `--no-gitignore-update` | Skip automatic `.gitignore` update |

```bash
index build --phase PREPARE --exclude "vendor/*"
```

### `index enrich`

Run Phase 3 — LLM enrichment on unenriched nodes. Only re-enriches nodes whose `content_hash` has changed since the last run.

| Option | Description |
|--------|-------------|
| `--db PATH` | Path to the SQLite database file |
| `--dry-run` | Show what would be enriched without making API calls |
| `--model MODEL` | Override the LLM model for enrichment |

```bash
index enrich --dry-run
```

### `index query`

Query the code index. The query router auto-selects a strategy (lexical, graph, or semantic) based on input, with cross-strategy fallback when results are empty.

| Option | Description |
|--------|-------------|
| `--db PATH` | Path to the SQLite database file |
| `--type lexical\|graph\|semantic` | Force a specific query strategy |
| `--format text\|json\|jsonl` | Output format (default: `text` for TTY, `json` otherwise) |
| `--with-source` | Include raw source in results |
| `--top-k N` | Maximum number of results (default: 10) |
| `--depth N` | Graph traversal depth (default: 2) |

```bash
# Human-readable lexical lookup
index query "CartService" --type lexical --with-source

# Structured output for agent consumption
index query "cart loses items after discount" --type semantic --format jsonl
```

### `index status`

Show index health: node count, edge count, unenriched nodes, last build time, and schema version.

| Option | Description |
|--------|-------------|
| `--db PATH` | Path to the SQLite database file |

### `index reset`

Drop and recreate all database tables.

| Option | Description |
|--------|-------------|
| `--db PATH` | Path to the SQLite database file |
| `--yes`, `-y` | Skip confirmation prompt (required for non-interactive use) |

```bash
index reset --yes && index build --phase PREPARE
```

## Architecture

The indexing pipeline runs in three phases:

1. **AST Parse** — Extracts files, classes, functions, methods, signatures, docstrings, and line ranges using Python's `ast` module (for `.py` files) and `tree-sitter` (for Kotlin and TypeScript). Large nodes are split into chunks within a configurable token limit (cAST split-merge).

2. **Dependency Map** — For each node, runs `ripgrep` to find all call sites and identifier references across the codebase, then resolves import statements to target nodes. Writes directed edges (`calls`, `imports`, `inherits`, `overrides`, `references`, `instantiates`) into the graph.

3. **LLM Enrich** — Sends each node's signature, docstring, and immediate graph neighbours to the Claude API. Receives back a `semantic_summary`, `domain_tags`, and `inferred_responsibility`. Only re-runs on nodes whose content hash has changed (hash-gated).

The resulting SQLite database (`.codeindex/codeindex.db`) supports three query paths:

- **Lexical** — ripgrep identifier match with re-ranking
- **Graph** — SQLite edge traversal with configurable depth
- **Semantic** — FTS5 full-text search over enriched metadata

All progress and diagnostic output goes to **stderr**; only structured query results go to **stdout**.

## Supported Languages

| Language | Parser |
|----------|--------|
| Python | `ast` (stdlib) |
| Kotlin | `tree-sitter-kotlin` |
| TypeScript | `tree-sitter-typescript` |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | For `enrich` | Anthropic API key for LLM enrichment |
| `CODEINDEX_DB` | No | Override default database path (`.codeindex/codeindex.db`) |

Database path resolution order: `--db` flag → `CODEINDEX_DB` env var → `.codeindex/codeindex.db` → exit 2.

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success — all phases completed without warnings |
| `1` | Completed with warnings (e.g. parse errors, unenriched nodes) |
| `2` | Fatal error (e.g. ripgrep missing, DB locked, schema mismatch) |

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
python3 -m pytest tests/ -v
```
