"""Tests for the query module — router, lexical, graph, semantic search."""

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from indexer.query import (
    DirectoryResult,
    EdgeResult,
    GraphResult,
    HierarchicalResult,
    NodeResult,
    format_results,
    graph_search,
    hierarchical_search,
    hierarchical_search_llm,
    hybrid_search,
    lexical_search,
    route_query,
    semantic_search,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _insert_sample_nodes(conn):
    """Insert sample nodes and edges for testing."""
    nodes = [
        ("src/app.py::function::parse_file", "src/app.py", "function", "parse_file",
         "parse_file", "def parse_file(path: str) -> dict", "Parse a single file.",
         1, 20, "python", "def parse_file(path):\n    pass", "abc123",
         "Parses a Python source file into an AST.", '["parsing", "ast"]',
         "Responsible for converting source files to AST nodes.",
         "2026-03-25T00:00:00Z", "claude-sonnet-4-6"),
        ("src/app.py::function::parse_directory", "src/app.py", "function", "parse_directory",
         "parse_directory", "def parse_directory(root: str) -> list", "Parse all files in a directory.",
         22, 50, "python", "def parse_directory(root):\n    pass", "def456",
         "Parses all files in a directory tree.", '["parsing", "directory"]',
         "Responsible for directory-level parsing orchestration.",
         "2026-03-25T00:00:00Z", "claude-sonnet-4-6"),
        ("src/db.py::function::get_connection", "src/db.py", "function", "get_connection",
         "get_connection", "def get_connection(path: str) -> Connection", "Get DB connection.",
         1, 10, "python", "def get_connection(path):\n    pass", "ghi789",
         "Returns a SQLite connection with WAL mode.", '["database", "sqlite"]',
         "Responsible for database connection management.",
         "2026-03-25T00:00:00Z", "claude-sonnet-4-6"),
        ("src/app.py::class::Parser", "src/app.py", "class", "Parser",
         "Parser", None, "Main parser class.",
         55, 100, "python", "class Parser:\n    pass", "jkl012",
         None, None, None, None, None),
    ]
    for n in nodes:
        conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, qualified_name, name, signature, "
            "docstring, start_line, end_line, language, raw_source, content_hash, "
            "semantic_summary, domain_tags, inferred_responsibility, enriched_at, enrichment_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            n,
        )

    edges = [
        ("src/app.py::function::parse_directory", "src/app.py::function::parse_file", "calls", 25),
        ("src/app.py::function::parse_file", "src/db.py::function::get_connection", "calls", 5),
        ("src/app.py::class::Parser", "src/app.py::function::parse_file", "references", 60),
    ]
    for e in edges:
        conn.execute(
            "INSERT INTO edges (source_id, target_id, edge_type, call_site_line) VALUES (?, ?, ?, ?)",
            e,
        )

    # Rebuild FTS
    conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
    conn.commit()


@pytest.fixture
def populated_db(db_conn):
    """DB with sample nodes, edges, and FTS data."""
    _insert_sample_nodes(db_conn)
    return db_conn


# ---------------------------------------------------------------------------
# Query Router
# ---------------------------------------------------------------------------

class TestQueryRouter:
    def test_identifier_routes_to_lexical(self):
        assert route_query("parse_file", None) == "lexical"

    def test_camelCase_routes_to_lexical(self):
        assert route_query("parseFile", None) == "lexical"

    def test_dotted_identifier_routes_to_lexical(self):
        assert route_query("Parser.parse_file", None) == "lexical"

    def test_natural_language_routes_to_semantic(self):
        assert route_query("parsing functions", None) == "semantic"

    def test_type_override_lexical(self):
        assert route_query("parsing functions", "lexical") == "lexical"

    def test_type_override_semantic(self):
        assert route_query("parse_file", "semantic") == "semantic"

    def test_type_override_graph(self):
        assert route_query("some_node_id", "graph") == "graph"

    def test_mixed_query_with_camelcase_routes_to_hybrid(self):
        # "parseFile" is camelCase identifier, "in" is not
        assert route_query("parseFile in directory", None) == "hybrid"

    def test_mixed_query_with_snake_case_routes_to_hybrid(self):
        # "parse_file" is snake_case identifier
        assert route_query("find parse_file usage", None) == "hybrid"

    def test_mixed_query_with_dotted_routes_to_hybrid(self):
        # "Parser.parse_file" is a dotted identifier
        assert route_query("Parser.parse_file callers", None) == "hybrid"

    def test_plain_words_route_to_semantic(self):
        # All tokens are plain words (no camelCase/snake_case/dotted), routes to semantic
        assert route_query("validate the cart", None) == "semantic"

    def test_all_plain_words_route_to_semantic(self):
        assert route_query("parsing functions", None) == "semantic"


# ---------------------------------------------------------------------------
# Hybrid Search
# ---------------------------------------------------------------------------

class TestHybridSearch:
    def test_hybrid_returns_results(self, populated_db, monkeypatch, tmp_path):
        """Hybrid search merges lexical + semantic results."""
        # Mock ripgrep for the lexical part
        rg_output = json.dumps({
            "type": "match",
            "data": {
                "path": {"text": "src/app.py"},
                "line_number": 5,
            },
        })

        def mock_run(cmd, **kwargs):
            class Result:
                stdout = rg_output
                stderr = ""
                returncode = 0
            return Result()

        monkeypatch.setattr("indexer.query.subprocess.run", mock_run)
        monkeypatch.setattr("indexer.query.find_rg", lambda: "/usr/bin/rg")

        results = hybrid_search(
            "parse_file in directory", populated_db, str(tmp_path), top_k=10,
        )
        assert len(results) > 0

    def test_hybrid_deduplicates(self, populated_db, monkeypatch, tmp_path):
        """Hybrid search does not return duplicate nodes."""
        rg_output = json.dumps({
            "type": "match",
            "data": {
                "path": {"text": "src/app.py"},
                "line_number": 5,
            },
        })

        def mock_run(cmd, **kwargs):
            class Result:
                stdout = rg_output
                stderr = ""
                returncode = 0
            return Result()

        monkeypatch.setattr("indexer.query.subprocess.run", mock_run)
        monkeypatch.setattr("indexer.query.find_rg", lambda: "/usr/bin/rg")

        results = hybrid_search(
            "parse_file parsing", populated_db, str(tmp_path), top_k=10,
        )
        ids = [r.id for r in results]
        assert len(ids) == len(set(ids)), "Hybrid search returned duplicate node IDs"


# ---------------------------------------------------------------------------
# Semantic Search (FTS5)
# ---------------------------------------------------------------------------

class TestSemanticSearch:
    def test_fts5_returns_results(self, populated_db):
        results = semantic_search("parsing", populated_db, top_k=10)
        assert len(results) > 0
        qnames = [r.qualified_name for r in results]
        assert any("parse" in q for q in qnames)

    def test_fts5_respects_top_k(self, populated_db):
        results = semantic_search("parsing", populated_db, top_k=1)
        assert len(results) <= 1

    def test_fts5_returns_empty_for_nonsense(self, populated_db):
        results = semantic_search("xyzzy_nonexistent_term", populated_db, top_k=10)
        assert results == []

    def test_semantic_summary_in_results(self, populated_db):
        results = semantic_search("database sqlite", populated_db, top_k=10)
        assert len(results) > 0
        assert results[0].semantic_summary is not None

    def test_with_source_includes_raw(self, populated_db):
        results = semantic_search("parsing", populated_db, top_k=10, with_source=True)
        assert len(results) > 0
        assert results[0].raw_source is not None

    def test_without_source_omits_raw(self, populated_db):
        results = semantic_search("parsing", populated_db, top_k=10, with_source=False)
        assert len(results) > 0
        assert results[0].raw_source is None


# ---------------------------------------------------------------------------
# Graph Search (recursive CTE)
# ---------------------------------------------------------------------------

class TestGraphSearch:
    def test_basic_traversal(self, populated_db):
        result = graph_search(
            "src/app.py::function::parse_file", populated_db, depth=1,
        )
        assert result is not None
        assert result.root_node.qualified_name == "parse_file"
        # Should find at least parse_file + one connected node
        assert len(result.nodes) >= 2

    def test_depth_2_expands(self, populated_db):
        result = graph_search(
            "src/app.py::function::parse_directory", populated_db, depth=2,
        )
        assert result is not None
        ids = {n.id for n in result.nodes}
        # parse_directory → parse_file → get_connection (2 hops)
        assert "src/db.py::function::get_connection" in ids

    def test_nonexistent_node_returns_none(self, populated_db):
        result = graph_search("nonexistent::node::id", populated_db, depth=1)
        assert result is None

    def test_edges_included(self, populated_db):
        result = graph_search(
            "src/app.py::function::parse_file", populated_db, depth=1,
        )
        assert result is not None
        assert len(result.edges) > 0
        edge_types = {e.edge_type for e in result.edges}
        assert "calls" in edge_types

    def test_with_source(self, populated_db):
        result = graph_search(
            "src/app.py::function::parse_file", populated_db, depth=1, with_source=True,
        )
        assert result is not None
        assert result.root_node.raw_source is not None


# ---------------------------------------------------------------------------
# Lexical Search (uses ripgrep — only testable with mocked subprocess or real rg)
# We test with the DB lookup part by inserting nodes and checking node_hits logic.
# ---------------------------------------------------------------------------

class TestLexicalSearch:
    def test_returns_empty_when_no_rg(self, populated_db, monkeypatch):
        """When ripgrep is not found, lexical search returns empty."""
        monkeypatch.setattr("indexer.query.find_rg", lambda: None)
        results = lexical_search("parse_file", populated_db, "/tmp", top_k=10)
        assert results == []

    def test_returns_results_with_mock_rg(self, populated_db, monkeypatch, tmp_path):
        """Mock ripgrep output and verify node lookup works."""
        # Create a fake source file so ripgrep JSON makes sense
        rg_output = json.dumps({
            "type": "match",
            "data": {
                "path": {"text": "src/app.py"},
                "line_number": 5,
            },
        })

        def mock_run(cmd, **kwargs):
            class Result:
                stdout = rg_output
                stderr = ""
                returncode = 0
            return Result()

        monkeypatch.setattr("indexer.query.subprocess.run", mock_run)
        monkeypatch.setattr("indexer.query.find_rg", lambda: "/usr/bin/rg")

        results = lexical_search("parse_file", populated_db, str(tmp_path), top_k=10)
        assert len(results) > 0
        assert results[0].qualified_name == "parse_file"


# ---------------------------------------------------------------------------
# Fallback routing (tested via CLI integration)
# ---------------------------------------------------------------------------

class TestFallbackRouting:
    def test_lexical_falls_back_to_semantic(self, populated_db, monkeypatch):
        """If lexical returns empty, semantic is attempted."""
        # Mock rg to return nothing
        def mock_run(cmd, **kwargs):
            class Result:
                stdout = ""
                stderr = ""
                returncode = 1
            return Result()

        monkeypatch.setattr("indexer.query.subprocess.run", mock_run)
        monkeypatch.setattr("indexer.query.find_rg", lambda: "/usr/bin/rg")

        # lexical returns empty
        lex = lexical_search("parsing", populated_db, "/tmp", top_k=10)
        assert lex == []

        # semantic should return results for same query
        sem = semantic_search("parsing", populated_db, top_k=10)
        assert len(sem) > 0


# ---------------------------------------------------------------------------
# Output Formatting
# ---------------------------------------------------------------------------

class TestFormatResults:
    def test_json_format_nodes(self):
        nodes = [NodeResult(
            id="a::b::c", file_path="a.py", node_type="function",
            qualified_name="c", signature="def c()", docstring=None,
            start_line=1, end_line=5, semantic_summary="Does stuff",
            domain_tags=["tag"], raw_source=None,
        )]
        out = format_results(nodes, "json")
        parsed = json.loads(out)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["qualified_name"] == "c"
        assert "raw_source" not in parsed[0]

    def test_jsonl_format_nodes(self):
        nodes = [
            NodeResult(id="a", file_path="a.py", node_type="function",
                       qualified_name="x", signature=None, docstring=None,
                       start_line=1, end_line=2, semantic_summary=None,
                       domain_tags=[], raw_source=None),
            NodeResult(id="b", file_path="b.py", node_type="class",
                       qualified_name="y", signature=None, docstring=None,
                       start_line=3, end_line=4, semantic_summary=None,
                       domain_tags=[], raw_source=None),
        ]
        out = format_results(nodes, "jsonl")
        lines = out.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["id"] == "a"
        assert json.loads(lines[1])["id"] == "b"

    def test_text_format_nodes(self):
        nodes = [NodeResult(
            id="a::b::c", file_path="a.py", node_type="function",
            qualified_name="my_func", signature="def my_func()", docstring=None,
            start_line=1, end_line=5, semantic_summary="A function",
            domain_tags=[], raw_source=None,
        )]
        out = format_results(nodes, "text")
        assert "my_func" in out
        assert "a.py" in out

    def test_json_format_graph(self):
        root = NodeResult(id="r", file_path="r.py", node_type="class",
                          qualified_name="Root", signature=None, docstring=None,
                          start_line=1, end_line=10, semantic_summary=None,
                          domain_tags=[], raw_source=None)
        gr = GraphResult(
            root_node=root,
            nodes=[root],
            edges=[EdgeResult(source_id="r", target_id="x", edge_type="calls", call_site_line=5)],
        )
        out = format_results(gr, "json")
        parsed = json.loads(out)
        assert "root_node" in parsed
        assert len(parsed["edges"]) == 1

    def test_empty_results_json(self):
        assert format_results([], "json") == "[]"

    def test_none_results_json(self):
        assert format_results(None, "json") == "[]"


# ---------------------------------------------------------------------------
# Hierarchical Search
# ---------------------------------------------------------------------------

class TestHierarchicalSearch:
    def test_returns_project_summary(self, db_conn):
        """Project summary (dir_path='.') appears in the result."""
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility, child_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (".", "A hybrid code indexing system", '["indexing"]', "Top-level project", 3),
        )
        db_conn.commit()

        result = hierarchical_search("indexing", db_conn, top_k=10)
        assert isinstance(result, HierarchicalResult)
        assert result.project_summary == "A hybrid code indexing system"

    def test_finds_matching_directories(self, db_conn):
        """Directories matching the query appear in matched_directories."""
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility, child_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src/parser", "AST parsing utilities for Python and Kotlin", '["parsing", "ast"]', "Source parsing", 2),
        )
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility, child_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src/db", "Database connection and query layer", '["database"]', "Data persistence", 0),
        )
        db_conn.commit()

        result = hierarchical_search("parsing AST", db_conn, top_k=10)
        assert isinstance(result, HierarchicalResult)
        assert len(result.matched_directories) >= 1
        matched_paths = [d.dir_path for d in result.matched_directories]
        assert "src/parser" in matched_paths

    def test_includes_nodes_from_matched_dirs(self, db_conn):
        """Nodes under matched directories appear in result.nodes."""
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility, child_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src/app", "Application entry point and CLI", '["cli"]', "CLI handling", 0),
        )
        # Insert a node under src/app/
        db_conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, qualified_name, name, signature, "
            "docstring, start_line, end_line, language, raw_source, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("src/app/main.py::function::main", "src/app/main.py", "function", "main",
             "main", "def main()", "Entry point", 1, 10, "python", "def main(): pass", "hash1"),
        )
        db_conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
        db_conn.commit()

        result = hierarchical_search("CLI application", db_conn, top_k=10)
        assert isinstance(result, HierarchicalResult)
        assert len(result.nodes) >= 1
        node_ids = [n.id for n in result.nodes]
        assert "src/app/main.py::function::main" in node_ids

    def test_child_dirs_populated(self, db_conn):
        """Child directories are populated for matched directories."""
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility, child_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src/click", "CLI framework components", '["cli", "framework"]', "CLI framework", 3),
        )
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility, child_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src/click/core", "Core click abstractions", '["cli"]', "Core components", 0),
        )
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility, child_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src/click/types", "Click type definitions", '["types"]', "Type system", 0),
        )
        db_conn.commit()

        result = hierarchical_search("CLI framework", db_conn, top_k=10)
        assert isinstance(result, HierarchicalResult)
        # Find the src/click directory result
        click_dirs = [d for d in result.matched_directories if d.dir_path == "src/click"]
        assert len(click_dirs) == 1
        child_dirs = click_dirs[0].child_dirs
        assert "src/click/core" in child_dirs
        assert "src/click/types" in child_dirs


# ---------------------------------------------------------------------------
# LLM-driven Hierarchical Search
# ---------------------------------------------------------------------------

class TestHierarchicalSearchLlm:
    @patch("indexer.enricher.call_llm")
    def test_llm_driven_search(self, mock_llm, db_conn):
        """LLM-driven hierarchical search calls LLM for directory and file selection."""
        # Insert directory summaries
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility) "
            "VALUES (?, ?, ?, ?)",
            (".", "Test project", '[]', "Main project"),
        )
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility) "
            "VALUES (?, ?, ?, ?)",
            ("src", "Source code for auth module", '["auth"]', "Authentication"),
        )
        # Insert a node under src/
        db_conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, qualified_name, name, "
            "start_line, end_line, language, content_hash, semantic_summary, enriched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("src/auth.py::function::login", "src/auth.py", "function", "login",
             "login", 1, 10, "python", "hash1", "Handles user login", "2024-01-01"),
        )
        db_conn.commit()

        # Mock LLM responses: first call returns directories, second returns files
        mock_llm.side_effect = [
            '["src"]',       # directory selection
            '["src/auth.py"]',  # file ranking
        ]

        result = hierarchical_search_llm(
            "authentication login", db_conn, model="test-model", provider="anthropic",
        )

        assert result.project_summary == "Test project"
        assert mock_llm.call_count == 2
        assert len(result.nodes) >= 1
        assert result.nodes[0].qualified_name == "login"

    @patch("indexer.enricher.call_llm")
    def test_fallback_on_llm_failure(self, mock_llm, db_conn):
        """Falls back to FTS5 search when LLM returns invalid JSON."""
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility) "
            "VALUES (?, ?, ?, ?)",
            (".", "Test project", '[]', "Main project"),
        )
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility) "
            "VALUES (?, ?, ?, ?)",
            ("src", "Source code", '[]', "Source"),
        )
        db_conn.commit()

        mock_llm.return_value = "I cannot help with that"

        result = hierarchical_search_llm(
            "test query", db_conn, model="test-model", provider="anthropic",
        )
        # Should not crash, returns a valid HierarchicalResult (fallback to FTS5)
        assert result is not None
        assert isinstance(result, HierarchicalResult)

    @patch("indexer.enricher.call_llm")
    def test_handles_markdown_fenced_json(self, mock_llm, db_conn):
        """Correctly parses JSON wrapped in markdown code fences."""
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility) "
            "VALUES (?, ?, ?, ?)",
            (".", "Test project", '[]', "Main project"),
        )
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility) "
            "VALUES (?, ?, ?, ?)",
            ("lib", "Library code", '["lib"]', "Library"),
        )
        db_conn.execute(
            "INSERT INTO nodes (id, file_path, node_type, qualified_name, name, "
            "start_line, end_line, language, content_hash, semantic_summary, enriched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("lib/utils.py::function::helper", "lib/utils.py", "function", "helper",
             "helper", 1, 5, "python", "hash2", "A helper function", "2024-01-01"),
        )
        db_conn.commit()

        # LLM returns markdown-fenced JSON
        mock_llm.side_effect = [
            '```json\n["lib"]\n```',
            '```\n["lib/utils.py"]\n```',
        ]

        result = hierarchical_search_llm(
            "helper utility", db_conn, model="test-model", provider="anthropic",
        )

        assert mock_llm.call_count == 2
        assert len(result.nodes) >= 1
        assert result.nodes[0].qualified_name == "helper"

    @patch("indexer.enricher.call_llm")
    def test_fallback_when_no_directories(self, mock_llm, db_conn):
        """Falls back to FTS5 when no directory summaries exist (except root)."""
        db_conn.execute(
            "INSERT INTO directory_summaries (dir_path, summary, domain_tags, responsibility) "
            "VALUES (?, ?, ?, ?)",
            (".", "Test project", '[]', "Main project"),
        )
        db_conn.commit()

        result = hierarchical_search_llm(
            "test query", db_conn, model="test-model", provider="anthropic",
        )

        # Should not call LLM at all — falls back immediately
        assert mock_llm.call_count == 0
        assert isinstance(result, HierarchicalResult)


# ---------------------------------------------------------------------------
# CLI Smoke Tests
# ---------------------------------------------------------------------------

class TestQueryCLISmoke:
    def test_query_no_args_exits_2(self):
        result = subprocess.run(
            ["index", "query"],
            capture_output=True, text=True,
        )
        assert result.returncode == 2

    def test_query_missing_db_exits_1(self, tmp_path):
        result = subprocess.run(
            ["index", "--db", str(tmp_path / "nonexistent.db"), "query", "test"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "not found" in result.stderr.lower() or "ERROR" in result.stderr
