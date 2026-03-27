-- 003_fts5_sync_triggers.sql
-- Keep FTS5 external-content table in sync with nodes table via triggers.

-- Trigger: keep FTS5 in sync with nodes table on INSERT
CREATE TRIGGER IF NOT EXISTS nodes_fts_insert AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, qualified_name, semantic_summary, domain_tags, inferred_responsibility)
    VALUES (new.rowid, new.id, new.qualified_name, new.semantic_summary, new.domain_tags, new.inferred_responsibility);
END;

-- Trigger: keep FTS5 in sync on DELETE
CREATE TRIGGER IF NOT EXISTS nodes_fts_delete AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, qualified_name, semantic_summary, domain_tags, inferred_responsibility)
    VALUES ('delete', old.rowid, old.id, old.qualified_name, old.semantic_summary, old.domain_tags, old.inferred_responsibility);
END;

-- Trigger: keep FTS5 in sync on UPDATE
CREATE TRIGGER IF NOT EXISTS nodes_fts_update AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, qualified_name, semantic_summary, domain_tags, inferred_responsibility)
    VALUES ('delete', old.rowid, old.id, old.qualified_name, old.semantic_summary, old.domain_tags, old.inferred_responsibility);
    INSERT INTO nodes_fts(rowid, id, qualified_name, semantic_summary, domain_tags, inferred_responsibility)
    VALUES (new.rowid, new.id, new.qualified_name, new.semantic_summary, new.domain_tags, new.inferred_responsibility);
END;

-- Update schema version
INSERT OR REPLACE INTO index_meta (key, value) VALUES ('schema_version', '3');
