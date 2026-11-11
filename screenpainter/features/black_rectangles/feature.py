"""Config-driven solid rectangles for occluding parts of a target window.

The rectangles themselves live in ``[groups.<name>]`` (they are grouped by
resolution and can be edited by the floating toolbar at runtime).  This feature
is the *painter*: every frame it copies the active group's shapes, plus any
transient preview, into the overlay surface.
"""

from __future__ import annotations

from typing import Any, Mapping

from screenpainter.core.feature import Feature, FeatureContext, register_feature
from screenpainter.core.surface import PixelSurface
from screenpainter.core.window import WindowTarget
from screenpainter.errors import FeatureError

_ALLOWED_KEYS: set[str] = set()


@register_feature("black_rectangles")
class BlackRectanglesFeature(Feature):
    """Draws the rectangles of the layout store's active group."""

    def __init__(self, context: FeatureContext, options: Mapping[str, Any]) -> None:
        super().__init__(context, options)
        unknown = sorted(set(options) - _ALLOWED_KEYS)
        if unknown:
            hint = ""
            if "rectangles" in unknown:
                hint = (
                    "; rectangles moved to [groups.<name>] in schema version 2 - "
                    "see docs/CONFIG.md"
                )
            raise FeatureError(
                f"feature {context.instance_name!r} has unknown option(s): "
                + ", ".join(repr(item) for item in unknown)
                + hint
            )
        if context.store is None:
            raise FeatureError(
                f"feature {context.instance_name!r} needs the layout store; "
                "this is an application bug, not a configuration problem"
            )

    def start(self) -> None:
        store = self.context.store
        assert store is not None
        group = store.active_group
        if group is not None:
            summary = f"group {group.name} with {len(group.shapes)} rectangle(s)"
        elif store.signature:
            summary = f"no group matches resolution {store.signature}"
        else:
            summary = "waiting for the target window to select a rectangle group"
        self.logger.info(
            "black_rectangles[%s] ready (%s)",
            self.context.instance_name,
            summary,
        )

    def draw(self, surface: PixelSurface, target: WindowTarget) -> None:
        # Surface clipping handles rectangles that are partially or entirely
        # outside the target, including when the target is resized smaller.
        store = self.context.store
        assert store is not None
        for shape in store.draw_shapes():
            surface.fill_rect(
                shape.x,
                shape.y,
                shape.width,
                shape.height,
                shape.color_value(),
            )
