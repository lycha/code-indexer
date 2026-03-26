"""Phase 3: LLM semantic enrichment."""

import json
import logging
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import anthropic
import click
import openai

__all__ = ["enrich_nodes", "call_llm", "parse_enrichment_response", "build_node_context"]

logger = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai", "openrouter", "litellm")

DEFAULT_PROVIDER = "anthropic"

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "openrouter": "anthropic/claude-sonnet-4-6",
    "litellm": "gpt-4o",
}

_PROVIDER_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "litellm": "LITELLM_API_KEY",
}

_PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
}

ENRICHMENT_PROMPT_TEMPLATE = """\
You are a code documentation assistant. Given the following code node and its
immediate context, provide structured metadata.

Node:
  Type: {node_type}
  Qualified name: {qualified_name}
  Signature: {signature}
  Docstring: {docstring}
  Source:
    {raw_source}

Context:
  Parent: {parent}
  Children: {children}
  Called by: {callers}
  Calls: {callees}

Respond in JSON only:
{{
  "semantic_summary": "One to two sentences describing what this code does and why it exists, in plain English.",
  "domain_tags": ["tag1", "tag2"],
  "inferred_responsibility": "Single sentence: what this code is responsible for in the broader system."
}}"""


def _get_unenriched_nodes(conn: sqlite3.Connection):
    """Select nodes where enriched_at IS NULL."""
    return conn.execute(
        "SELECT id, file_path, node_type, name, qualified_name, signature, docstring, raw_source FROM nodes WHERE enriched_at IS NULL"
    ).fetchall()


def build_node_context(node_id: str, conn: sqlite3.Connection):
    """Build context dict with parent, children, callers, callees from edges table."""
    # Parent: node that has an edge where this node is a child (target of 'calls' from parent isn't right)
    # Parent = source of edges where target is this node and edge_type is not 'calls'
    # Actually, parent/children are structural. Let's use qualified_name hierarchy.
    # The spec says: parent + children + callers + callees from edges table.
    # callers = inbound 'calls' edges, callees = outbound 'calls' edges
    # parent/children = based on qualified_name hierarchy or imports/inherits edges

    # Get node's qualified_name for parent detection
    node_row = conn.execute(
        "SELECT qualified_name, file_path FROM nodes WHERE id = ?", (node_id,)
    ).fetchone()

    parent_info = "none"
    children_info = "[]"

    if node_row:
        qname = node_row[0]
        file_path = node_row[1]
        # Parent: find a node in the same file whose qualified_name is a prefix
        parts = qname.rsplit(".", 1)
        if len(parts) == 2:
            parent_qname = parts[0]
            parent = conn.execute(
                "SELECT qualified_name, signature FROM nodes WHERE file_path = ? AND qualified_name = ?",
                (file_path, parent_qname),
            ).fetchone()
            if parent:
                parent_info = f"{parent[0]} — {parent[1] or 'none'}"

        # Children: nodes whose qualified_name starts with this node's qname + "."
        children_rows = conn.execute(
            "SELECT qualified_name FROM nodes WHERE file_path = ? AND qualified_name LIKE ? AND qualified_name != ?",
            (file_path, qname + ".%", qname),
        ).fetchall()
        # Only direct children (one level deeper)
        direct_children = []
        for (cqn,) in children_rows:
            suffix = cqn[len(qname) + 1:]
            if "." not in suffix:
                direct_children.append(cqn)
        if direct_children:
            children_info = str(direct_children)

    # Callers: inbound 'calls' edges
    callers_rows = conn.execute(
        "SELECT n.qualified_name FROM edges e JOIN nodes n ON e.source_id = n.id WHERE e.target_id = ? AND e.edge_type = 'calls'",
        (node_id,),
    ).fetchall()
    callers_info = str([r[0] for r in callers_rows]) if callers_rows else "[]"

    # Callees: outbound 'calls' edges
    callees_rows = conn.execute(
        "SELECT n.qualified_name FROM edges e JOIN nodes n ON e.target_id = n.id WHERE e.source_id = ? AND e.edge_type = 'calls'",
        (node_id,),
    ).fetchall()
    callees_info = str([r[0] for r in callees_rows]) if callees_rows else "[]"

    return {
        "parent": parent_info,
        "children": children_info,
        "callers": callers_info,
        "callees": callees_info,
    }


def _build_prompt(node_row, context):
    """Build the enrichment prompt for a node."""
    _, _, node_type, _, qualified_name, signature, docstring, raw_source = node_row
    return ENRICHMENT_PROMPT_TEMPLATE.format(
        node_type=node_type,
        qualified_name=qualified_name,
        signature=signature or "none",
        docstring=docstring or "none",
        raw_source=raw_source or "",
        parent=context["parent"],
        children=context["children"],
        callers=context["callers"],
        callees=context["callees"],
    )


def _call_anthropic(prompt, model):
    """Call Anthropic API. Returns response text."""
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _call_openai_compat(prompt, model, api_key=None, base_url=None):
    """Call an OpenAI-compatible API. Works for OpenAI, OpenRouter, and LiteLLM."""
    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    client = openai.OpenAI(**kwargs)
    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _retryable_exceptions(provider):
    """Return a tuple of retryable exception classes for the given provider."""
    if provider == "anthropic":
        return (anthropic.RateLimitError, anthropic.APITimeoutError)
    return (openai.RateLimitError, openai.APITimeoutError)


def call_llm(prompt, model, provider=None):
    """Call the configured LLM provider with the enrichment prompt. Returns the response text.

    Retries with exponential backoff on rate-limit/timeout errors.
    """
    if provider is None:
        provider = DEFAULT_PROVIDER

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            if provider == "anthropic":
                return _call_anthropic(prompt, model)
            else:
                env_key = _PROVIDER_ENV_KEYS.get(provider, "OPENAI_API_KEY")
                api_key = os.environ.get(env_key)
                base_url = _PROVIDER_BASE_URLS.get(provider)
                if provider == "litellm":
                    base_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
                return _call_openai_compat(prompt, model, api_key=api_key, base_url=base_url)
        except _retryable_exceptions(provider) as e:
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                click.echo(f"[WARNING] API error (attempt {attempt + 1}/{max_attempts}): {e}. Retrying in {wait}s...", err=True)
                time.sleep(wait)
            else:
                raise


def parse_enrichment_response(response):
    """Parse enrichment JSON response. Returns dict or None on failure."""
    try:
        # Strip markdown code fences if present
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (fences)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)
        # Validate required keys
        if not isinstance(data, dict):
            return None
        if "semantic_summary" not in data or "domain_tags" not in data or "inferred_responsibility" not in data:
            return None
        # Ensure domain_tags is a list of strings
        if not isinstance(data["domain_tags"], list):
            return None
        return {
            "semantic_summary": str(data["semantic_summary"]),
            "domain_tags": json.dumps(data["domain_tags"]),
            "inferred_responsibility": str(data["inferred_responsibility"]),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _rebuild_fts(conn: sqlite3.Connection):
    """Rebuild FTS5 index from nodes table."""
    conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")


def _resolve_provider_and_model(provider, model):
    """Resolve provider and model defaults, auto-detecting provider from env if needed."""
    if provider is None:
        if model is not None:
            provider = DEFAULT_PROVIDER
        else:
            for prov, env_key in _PROVIDER_ENV_KEYS.items():
                if os.environ.get(env_key):
                    provider = prov
                    break
            if provider is None and os.environ.get("LITELLM_BASE_URL"):
                provider = "litellm"
            if provider is None:
                provider = DEFAULT_PROVIDER
    if model is None:
        model = DEFAULT_MODELS[provider]
    return provider, model


def enrich_nodes(conn: sqlite3.Connection, model=None, dry_run=False, provider=None):
    """Enrich unenriched nodes with LLM-generated metadata.

    Returns exit code: 0 if all enriched, 1 if any remain.
    """
    provider, model = _resolve_provider_and_model(provider, model)

    # Check API key (unless dry run)
    if not dry_run:
        env_key = _PROVIDER_ENV_KEYS.get(provider, "OPENAI_API_KEY")
        api_key = os.environ.get(env_key)
        if not api_key and provider != "litellm":
            click.echo(f"[ERROR] {env_key} not set.", err=True)
            sys.exit(2)
        if provider == "litellm" and not api_key and not os.environ.get("LITELLM_BASE_URL"):
            click.echo("[ERROR] LITELLM_API_KEY or LITELLM_BASE_URL not set.", err=True)
            sys.exit(2)

    nodes = _get_unenriched_nodes(conn)
    count = len(nodes)
    estimated_minutes = math.ceil(count / 60) if count > 0 else 0

    click.echo(f"{count} nodes to enrich (provider={provider}, model={model}). Estimated time: ~{estimated_minutes} minutes.", err=True)

    if dry_run:
        return 0

    if count == 0:
        _update_meta(conn)
        return 0

    enriched_count = 0
    _COMMIT_BATCH_SIZE = 50
    for node_idx, node_row in enumerate(nodes, 1):
        node_id = node_row[0]
        node_type = node_row[2]
        qualified_name = node_row[4]

        # Skip file-level nodes — their source is the entire file (can be 50K+
        # tokens) which blows up context windows and costs.  File nodes get
        # semantic metadata from their children's enrichment.
        if node_type == "file":
            click.echo(f"[ENRICH] Skipping file-level node: {qualified_name}", err=True)
            continue

        click.echo(f"[ENRICH] Processing node {node_idx}/{count}: {qualified_name}", err=True)
        try:
            context = build_node_context(node_id, conn)
            prompt = _build_prompt(node_row, context)
            response = call_llm(prompt, model, provider=provider)
            parsed = parse_enrichment_response(response)

            if parsed is None:
                click.echo(f"[WARNING] Malformed JSON for node {qualified_name}, skipping.", err=True)
                continue

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE nodes SET semantic_summary = ?, domain_tags = ?, inferred_responsibility = ?, enriched_at = ?, enrichment_model = ? WHERE id = ?",
                (parsed["semantic_summary"], parsed["domain_tags"], parsed["inferred_responsibility"], now, model, node_id),
            )
            enriched_count += 1
            if enriched_count % _COMMIT_BATCH_SIZE == 0:
                conn.commit()
            # Pace requests to avoid triggering API rate limits (429s)
            time.sleep(0.5)
        except (anthropic.APIError, openai.APIError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            click.echo(f"[WARNING] Failed to enrich node {qualified_name}: {type(e).__name__}: {e}", err=True)
            continue

    # Final commit for any remaining uncommitted enrichments
    if enriched_count % _COMMIT_BATCH_SIZE != 0:
        conn.commit()

    # Rebuild FTS5 after all enrichments
    if enriched_count > 0:
        _rebuild_fts(conn)
        conn.commit()

    _update_meta(conn)

    # Check if any remain unenriched
    remaining = conn.execute("SELECT COUNT(*) FROM nodes WHERE enriched_at IS NULL").fetchone()[0]
    if remaining > 0:
        return 1
    return 0


def _update_meta(conn: sqlite3.Connection):
    """Update index_meta.unenriched_nodes count."""
    remaining = conn.execute("SELECT COUNT(*) FROM nodes WHERE enriched_at IS NULL").fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
        ("unenriched_nodes", str(remaining)),
    )
    conn.commit()
