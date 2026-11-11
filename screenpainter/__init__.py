"""ScreenPainter: a small, config-driven window overlay toolkit.

The public entry point is :func:`screenpainter.cli.main`.  The package is
intentionally split into small layers so that new drawable effects, window
resolvers and platform backends can be added without changing the application
loop.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.3.0"
