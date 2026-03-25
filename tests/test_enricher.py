"""Tests for the enrichment module (Phase 3 LLM enrichment)."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from indexer.enricher import (
    DEFAULT_MODELS,
    DEFAULT_PROVIDER,
    PROVIDERS,
    _resolve_provider_and_model,
    build_node_context,
    call_llm,
    enrich_nodes,
    parse_enrichment_response,
)


def _insert_node(conn, node_id="test.py::function::foo", enriched_at=None, **kwargs):
    """Helper to insert a node for testing."""
    defaults = {
        "file_path": "test.py",
        "node_type": "function",
        "name": "foo",
        "qualified_name": "foo",
        "signature": "def foo(x)",
        "docstring": "Does stuff.",
        "start_line": 1,
        "end_line": 5,
        "language": "python",
        "raw_source": "def foo(x):\n    return x + 1",
        "content_hash": "abc123",
    }
    defaults.update(kwargs)
    conn.execute(
        """INSERT INTO nodes (id, file_path, node_type, name, qualified_name, signature,
           docstring, start_line, end_line, language, raw_source, content_hash, enriched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            node_id,
            defaults["file_path"],
            defaults["node_type"],
            defaults["name"],
            defaults["qualified_name"],
            defaults["signature"],
            defaults["docstring"],
            defaults["start_line"],
            defaults["end_line"],
            defaults["language"],
            defaults["raw_source"],
            defaults["content_hash"],
            enriched_at,
        ),
    )
    conn.commit()


def _make_llm_response(summary="Does stuff", tags=None, responsibility="Handles stuff"):
    """Create a valid LLM JSON response."""
    if tags is None:
        tags = ["utility", "math"]
    return json.dumps({
        "semantic_summary": summary,
        "domain_tags": tags,
        "inferred_responsibility": responsibility,
    })


class TestGetUnenrichedNodes:
    def test_selects_only_unenriched(self, db_conn):
        _insert_node(db_conn, "test.py::function::foo", enriched_at=None)
        _insert_node(db_conn, "test.py::function::bar", enriched_at="2024-01-01T00:00:00Z", name="bar", qualified_name="bar")
        from indexer.enricher import _get_unenriched_nodes
        nodes = _get_unenriched_nodes(db_conn)
        assert len(nodes) == 1
        assert nodes[0][0] == "test.py::function::foo"

    def test_empty_when_all_enriched(self, db_conn):
        _insert_node(db_conn, "test.py::function::foo", enriched_at="2024-01-01T00:00:00Z")
        from indexer.enricher import _get_unenriched_nodes
        nodes = _get_unenriched_nodes(db_conn)
        assert len(nodes) == 0


class TestBuildNodeContext:
    def test_parent_detected(self, db_conn):
        _insert_node(db_conn, "test.py::class::MyClass", node_type="class", name="MyClass",
                     qualified_name="MyClass", signature="class MyClass")
        _insert_node(db_conn, "test.py::method::MyClass.foo", node_type="method", name="foo",
                     qualified_name="MyClass.foo", signature="def foo(self)")
        ctx = build_node_context("test.py::method::MyClass.foo", db_conn)
        assert "MyClass" in ctx["parent"]

    def test_children_detected(self, db_conn):
        _insert_node(db_conn, "test.py::class::MyClass", node_type="class", name="MyClass",
                     qualified_name="MyClass", signature="class MyClass")
        _insert_node(db_conn, "test.py::method::MyClass.foo", node_type="method", name="foo",
                     qualified_name="MyClass.foo", signature="def foo(self)")
        ctx = build_node_context("test.py::class::MyClass", db_conn)
        assert "MyClass.foo" in ctx["children"]

    def test_callers_and_callees(self, db_conn):
        _insert_node(db_conn, "a.py::function::caller", name="caller", qualified_name="caller",
                     file_path="a.py")
        _insert_node(db_conn, "b.py::function::callee", name="callee", qualified_name="callee",
                     file_path="b.py")
        _insert_node(db_conn, "c.py::function::target", name="target", qualified_name="target",
                     file_path="c.py")
        # caller -> target (calls)
        db_conn.execute("INSERT INTO edges (source_id, target_id, edge_type) VALUES (?, ?, ?)",
                        ("a.py::function::caller", "c.py::function::target", "calls"))
        # target -> callee (calls)
        db_conn.execute("INSERT INTO edges (source_id, target_id, edge_type) VALUES (?, ?, ?)",
                        ("c.py::function::target", "b.py::function::callee", "calls"))
        db_conn.commit()

        ctx = build_node_context("c.py::function::target", db_conn)
        assert "caller" in ctx["callers"]
        assert "callee" in ctx["callees"]

    def test_no_context(self, db_conn):
        _insert_node(db_conn, "test.py::function::lonely")
        ctx = build_node_context("test.py::function::lonely", db_conn)
        assert ctx["parent"] == "none"
        assert ctx["children"] == "[]"
        assert ctx["callers"] == "[]"
        assert ctx["callees"] == "[]"


class TestParseEnrichmentResponse:
    def test_valid_json(self):
        result = parse_enrichment_response(_make_llm_response())
        assert result is not None
        assert result["semantic_summary"] == "Does stuff"
        assert "utility" in result["domain_tags"]

    def test_json_with_code_fences(self):
        response = "```json\n" + _make_llm_response() + "\n```"
        result = parse_enrichment_response(response)
        assert result is not None
        assert result["semantic_summary"] == "Does stuff"

    def test_malformed_json(self):
        result = parse_enrichment_response("not json at all")
        assert result is None

    def test_missing_keys(self):
        result = parse_enrichment_response(json.dumps({"semantic_summary": "hi"}))
        assert result is None

    def test_non_dict(self):
        result = parse_enrichment_response(json.dumps([1, 2, 3]))
        assert result is None

    def test_domain_tags_not_list(self):
        result = parse_enrichment_response(json.dumps({
            "semantic_summary": "hi",
            "domain_tags": "not a list",
            "inferred_responsibility": "does stuff",
        }))
        assert result is None


class TestCallLlm:
    @patch("indexer.enricher.anthropic")
    def test_calls_api(self, mock_anthropic):
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="response")]
        mock_client.messages.create.return_value = mock_msg

        result = call_llm("test prompt", "claude-sonnet-4-6")
        assert result == "response"
        mock_client.messages.create.assert_called_once()

    @patch("indexer.enricher.time.sleep")
    @patch("indexer.enricher.anthropic")
    def test_retries_on_rate_limit(self, mock_anthropic, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_anthropic.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_anthropic.APITimeoutError = type("APITimeoutError", (Exception,), {})

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="response")]
        mock_client.messages.create.side_effect = [
            mock_anthropic.RateLimitError("rate limited"),
            mock_msg,
        ]

        result = call_llm("test prompt", "claude-sonnet-4-6")
        assert result == "response"
        assert mock_sleep.call_count == 1

    @patch("indexer.enricher.time.sleep")
    @patch("indexer.enricher.anthropic")
    def test_raises_after_max_retries(self, mock_anthropic, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_anthropic.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_anthropic.APITimeoutError = type("APITimeoutError", (Exception,), {})

        mock_client.messages.create.side_effect = mock_anthropic.RateLimitError("rate limited")

        with pytest.raises(Exception, match="rate limited"):
            call_llm("test prompt", "claude-sonnet-4-6")
        assert mock_client.messages.create.call_count == 3


class TestEnrichNodes:
    @patch("indexer.enricher.call_llm")
    def test_enriches_unenriched_nodes(self, mock_llm, db_conn):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::function::foo")
            mock_llm.return_value = _make_llm_response()

            exit_code = enrich_nodes(db_conn, model="claude-sonnet-4-6")
            assert exit_code == 0

            row = db_conn.execute("SELECT semantic_summary, domain_tags, inferred_responsibility, enriched_at, enrichment_model FROM nodes WHERE id = ?",
                                  ("test.py::function::foo",)).fetchone()
            assert row[0] == "Does stuff"
            assert "utility" in row[1]
            assert row[2] == "Handles stuff"
            assert row[3] is not None  # enriched_at set
            assert row[4] == "claude-sonnet-4-6"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    @patch("indexer.enricher.call_llm")
    def test_skips_already_enriched(self, mock_llm, db_conn):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::function::foo", enriched_at="2024-01-01T00:00:00Z")
            exit_code = enrich_nodes(db_conn, model="claude-sonnet-4-6")
            assert exit_code == 0
            mock_llm.assert_not_called()
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_dry_run_no_api_calls(self, db_conn):
        _insert_node(db_conn, "test.py::function::foo")
        # No API key needed for dry run
        exit_code = enrich_nodes(db_conn, dry_run=True)
        assert exit_code == 0
        # Node should still be unenriched
        row = db_conn.execute("SELECT enriched_at FROM nodes WHERE id = ?",
                              ("test.py::function::foo",)).fetchone()
        assert row[0] is None

    @patch("indexer.enricher.call_llm")
    def test_malformed_json_skipped(self, mock_llm, db_conn):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::function::foo")
            mock_llm.return_value = "not valid json"

            exit_code = enrich_nodes(db_conn, model="claude-sonnet-4-6")
            assert exit_code == 1  # node remains unenriched

            row = db_conn.execute("SELECT enriched_at FROM nodes WHERE id = ?",
                                  ("test.py::function::foo",)).fetchone()
            assert row[0] is None
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_missing_api_key_exits_2(self, db_conn):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        _insert_node(db_conn, "test.py::function::foo")
        with pytest.raises(SystemExit) as exc:
            enrich_nodes(db_conn, model="claude-sonnet-4-6")
        assert exc.value.code == 2

    @patch("indexer.enricher.call_llm")
    def test_exit_1_when_some_remain(self, mock_llm, db_conn):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::function::foo")
            _insert_node(db_conn, "test.py::function::bar", name="bar", qualified_name="bar")
            # First node succeeds, second fails
            mock_llm.side_effect = [_make_llm_response(), "invalid json"]

            exit_code = enrich_nodes(db_conn, model="claude-sonnet-4-6")
            assert exit_code == 1
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    @patch("indexer.enricher.call_llm")
    def test_fts_updated_after_enrichment(self, mock_llm, db_conn):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::function::foo")
            mock_llm.return_value = _make_llm_response(summary="Calculates incremented value")

            enrich_nodes(db_conn, model="claude-sonnet-4-6")

            # Query FTS for the enriched content
            fts_rows = db_conn.execute(
                "SELECT id FROM nodes_fts WHERE nodes_fts MATCH 'incremented'"
            ).fetchall()
            assert len(fts_rows) == 1
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    @patch("indexer.enricher.call_llm")
    def test_meta_updated(self, mock_llm, db_conn):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::function::foo")
            mock_llm.return_value = _make_llm_response()

            enrich_nodes(db_conn, model="claude-sonnet-4-6")

            row = db_conn.execute("SELECT value FROM index_meta WHERE key = 'unenriched_nodes'").fetchone()
            assert row is not None
            assert row[0] == "0"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    @patch("indexer.enricher.call_llm")
    def test_model_override(self, mock_llm, db_conn):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::function::foo")
            mock_llm.return_value = _make_llm_response()

            enrich_nodes(db_conn, model="claude-opus-4")

            row = db_conn.execute("SELECT enrichment_model FROM nodes WHERE id = ?",
                                  ("test.py::function::foo",)).fetchone()
            assert row[0] == "claude-opus-4"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    @patch("indexer.enricher.call_llm")
    def test_context_includes_graph_neighbors(self, mock_llm, db_conn):
        """VAL-ENRICH-011: prompt includes parent, children, callers, callees."""
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::class::MyClass", node_type="class", name="MyClass",
                         qualified_name="MyClass", signature="class MyClass")
            _insert_node(db_conn, "test.py::method::MyClass.foo", node_type="method", name="foo",
                         qualified_name="MyClass.foo", signature="def foo(self)")
            _insert_node(db_conn, "other.py::function::caller_fn", name="caller_fn",
                         qualified_name="caller_fn", file_path="other.py")
            # caller -> MyClass.foo
            db_conn.execute("INSERT INTO edges (source_id, target_id, edge_type) VALUES (?, ?, ?)",
                            ("other.py::function::caller_fn", "test.py::method::MyClass.foo", "calls"))
            db_conn.commit()

            mock_llm.return_value = _make_llm_response()
            enrich_nodes(db_conn, model="claude-sonnet-4-6")

            # Check that the prompt for MyClass.foo included context
            calls = mock_llm.call_args_list
            # Find the call for MyClass.foo (look for it in Qualified name field)
            found = False
            for call in calls:
                prompt = call[0][0]
                if "Qualified name: MyClass.foo" in prompt:
                    assert "MyClass" in prompt  # parent
                    assert "caller_fn" in prompt  # caller
                    found = True
                    break
            assert found, "Expected a call_llm call with MyClass.foo prompt"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)


class TestEnrichDryRunEstimate:
    def test_estimate_printed(self, db_conn, capsys):
        _insert_node(db_conn, "test.py::function::foo")
        _insert_node(db_conn, "test.py::function::bar", name="bar", qualified_name="bar")
        enrich_nodes(db_conn, dry_run=True)
        captured = capsys.readouterr()
        assert "2 nodes to enrich" in captured.err
        assert "~1 minutes" in captured.err


class TestResolveProviderAndModel:
    def test_defaults_to_anthropic(self):
        provider, model = _resolve_provider_and_model(None, None)
        assert provider == "anthropic"
        assert model == DEFAULT_MODELS["anthropic"]

    def test_explicit_provider(self):
        provider, model = _resolve_provider_and_model("openai", None)
        assert provider == "openai"
        assert model == DEFAULT_MODELS["openai"]

    def test_explicit_model_defaults_provider(self):
        provider, model = _resolve_provider_and_model(None, "custom-model")
        assert provider == "anthropic"
        assert model == "custom-model"

    def test_auto_detect_openai(self):
        os.environ["OPENAI_API_KEY"] = "test"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            provider, model = _resolve_provider_and_model(None, None)
            assert provider == "openai"
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_auto_detect_openrouter(self):
        os.environ["OPENROUTER_API_KEY"] = "test"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            provider, model = _resolve_provider_and_model(None, None)
            assert provider == "openrouter"
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)

    def test_auto_detect_litellm_base_url(self):
        os.environ["LITELLM_BASE_URL"] = "http://localhost:4000/v1"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("LITELLM_API_KEY", None)
        try:
            provider, model = _resolve_provider_and_model(None, None)
            assert provider == "litellm"
        finally:
            os.environ.pop("LITELLM_BASE_URL", None)

    def test_anthropic_takes_priority(self):
        os.environ["ANTHROPIC_API_KEY"] = "test"
        os.environ["OPENAI_API_KEY"] = "test"
        try:
            provider, model = _resolve_provider_and_model(None, None)
            assert provider == "anthropic"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)


class TestCallLlmProviders:
    @patch("indexer.enricher._call_anthropic")
    def test_anthropic_provider(self, mock_call):
        mock_call.return_value = "response"
        result = call_llm("prompt", "claude-sonnet-4-6", provider="anthropic")
        assert result == "response"
        mock_call.assert_called_once_with("prompt", "claude-sonnet-4-6")

    @patch("indexer.enricher._call_openai_compat")
    def test_openai_provider(self, mock_call):
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            mock_call.return_value = "response"
            result = call_llm("prompt", "gpt-4o", provider="openai")
            assert result == "response"
            mock_call.assert_called_once_with("prompt", "gpt-4o", api_key="test-key", base_url=None)
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    @patch("indexer.enricher._call_openai_compat")
    def test_openrouter_provider(self, mock_call):
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        try:
            mock_call.return_value = "response"
            result = call_llm("prompt", "anthropic/claude-sonnet-4-6", provider="openrouter")
            assert result == "response"
            mock_call.assert_called_once_with(
                "prompt", "anthropic/claude-sonnet-4-6",
                api_key="test-key", base_url="https://openrouter.ai/api/v1",
            )
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)

    @patch("indexer.enricher._call_openai_compat")
    def test_litellm_provider(self, mock_call):
        os.environ["LITELLM_API_KEY"] = "test-key"
        os.environ["LITELLM_BASE_URL"] = "http://localhost:4000/v1"
        try:
            mock_call.return_value = "response"
            result = call_llm("prompt", "gpt-4o", provider="litellm")
            assert result == "response"
            mock_call.assert_called_once_with(
                "prompt", "gpt-4o",
                api_key="test-key", base_url="http://localhost:4000/v1",
            )
        finally:
            os.environ.pop("LITELLM_API_KEY", None)
            os.environ.pop("LITELLM_BASE_URL", None)


class TestEnrichWithProvider:
    @patch("indexer.enricher.call_llm")
    def test_enrich_with_openai(self, mock_llm, db_conn):
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            _insert_node(db_conn, "test.py::function::foo")
            mock_llm.return_value = _make_llm_response()

            exit_code = enrich_nodes(db_conn, provider="openai")
            assert exit_code == 0
            mock_llm.assert_called_once()
            _, kwargs = mock_llm.call_args
            assert kwargs["provider"] == "openai"
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_missing_openai_key_exits_2(self, db_conn):
        os.environ.pop("OPENAI_API_KEY", None)
        _insert_node(db_conn, "test.py::function::foo")
        with pytest.raises(SystemExit) as exc:
            enrich_nodes(db_conn, provider="openai")
        assert exc.value.code == 2

    def test_missing_openrouter_key_exits_2(self, db_conn):
        os.environ.pop("OPENROUTER_API_KEY", None)
        _insert_node(db_conn, "test.py::function::foo")
        with pytest.raises(SystemExit) as exc:
            enrich_nodes(db_conn, provider="openrouter")
        assert exc.value.code == 2

    def test_litellm_accepts_base_url_only(self, db_conn):
        """LiteLLM should not exit if only LITELLM_BASE_URL is set (no key)."""
        os.environ.pop("LITELLM_API_KEY", None)
        os.environ["LITELLM_BASE_URL"] = "http://localhost:4000/v1"
        try:
            _insert_node(db_conn, "test.py::function::foo")
            # Should not exit 2 -- the actual LLM call will fail but key check passes
            with patch("indexer.enricher.call_llm", return_value=_make_llm_response()):
                exit_code = enrich_nodes(db_conn, provider="litellm")
                assert exit_code == 0
        finally:
            os.environ.pop("LITELLM_BASE_URL", None)
