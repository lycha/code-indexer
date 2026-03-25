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

## Tutorial

This walkthrough indexes a real project from scratch and shows how to use every major feature.

### Step 1: Install and verify prerequisites

```bash
pip install -e .

# Install ripgrep and other external dependencies automatically
index install
```

### Step 2: Index your project

Navigate to your project root and run the full pipeline:

```bash
cd /path/to/your/project

# Build the index (init is automatic)
index build
```

This creates a `.codeindex/` directory containing the SQLite database. The build runs two phases: AST parsing extracts every file, class, function, and method, then ripgrep maps all call-site and import relationships between them.

To exclude vendored or generated code:

```bash
index build --exclude "vendor/*" --exclude "generated/*"
```

### Step 3: Check index health

```bash
index status
```

Example output:

```
Nodes:            142
Edges:            387
Unenriched:       142
Last build:       2026-03-25T10:15:00+00:00
Schema version:   3
DB path:          .codeindex/codeindex.db
```

The `Unenriched: 142` line means no nodes have semantic metadata yet — that comes next.

### Step 4: Enrich with LLM metadata (optional)

This step calls the Claude API to generate summaries, domain tags, and inferred responsibilities for each node. It requires an API key:

```bash
export ANTHROPIC_API_KEY="sk-..."

# Preview what will be enriched
index enrich --dry-run

# Run enrichment
index enrich
```

Enrichment is hash-gated: re-running `index enrich` after code changes only processes nodes whose content actually changed.

#### Phase 3 Enrichment — Cost Model
**First-run cost (one-time per repository):** enriching a ~14,000-node codebase with Claude Sonnet costs approximately $42–67 depending on average node size. This is paid once when you first index a repository.
**Incremental cost (every subsequent run):** the indexer is hash-gated. Phase 1 clears `enriched_at` only on nodes whose `content_hash` changed. Phase 3 then only processes those nodes. On a normally-evolving codebase where a sprint touches 1–2% of nodes, a rebuild enrichment run costs under $5 — often under $1.

**What drives cost up:**
- Large-scale refactors that invalidate many `content_hash` values in one go
- Onboarding many repositories (each pays the first-run cost once)
- Branch switches between long-lived divergent branches

**If Phase 3 cost is a concern**, run `index enrich --dry-run first` — it reports the number of unenriched nodes before making any API calls. You can also skip Phase 3 entirely; the structural index (Phase 1+2) still provides AST nodes and dependency graph context at zero LLM cost.

### Step 5: Query the index

**Find a symbol by name** (lexical search):

```bash
index query "UserService"
```

**Explore a node's dependency graph**:

```bash
index query "UserService.validate" --type graph --depth 3
```

**Ask a natural-language question** (semantic search — requires enrichment):

```bash
index query "where is authentication handled" --type semantic
```

**Get machine-readable output for scripts or agents**:

```bash
index query "CartService" --format json --with-source
```

The query router automatically picks the best strategy (lexical, graph, or semantic) when `--type` is omitted, and falls back to an alternative strategy if the first returns no results.

### Step 6: Rebuild after code changes

```bash
index build
```

The build is incremental at the enrichment layer — only changed nodes need re-enrichment. To start completely fresh:

```bash
index reset --yes
index build
```

### Typical workflow summary

```bash
index build                          # parse + map dependencies
index enrich                         # add semantic metadata (optional)
index query "MyClass"                # find symbols
index query "how does auth work"     # semantic search
index status                         # check health
```

## Commands

### `index install`

Install external dependencies required by the indexer. Currently installs [ripgrep](https://github.com/BurntSushi/ripgrep) using the system package manager (Homebrew on macOS, apt/dnf/pacman on Linux, Chocolatey/Scoop on Windows). No-op if all dependencies are already present.

```bash
index install
```

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
| Java | `tree-sitter-java` |
| Ruby | `tree-sitter-ruby` |

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
