"""Runtime rectangle store shared by drawing features and the toolbar.

Why a store instead of plain feature options?

* The floating toolbar edits rectangles while the application is running and has
  to persist those edits to the TOML configuration immediately.
* Rectangle layouts are grouped by resolution and the active group can change at
  runtime (display change, manual selection, config hot reload).
* The toolbar needs transient previews (rubber band, drag highlight) that must
  never be written to the configuration.

``LayoutStore`` owns that state.  Features only read from it through
``draw_shapes()``; the toolbar mutates it.  Every mutation is written back with
the comment-preserving editor in :mod:`screenpainter.toml_edit`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from screenpainter.core.display import ResolutionMatch, ResolutionMode
from screenpainter.core.surface import Color
from screenpainter.errors import ConfigWriteError
from screenpainter.toml_edit import TomlDocument

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from screenpainter.config import Config

DEFAULT_COLOR = "#000000"
DEFAULT_OPACITY = 1.0


@dataclass(frozen=True, slots=True)
class Shape:
    """One solid rectangle, in overlay (target window) coordinates."""

    x: int
    y: int
    width: int
    height: int
    name: str = ""
    color: str = DEFAULT_COLOR
    opacity: float = DEFAULT_OPACITY

    def color_value(self) -> Color:
        return Color.from_hex(self.color, self.opacity)

    def translated(self, dx: int, dy: int) -> "Shape":
        return replace(self, x=self.x + int(dx), y=self.y + int(dy))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "x": int(self.x),
            "y": int(self.y),
            "width": int(self.width),
            "height": int(self.height),
            "color": self.color,
            "opacity": float(self.opacity),
        }


@dataclass(frozen=True, slots=True)
class LayoutGroup:
    """A named rectangle layout for one resolution signature."""

    name: str
    resolution: str
    description: str = ""
    shapes: tuple[Shape, ...] = ()


@dataclass(frozen=True, slots=True)
class LayoutSettings:
    """The ``[layout]`` section."""

    mode: ResolutionMode = "auto"
    match: ResolutionMatch = "monitor"
    group: str = ""
    hidden: bool = False


# ---------------------------------------------------------------------------
# Persistence helpers (also used by the command line)


def save_group_rectangles(
    config_path: Path, group_name: str, shapes: Sequence[Shape]
) -> bool:
    """Rewrite ``[groups.<name>] rectangles`` and save."""
    document = TomlDocument.from_path(config_path)
    document.set_inline_table_array(
        ("groups", group_name),
        "rectangles",
        [shape.to_mapping() for shape in shapes],
        empty_comment="no rectangles yet; draw one with the floating toolbar",
    )
    return document.write(config_path)


def save_layout_values(config_path: Path, values: Mapping[str, Any]) -> bool:
    document = TomlDocument.from_path(config_path)
    document.set_values(("layout",), values)
    return document.write(config_path)


def save_app_values(config_path: Path, values: Mapping[str, Any]) -> bool:
    document = TomlDocument.from_path(config_path)
    document.set_values(("app",), values)
    return document.write(config_path)


def save_new_group(
    config_path: Path,
    *,
    name: str,
    resolution: str,
    description: str = "",
    shapes: Sequence[Shape] = (),
) -> bool:
    document = TomlDocument.from_path(config_path)
    if document.has_section(("groups", name)):
        raise ConfigWriteError(f"[groups.{name}] already exists in {config_path}")
    document.create_section(
        ("groups", name),
        {"description": description, "resolution": resolution},
    )
    document.set_inline_table_array(
        ("groups", name),
        "rectangles",
        [shape.to_mapping() for shape in shapes],
        empty_comment="no rectangles yet; draw one with the floating toolbar",
    )
    return document.write(config_path)


def save_feature_options(
    config_path: Path, instance_name: str, values: Mapping[str, Any]
) -> bool:
    """Persist options under ``[features.<instance_name>]``."""
    document = TomlDocument.from_path(config_path)
    document.set_values(("features", instance_name), values)
    return document.write(config_path)


# ---------------------------------------------------------------------------
# Runtime store


class LayoutStore:
    """Holds the configured groups, the active group and transient previews."""

    def __init__(
        self,
        config: "Config",
        *,
        logger: logging.Logger | None = None,
        notify_config_saved: Callable[[], None] | None = None,
    ) -> None:
        # Imported lazily: logging_setup imports the config module, and the config
        # module imports this one for the shape/group models.
        from screenpainter.logging_setup import get_logger

        self.logger = logger or get_logger("layout")
        self._config = config
        self._config_path = Path(config.path)
        self._notify_saved = notify_config_saved or (lambda: None)

        self.groups: dict[str, LayoutGroup] = {}
        self.settings = LayoutSettings()
        self.signature = ""
        self._active_name: str | None = None
        self._preview: tuple[Shape, ...] = ()
        self._preview_replaces = False
        self.apply_config(config)

    # -- configuration ---------------------------------------------------
    def apply_config(self, config: "Config") -> None:
        self._config = config
        self._config_path = Path(config.path)
        self.groups = {group.name: group for group in config.groups}
        self.settings = config.layout
        self._preview = ()
        self._preview_replaces = False
        self._recompute_active()

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def mode(self) -> ResolutionMode:
        return self.settings.mode

    @property
    def match(self) -> ResolutionMatch:
        return self.settings.match

    @property
    def hidden(self) -> bool:
        return self.settings.hidden

    # -- resolution / active group --------------------------------------
    def update_signature(self, signature: str) -> bool:
        """Tell the store which resolution the target currently has."""
        signature = signature or ""
        if signature == self.signature:
            return False
        previous = self.signature
        self.signature = signature
        changed = self._recompute_active()
        self.logger.debug(
            "resolution signature %r -> %r (active group: %s)",
            previous,
            signature,
            self._active_name or "<none>",
        )
        return changed

    @property
    def active_group_name(self) -> str | None:
        return self._active_name

    @property
    def active_group(self) -> LayoutGroup | None:
        if self._active_name is None:
            return None
        return self.groups.get(self._active_name)

    def _recompute_active(self, *, log: bool = True) -> bool:
        previous = self._active_name
        candidate: str | None = None
        if self.settings.mode == "manual" and self.settings.group:
            if self.settings.group in self.groups:
                candidate = self.settings.group
            elif log:
                self.logger.warning(
                    "[layout] mode=manual selects unknown group %r; available: %s",
                    self.settings.group,
                    ", ".join(sorted(self.groups)) or "<none>",
                )
        else:
            for group in self.groups.values():
                if self.signature and group.resolution == self.signature:
                    candidate = group.name
                    break
        self._active_name = candidate
        if candidate != previous and log:
            if candidate is None:
                self.logger.info(
                    "no rectangle group matches signature %r (mode=%s); "
                    "draw with the toolbar to create one",
                    self.signature,
                    self.settings.mode,
                )
            else:
                self.logger.info(
                    "active rectangle group: %s (%s)",
                    candidate,
                    self.groups[candidate].resolution,
                )
        return candidate != previous

    def set_signature_now(self, signature: str) -> None:
        """Set the signature without recomputing (used before first target)."""
        self.signature = signature or ""

    # -- drawing ---------------------------------------------------------
    def active_shapes(self) -> tuple[Shape, ...]:
        group = self.active_group
        return group.shapes if group is not None else ()

    def draw_shapes(self) -> tuple[Shape, ...]:
        """Compose the shapes to paint on the overlay."""
        if self._preview_replaces:
            return self._preview
        base: tuple[Shape, ...] = () if self.settings.hidden else self.active_shapes()
        if not self._preview:
            return base
        return base + self._preview

    @property
    def preview_shapes(self) -> tuple[Shape, ...]:
        return self._preview

    @property
    def preview_replaces(self) -> bool:
        return self._preview_replaces

    def set_preview(self, shapes: Iterable[Shape], *, replace_base: bool = False) -> None:
        self._preview = tuple(shapes)
        self._preview_replaces = bool(replace_base)

    def clear_preview(self) -> None:
        self._preview = ()
        self._preview_replaces = False

    # -- mutations (persisted) ------------------------------------------
    def ensure_group_for_drawing(self) -> str | None:
        """Return the active group, creating a matching group when needed."""
        if self._active_name is not None:
            return self._active_name
        if not self.signature:
            self.logger.warning(
                "cannot create a rectangle group: the target window has no resolution yet"
            )
            return None
        base_name = "auto_" + self.signature.replace(" ", "_")
        name = base_name
        counter = 2
        while name in self.groups:
            name = f"{base_name}_{counter}"
            counter += 1
        group = LayoutGroup(
            name=name,
            resolution=self.signature,
            description=f"created automatically for {self.signature}",
        )
        try:
            save_new_group(
                self._config_path,
                name=name,
                resolution=self.signature,
                description=group.description,
            )
        except ConfigWriteError:
            self.logger.exception("could not persist the new rectangle group %r", name)
        else:
            self._notify_saved()
        self.groups[name] = group
        self._recompute_active()
        self.logger.info("created rectangle group %r for %s", name, self.signature)
        return name

    def add_shapes(self, shapes: Sequence[Shape]) -> int:
        """Append shapes to the active group and persist the result."""
        if not shapes:
            return 0
        name = self.ensure_group_for_drawing()
        if name is None:
            return 0
        group = self.groups[name]
        merged = group.shapes + tuple(shapes)
        self._commit_group(name, merged)
        return len(shapes)

    def replace_shapes(
        self, shapes: Sequence[Shape], *, group_name: str | None = None
    ) -> bool:
        name = group_name or self._active_name
        if name is None or name not in self.groups:
            return False
        if tuple(shapes) == self.groups[name].shapes:
            return False
        self._commit_group(name, tuple(shapes))
        return True

    def _commit_group(self, name: str, shapes: tuple[Shape, ...]) -> None:
        self.groups[name] = replace(self.groups[name], shapes=shapes)
        try:
            save_group_rectangles(self._config_path, name, shapes)
        except ConfigWriteError:
            self.logger.exception(
                "could not persist %d rectangle(s) to [groups.%s]", len(shapes), name
            )
        else:
            self._notify_saved()

    def set_hidden(self, hidden: bool) -> bool:
        hidden = bool(hidden)
        if hidden == self.settings.hidden:
            return False
        self.settings = replace(self.settings, hidden=hidden)
        try:
            save_layout_values(self._config_path, {"hidden": hidden})
        except ConfigWriteError:
            self.logger.exception("could not persist [layout] hidden = %s", hidden)
        else:
            self._notify_saved()
        self.logger.info("shapes are now %s", "hidden" if hidden else "visible")
        return True

    def set_active_group(self, name: str, *, mode: ResolutionMode = "manual") -> bool:
        if name not in self.groups:
            raise ConfigWriteError(f"unknown rectangle group {name!r}")
        values: dict[str, Any] = {"mode": mode, "group": name}
        self.settings = replace(self.settings, mode=mode, group=name)
        self._recompute_active()
        try:
            save_layout_values(self._config_path, values)
        except ConfigWriteError:
            self.logger.exception("could not persist the active rectangle group")
            return False
        self._notify_saved()
        self.logger.info("active rectangle group set to %r (mode=%s)", name, mode)
        return True

    def set_mode(self, mode: ResolutionMode) -> bool:
        self.settings = replace(self.settings, mode=mode)
        self._recompute_active()
        try:
            save_layout_values(self._config_path, {"mode": mode})
        except ConfigWriteError:
            self.logger.exception("could not persist [layout] mode = %s", mode)
            return False
        self._notify_saved()
        self.logger.info("layout mode set to %s", mode)
        return True
