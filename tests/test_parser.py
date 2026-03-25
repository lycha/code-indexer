"""Tests for the Python AST parser, incremental detection, and cAST chunking."""

import hashlib
import os
import textwrap
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_PY = FIXTURES_DIR / "sample.py"
SAMPLE_KT = FIXTURES_DIR / "Sample.kt"
SAMPLE_TS = FIXTURES_DIR / "sample.ts"
SAMPLE_JAVA = FIXTURES_DIR / "Sample.java"
SAMPLE_RB = FIXTURES_DIR / "sample.rb"


class TestParseFile:
    """Test parse_file extracts correct nodes from Python files."""

    def test_extracts_file_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        file_nodes = [n for n in nodes if n["node_type"] == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0]["name"] == "sample.py"
        assert file_nodes[0]["language"] == "python"

    def test_extracts_class_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        class_nodes = [n for n in nodes if n["node_type"] == "class"]
        assert len(class_nodes) == 1
        assert class_nodes[0]["name"] == "Calculator"
        assert class_nodes[0]["qualified_name"] == "Calculator"
        assert class_nodes[0]["docstring"] == "A simple calculator class."

    def test_extracts_method_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        method_nodes = [n for n in nodes if n["node_type"] == "method"]
        names = {n["name"] for n in method_nodes}
        assert "add" in names
        assert "subtract" in names
        for m in method_nodes:
            assert m["qualified_name"].startswith("Calculator.")

    def test_extracts_function_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        func_nodes = [n for n in nodes if n["node_type"] == "function"]
        names = {n["name"] for n in func_nodes}
        assert "helper_function" in names
        assert "oversized_function" in names

    def test_node_id_format(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            # Node ID format: {file_path}::{node_type}::{qualified_name}
            parts = node["id"].split("::")
            assert len(parts) == 3, f"Bad node ID format: {node['id']}"
            assert parts[1] == node["node_type"]

    def test_content_hash_is_sha256(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            expected = hashlib.sha256(node["raw_source"].encode()).hexdigest()
            assert node["content_hash"] == expected, f"Hash mismatch for {node['id']}"

    def test_start_end_lines(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            assert node["start_line"] >= 1
            assert node["end_line"] >= node["start_line"]

    def test_signature_present_for_functions_methods(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            if node["node_type"] in ("function", "method"):
                assert node["signature"] is not None and len(node["signature"]) > 0


class TestParseDirectory:
    """Test parse_directory walks directory and processes .py files."""

    def test_parses_python_files(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        # Create a small repo
        py_file = tmp_path / "module.py"
        py_file.write_text('def foo():\n    """A function."""\n    pass\n')

        parse_directory(tmp_path, db_conn, token_limit=512)

        rows = db_conn.execute("SELECT * FROM nodes WHERE language='python'").fetchall()
        assert len(rows) > 0

    def test_files_table_upserted(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        py_file = tmp_path / "module.py"
        py_file.write_text('def foo():\n    pass\n')

        parse_directory(tmp_path, db_conn, token_limit=512)

        files = db_conn.execute("SELECT * FROM files").fetchall()
        assert len(files) == 1
        # Check columns: path, last_modified, content_hash, language, node_count, indexed_at
        row = db_conn.execute(
            "SELECT path, language, node_count FROM files"
        ).fetchone()
        assert row[1] == "python"
        assert row[2] >= 1  # at least file node + function node


class TestIncrementalDetection:
    """Test that unchanged files are skipped on re-parse."""

    def test_skip_unchanged_file(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        py_file = tmp_path / "module.py"
        py_file.write_text('def foo():\n    pass\n')

        # First parse
        parse_directory(tmp_path, db_conn, token_limit=512)
        count1 = db_conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

        # Second parse (no changes)
        parse_directory(tmp_path, db_conn, token_limit=512)
        count2 = db_conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

        assert count1 == count2

    def test_reparse_changed_file(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        py_file = tmp_path / "module.py"
        py_file.write_text('def foo():\n    pass\n')
        parse_directory(tmp_path, db_conn, token_limit=512)

        # Modify file
        py_file.write_text('def foo():\n    return 1\n\ndef bar():\n    pass\n')
        parse_directory(tmp_path, db_conn, token_limit=512)

        nodes = db_conn.execute("SELECT name FROM nodes WHERE node_type='function'").fetchall()
        names = {r[0] for r in nodes}
        assert "bar" in names


class TestEnrichedAtClearing:
    """Test that enriched_at is cleared when content changes."""

    def test_enriched_at_cleared_on_change(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        py_file = tmp_path / "module.py"
        py_file.write_text('def foo():\n    """Original."""\n    pass\n')
        parse_directory(tmp_path, db_conn, token_limit=512)

        # Simulate enrichment by setting enriched_at
        db_conn.execute("UPDATE nodes SET enriched_at = '2024-01-01T00:00:00'")
        db_conn.commit()

        # Verify enriched_at is set
        enriched = db_conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NOT NULL"
        ).fetchone()[0]
        assert enriched > 0

        # Modify file
        py_file.write_text('def foo():\n    """Changed."""\n    return 42\n')
        parse_directory(tmp_path, db_conn, token_limit=512)

        # enriched_at should be cleared for changed nodes
        cleared = db_conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE enriched_at IS NULL"
        ).fetchone()[0]
        assert cleared > 0


class TestCASTChunking:
    """Test cAST chunking of oversized functions."""

    def test_oversized_function_chunked(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent, token_limit=512)

        # The oversized_function should have been chunked
        # Look for nodes whose qualified_name starts with "oversized_function"
        oversized_nodes = [
            n for n in nodes
            if "oversized_function" in n.get("qualified_name", "")
        ]
        # Should have the parent plus at least one chunk
        assert len(oversized_nodes) >= 2, (
            f"Expected chunked nodes for oversized_function, got {len(oversized_nodes)}: "
            f"{[n['qualified_name'] for n in oversized_nodes]}"
        )

    def test_small_function_not_chunked(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent, token_limit=512)

        helper_nodes = [
            n for n in nodes
            if n.get("qualified_name", "").startswith("helper_function")
        ]
        # Should only be the function itself, no chunks
        assert len(helper_nodes) == 1

    def test_chunk_qualified_name_hierarchy(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_PY, db_conn, repo_root=FIXTURES_DIR.parent.parent, token_limit=512)

        oversized_nodes = [
            n for n in nodes
            if "oversized_function" in n.get("qualified_name", "")
        ]
        # Parent node should be "oversized_function"
        parent = [n for n in oversized_nodes if n["qualified_name"] == "oversized_function"]
        assert len(parent) == 1
        # Children should have qualified_name like "oversized_function.chunk_N"
        children = [n for n in oversized_nodes if n["qualified_name"] != "oversized_function"]
        assert len(children) >= 1
        for child in children:
            assert child["qualified_name"].startswith("oversized_function.")


class TestGitignoreExclusion:
    """Test that files matching .gitignore patterns are excluded."""

    def test_gitignore_patterns_excluded(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        # Create .gitignore
        (tmp_path / ".gitignore").write_text("ignored_dir/\n*.generated.py\n")

        # Create files that should be ignored
        ignored_dir = tmp_path / "ignored_dir"
        ignored_dir.mkdir()
        (ignored_dir / "module.py").write_text("def ignored(): pass\n")
        (tmp_path / "auto.generated.py").write_text("def generated(): pass\n")

        # Create a file that should be parsed
        (tmp_path / "normal.py").write_text("def normal(): pass\n")

        parse_directory(tmp_path, db_conn, token_limit=512)

        nodes = db_conn.execute("SELECT name FROM nodes WHERE node_type='function'").fetchall()
        names = {r[0] for r in nodes}
        assert "normal" in names
        assert "ignored" not in names
        assert "generated" not in names

    def test_codeindex_db_excluded(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        # Create .codeindex directory with a .db file (should be excluded)
        codeindex_dir = tmp_path / ".codeindex"
        codeindex_dir.mkdir()
        (codeindex_dir / "test.db").write_text("")

        # Create a normal Python file
        (tmp_path / "normal.py").write_text("def normal(): pass\n")

        parse_directory(tmp_path, db_conn, token_limit=512)

        files = db_conn.execute("SELECT path FROM files").fetchall()
        paths = {r[0] for r in files}
        assert not any(".codeindex" in p for p in paths)


class TestSyntaxErrorHandling:
    """Test that syntax errors are handled gracefully."""

    def test_syntax_error_skipped(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        # Create a file with syntax error
        (tmp_path / "bad.py").write_text("def broken(\n")
        # Create a valid file
        (tmp_path / "good.py").write_text("def good(): pass\n")

        parse_directory(tmp_path, db_conn, token_limit=512)

        nodes = db_conn.execute("SELECT name FROM nodes WHERE node_type='function'").fetchall()
        names = {r[0] for r in nodes}
        assert "good" in names

    def test_syntax_error_warning_logged(self, db_conn, tmp_path, capsys):
        from indexer.parser import parse_directory

        (tmp_path / "bad.py").write_text("def broken(\n")
        (tmp_path / "good.py").write_text("def good(): pass\n")

        parse_directory(tmp_path, db_conn, token_limit=512)

        captured = capsys.readouterr()
        assert "WARNING" in captured.err or "Skipped" in captured.err


class TestKotlinParsing:
    """Test tree-sitter Kotlin parsing."""

    def test_extracts_file_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        file_nodes = [n for n in nodes if n["node_type"] == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0]["name"] == "Sample.kt"
        assert file_nodes[0]["language"] == "kotlin"

    def test_extracts_class_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        class_nodes = [n for n in nodes if n["node_type"] == "class"]
        assert len(class_nodes) == 1
        assert class_nodes[0]["name"] == "SampleClass"
        assert class_nodes[0]["qualified_name"] == "SampleClass"

    def test_extracts_interface_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        iface_nodes = [n for n in nodes if n["node_type"] == "interface"]
        assert len(iface_nodes) == 1
        assert iface_nodes[0]["name"] == "Greeter"

    def test_extracts_object_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        obj_nodes = [n for n in nodes if n["node_type"] == "object"]
        assert len(obj_nodes) == 1
        assert obj_nodes[0]["name"] == "Registry"

    def test_extracts_method_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        method_nodes = [n for n in nodes if n["node_type"] == "method"]
        names = {n["name"] for n in method_nodes}
        assert "greet" in names
        assert "helper" in names
        assert "register" in names
        for m in method_nodes:
            assert "." in m["qualified_name"]

    def test_extracts_function_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        func_nodes = [n for n in nodes if n["node_type"] == "function"]
        names = {n["name"] for n in func_nodes}
        assert "topLevelFunction" in names

    def test_node_id_format(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            parts = node["id"].split("::")
            assert len(parts) == 3, f"Bad node ID format: {node['id']}"
            assert parts[1] == node["node_type"]

    def test_content_hash_is_sha256(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            expected = hashlib.sha256(node["raw_source"].encode()).hexdigest()
            assert node["content_hash"] == expected

    def test_start_end_lines(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            assert node["start_line"] >= 1
            assert node["end_line"] >= node["start_line"]

    def test_signature_present_for_functions_methods(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            if node["node_type"] in ("function", "method"):
                assert node["signature"] is not None and len(node["signature"]) > 0, (
                    f"Missing signature for {node['qualified_name']}"
                )

    def test_docstring_extraction(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_KT, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        greeter = [n for n in nodes if n["name"] == "Greeter"][0]
        assert greeter["docstring"] is not None
        assert "sample interface" in greeter["docstring"].lower()


class TestTypeScriptParsing:
    """Test tree-sitter TypeScript parsing."""

    def test_extracts_file_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        file_nodes = [n for n in nodes if n["node_type"] == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0]["name"] == "sample.ts"
        assert file_nodes[0]["language"] == "typescript"

    def test_extracts_class_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        class_nodes = [n for n in nodes if n["node_type"] == "class"]
        assert len(class_nodes) == 1
        assert class_nodes[0]["name"] == "SampleClass"

    def test_extracts_interface_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        iface_nodes = [n for n in nodes if n["node_type"] == "interface"]
        assert len(iface_nodes) == 1
        assert iface_nodes[0]["name"] == "Greeter"

    def test_extracts_method_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        method_nodes = [n for n in nodes if n["node_type"] == "method"]
        names = {n["name"] for n in method_nodes}
        assert "greet" in names
        assert "helper" in names
        assert "constructor" in names
        for m in method_nodes:
            assert "." in m["qualified_name"]

    def test_extracts_function_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        func_nodes = [n for n in nodes if n["node_type"] == "function"]
        names = {n["name"] for n in func_nodes}
        assert "topLevelFunction" in names

    def test_node_id_format(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            parts = node["id"].split("::")
            assert len(parts) == 3, f"Bad node ID format: {node['id']}"
            assert parts[1] == node["node_type"]

    def test_content_hash_is_sha256(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            expected = hashlib.sha256(node["raw_source"].encode()).hexdigest()
            assert node["content_hash"] == expected

    def test_start_end_lines(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            assert node["start_line"] >= 1
            assert node["end_line"] >= node["start_line"]

    def test_signature_present_for_functions_methods(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            if node["node_type"] in ("function", "method"):
                assert node["signature"] is not None and len(node["signature"]) > 0, (
                    f"Missing signature for {node['qualified_name']}"
                )

    def test_docstring_extraction(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_TS, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        greeter = [n for n in nodes if n["name"] == "Greeter"][0]
        assert greeter["docstring"] is not None
        assert "sample interface" in greeter["docstring"].lower()


class TestUnsupportedLanguage:
    """Test that unsupported languages log a warning and continue."""

    def test_go_file_warning(self, db_conn, tmp_path, capsys):
        from indexer.parser import parse_file

        go_file = tmp_path / "main.go"
        go_file.write_text("package main")
        nodes = parse_file(go_file, db_conn, repo_root=tmp_path)
        assert nodes == []
        captured = capsys.readouterr()
        assert "Unsupported language: go" in captured.err

    def test_unsupported_in_directory(self, db_conn, tmp_path, capsys):
        from indexer.parser import parse_directory

        (tmp_path / "main.go").write_text("package main")
        (tmp_path / "good.py").write_text("def good(): pass\n")
        parse_directory(tmp_path, db_conn, token_limit=512)

        # Go warning logged
        captured = capsys.readouterr()
        assert "Unsupported language: go" in captured.err

        # Python file was still parsed
        nodes = db_conn.execute("SELECT name FROM nodes WHERE node_type='function'").fetchall()
        names = {r[0] for r in nodes}
        assert "good" in names


class TestJavaParsing:
    """Test tree-sitter Java parsing."""

    def test_extracts_file_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        file_nodes = [n for n in nodes if n["node_type"] == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0]["name"] == "Sample.java"
        assert file_nodes[0]["language"] == "java"

    def test_extracts_class_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        class_nodes = [n for n in nodes if n["node_type"] == "class" and n["name"] == "SampleService"]
        assert len(class_nodes) == 1
        assert class_nodes[0]["qualified_name"] == "SampleService"

    def test_extracts_interface_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        iface_nodes = [n for n in nodes if n["node_type"] == "interface"]
        assert len(iface_nodes) == 1
        assert iface_nodes[0]["name"] == "Repository"

    def test_extracts_enum_as_class(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        enum_nodes = [n for n in nodes if n["name"] == "Status"]
        assert len(enum_nodes) == 1
        assert enum_nodes[0]["node_type"] == "class"

    def test_extracts_method_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        method_nodes = [n for n in nodes if n["node_type"] == "method"]
        names = {n["name"] for n in method_nodes}
        assert "getName" in names
        assert "setName" in names
        assert "SampleService" in names  # constructor
        assert "findById" in names
        assert "findAll" in names
        for m in method_nodes:
            assert "." in m["qualified_name"]

    def test_node_id_format(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            parts = node["id"].split("::")
            assert len(parts) == 3, f"Bad node ID format: {node['id']}"
            assert parts[1] == node["node_type"]

    def test_content_hash_is_sha256(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            expected = hashlib.sha256(node["raw_source"].encode()).hexdigest()
            assert node["content_hash"] == expected

    def test_signature_present_for_methods(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            if node["node_type"] == "method":
                assert node["signature"] is not None, f"Missing signature for {node['qualified_name']}"

    def test_docstring_extraction(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_JAVA, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        service = [n for n in nodes if n["name"] == "SampleService" and n["node_type"] == "class"][0]
        assert service["docstring"] is not None
        assert "sample service" in service["docstring"].lower()

        get_name = [n for n in nodes if n["name"] == "getName"][0]
        assert get_name["docstring"] is not None
        assert "name" in get_name["docstring"].lower()


class TestRubyParsing:
    """Test tree-sitter Ruby parsing."""

    def test_extracts_file_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        file_nodes = [n for n in nodes if n["node_type"] == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0]["name"] == "sample.rb"
        assert file_nodes[0]["language"] == "ruby"

    def test_extracts_module_as_class(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        mod_nodes = [n for n in nodes if n["name"] == "Authentication"]
        assert len(mod_nodes) == 1
        assert mod_nodes[0]["node_type"] == "class"

    def test_extracts_class_node(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        class_nodes = [n for n in nodes if n["name"] == "UserService"]
        assert len(class_nodes) == 1
        assert class_nodes[0]["node_type"] == "class"
        assert class_nodes[0]["qualified_name"] == "Authentication.UserService"

    def test_extracts_method_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        method_nodes = [n for n in nodes if n["node_type"] == "method"]
        names = {n["name"] for n in method_nodes}
        assert "initialize" in names
        assert "greet" in names
        assert "create" in names
        for m in method_nodes:
            assert "." in m["qualified_name"]

    def test_extracts_function_nodes(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        func_nodes = [n for n in nodes if n["node_type"] == "function"]
        names = {n["name"] for n in func_nodes}
        assert "standalone_function" in names

    def test_node_id_format(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            parts = node["id"].split("::")
            assert len(parts) == 3, f"Bad node ID format: {node['id']}"
            assert parts[1] == node["node_type"]

    def test_content_hash_is_sha256(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        for node in nodes:
            expected = hashlib.sha256(node["raw_source"].encode()).hexdigest()
            assert node["content_hash"] == expected

    def test_signature_present_for_methods(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        greet = [n for n in nodes if n["name"] == "greet"][0]
        assert greet["signature"] is not None

    def test_docstring_extraction(self, db_conn):
        from indexer.parser import parse_file

        nodes = parse_file(SAMPLE_RB, db_conn, repo_root=FIXTURES_DIR.parent.parent)
        user_service = [n for n in nodes if n["name"] == "UserService"][0]
        assert user_service["docstring"] is not None
        assert "sample user service" in user_service["docstring"].lower()

        greet = [n for n in nodes if n["name"] == "greet"][0]
        assert greet["docstring"] is not None
        assert "greets" in greet["docstring"].lower()


class TestMultiLanguageDirectory:
    """Test parsing a directory with .py, .kt, .ts, .java, and .rb files."""

    def test_parses_all_languages(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        (tmp_path / "module.py").write_text('def py_func():\n    pass\n')
        (tmp_path / "Module.kt").write_text('fun kt_func(): Int {\n    return 1\n}\n')
        (tmp_path / "module.ts").write_text('function ts_func(): number {\n    return 1;\n}\n')
        (tmp_path / "Module.java").write_text('class Mod {\n    void java_func() {}\n}\n')
        (tmp_path / "module.rb").write_text('def rb_func(x)\n  x\nend\n')

        parse_directory(tmp_path, db_conn, token_limit=512)

        py_nodes = db_conn.execute("SELECT name FROM nodes WHERE language='python' AND node_type='function'").fetchall()
        kt_nodes = db_conn.execute("SELECT name FROM nodes WHERE language='kotlin' AND node_type='function'").fetchall()
        ts_nodes = db_conn.execute("SELECT name FROM nodes WHERE language='typescript' AND node_type='function'").fetchall()
        java_nodes = db_conn.execute("SELECT name FROM nodes WHERE language='java' AND node_type='method'").fetchall()
        rb_nodes = db_conn.execute("SELECT name FROM nodes WHERE language='ruby' AND node_type='function'").fetchall()

        assert {r[0] for r in py_nodes} == {"py_func"}
        assert {r[0] for r in kt_nodes} == {"kt_func"}
        assert {r[0] for r in ts_nodes} == {"ts_func"}
        assert {r[0] for r in java_nodes} == {"java_func"}
        assert {r[0] for r in rb_nodes} == {"rb_func"}

    def test_files_table_has_correct_languages(self, db_conn, tmp_path):
        from indexer.parser import parse_directory

        (tmp_path / "module.py").write_text('def py_func():\n    pass\n')
        (tmp_path / "Module.kt").write_text('fun kt_func(): Int {\n    return 1\n}\n')
        (tmp_path / "module.ts").write_text('function ts_func(): number {\n    return 1;\n}\n')
        (tmp_path / "Module.java").write_text('class Mod {\n    void java_func() {}\n}\n')
        (tmp_path / "module.rb").write_text('def rb_func(x)\n  x\nend\n')

        parse_directory(tmp_path, db_conn, token_limit=512)

        files = db_conn.execute("SELECT path, language FROM files ORDER BY path").fetchall()
        langs = {r[0]: r[1] for r in files}
        assert langs["Module.kt"] == "kotlin"
        assert langs["module.py"] == "python"
        assert langs["module.ts"] == "typescript"
        assert langs["Module.java"] == "java"
        assert langs["module.rb"] == "ruby"
