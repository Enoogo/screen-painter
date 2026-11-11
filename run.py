"""Thin launcher so the project can be run without installing a package."""

from __future__ import annotations

from screenpainter.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
