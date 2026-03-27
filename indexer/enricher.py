"""Phase 3: LLM semantic enrichment."""

import asyncio
import json
import logging
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

from pathlib import Path

import anthropic
import click
import openai

__all__ = ["enrich_nodes", "enrich_directories", "enrich_files", "call_llm", "parse_enrichment_response", "build_node_context"]

logger = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai", "openrouter", "litellm")

DEFAULT_PROVIDER = "litellm"

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "openrouter": "anthropic/claude-sonnet-4-6",
    "litellm": "gemini-2.5-flash-lite",
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


def _sanitize_error(e: Exception) -> str:
    """Redact API keys from exception messages to prevent accidental leakage."""
    err_msg = str(e)
    for env_var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "LITELLM_API_KEY"):
        key_val = os.environ.get(env_var, "")
        if key_val and key_val in err_msg:
            err_msg = err_msg.replace(key_val, "***")
    return err_msg


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

FILE_SUMMARY_PROMPT = """Summarize this source file as a cohesive unit.

File: {file_path}

The file contains the following components:

{node_summaries}

Write a concise summary (2-4 sentences) covering:
1. The file's primary purpose and responsibility
2. Key classes, functions, or exports it provides
3. Its imports and dependencies on other files/modules
4. How it fits into the broader module/package

Return ONLY a JSON object:
{{"summary": "...", "domain_tags": ["tag1", "tag2"], "responsibility": "..."}}
"""


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


def _call_anthropic(prompt, model, system_prompt=None):
    """Call Anthropic API. Returns response text."""
    client = anthropic.Anthropic()
    kwargs = dict(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    if system_prompt:
        kwargs["system"] = system_prompt
    message = client.messages.create(**kwargs)
    return message.content[0].text


def _call_openai_compat(prompt, model, api_key=None, base_url=None, system_prompt=None):
    """Call an OpenAI-compatible API. Works for OpenAI, OpenRouter, and LiteLLM."""
    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    client = openai.OpenAI(**kwargs)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=messages,
    )
    return response.choices[0].message.content


def _retryable_exceptions(provider):
    """Return a tuple of retryable exception classes for the given provider."""
    if provider == "anthropic":
        return (anthropic.RateLimitError, anthropic.APITimeoutError)
    return (openai.RateLimitError, openai.APITimeoutError)


def call_llm(prompt, model, provider=None, system_prompt=None):
    """Call the configured LLM provider with the enrichment prompt. Returns the response text.

    Retries with exponential backoff on rate-limit/timeout errors.
    """
    if provider is None:
        provider = DEFAULT_PROVIDER

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            if provider == "anthropic":
                return _call_anthropic(prompt, model, system_prompt=system_prompt)
            else:
                env_key = _PROVIDER_ENV_KEYS.get(provider, "OPENAI_API_KEY")
                api_key = os.environ.get(env_key)
                base_url = _PROVIDER_BASE_URLS.get(provider)
                if provider == "litellm":
                    base_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
                return _call_openai_compat(prompt, model, api_key=api_key, base_url=base_url, system_prompt=system_prompt)
        except _retryable_exceptions(provider) as e:
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                click.echo(f"[WARNING] API error (attempt {attempt + 1}/{max_attempts}): {_sanitize_error(e)}. Retrying in {wait}s...", err=True)
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


async def _enrich_node_async(semaphore, node_row, conn, model, provider, system_prompt=None):
    """Enrich a single node asynchronously using a thread for the sync LLM call."""
    async with semaphore:
        node_id = node_row[0]
        node_type = node_row[2]
        qualified_name = node_row[4]

        # Skip file-level nodes
        if node_type == "file":
            return (node_id, qualified_name, None, "skipped")

        context = build_node_context(node_id, conn)
        prompt = _build_prompt(node_row, context)
        response = await asyncio.to_thread(call_llm, prompt, model, provider=provider, system_prompt=system_prompt)
        return (node_id, qualified_name, response, "ok")


async def _enrich_batch_async(nodes, conn, model, provider, concurrency=5, system_prompt=None):
    """Enrich nodes concurrently with a semaphore limiting concurrency."""
    semaphore = asyncio.Semaphore(concurrency)

    async def _safe_enrich(node_row, idx):
        try:
            return await _enrich_node_async(semaphore, node_row, conn, model, provider, system_prompt=system_prompt)
        except Exception as e:
            node_id = node_row[0]
            qualified_name = node_row[4]
            return (node_id, qualified_name, None, "error", e)

    tasks = [_safe_enrich(row, i) for i, row in enumerate(nodes)]
    return await asyncio.gather(*tasks)


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


def _build_system_prompt(conn: sqlite3.Connection):
    """Build a project-context system prompt from README, project summary, and directory tree.

    Returns the system prompt string, or None if no project context is available.
    """
    parts = ["You are a code documentation assistant with deep knowledge of this project."]

    # Determine repo root from database file path
    try:
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    except (TypeError, IndexError):
        db_path = None

    if db_path:
        repo_root = Path(db_path).parent.parent
        for readme_name in ("README.md", "readme.md", "README.rst", "README.txt"):
            readme_file = repo_root / readme_name
            if readme_file.exists():
                try:
                    readme_content = readme_file.read_text(errors="replace")[:3000]
                    parts.append(f"\n## README\n{readme_content}")
                except OSError:
                    pass
                break

    # Project summary from directory_summaries
    try:
        project_row = conn.execute(
            "SELECT summary FROM directory_summaries WHERE dir_path = '.' LIMIT 1"
        ).fetchone()
        if project_row and project_row[0]:
            parts.append(f"\n## Project Overview\n{project_row[0]}")
    except sqlite3.OperationalError:
        pass  # Table may not exist yet

    # Directory tree from directory_summaries
    try:
        dir_rows = conn.execute(
            "SELECT dir_path, summary FROM directory_summaries WHERE dir_path != '.' ORDER BY dir_path"
        ).fetchall()
        if dir_rows:
            tree_lines = ["## Repository Structure"]
            for dir_path, summary in dir_rows[:50]:  # Cap at 50 entries
                short_summary = (summary[:100] + "...") if summary and len(summary) > 100 else (summary or "")
                tree_lines.append(f"- {dir_path}/: {short_summary}")
            parts.append("\n".join(tree_lines))
    except sqlite3.OperationalError:
        pass  # Table may not exist yet

    if len(parts) > 1:
        return "\n\n".join(parts)
    return None


def enrich_nodes(conn: sqlite3.Connection, model=None, dry_run=False, provider=None, concurrency: int = 10):
    """Enrich unenriched nodes with LLM-generated metadata.

    Uses asyncio with configurable concurrency to call the LLM in parallel.
    SQLite operations remain synchronous.

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
    estimated_minutes = math.ceil(count / (60 * concurrency)) if count > 0 else 0

    click.echo(f"{count} nodes to enrich (provider={provider}, model={model}, concurrency={concurrency}). Estimated time: ~{estimated_minutes} minutes.", err=True)

    if dry_run:
        return 0

    if count == 0:
        _update_meta(conn)
        return 0

    # Build project context system prompt
    system_prompt = _build_system_prompt(conn)

    # Run async LLM calls (DB reads for context happen in threads too, but
    # SQLite in WAL mode handles concurrent readers fine)
    click.echo(f"[ENRICH] Starting concurrent enrichment with concurrency={concurrency}...", err=True)
    results = asyncio.run(_enrich_batch_async(nodes, conn, model, provider, concurrency=concurrency, system_prompt=system_prompt))

    # Process results synchronously (DB writes)
    enriched_count = 0
    _COMMIT_BATCH_SIZE = 50
    for i, result in enumerate(results):
        node_id = result[0]
        qualified_name = result[1]
        status = result[3] if len(result) > 3 else "ok"

        if status == "skipped":
            click.echo(f"[ENRICH] Skipping file-level node: {qualified_name}", err=True)
            continue

        if status == "error":
            err = result[4] if len(result) > 4 else "unknown error"
            click.echo(f"[WARNING] Failed to enrich node {qualified_name}: {type(err).__name__}: {_sanitize_error(err)}", err=True)
            continue

        response = result[2]
        click.echo(f"[ENRICH] Processing result {i + 1}/{count}: {qualified_name}", err=True)

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

    # Final commit for any remaining uncommitted enrichments
    if enriched_count % _COMMIT_BATCH_SIZE != 0:
        conn.commit()

    # Rebuild FTS5 after all enrichments (consistency check)
    if enriched_count > 0:
        _rebuild_fts(conn)
        conn.commit()

    _update_meta(conn)

    # Check if any remain unenriched
    remaining = conn.execute("SELECT COUNT(*) FROM nodes WHERE enriched_at IS NULL").fetchone()[0]
    if remaining > 0:
        return 1
    return 0


async def _enrich_file_async(semaphore, file_row, conn, model, provider, system_prompt=None):
    """Enrich a single file node asynchronously by aggregating its child node summaries."""
    async with semaphore:
        file_id, file_path, qualified_name = file_row

        # Gather enriched child node summaries (cap at 50)
        child_nodes = conn.execute(
            "SELECT qualified_name, node_type, signature, semantic_summary, domain_tags, inferred_responsibility "
            "FROM nodes WHERE file_path = ? AND node_type != 'file' AND enriched_at IS NOT NULL",
            (file_path,),
        ).fetchall()[:50]

        if not child_nodes:
            return (file_id, file_path, None, "skipped")

        # Format node summaries into readable text
        summary_parts = []
        for qname, ntype, sig, summary, tags, responsibility in child_nodes:
            parts = [f"- {ntype} {qname}"]
            if sig:
                parts[0] += f" ({sig})"
            if summary:
                parts.append(f"    Summary: {summary}")
            if tags:
                parts.append(f"    Tags: {tags}")
            if responsibility:
                parts.append(f"    Responsibility: {responsibility}")
            summary_parts.append("\n".join(parts))

        node_summaries_text = "\n".join(summary_parts)
        prompt = FILE_SUMMARY_PROMPT.format(file_path=file_path, node_summaries=node_summaries_text)
        response = await asyncio.to_thread(call_llm, prompt, model, provider=provider, system_prompt=system_prompt)
        return (file_id, file_path, response, "ok")


def enrich_files(conn, model=None, provider=None, concurrency=10):
    """Generate file-level summaries by aggregating node summaries."""
    provider, model = _resolve_provider_and_model(provider, model)

    # Check API key
    env_key = _PROVIDER_ENV_KEYS.get(provider, "OPENAI_API_KEY")
    api_key = os.environ.get(env_key)
    if not api_key and provider != "litellm":
        click.echo(f"[ERROR] {env_key} not set.", err=True)
        sys.exit(2)
    if provider == "litellm" and not api_key and not os.environ.get("LITELLM_BASE_URL"):
        click.echo("[ERROR] LITELLM_API_KEY or LITELLM_BASE_URL not set.", err=True)
        sys.exit(2)

    # Get unenriched file nodes
    file_nodes = conn.execute(
        "SELECT id, file_path, qualified_name FROM nodes WHERE node_type = 'file' AND enriched_at IS NULL"
    ).fetchall()

    click.echo(f"[FILE-ENRICH] {len(file_nodes)} file nodes to enrich (provider={provider}, model={model}).", err=True)

    if not file_nodes:
        return

    # Build project context system prompt
    system_prompt = _build_system_prompt(conn)

    # Run async LLM calls
    async def _enrich_all():
        semaphore = asyncio.Semaphore(concurrency)
        tasks = []
        for file_row in file_nodes:
            tasks.append(_enrich_file_async(semaphore, file_row, conn, model, provider, system_prompt=system_prompt))
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = asyncio.run(_enrich_all())

    # Process results synchronously (DB writes)
    enriched_count = 0
    for result in results:
        if isinstance(result, Exception):
            click.echo(f"[WARNING] File enrichment failed: {_sanitize_error(result)}", err=True)
            continue

        file_id, file_path, response, status = result

        if status == "skipped":
            click.echo(f"[FILE-ENRICH] Skipping {file_path} (no enriched child nodes).", err=True)
            continue

        parsed = _parse_dir_enrichment_response(response)
        if parsed is None:
            click.echo(f"[WARNING] Malformed JSON for file {file_path}, skipping.", err=True)
            continue

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE nodes SET semantic_summary = ?, domain_tags = ?, inferred_responsibility = ?, enriched_at = ?, enrichment_model = ? WHERE id = ?",
            (parsed["summary"], parsed["domain_tags"], parsed["responsibility"], now, model, file_id),
        )
        enriched_count += 1
        click.echo(f"[FILE-ENRICH] Enriched: {file_path}", err=True)

    if enriched_count > 0:
        conn.commit()
        _rebuild_fts(conn)
        conn.commit()

    click.echo(f"[FILE-ENRICH] Enriched {enriched_count} file nodes.", err=True)


DIRECTORY_SUMMARY_PROMPT = """\
You are a code documentation assistant. Summarize what this directory/module does.

Directory: {dir_path}

Contents:
{contents}

Respond in JSON only:
{{
  "summary": "2-3 sentences describing what this directory/module is responsible for.",
  "domain_tags": ["tag1", "tag2"],
  "responsibility": "Single sentence: the role of this directory in the broader system."
}}"""


def _parse_dir_enrichment_response(response):
    """Parse directory enrichment JSON response. Returns dict or None on failure."""
    try:
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        if "summary" not in data or "domain_tags" not in data or "responsibility" not in data:
            return None
        if not isinstance(data["domain_tags"], list):
            return None
        return {
            "summary": str(data["summary"]),
            "domain_tags": json.dumps(data["domain_tags"]),
            "responsibility": str(data["responsibility"]),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _needs_dir_enrichment(conn, dir_path):
    """Check if a directory needs (re-)enrichment."""
    row = conn.execute(
        "SELECT enriched_at FROM directory_summaries WHERE dir_path = ?", (dir_path,)
    ).fetchone()
    if row is None:
        return True
    dir_enriched_at = row[0]
    if dir_enriched_at is None:
        return True
    # Check if any child node has been enriched more recently
    # Match files directly in this dir (not subdirs)
    if dir_path == ".":
        # Top-level: files with no directory separator
        child_newer = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path NOT LIKE '%/%' AND enriched_at IS NOT NULL AND enriched_at > ?",
            (dir_enriched_at,),
        ).fetchone()[0]
    else:
        # Files directly in this dir: file_path starts with dir_path/ but has no additional /
        pattern = dir_path + "/%"
        child_newer = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path LIKE ? AND file_path NOT LIKE ? AND enriched_at IS NOT NULL AND enriched_at > ?",
            (pattern, dir_path + "/%/%", dir_enriched_at),
        ).fetchone()[0]
    return child_newer > 0


def _gather_dir_contents(conn, dir_path):
    """Gather content descriptions for a directory's direct children.

    Prefers file-level summaries when available; falls back to individual
    node summaries for files that don't have a file-level summary.
    """
    parts = []

    # 1. Get file-level summaries for files in this directory
    if dir_path == ".":
        file_summaries = conn.execute(
            "SELECT file_path, semantic_summary, domain_tags FROM nodes "
            "WHERE file_path NOT LIKE '%/%' AND node_type = 'file' AND enriched_at IS NOT NULL"
        ).fetchall()
    else:
        pattern = dir_path + "/%"
        file_summaries = conn.execute(
            "SELECT file_path, semantic_summary, domain_tags FROM nodes "
            "WHERE file_path LIKE ? AND file_path NOT LIKE ? AND node_type = 'file' AND enriched_at IS NOT NULL",
            (pattern, dir_path + "/%/%"),
        ).fetchall()

    # Track which files have file-level summaries
    files_with_summaries = set()
    for fpath, summary, tags in file_summaries:
        files_with_summaries.add(fpath)
        parts.append(f"File: {fpath}\n  Summary: {summary or 'no summary'}\n  Tags: {tags or '[]'}")

    # 2. Fall back to individual node summaries for files without file-level summaries
    if dir_path == ".":
        child_nodes = conn.execute(
            "SELECT qualified_name, semantic_summary, domain_tags, file_path FROM nodes "
            "WHERE file_path NOT LIKE '%/%' AND node_type != 'file' AND enriched_at IS NOT NULL"
        ).fetchall()
    else:
        pattern = dir_path + "/%"
        child_nodes = conn.execute(
            "SELECT qualified_name, semantic_summary, domain_tags, file_path FROM nodes "
            "WHERE file_path LIKE ? AND file_path NOT LIKE ? AND node_type != 'file' AND enriched_at IS NOT NULL",
            (pattern, dir_path + "/%/%"),
        ).fetchall()

    for qname, summary, tags, fpath in child_nodes:
        if fpath not in files_with_summaries:
            parts.append(f"- {qname}: {summary or 'no summary'} (tags: {tags or '[]'})")

    # 3. Child directory summaries (already computed since we go bottom-up)
    if dir_path == ".":
        # Top-level dirs: those with no / in dir_path (except '.' itself)
        child_dirs = conn.execute(
            "SELECT dir_path, summary, domain_tags FROM directory_summaries "
            "WHERE dir_path != '.' AND dir_path NOT LIKE '%/%'"
        ).fetchall()
    else:
        pattern = dir_path + "/%"
        child_dirs = conn.execute(
            "SELECT dir_path, summary, domain_tags FROM directory_summaries "
            "WHERE dir_path LIKE ? AND dir_path NOT LIKE ?",
            (pattern, dir_path + "/%/%"),
        ).fetchall()

    for dpath, summary, tags in child_dirs:
        parts.append(f"- [dir] {dpath}: {summary or 'no summary'} (tags: {tags or '[]'})")

    return "\n".join(parts) if parts else "(empty directory)"


async def _enrich_dir_async(semaphore, dir_path, conn, model, provider, system_prompt=None):
    """Enrich a single directory asynchronously."""
    async with semaphore:
        contents = _gather_dir_contents(conn, dir_path)
        prompt = DIRECTORY_SUMMARY_PROMPT.format(dir_path=dir_path, contents=contents)
        response = await asyncio.to_thread(call_llm, prompt, model, provider=provider, system_prompt=system_prompt)
        return (dir_path, response, contents, "ok")


def enrich_directories(conn, model=None, provider=None, concurrency=10):
    """Enrich directories with LLM-generated summaries, bottom-up.

    Generates summaries for each directory containing enriched nodes,
    then creates a project-level summary from top-level directory summaries.
    """
    provider, model = _resolve_provider_and_model(provider, model)

    # Check API key
    env_key = _PROVIDER_ENV_KEYS.get(provider, "OPENAI_API_KEY")
    api_key = os.environ.get(env_key)
    if not api_key and provider != "litellm":
        click.echo(f"[ERROR] {env_key} not set.", err=True)
        sys.exit(2)
    if provider == "litellm" and not api_key and not os.environ.get("LITELLM_BASE_URL"):
        click.echo("[ERROR] LITELLM_API_KEY or LITELLM_BASE_URL not set.", err=True)
        sys.exit(2)

    # Ensure directory_summaries table exists (migration may not have run in test fixtures)
    try:
        conn.execute("SELECT 1 FROM directory_summaries LIMIT 0")
    except sqlite3.OperationalError:
        click.echo("[WARNING] directory_summaries table not found, skipping directory enrichment.", err=True)
        return

    # Build project context system prompt
    system_prompt = _build_system_prompt(conn)

    # Collect all unique directory paths from enriched nodes
    rows = conn.execute(
        "SELECT DISTINCT file_path FROM nodes WHERE enriched_at IS NOT NULL"
    ).fetchall()

    dir_set = set()
    for (file_path,) in rows:
        d = os.path.dirname(file_path)
        while d:
            dir_set.add(d)
            d = os.path.dirname(d)
    # Always include project root
    dir_set.add(".")

    if not dir_set:
        click.echo("[DIR-ENRICH] No directories to enrich.", err=True)
        return

    # Sort bottom-up: deepest directories first
    dirs_sorted = sorted(dir_set, key=lambda p: p.count("/"), reverse=True)

    # Filter to those needing enrichment
    dirs_to_enrich = [d for d in dirs_sorted if d != "." and _needs_dir_enrichment(conn, d)]

    click.echo(f"[DIR-ENRICH] {len(dirs_to_enrich)} directories to enrich (provider={provider}, model={model}).", err=True)

    # Enrich directories bottom-up (sequential by depth level to ensure child summaries are available)
    # Group by depth
    if dirs_to_enrich:
        depth_groups = {}
        for d in dirs_to_enrich:
            depth = d.count("/")
            depth_groups.setdefault(depth, []).append(d)

        for depth in sorted(depth_groups.keys(), reverse=True):
            group = depth_groups[depth]

            async def _enrich_group(group_dirs):
                semaphore = asyncio.Semaphore(concurrency)
                tasks = []
                for d in group_dirs:
                    tasks.append(_enrich_dir_async(semaphore, d, conn, model, provider, system_prompt=system_prompt))
                return await asyncio.gather(*tasks, return_exceptions=True)

            results = asyncio.run(_enrich_group(group))

            for result in results:
                if isinstance(result, Exception):
                    click.echo(f"[WARNING] Directory enrichment failed: {_sanitize_error(result)}", err=True)
                    continue

                dir_path, response, contents, status = result
                parsed = _parse_dir_enrichment_response(response)
                if parsed is None:
                    click.echo(f"[WARNING] Malformed JSON for directory {dir_path}, skipping.", err=True)
                    continue

                now = datetime.now(timezone.utc).isoformat()
                # Count direct children (nodes + child dirs)
                child_count = contents.count("\n") + 1 if contents != "(empty directory)" else 0

                conn.execute(
                    "INSERT OR REPLACE INTO directory_summaries "
                    "(dir_path, summary, domain_tags, responsibility, child_count, enriched_at, enrichment_model) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (dir_path, parsed["summary"], parsed["domain_tags"], parsed["responsibility"],
                     child_count, now, model),
                )
                click.echo(f"[DIR-ENRICH] Enriched: {dir_path}", err=True)

            conn.commit()

    # Project-level summary (dir_path = '.')
    if _needs_dir_enrichment(conn, "."):
        click.echo("[DIR-ENRICH] Generating project-level summary...", err=True)
        contents = _gather_dir_contents(conn, ".")
        if contents != "(empty directory)":
            prompt = DIRECTORY_SUMMARY_PROMPT.format(dir_path=".", contents=contents)
            try:
                response = call_llm(prompt, model, provider=provider, system_prompt=system_prompt)
                parsed = _parse_dir_enrichment_response(response)
                if parsed:
                    now = datetime.now(timezone.utc).isoformat()
                    child_count = contents.count("\n") + 1
                    conn.execute(
                        "INSERT OR REPLACE INTO directory_summaries "
                        "(dir_path, summary, domain_tags, responsibility, child_count, enriched_at, enrichment_model) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (".", parsed["summary"], parsed["domain_tags"], parsed["responsibility"],
                         child_count, now, model),
                    )
                    conn.commit()
                    click.echo("[DIR-ENRICH] Project summary generated.", err=True)
                else:
                    click.echo("[WARNING] Malformed JSON for project summary.", err=True)
            except Exception as e:
                click.echo(f"[WARNING] Project summary failed: {_sanitize_error(e)}", err=True)
        else:
            click.echo("[DIR-ENRICH] No content for project summary.", err=True)


def _update_meta(conn: sqlite3.Connection):
    """Update index_meta.unenriched_nodes count."""
    remaining = conn.execute("SELECT COUNT(*) FROM nodes WHERE enriched_at IS NULL").fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
        ("unenriched_nodes", str(remaining)),
    )
    conn.commit()
