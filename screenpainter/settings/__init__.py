"""Settings window for the ScreenPainter configuration file.

Package layout:

``screenpainter.settings.model``
    Headless, tkinter free editing model: load the TOML file, stage changes,
    validate them against :func:`screenpainter.config.load_config`, then write
    them back surgically with :class:`screenpainter.toml_edit.TomlDocument`.

``screenpainter.settings.app``
    The Tkinter/ttk window itself (``main()`` entry point).

The model is importable and testable without a desktop; only ``app`` imports
Tk.  Nothing in this package is imported by the overlay application at runtime,
so the settings window can run next to a live ScreenPainter process and edit
the same file.
"""

from __future__ import annotations

from pathlib import Path

from .model import SettingsDraft, SettingsValidationError, SettingsValueError

__all__ = ["SettingsDraft", "SettingsValidationError", "SettingsValueError", "main"]


def main(config_path: Path | str | None = None) -> int:
    """Open the settings window; see :func:`screenpainter.settings.app.main`."""
    # Imported lazily so that ``import screenpainter.settings`` stays headless.
    from .app import main as _main

    return _main(config_path)
