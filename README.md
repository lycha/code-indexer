# Hybrid Code Indexing System

A Python CLI tool that creates a hybrid code indexing system with three phases: deterministic AST parsing, GrepRAG dependency mapping, and LLM semantic enrichment.

## Installation

```bash
pip install -e .
```

## Usage

```bash
# Show available commands
index --help

# Initialise the database
index init

# Build the index (parse + map dependencies)
index build

# Enrich nodes with LLM metadata
index enrich

# Query the index
index query "search term"

# Show index status
index status

# Reset the index
index reset --yes
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
python3 -m pytest tests/ -v
```
