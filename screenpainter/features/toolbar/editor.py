"""Rectangle editing state: selection, dragging, resizing, undo/redo.

The toolbar feature owns the windows; all decisions about *what* the user is
editing live here, which keeps that logic unit testable without a desktop.

Two modes share the same machinery:

``edit``
    Every rectangle gets a thin outline.  Clicking selects, dragging inside
    moves, dragging an edge or corner resizes, dragging empty space draws a new
    rectangle.  The selected rectangle gets a thick outline plus corner handles.
``move``
    Every rectangle gets a thin outline.  Clicking selects, dragging moves.
    Empty space only clears the selection.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from screenpainter.core.geometry import Rect
from screenpainter.core.layout import Shape
from screenpainter.core.surface import Color

MODE_EDIT = "edit"
MODE_MOVE = "move"

_HISTORY_LIMIT = 200
_FLUSH_DELAY_SECONDS = 0.35

# Resize edge bit flags.
EDGE_LEFT = 1
EDGE_RIGHT = 2
EDGE_TOP = 4
EDGE_BOTTOM = 8
EDGE_LR = EDGE_LEFT | EDGE_RIGHT
EDGE_TB = EDGE_TOP | EDGE_BOTTOM


@dataclass(frozen=True, slots=True)
class EditorStyle:
    """Colours and sizes used for the editing overlay."""

    outline_color: str = "#FF3B30"
    outline_width: int = 1
    selection_color: str = "#0A84FF"
    selection_width: int = 3
    handle_size: int = 7
    handle_tolerance: int = 6
    min_size: int = 2

    def validated(self) -> "EditorStyle":
        return replace(
            self,
            outline_width=max(1, int(self.outline_width)),
            selection_width=max(1, int(self.selection_width)),
            handle_size=max(1, int(self.handle_size)),
            handle_tolerance=max(1, int(self.handle_tolerance)),
            min_size=max(1, int(self.min_size)),
        )


@dataclass(slots=True)
class _Drag:
    kind: str  # "create" | "move" | "resize"
    index: int
    start_x: int
    start_y: int
    original: tuple[Shape, ...]
    before: tuple[Shape, ...]
    edges: int = 0
    moved: bool = False
    current: Rect | None = None


# ---------------------------------------------------------------------------
# Geometry helpers (pure, used by tests and the editor)


def outline_shapes(
    rect: Rect,
    *,
    color: str = "#FF3B30",
    opacity: float = 1.0,
    thickness: int = 1,
) -> list[Shape]:
    """Return the four thin rectangles that draw an outline."""
    width = max(1, int(rect.width))
    height = max(1, int(rect.height))
    edge = max(1, min(int(thickness), width, height))
    pieces = [
        (rect.x, rect.y, width, edge),
        (rect.x, rect.y + height - edge, width, edge),
        (rect.x, rect.y, edge, height),
        (rect.x + width - edge, rect.y, edge, height),
    ]
    shapes: list[Shape] = []
    for index, (x, y, w, h) in enumerate(pieces):
        if w <= 0 or h <= 0:
            continue
        shapes.append(
            Shape(
                x=x,
                y=y,
                width=w,
                height=h,
                name=f"outline_{index}",
                color=color,
                opacity=opacity,
            )
        )
    return shapes


def handle_shapes(
    rect: Rect, *, color: str, size: int, opacity: float = 1.0
) -> list[Shape]:
    """Four small filled squares at the corners of ``rect``."""
    side = max(1, min(int(size), max(1, int(rect.width)), max(1, int(rect.height))))
    positions = (
        (rect.x, rect.y),
        (rect.x + max(0, int(rect.width) - side), rect.y),
        (rect.x, rect.y + max(0, int(rect.height) - side)),
        (
            rect.x + max(0, int(rect.width) - side),
            rect.y + max(0, int(rect.height) - side),
        ),
    )
    return [
        Shape(
            x=x,
            y=y,
            width=side,
            height=side,
            name=f"handle_{index}",
            color=color,
            opacity=opacity,
        )
        for index, (x, y) in enumerate(positions)
    ]


def rubber_band(
    rect: Rect,
    *,
    color: str = "#FF3B30",
    fill_opacity: float = 0.18,
    border_opacity: float = 0.95,
) -> list[Shape]:
    """Translucent fill plus a solid outline for the rectangle being drawn."""
    if rect.width < 1 or rect.height < 1:
        return []
    fill = Shape(
        x=rect.x,
        y=rect.y,
        width=rect.width,
        height=rect.height,
        name="preview_fill",
        color=color,
        opacity=fill_opacity,
    )
    return [fill, *outline_shapes(rect, color=color, opacity=border_opacity)]


def clamp_rect(rect: Rect, bounds: Rect) -> Rect | None:
    """Clip a dragged rectangle to the overlay area."""
    overlap = rect.intersection(Rect(0, 0, bounds.width, bounds.height))
    if overlap is None or overlap.width < 1 or overlap.height < 1:
        return None
    return overlap


def clamp_move(
    x: int, y: int, width: int, height: int, bounds: Rect
) -> tuple[int, int]:
    """Keep a dragged rectangle reachable: inside the area when it fits."""
    if width <= bounds.width:
        x = max(0, min(int(x), bounds.width - width))
    else:
        x = max(-int(width) + 1, min(int(x), bounds.width - 1))
    if height <= bounds.height:
        y = max(0, min(int(y), bounds.height - height))
    else:
        y = max(-int(height) + 1, min(int(y), bounds.height - 1))
    return int(x), int(y)


def hit_edges(shape: Shape, x: int, y: int, tolerance: int) -> int:
    """Which edges of ``shape`` the point ``(x, y)`` grabs (0 = none)."""
    left = int(shape.x)
    top = int(shape.y)
    right = left + int(shape.width)
    bottom = top + int(shape.height)
    if not (left - tolerance <= x <= right + tolerance):
        return 0
    if not (top - tolerance <= y <= bottom + tolerance):
        return 0
    edges = 0
    if abs(x - left) <= tolerance:
        edges |= EDGE_LEFT
    if abs(x - right) <= tolerance:
        edges |= EDGE_RIGHT
    if abs(y - top) <= tolerance:
        edges |= EDGE_TOP
    if abs(y - bottom) <= tolerance:
        edges |= EDGE_BOTTOM
    return edges


def resize_shape(
    shape: Shape,
    edges: int,
    x: int,
    y: int,
    *,
    bounds: Rect,
    min_size: int,
) -> Shape:
    """Move the grabbed ``edges`` of ``shape`` to the pointer position."""
    left = int(shape.x)
    top = int(shape.y)
    right = left + int(shape.width)
    bottom = top + int(shape.height)

    if edges & EDGE_LEFT:
        left = min(int(x), right - min_size)
    if edges & EDGE_RIGHT:
        right = max(int(x), left + min_size)
    if edges & EDGE_TOP:
        top = min(int(y), bottom - min_size)
    if edges & EDGE_BOTTOM:
        bottom = max(int(y), top + min_size)

    left = max(0, min(left, max(0, bounds.width - min_size)))
    top = max(0, min(top, max(0, bounds.height - min_size)))
    right = max(left + min_size, min(right, bounds.width))
    bottom = max(top + min_size, min(bottom, bounds.height))

    return replace(
        shape,
        x=left,
        y=top,
        width=max(min_size, right - left),
        height=max(min_size, bottom - top),
    )


def resize_cursor(edges: int) -> str:
    vertical = bool(edges & EDGE_TB)
    horizontal = bool(edges & EDGE_LR)
    if vertical and horizontal:
        return "sizenwse"
    if horizontal:
        return "sizewe"
    if vertical:
        return "sizens"
    return "move"


# ---------------------------------------------------------------------------


class RectangleEditor:
    """Selection, drag/resize, undo/redo and persistence for one layout group."""

    def __init__(
        self,
        *,
        persist: Callable[[tuple[Shape, ...]], bool] | None = None,
        style: EditorStyle | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.style = (style or EditorStyle()).validated()
        self.logger = logger or logging.getLogger("screenpainter.editor")
        self.mode: str | None = None
        self.shapes: list[Shape] = []
        self.selected: int | None = None
        self.bounds = Rect(0, 0, 1, 1)
        self._persist_callback = persist or (lambda shapes: False)
        self._drag: _Drag | None = None
        self._undo: list[tuple[Shape, ...]] = []
        self._redo: list[tuple[Shape, ...]] = []
        self._coalesce: str | None = None
        self._dirty = False
        self._flush_at = 0.0
        self.new_shape_color = "#000000"
        self.new_shape_opacity = 1.0

    # -- lifecycle -------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.mode is not None

    @property
    def dragging(self) -> bool:
        return self._drag is not None

    def begin(self, mode: str, shapes: Sequence[Shape], bounds: Rect) -> None:
        self.mode = MODE_EDIT if mode == MODE_EDIT else MODE_MOVE
        self.shapes = [replace(shape) for shape in shapes]
        self.bounds = Rect(0, 0, max(1, bounds.width), max(1, bounds.height))
        self.selected = None
        self._drag = None
        self._dirty = False
        self._coalesce = None

    def end(self) -> None:
        self.flush()
        self._drag = None
        self.mode = None
        self.selected = None
        self.shapes = []
        self._coalesce = None

    def set_area(self, bounds: Rect) -> None:
        self.bounds = Rect(0, 0, max(1, bounds.width), max(1, bounds.height))

    def set_new_shape_style(self, color: str, opacity: float) -> None:
        try:
            Color.from_hex(color, float(opacity))
        except ValueError:
            return
        self.new_shape_color = color
        self.new_shape_opacity = float(opacity)

    # -- queries ---------------------------------------------------------
    def selected_shape(self) -> Shape | None:
        if self.selected is None:
            return None
        if 0 <= self.selected < len(self.shapes):
            return self.shapes[self.selected]
        return None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def wants_flush(self, now: float) -> bool:
        return self._dirty and now >= self._flush_at

    def shape_at(self, x: int, y: int) -> int | None:
        for index in range(len(self.shapes) - 1, -1, -1):
            shape = self.shapes[index]
            if (
                shape.x <= x < shape.x + shape.width
                and shape.y <= y < shape.y + shape.height
            ):
                return index
        return None

    # -- selection -------------------------------------------------------
    def select(self, index: int | None) -> bool:
        if index is not None and not (0 <= index < len(self.shapes)):
            index = None
        if index == self.selected:
            return False
        self.selected = index
        return True

    def select_next(self, delta: int = 1) -> bool:
        if not self.shapes:
            self.selected = None
            return False
        if self.selected is None:
            new_index = 0 if delta >= 0 else len(self.shapes) - 1
        else:
            new_index = (self.selected + delta) % len(self.shapes)
        self.selected = new_index
        self._coalesce = None
        return True

    # -- pointer input ---------------------------------------------------
    def pointer_down(self, x: int, y: int) -> bool:
        if self.mode is None:
            return False
        self._coalesce = None
        self._dirty = False
        self._flush_at = 0.0
        original = tuple(self.shapes)

        if self.mode == MODE_EDIT:
            # Resizing the current selection takes priority over selecting
            # whatever happens to be underneath the handle.
            current = self.selected_shape()
            if current is not None:
                edges = hit_edges(current, x, y, self.style.handle_tolerance)
                if edges:
                    self._drag = _Drag(
                        kind="resize",
                        index=int(self.selected or 0),
                        start_x=x,
                        start_y=y,
                        original=original,
                        before=original,
                        edges=edges,
                    )
                    return True

            index = self.shape_at(x, y)
            if index is not None:
                self.selected = index
                shape = self.shapes[index]
                edges = hit_edges(shape, x, y, self.style.handle_tolerance)
                self._drag = _Drag(
                    kind="resize" if edges else "move",
                    index=index,
                    start_x=x,
                    start_y=y,
                    original=original,
                    before=original,
                    edges=edges,
                )
                return True

            self.selected = None
            self._drag = _Drag(
                kind="create",
                index=-1,
                start_x=x,
                start_y=y,
                original=original,
                before=original,
                current=Rect(x, y, 0, 0),
            )
            return True

        # move mode
        if not self.shapes:
            return False
        index = self.shape_at(x, y)
        if index is None:
            self.selected = None
            self._drag = None
            return True
        self.selected = index
        self._drag = _Drag(
            kind="move",
            index=index,
            start_x=x,
            start_y=y,
            original=original,
            before=original,
        )
        return True

    def pointer_move(self, x: int, y: int) -> bool:
        drag = self._drag
        if drag is None:
            return False
        drag.moved = True
        if drag.kind == "create":
            drag.current = Rect(
                min(drag.start_x, x),
                min(drag.start_y, y),
                abs(x - drag.start_x),
                abs(y - drag.start_y),
            )
            return True
        if drag.kind == "move":
            original = drag.original[drag.index]
            new_x, new_y = clamp_move(
                original.x + (x - drag.start_x),
                original.y + (y - drag.start_y),
                original.width,
                original.height,
                self.bounds,
            )
            self.shapes[drag.index] = replace(original, x=new_x, y=new_y)
            return True
        if drag.kind == "resize":
            original = drag.original[drag.index]
            self.shapes[drag.index] = resize_shape(
                original,
                drag.edges,
                x,
                y,
                bounds=self.bounds,
                min_size=self.style.min_size,
            )
            return True
        return False

    def pointer_up(self, x: int, y: int) -> bool:
        drag = self._drag
        self._drag = None
        if drag is None:
            return False
        if drag.kind == "create":
            if not drag.moved:
                return False
            rect = Rect(
                min(drag.start_x, x),
                min(drag.start_y, y),
                abs(x - drag.start_x),
                abs(y - drag.start_y),
            )
            if rect.width < self.style.min_size or rect.height < self.style.min_size:
                return False
            clamped = clamp_rect(rect, self.bounds)
            if clamped is None:
                return False
            shape = Shape(
                x=clamped.x,
                y=clamped.y,
                width=clamped.width,
                height=clamped.height,
                name=self._next_name(),
                color=self.new_shape_color,
                opacity=self.new_shape_opacity,
            )
            self._record(drag.before)
            self.shapes.append(shape)
            self.selected = len(self.shapes) - 1
            self._persist_now()
            self.logger.info("created rectangle %s at %s", shape.name, clamped)
            return True

        changed = tuple(self.shapes) != drag.before
        if changed:
            self._record(drag.before)
            self._persist_now()
            shape = self.shapes[drag.index]
            self.logger.info(
                "%s rectangle %s to %d,%d %dx%d",
                "resized" if drag.kind == "resize" else "moved",
                shape.name,
                shape.x,
                shape.y,
                shape.width,
                shape.height,
            )
        return changed

    def cancel_drag(self) -> bool:
        drag = self._drag
        self._drag = None
        if drag is None:
            return False
        self.shapes = [replace(shape) for shape in drag.before]
        self._dirty = False
        return True

    # -- keyboard / direct edits -----------------------------------------
    def delete_selected(self) -> bool:
        index = self.selected
        if index is None or not (0 <= index < len(self.shapes)):
            return False
        before = tuple(self.shapes)
        name = self.shapes[index].name
        del self.shapes[index]
        if not self.shapes:
            self.selected = None
        else:
            self.selected = min(index, len(self.shapes) - 1)
        self._record(before)
        self._persist_now()
        self.logger.info("deleted rectangle %s", name)
        return True

    def nudge(self, dx: int, dy: int) -> bool:
        shape = self.selected_shape()
        if shape is None:
            return False
        before = tuple(self.shapes)
        new_x, new_y = clamp_move(
            shape.x + int(dx),
            shape.y + int(dy),
            shape.width,
            shape.height,
            self.bounds,
        )
        if (new_x, new_y) == (shape.x, shape.y):
            return False
        self.shapes[int(self.selected or 0)] = replace(shape, x=new_x, y=new_y)
        self._record(before, coalesce="nudge")
        self._dirty = True
        self._flush_at = time.monotonic() + _FLUSH_DELAY_SECONDS
        return True

    def undo(self) -> bool:
        if not self._undo:
            return False
        before = self._undo.pop()
        self._redo.append(tuple(self.shapes))
        self.shapes = [replace(shape) for shape in before]
        self._clamp_selection()
        self._dirty = False
        self._flush_at = 0.0
        self._coalesce = None
        self._persist_now()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        before = self._redo.pop()
        self._undo.append(tuple(self.shapes))
        self.shapes = [replace(shape) for shape in before]
        self._clamp_selection()
        self._dirty = False
        self._flush_at = 0.0
        self._coalesce = None
        self._persist_now()
        return True

    # -- persistence -----------------------------------------------------
    def flush(self) -> bool:
        """Write deferred changes (arrow-key nudges) to the configuration."""
        if not self._dirty:
            return False
        self._persist_now()
        return True

    def _persist_now(self) -> bool:
        self._dirty = False
        self._flush_at = 0.0
        if self.mode is None:
            return False
        return bool(self._persist_callback(tuple(self.shapes)))

    def _record(self, before: tuple[Shape, ...], *, coalesce: str | None = None) -> None:
        if coalesce is not None and self._coalesce == coalesce and self._undo:
            self._redo.clear()
            return
        self._undo.append(before)
        if len(self._undo) > _HISTORY_LIMIT:
            self._undo.pop(0)
        self._redo.clear()
        self._coalesce = coalesce

    def _clamp_selection(self) -> None:
        if self.selected is None:
            return
        if not self.shapes:
            self.selected = None
        elif self.selected >= len(self.shapes):
            self.selected = len(self.shapes) - 1

    def _next_name(self) -> str:
        used = {shape.name for shape in self.shapes}
        index = 1
        while f"rect_{index}" in used:
            index += 1
        return f"rect_{index}"

    # -- preview ---------------------------------------------------------
    def preview(self) -> list[Shape]:
        """Everything the overlay should draw while this editor is active."""
        shapes: list[Shape] = [replace(shape) for shape in self.shapes]
        for shape in self.shapes:
            rect = Rect(shape.x, shape.y, shape.width, shape.height)
            shapes.extend(
                outline_shapes(
                    rect,
                    color=self.style.outline_color,
                    thickness=self.style.outline_width,
                )
            )
        selected = self.selected_shape()
        if selected is not None:
            rect = Rect(selected.x, selected.y, selected.width, selected.height)
            shapes.extend(
                outline_shapes(
                    rect,
                    color=self.style.selection_color,
                    thickness=self.style.selection_width,
                )
            )
            if self.mode == MODE_EDIT:
                shapes.extend(
                    handle_shapes(
                        rect,
                        color=self.style.selection_color,
                        size=self.style.handle_size,
                    )
                )
        drag = self._drag
        if drag is not None and drag.kind == "create" and drag.current is not None:
            shapes.extend(
                rubber_band(drag.current, color=self.style.selection_color)
            )
        return shapes

    def cursor_at(self, x: int, y: int) -> str:
        if self.mode == MODE_EDIT and self.selected is not None:
            shape = self.selected_shape()
            if shape is not None:
                edges = hit_edges(shape, x, y, self.style.handle_tolerance)
                if edges:
                    return resize_cursor(edges)
        if self.shape_at(x, y) is not None:
            return "move"
        return "cross" if self.mode == MODE_EDIT else "arrow"
