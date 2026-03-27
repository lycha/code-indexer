CREATE TABLE IF NOT EXISTS directory_summaries (
    dir_path            TEXT PRIMARY KEY,
    summary             TEXT,
    domain_tags         TEXT,
    responsibility      TEXT,
    child_count         INTEGER NOT NULL DEFAULT 0,
    enriched_at         TEXT,
    enrichment_model    TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS dir_summaries_fts USING fts5(
    dir_path UNINDEXED,
    summary,
    domain_tags,
    responsibility
);

CREATE TRIGGER IF NOT EXISTS dir_fts_insert AFTER INSERT ON directory_summaries BEGIN
    INSERT INTO dir_summaries_fts(rowid, dir_path, summary, domain_tags, responsibility)
    VALUES (new.rowid, new.dir_path, new.summary, new.domain_tags, new.responsibility);
END;

CREATE TRIGGER IF NOT EXISTS dir_fts_delete AFTER DELETE ON directory_summaries BEGIN
    INSERT INTO dir_summaries_fts(dir_summaries_fts, rowid, dir_path, summary, domain_tags, responsibility)
    VALUES ('delete', old.rowid, old.dir_path, old.summary, old.domain_tags, old.responsibility);
END;

CREATE TRIGGER IF NOT EXISTS dir_fts_update AFTER UPDATE ON directory_summaries BEGIN
    INSERT INTO dir_summaries_fts(dir_summaries_fts, rowid, dir_path, summary, domain_tags, responsibility)
    VALUES ('delete', old.rowid, old.dir_path, old.summary, old.domain_tags, old.responsibility);
    INSERT INTO dir_summaries_fts(rowid, dir_path, summary, domain_tags, responsibility)
    VALUES (new.rowid, new.dir_path, new.summary, new.domain_tags, new.responsibility);
END;

INSERT OR REPLACE INTO index_meta (key, value) VALUES ('schema_version', '4');
