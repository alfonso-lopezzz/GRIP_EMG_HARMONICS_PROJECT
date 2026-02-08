"""Package metadata and convenience entry points for the EMG app."""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:
	"""Invoke the Tk application entry point without importing eagerly."""
	from .main import main as _main

	_main()
