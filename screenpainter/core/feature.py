"""Feature plugin API and in-process registry.

A feature is a self-contained effect.  New effects live in their own package
under ``screenpainter/features/`` and register themselves with
``@register_feature("type_name")``.  The application discovers the packages at
startup, creates the instances listed in the configuration, and calls the
lifecycle hooks below.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Mapping

from screenpainter.errors import FeatureError

from .surface import PixelSurface
from .window import WindowTarget

if TYPE_CHECKING:  # pragma: no cover - typing only
    from screenpainter.core.keymap import KeyMap
    from screenpainter.core.layout import LayoutStore
    from screenpainter.platform.base import WindowService


@dataclass(slots=True)
class FeatureContext:
    """Services and metadata handed to every feature instance."""

    instance_name: str
    type_name: str
    project_root: Path
    config_path: Path
    assets_dir: Path
    logger: logging.Logger
    request_repaint: Callable[[], None] = field(default=lambda: None)
    store: "LayoutStore | None" = None
    service: "WindowService | None" = None
    notify_config_saved: Callable[[], None] = field(default=lambda: None)
    request_config_reload: Callable[[], None] = field(default=lambda: None)
    keys: "KeyMap | None" = None

    @property
    def config_dir(self) -> Path:
        return self.config_path.parent

    def asset_path(self, relative: str | Path) -> Path:
        """Resolve a path below ``assets/`` and reject directory traversal."""
        candidate = (self.assets_dir / relative).resolve()
        assets_root = self.assets_dir.resolve()
        try:
            candidate.relative_to(assets_root)
        except ValueError as exc:
            raise FeatureError(
                f"asset path escapes assets/ directory: {relative!r}"
            ) from exc
        return candidate


class Feature:
    """Base class for drawable features.

    Only ``draw`` is required for a drawing feature.  Lifecycle hooks are
    intentionally small and stable so the app loop does not need to know about
    each feature's implementation details.
    """

    type_name: ClassVar[str] = ""

    def __init__(self, context: FeatureContext, options: Mapping[str, Any]) -> None:
        self.context = context
        self.logger = context.logger
        self.options = dict(options)

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        """Called once after construction, before the first frame."""

    def stop(self) -> None:
        """Called once on shutdown (also if a later feature fails to start)."""

    def on_target_changed(self, target: WindowTarget | None) -> None:
        """Called when the tracked window is attached, replaced, lost or hidden.

        ``None`` means "the target is temporarily unavailable": the window was
        closed, or the user minimized it (the overlay is hidden in that case).
        The same target is reported again when it becomes usable.
        """

    def tick(self, now: float) -> None:
        """Called on every loop iteration, even while no target is present.

        Animated features should advance their state in :meth:`update` (which
        only runs with a target); features with their own windows (for example
        the floating toolbar) poll their input here so the UI keeps responding
        while the application waits for the target window.
        """

    def update(self, target: WindowTarget, now: float) -> None:
        """Called each application tick while a target is available."""

    def draw(self, surface: PixelSurface, target: WindowTarget) -> None:
        """Draw into the transparent overlay surface."""

    # -- optional behaviour -------------------------------------------
    def request_repaint(self) -> None:
        self.context.request_repaint()

    def keep_alive(self) -> bool:
        """True when the app must keep the fast loop without a target.

        The toolbar is the main user: its buttons have to stay responsive even
        before the target window exists.
        """
        return False


_REGISTRY: dict[str, type[Feature]] = {}
def register_feature(feature_type: str) -> Callable[[type[Feature]], type[Feature]]:
    """Class decorator that adds a feature type to the global registry."""

    def decorator(cls: type[Feature]) -> type[Feature]:
        if not feature_type or not str(feature_type).strip():
            raise FeatureError("feature type must not be empty")
        if not isinstance(cls, type) or not issubclass(cls, Feature):
            raise FeatureError(f"{cls!r} must inherit from Feature")
        existing = _REGISTRY.get(feature_type)
        if existing is not None and existing is not cls:
            raise FeatureError(
                f"feature type {feature_type!r} is already registered by {existing.__module__}"
            )
        cls.type_name = feature_type
        _REGISTRY[feature_type] = cls
        return cls

    return decorator


def feature_class(feature_type: str) -> type[Feature]:
    try:
        return _REGISTRY[feature_type]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise FeatureError(
            f"unknown feature type {feature_type!r}; available types: {available}"
        ) from exc


def registered_feature_types() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def clear_registry_for_tests() -> None:
    """Test helper; production code registers once at import time."""
    _REGISTRY.clear()
