"""Per-language parser modules for the code indexer."""

from indexer.parsers.base import parse_file, parse_directory  # noqa: F401

__all__ = ["parse_file", "parse_directory"]
