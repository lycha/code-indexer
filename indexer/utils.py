"""Shared utilities for the code indexer."""

import shutil
import sys

import click

__all__ = ["find_rg"]


def find_rg(*, required: bool = False) -> str | None:
    """Find the ripgrep binary on *PATH*.

    Returns the absolute path to ``rg``, or ``None`` if not found.
    When *required* is ``True`` and ripgrep is missing, an error is
    printed to stderr and the process exits with code 2.
    """
    rg = shutil.which("rg")
    if rg is None and required:
        click.echo(
            "[ERROR] ripgrep not found. Install it: "
            "https://github.com/BurntSushi/ripgrep#installation",
            err=True,
        )
        sys.exit(2)
    return rg
