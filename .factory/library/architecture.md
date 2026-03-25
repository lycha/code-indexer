# Architecture

Architectural decisions, patterns discovered during implementation.

**What belongs here:** Design decisions, module boundaries, data flow patterns, schema notes.

---

## Module Structure
```
indexer/
├── __init__.py
├── cli.py          — Click CLI entry point + all subcommands
├── db.py           — SQLite connection, bootstrap, migrations
├── parser.py       — Phase 1: AST parsing (ast + tree-sitter), cAST chunking
├── mapper.py       — Phase 2: GrepRAG dependency mapping via ripgrep
├── enricher.py     — Phase 3: LLM enrichment via Claude API
├── query.py        — Query router: lexical, graph, semantic search
└── migrations/
    └── 001_initial.sql  — Full DDL
```

## Data Flow
1. Phase 1 (parser.py): Source files → AST → nodes table + files table
2. Phase 2 (mapper.py): ripgrep identifier search → edges table + FTS5 rebuild
3. Phase 3 (enricher.py): Node context → Claude API → enriched fields + FTS5 update

## Key Patterns
- Hash-gating: content_hash in nodes table gates re-enrichment
- Incremental detection: content_hash in files table gates re-parsing
- enriched_at clearing: Phase 1 sets enriched_at=NULL when content_hash changes
- FTS5 external content: nodes_fts uses content=nodes, requires manual rebuild
- Lock file: .codeindex/build.lock with PID+timestamp, stale after 10min
