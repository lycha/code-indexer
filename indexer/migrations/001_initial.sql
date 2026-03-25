-- 001_initial.sql — Full DDL for the hybrid code indexing system

-- Core node table: one row per syntactically complete code unit
CREATE TABLE nodes (
    id                      TEXT PRIMARY KEY,
    file_path               TEXT NOT NULL,
    node_type               TEXT NOT NULL CHECK (node_type IN ('file', 'class', 'function', 'method', 'interface', 'object')),
    name                    TEXT NOT NULL,
    qualified_name          TEXT NOT NULL,
    signature               TEXT,
    docstring               TEXT,
    start_line              INTEGER NOT NULL,
    end_line                INTEGER NOT NULL,
    language                TEXT NOT NULL,
    raw_source              TEXT,
    content_hash            TEXT NOT NULL,
    semantic_summary        TEXT,
    domain_tags             TEXT,
    inferred_responsibility TEXT,
    enriched_at             TEXT,
    enrichment_model        TEXT
);

-- Dependency edge table: directed graph of code relationships
CREATE TABLE edges (
    source_id               TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id               TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    edge_type               TEXT NOT NULL CHECK (edge_type IN ('calls', 'imports', 'inherits', 'overrides', 'references', 'instantiates')),
    call_site_line          INTEGER,
    PRIMARY KEY (source_id, target_id, edge_type)
);

-- File registry: change detection and language tracking
CREATE TABLE files (
    path                    TEXT PRIMARY KEY,
    last_modified           TEXT NOT NULL,
    content_hash            TEXT NOT NULL,
    language                TEXT NOT NULL,
    node_count              INTEGER DEFAULT 0,
    indexed_at              TEXT NOT NULL
);

-- Full-text search virtual table for semantic queries
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    id UNINDEXED,
    qualified_name,
    semantic_summary,
    domain_tags,
    inferred_responsibility,
    content=nodes,
    content_rowid=rowid
);

-- Index build metadata
CREATE TABLE index_meta (
    key                     TEXT PRIMARY KEY,
    value                   TEXT NOT NULL
);

-- Node lookups
CREATE INDEX idx_nodes_file_path      ON nodes(file_path);
CREATE INDEX idx_nodes_name           ON nodes(name);
CREATE INDEX idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX idx_nodes_node_type      ON nodes(node_type);
CREATE INDEX idx_nodes_language       ON nodes(language);
CREATE INDEX idx_nodes_unenriched     ON nodes(enriched_at) WHERE enriched_at IS NULL;

-- Edge traversal (both directions needed for dependency graph)
CREATE INDEX idx_edges_source         ON edges(source_id);
CREATE INDEX idx_edges_target         ON edges(target_id);
CREATE INDEX idx_edges_type           ON edges(edge_type);
CREATE INDEX idx_edges_source_type    ON edges(source_id, edge_type);
CREATE INDEX idx_edges_target_type    ON edges(target_id, edge_type);
