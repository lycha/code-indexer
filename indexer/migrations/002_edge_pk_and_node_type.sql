-- 002_edge_pk_and_node_type.sql
-- 1. Add 'module' to nodes.node_type CHECK constraint
-- 2. Add call_site_line to edges PRIMARY KEY

-- Disable FK enforcement during table recreation
PRAGMA foreign_keys=OFF;

-- Step 1: Backup edges data
CREATE TABLE _edges_backup AS SELECT * FROM edges;

-- Step 2: Drop edges (has FK reference to nodes)
DROP TABLE edges;

-- Step 3: Drop FTS virtual table (references nodes content)
DROP TABLE IF EXISTS nodes_fts;

-- Step 4: Recreate nodes with 'module' added to CHECK constraint
CREATE TABLE _nodes_backup AS SELECT * FROM nodes;
DROP TABLE nodes;

CREATE TABLE nodes (
    id                      TEXT PRIMARY KEY,
    file_path               TEXT NOT NULL,
    node_type               TEXT NOT NULL CHECK (node_type IN ('file', 'class', 'function', 'method', 'interface', 'object', 'module')),
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

INSERT INTO nodes SELECT * FROM _nodes_backup;
DROP TABLE _nodes_backup;

-- Step 5: Recreate edges with call_site_line in PRIMARY KEY
CREATE TABLE edges (
    source_id       TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id       TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    edge_type       TEXT NOT NULL CHECK (edge_type IN ('calls', 'imports', 'inherits', 'overrides', 'references', 'instantiates')),
    call_site_line  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, target_id, edge_type, call_site_line)
);

INSERT INTO edges (source_id, target_id, edge_type, call_site_line)
SELECT source_id, target_id, edge_type, COALESCE(call_site_line, 0)
FROM _edges_backup;

DROP TABLE _edges_backup;

-- Step 6: Recreate FTS5 virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id UNINDEXED,
    qualified_name,
    semantic_summary,
    domain_tags,
    inferred_responsibility,
    content=nodes,
    content_rowid=rowid
);

-- Step 7: Recreate all indexes (dropped with old tables)
CREATE INDEX IF NOT EXISTS idx_nodes_file_path      ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_name           ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_node_type      ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_language       ON nodes(language);
CREATE INDEX IF NOT EXISTS idx_nodes_unenriched     ON nodes(enriched_at) WHERE enriched_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_edges_source         ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target         ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type           ON edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_source_type    ON edges(source_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_target_type    ON edges(target_id, edge_type);

-- Restore FK enforcement
PRAGMA foreign_keys=ON;
