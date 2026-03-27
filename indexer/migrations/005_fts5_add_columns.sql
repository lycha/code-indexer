-- 005_fts5_add_columns.sql
-- Add name, signature, docstring columns to FTS5 index for improved search coverage.

-- Drop existing sync triggers
DROP TRIGGER IF EXISTS nodes_fts_insert;
DROP TRIGGER IF EXISTS nodes_fts_delete;
DROP TRIGGER IF EXISTS nodes_fts_update;

-- Drop and recreate FTS5 table with additional columns
DROP TABLE IF EXISTS nodes_fts;

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id UNINDEXED,
    name,
    qualified_name,
    signature,
    docstring,
    semantic_summary,
    domain_tags,
    inferred_responsibility,
    content=nodes,
    content_rowid=rowid
);

-- Trigger: keep FTS5 in sync with nodes table on INSERT
CREATE TRIGGER IF NOT EXISTS nodes_fts_insert AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, signature, docstring, semantic_summary, domain_tags, inferred_responsibility)
    VALUES (new.rowid, new.id, new.name, new.qualified_name, new.signature, new.docstring, new.semantic_summary, new.domain_tags, new.inferred_responsibility);
END;

-- Trigger: keep FTS5 in sync on DELETE
CREATE TRIGGER IF NOT EXISTS nodes_fts_delete AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, signature, docstring, semantic_summary, domain_tags, inferred_responsibility)
    VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.signature, old.docstring, old.semantic_summary, old.domain_tags, old.inferred_responsibility);
END;

-- Trigger: keep FTS5 in sync on UPDATE
CREATE TRIGGER IF NOT EXISTS nodes_fts_update AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, signature, docstring, semantic_summary, domain_tags, inferred_responsibility)
    VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.signature, old.docstring, old.semantic_summary, old.domain_tags, old.inferred_responsibility);
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, signature, docstring, semantic_summary, domain_tags, inferred_responsibility)
    VALUES (new.rowid, new.id, new.name, new.qualified_name, new.signature, new.docstring, new.semantic_summary, new.domain_tags, new.inferred_responsibility);
END;

-- Rebuild FTS5 index from existing data
INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild');

-- Update schema version
INSERT OR REPLACE INTO index_meta (key, value) VALUES ('schema_version', '5');
