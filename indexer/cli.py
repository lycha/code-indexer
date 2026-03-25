"""CLI entry point for the code indexer."""

import sys

import click


@click.group()
@click.option("--db", "db_path", type=click.Path(), default=None, help="Path to the SQLite database file.")
@click.pass_context
def cli(ctx: click.Context, db_path: str | None) -> None:
    """Hybrid code indexing system — build, enrich, and query a code index."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path


@cli.command()
@click.option("--no-gitignore-update", is_flag=True, default=False, help="Skip automatic .gitignore update.")
@click.pass_context
def init(ctx: click.Context, no_gitignore_update: bool) -> None:
    """Initialise the code index database."""
    click.echo("[TODO] init not yet implemented", err=True)


@cli.command()
@click.option("--phase", type=click.Choice(["PREPARE", "DEPLOY"]), default=None, help="Phase boundary tag.")
@click.option("--token-limit", type=int, default=512, help="Token limit for cAST chunking.")
@click.option("--exclude", multiple=True, help="Glob patterns to exclude from parsing.")
@click.pass_context
def build(ctx: click.Context, phase: str | None, token_limit: int, exclude: tuple[str, ...]) -> None:
    """Parse source files and map dependencies."""
    click.echo("[TODO] build not yet implemented", err=True)


@cli.command()
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be enriched without making API calls.")
@click.option("--model", type=str, default=None, help="Override the LLM model for enrichment.")
@click.pass_context
def enrich(ctx: click.Context, dry_run: bool, model: str | None) -> None:
    """Enrich code nodes with LLM-generated semantic metadata."""
    click.echo("[TODO] enrich not yet implemented", err=True)


@cli.command()
@click.argument("query_text", default="")
@click.option("--type", "query_type", type=click.Choice(["lexical", "graph", "semantic"]), default=None, help="Query type.")
@click.option("--format", "output_format", type=click.Choice(["text", "json", "jsonl"]), default="text", help="Output format.")
@click.option("--with-source", is_flag=True, default=False, help="Include raw source in results.")
@click.option("--top-k", type=int, default=10, help="Maximum number of results.")
@click.option("--depth", type=int, default=2, help="Graph traversal depth.")
@click.pass_context
def query(ctx: click.Context, query_text: str, query_type: str | None, output_format: str, with_source: bool, top_k: int, depth: int) -> None:
    """Query the code index."""
    click.echo("[TODO] query not yet implemented", err=True)


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show index status and statistics."""
    click.echo("[TODO] status not yet implemented", err=True)


@cli.command()
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_context
def reset(ctx: click.Context, yes: bool) -> None:
    """Reset the code index by dropping and recreating all tables."""
    click.echo("[TODO] reset not yet implemented", err=True)
