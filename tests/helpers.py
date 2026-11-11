"""Shared helpers for the unit tests (no windows, no desktop)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from screenpainter.config import Config, load_config

BASE_CONFIG = """\
version = 3

[app]
verify_ms = 1000
idle_tick_ms = 500
debug = false
debug_interval_ms = 400

[logging]
level = "DEBUG"
file = ""

[target]
window = "demo"

[overlay]
coordinate_space = "window"
click_through = true

{layout}

[groups.demo]
description = "demo"
resolution = "1920x1080"
rectangles = [
  {{ name = "one", x = 10, y = 20, width = 30, height = 40 }},
]

[window_profiles.demo]
description = "demo"
process_names = ["demo.exe"]
select = "largest"

[features.occluder]
type = "black_rectangles"
enabled = true

{features}
"""

DEFAULT_LAYOUT = """\
[layout]
mode = "auto"
match = "monitor"
group = ""
hidden = false
"""


def config_text(
    *,
    layout: str = DEFAULT_LAYOUT,
    features: str = "",
    groups: str = "",
) -> str:
    text = BASE_CONFIG.format(layout=layout, features=features)
    if groups:
        text += "\n" + groups
    return text


def make_config(text: str) -> tuple[Config, Path]:
    """Write a temporary configuration file and load it."""
    directory = Path(tempfile.mkdtemp(prefix="screenpainter-test-"))
    path = directory / "config.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path), directory


def cleanup(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)
