"""Phase 3: LLM semantic enrichment."""

import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone

import anthropic
import click

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"

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


def _get_unenriched_nodes(conn):
    """Select nodes where enriched_at IS NULL."""
    return conn.execute(
        "SELECT id, file_path, node_type, name, qualified_name, signature, docstring, raw_source FROM nodes WHERE enriched_at IS NULL"
    ).fetchall()


def build_node_context(node_id, conn):
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


def call_llm(prompt, model):
    """Call Claude API with the enrichment prompt. Returns the response text.

    Retries with exponential backoff on RateLimitError/APITimeoutError.
    """
    client = anthropic.Anthropic()

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except (anthropic.RateLimitError, anthropic.APITimeoutError) as e:
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


def _rebuild_fts(conn):
    """Rebuild FTS5 index from nodes table."""
    conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")


def enrich_nodes(conn, model=None, dry_run=False):
    """Enrich unenriched nodes with LLM-generated metadata.

    Returns exit code: 0 if all enriched, 1 if any remain.
    """
    if model is None:
        model = DEFAULT_MODEL

    # Check API key (unless dry run)
    if not dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            click.echo("[ERROR] ANTHROPIC_API_KEY not set.", err=True)
            sys.exit(2)

    nodes = _get_unenriched_nodes(conn)
    count = len(nodes)
    estimated_minutes = math.ceil(count / 60) if count > 0 else 0

    click.echo(f"{count} nodes to enrich. Estimated time: ~{estimated_minutes} minutes.", err=True)

    if dry_run:
        return 0

    if count == 0:
        _update_meta(conn)
        return 0

    enriched_count = 0
    for node_row in nodes:
        node_id = node_row[0]
        qualified_name = node_row[4]
        try:
            context = build_node_context(node_id, conn)
            prompt = _build_prompt(node_row, context)
            response = call_llm(prompt, model)
            parsed = parse_enrichment_response(response)

            if parsed is None:
                click.echo(f"[WARNING] Malformed JSON for node {qualified_name}, skipping.", err=True)
                continue

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE nodes SET semantic_summary = ?, domain_tags = ?, inferred_responsibility = ?, enriched_at = ?, enrichment_model = ? WHERE id = ?",
                (parsed["semantic_summary"], parsed["domain_tags"], parsed["inferred_responsibility"], now, model, node_id),
            )
            conn.commit()
            enriched_count += 1
        except Exception as e:
            click.echo(f"[WARNING] Failed to enrich node {qualified_name}: {e}", err=True)
            continue

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


def _update_meta(conn):
    """Update index_meta.unenriched_nodes count."""
    remaining = conn.execute("SELECT COUNT(*) FROM nodes WHERE enriched_at IS NULL").fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
        ("unenriched_nodes", str(remaining)),
    )
    conn.commit()
