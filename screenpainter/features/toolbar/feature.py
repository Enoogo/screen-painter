"""Floating toolbar: edit, move, hide and configure the rectangle mask.

The panel is a small always-on-top window with native buttons:

``编辑``
    Show every rectangle of the active group with an outline.  Click a
    rectangle to select it, drag it to move it, drag one of its edges or
    corners to resize it and drag empty space to draw a new rectangle.  The
    selected rectangle gets a thicker outline, corner handles and a small
    properties panel that shows its colour (hex), position and size.
    The button turns into ``完成``.
``移动``
    The same selection, but dragging only moves rectangles; empty space clears
    the selection.  The button turns into ``完成``.
``隐藏`` / ``显示``
    Toggle ``[layout] hidden``.
``设置``
    Open the settings window (a separate process) to edit every value of the
    configuration file.

While an editing mode is active, the configured shortcuts from ``[keys]`` are
captured by a low level keyboard hook (the capture window never takes focus):
delete, undo/redo, select next/previous, arrow-key nudging and escape/enter.
Every change is written back through the comment-preserving TOML editor, so the
configuration file stays the single source of truth.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from screenpainter.core.display import DisplayInfo, MonitorInfo
from screenpainter.core.feature import Feature, FeatureContext, register_feature
from screenpainter.core.geometry import Rect
from screenpainter.core.keymap import KeyMap, binding_names
from screenpainter.core.layout import LayoutStore, Shape
from screenpainter.core.surface import Color
from screenpainter.core.ui import (
    ButtonSpec,
    KeyEvent,
    PanelEvent,
    PanelStatus,
    PointerEvent,
)
from screenpainter.core.window import WindowTarget
from screenpainter.errors import ConfigWriteError, FeatureError, OverlayError
from screenpainter.platform.base import (
    ButtonPanel,
    KeyboardHook,
    PropertiesPanel,
    ScreenCapture,
)

from .editor import (
    MODE_EDIT,
    MODE_MOVE,
    EditorStyle,
    RectangleEditor,
    clamp_move,
    clamp_rect,
    handle_shapes,
    hit_edges,
    outline_shapes,
    resize_cursor,
    resize_shape,
    rubber_band,
)

__all__ = [
    "ToolbarFeature",
    "clamp_move",
    "clamp_rect",
    "handle_shapes",
    "hit_edges",
    "outline_shapes",
    "resize_cursor",
    "resize_shape",
    "rubber_band",
]

_EDIT_LABEL = "编辑"
_MOVE_LABEL = "移动"
_DONE_LABEL = "完成"
_HIDE_LABEL = "隐藏"
_SHOW_LABEL = "显示"
_SETTINGS_LABEL = "设置"

_KNOWN_BUTTONS = ("edit", "move", "hide", "settings")
_BUTTON_ALIASES = {"draw": "edit"}

_ALLOWED_OPTIONS = {
    "buttons",
    "auto_position",
    "x",
    "y",
    "default_color",
    "default_opacity",
    "min_drag_size",
    "title",
    "outline_color",
    "outline_width",
    "selection_color",
    "selection_width",
    "handle_size",
    "handle_tolerance",
    "properties_panel",
}

_PROPERTIES_WIDTH = 218
_PROPERTIES_HEIGHT = 172
_FAST_NUDGE = 10

_CREATE_NO_WINDOW = 0x08000000


@register_feature("toolbar")
class ToolbarFeature(Feature):
    """The floating button panel described above."""

    def __init__(self, context: FeatureContext, options: Mapping[str, Any]) -> None:
        super().__init__(context, options)
        unknown = sorted(set(options) - _ALLOWED_OPTIONS)
        if unknown:
            raise FeatureError(
                f"feature {context.instance_name!r} has unknown option(s): "
                + ", ".join(repr(item) for item in unknown)
                + f"; allowed: {', '.join(sorted(_ALLOWED_OPTIONS))}"
            )

        raw_buttons = options.get("buttons", list(_KNOWN_BUTTONS))
        if not isinstance(raw_buttons, list) or not raw_buttons:
            raise FeatureError(
                f"feature {context.instance_name!r}: 'buttons' must be a non-empty "
                'array such as ["edit", "move", "hide"]'
            )
        buttons: list[str] = []
        for item in raw_buttons:
            if not isinstance(item, str):
                raise FeatureError(
                    f"feature {context.instance_name!r}: option 'buttons' must "
                    "contain strings"
                )
            key = _BUTTON_ALIASES.get(item, item)
            if key not in _KNOWN_BUTTONS:
                raise FeatureError(
                    f"feature {context.instance_name!r}: unknown button {item!r}; "
                    f"available: {', '.join(_KNOWN_BUTTONS)}"
                )
            if key not in buttons:
                buttons.append(key)
        if "draw" in raw_buttons:
            self.logger.info(
                'toolbar button "draw" is deprecated; use "edit" instead'
            )
        self._button_order: tuple[str, ...] = tuple(buttons)

        self._auto_position = _boolean_option(
            options, "auto_position", True, context.instance_name
        )
        self._x = _integer_option(options, "x", 0, context.instance_name)
        self._y = _integer_option(options, "y", 0, context.instance_name)
        self._min_drag_size = _integer_option(
            options, "min_drag_size", 3, context.instance_name, minimum=1
        )
        self._title = _string_option(options, "title", "屏幕遮罩", context.instance_name)

        color_text = options.get("default_color", "#000000")
        if not isinstance(color_text, str):
            raise FeatureError(
                f"feature {context.instance_name!r}: 'default_color' must be a "
                "string like '#000000'"
            )
        opacity = options.get("default_opacity", 1.0)
        if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
            raise FeatureError(
                f"feature {context.instance_name!r}: 'default_opacity' must be a "
                "number between 0 and 1"
            )
        if not (0.0 <= float(opacity) <= 1.0):
            raise FeatureError(
                f"feature {context.instance_name!r}: 'default_opacity' must be "
                "between 0 and 1"
            )
        try:
            Color.from_hex(color_text, float(opacity))
        except ValueError as exc:
            raise FeatureError(f"feature {context.instance_name!r}: {exc}") from exc
        self._default_color = color_text
        self._default_opacity = float(opacity)

        self._style = EditorStyle(
            outline_color=_hex_option(
                options, "outline_color", "#FF3B30", context.instance_name
            ),
            outline_width=_integer_option(
                options, "outline_width", 1, context.instance_name, minimum=1
            ),
            selection_color=_hex_option(
                options, "selection_color", "#0A84FF", context.instance_name
            ),
            selection_width=_integer_option(
                options, "selection_width", 3, context.instance_name, minimum=1
            ),
            handle_size=_integer_option(
                options, "handle_size", 7, context.instance_name, minimum=1
            ),
            handle_tolerance=_integer_option(
                options, "handle_tolerance", 6, context.instance_name, minimum=1
            ),
            min_size=max(2, self._min_drag_size),
        ).validated()
        self._properties_enabled = _boolean_option(
            options, "properties_panel", True, context.instance_name
        )

        self._editor = RectangleEditor(
            persist=self._persist_shapes,
            style=self._style,
            logger=context.logger,
        )
        self._editor.set_new_shape_style(self._default_color, self._default_opacity)
        self._editor.style = EditorStyle(
            outline_color=self._style.outline_color,
            outline_width=self._style.outline_width,
            selection_color=self._style.selection_color,
            selection_width=self._style.selection_width,
            handle_size=self._style.handle_size,
            handle_tolerance=self._style.handle_tolerance,
            min_size=max(2, self._min_drag_size),
        ).validated()

        self._panel: ButtonPanel | None = None
        self._capture: ScreenCapture | None = None
        self._properties: PropertiesPanel | None = None
        self._hook: KeyboardHook | None = None
        self._target: WindowTarget | None = None
        self._settings_process: subprocess.Popen[bytes] | None = None
        self._last_status = ""
        self._reported_no_target = False
        self._cursor = "arrow"
        self._last_preview: tuple[Shape, ...] = ()
        self._last_keys: Any = None

    # -- feature lifecycle ---------------------------------------------
    def start(self) -> None:
        service = self.context.service
        store = self.context.store
        if service is None:
            raise FeatureError(
                f"feature {self.context.instance_name!r} needs the platform window "
                "service; this is an application bug, not a configuration problem"
            )
        if store is None:
            raise FeatureError(
                f"feature {self.context.instance_name!r} needs the layout store; "
                "this is an application bug, not a configuration problem"
            )
        scale = self._scale_for(self._monitor())
        try:
            self._panel = service.create_button_panel(
                x=self._x,
                y=self._y,
                items=self._items(),
                title=self._title,
                status=self._status_text(),
                scale=scale,
            )
        except OverlayError as exc:
            raise FeatureError(
                f"could not create the floating toolbar: {exc}"
            ) from exc
        self._position_panel()
        self.logger.info(
            "toolbar ready at %d,%d (buttons: %s, auto_position=%s)",
            self._panel.x,
            self._panel.y,
            ", ".join(self._button_order),
            self._auto_position,
        )

    def stop(self) -> None:
        self._end_mode("the application is stopping", quiet=True)
        self._destroy_properties()
        self._stop_settings_process(kill=True)
        hook = self._hook
        self._hook = None
        if hook is not None:
            try:
                hook.uninstall()
            except Exception:  # pragma: no cover - defensive
                self.logger.exception("could not remove the keyboard hook")
        self._destroy_capture()
        if self.context.store is not None:
            self._store().clear_preview()
            self.request_repaint()
        panel = self._panel
        self._panel = None
        if panel is not None:
            try:
                panel.destroy()
            except Exception:  # pragma: no cover - defensive
                self.logger.exception("could not destroy the toolbar cleanly")

    def on_target_changed(self, target: WindowTarget | None) -> None:
        self._target = target
        if target is None:
            self._end_mode("the target window is gone", quiet=True)
            self._destroy_properties()
        else:
            self._reported_no_target = False
            if self._editor.active:
                self._editor.set_area(target.bounds)
                self._sync_capture()
            if self._auto_position:
                self._position_panel()
        self._refresh_buttons()
        self._refresh_status()

    def tick(self, now: float) -> None:
        if self.context.keys is not self._last_keys:
            self._last_keys = self.context.keys
            if self._editor.active:
                self._install_hook()
        if self._panel is not None:
            self._consume_panel_events()
        if self._capture is not None:
            self._consume_capture_events()
        self._consume_key_events()
        self._poll_settings_process()
        self._refresh_status()
        if self._editor.active:
            if self._editor.wants_flush(now):
                self._editor.flush()
            self._refresh_preview()
            self._update_properties()

    def update(self, target: WindowTarget, now: float) -> None:
        self._target = target
        if self._editor.active:
            self._editor.set_area(target.bounds)
            self._sync_capture()
            self._update_properties()

    def keep_alive(self) -> bool:
        return self._editor.active

    # -- store plumbing --------------------------------------------------
    def _store(self) -> LayoutStore:
        store = self.context.store
        assert store is not None
        return store

    def _persist_shapes(self, shapes: tuple[Shape, ...]) -> bool:
        try:
            return self._store().replace_shapes(shapes)
        except ConfigWriteError:
            self.logger.exception("could not save the edited rectangles")
            return False

    def _scale_for(self, monitor: MonitorInfo | None) -> float:
        dpi = monitor.dpi if monitor is not None else 96
        return max(0.75, min(3.0, dpi / 96.0))

    def _displays(self) -> DisplayInfo:
        service = self.context.service
        monitors: tuple[MonitorInfo, ...] = ()
        virtual_bounds = None
        if service is not None:
            monitors = tuple(service.list_monitors())
            virtual_bounds = service.virtual_desktop()
        return DisplayInfo(monitors=monitors, virtual_bounds=virtual_bounds)

    def _monitor(self) -> MonitorInfo | None:
        displays = self._displays()
        target = self._target
        if target is not None:
            monitor = displays.monitor_for(target.bounds)
            if monitor is not None:
                return monitor
        return displays.primary

    def _position_panel(self) -> None:
        panel = self._panel
        if panel is None:
            return
        if not self._auto_position:
            panel.move(self._x, self._y)
            return
        monitor = self._monitor()
        if monitor is None:
            return
        margin = int(round(16 * self._scale_for(monitor)))
        work = monitor.work_area
        x = work.x + work.width - panel.width - margin
        y = work.y + max(0, (work.height - panel.height) // 2)
        panel.move(x, y)

    # -- panel -----------------------------------------------------------
    def _items(self) -> list[ButtonSpec]:
        store = self._store()
        specs: list[ButtonSpec] = []
        for key in self._button_order:
            if key == "edit":
                label = _DONE_LABEL if self._editor.mode == MODE_EDIT else _EDIT_LABEL
            elif key == "move":
                label = _DONE_LABEL if self._editor.mode == MODE_MOVE else _MOVE_LABEL
            elif key == "hide":
                label = _SHOW_LABEL if store.hidden else _HIDE_LABEL
            else:
                label = _SETTINGS_LABEL
            specs.append(ButtonSpec(key=key, label=label))
        return specs

    def _status_text(self) -> str:
        store = self._store()
        if self._editor.mode == MODE_EDIT:
            selected = self._editor.selected_shape()
            return "编辑 · 已选中" if selected is not None else "编辑 · 拖动边缘缩放"
        if self._editor.mode == MODE_MOVE:
            selected = self._editor.selected_shape()
            return "移动 · 已选中" if selected is not None else "移动 · 点选矩形"
        if self._target is None:
            return "等待目标窗口"
        group = store.active_group
        if store.hidden:
            return "已隐藏"
        if group is None:
            return f"无匹配组 {store.signature}".strip()
        return _truncate(group.name, 18)

    def _refresh_status(self) -> None:
        panel = self._panel
        if panel is None:
            return
        status = self._status_text()
        if status != self._last_status:
            self._last_status = status
            panel.set_status(status)

    def _refresh_buttons(self) -> None:
        if self._panel is not None:
            self._panel.set_items(self._items())

    def _raise_panel(self) -> None:
        if self._panel is not None:
            self._panel.raise_above()

    # -- settings window -------------------------------------------------
    def _launch_settings(self) -> None:
        if self._settings_process is not None and self._settings_process.poll() is None:
            self.logger.info("the settings window is already open")
            return
        entry = self.context.project_root / "run.py"
        try:
            self._settings_process = subprocess.Popen(
                [
                    sys.executable,
                    str(entry),
                    "--settings",
                    "--config",
                    str(self.context.config_path),
                ],
                cwd=str(self.context.project_root),
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError:
            self.logger.exception("could not start the settings window")
            return
        self.logger.info("settings window opened (pid %d)", self._settings_process.pid)

    def _poll_settings_process(self) -> None:
        process = self._settings_process
        if process is None:
            return
        code = process.poll()
        if code is None:
            return
        self._settings_process = None
        if code == 0:
            self.logger.info("settings window saved the configuration; reloading it")
            self.context.request_config_reload()
        else:
            self.logger.info("settings window closed with code %d", code)

    def _stop_settings_process(self, *, kill: bool = False) -> None:
        process = self._settings_process
        self._settings_process = None
        if process is None or process.poll() is not None:
            return
        if kill:
            try:
                process.terminate()
            except OSError:  # pragma: no cover - defensive
                pass

    # -- panel events ----------------------------------------------------
    def _consume_panel_events(self) -> None:
        panel = self._panel
        if panel is None:
            return
        for event in panel.take_events():
            try:
                self._handle_panel_event(event)
            except Exception:
                self.logger.exception("failed to handle toolbar event %r", event)

    def _handle_panel_event(self, event: PanelEvent) -> None:
        if event.kind == "moved":
            self._persist_panel_position(event.x, event.y)
            return
        if event.kind != "button":
            return
        if event.key == "edit":
            if self._editor.mode == MODE_EDIT:
                self._end_mode("finished editing")
            else:
                self._begin_mode(MODE_EDIT)
        elif event.key == "move":
            if self._editor.mode == MODE_MOVE:
                self._end_mode("finished moving")
            else:
                self._begin_mode(MODE_MOVE)
        elif event.key == "hide":
            self._toggle_hidden()
        elif event.key == "settings":
            self._launch_settings()

    def _persist_panel_position(self, x: int, y: int) -> None:
        self._auto_position = False
        self._x = int(x)
        self._y = int(y)
        self.options["auto_position"] = False
        self.options["x"] = self._x
        self.options["y"] = self._y
        try:
            from screenpainter.core.layout import save_feature_options

            save_feature_options(
                self.context.config_path,
                self.context.instance_name,
                {"auto_position": False, "x": self._x, "y": self._y},
            )
        except ConfigWriteError:
            self.logger.exception("could not save the toolbar position")
        else:
            self.context.notify_config_saved()
        self.logger.debug("toolbar moved to %d,%d", self._x, self._y)

    def _toggle_hidden(self) -> None:
        store = self._store()
        if store.set_hidden(not store.hidden):
            self.request_repaint()
        self._refresh_buttons()
        self._refresh_status()

    def _warn_no_target(self) -> None:
        if self._reported_no_target:
            return
        self._reported_no_target = True
        self.logger.warning(
            "no target window is attached; open the configured window first"
        )

    # -- capture window --------------------------------------------------
    def _ensure_capture(self, cursor: str) -> bool:
        target = self._target
        if target is None:
            return False
        if self._capture is not None and not self._capture.is_alive():
            self._capture = None
        if self._capture is None:
            service = self.context.service
            assert service is not None
            try:
                self._capture = service.create_screen_capture(
                    target.bounds, cursor=cursor
                )
            except OverlayError:
                self.logger.exception(
                    "could not create the input capture window; editing is unavailable"
                )
                self._capture = None
                return False
        else:
            try:
                self._capture.move_to(target.bounds)
                self._capture.set_cursor(cursor)
                self._capture.show()
            except OverlayError:
                self.logger.exception("could not update the capture window")
                self._destroy_capture()
                return False
        self._cursor = cursor
        self._raise_panel()
        return True

    def _sync_capture(self) -> None:
        if self._capture is None or self._target is None:
            return
        if self._editor.dragging:
            return
        bounds = self._target.bounds
        if self._capture.rect == bounds:
            return
        try:
            self._capture.move_to(bounds)
        except OverlayError:
            self.logger.exception("could not resize the capture window")
            self._destroy_capture()
            return
        self._raise_panel()

    def _destroy_capture(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None:
            try:
                capture.destroy()
            except Exception:  # pragma: no cover - defensive
                self.logger.exception("could not destroy the capture window cleanly")

    def _set_cursor(self, cursor: str) -> None:
        if self._capture is None or cursor == self._cursor:
            return
        self._cursor = cursor
        try:
            self._capture.set_cursor(cursor)
        except OverlayError:  # pragma: no cover - defensive
            self.logger.exception("could not change the capture cursor")

    # -- mode handling ---------------------------------------------------
    def _begin_mode(self, mode: str) -> None:
        if self._target is None:
            self._warn_no_target()
            return
        if self._editor.mode is not None:
            self._end_mode("switching mode", quiet=True)
        target = self._target
        self._editor.begin(mode, self._store().active_shapes(), target.bounds)
        if not self._ensure_capture("cross" if mode == MODE_EDIT else "move"):
            self._editor.end()
            return
        self._install_hook()
        self._last_preview = ()
        self._refresh_preview()
        self._refresh_buttons()
        self._refresh_status()
        self._update_properties()
        if mode == MODE_EDIT:
            self.logger.info(
                "edit mode: click to select, drag edges to resize, drag empty "
                "space to draw, %s to finish",
                self._key_hint("cancel"),
            )
        else:
            self.logger.info(
                "move mode: click to select, drag to move, %s to finish",
                self._key_hint("cancel"),
            )

    def _end_mode(self, reason: str, *, quiet: bool = False) -> None:
        if self._editor.mode is None:
            self._destroy_capture()
            self._destroy_properties()
            return
        self._editor.end()
        self._uninstall_hook()
        self._destroy_capture()
        self._destroy_properties()
        self._last_preview = ()
        if self.context.store is not None:
            self._store().clear_preview()
        self.request_repaint()
        self._refresh_buttons()
        self._refresh_status()
        if not quiet:
            self.logger.info("editing finished (%s)", reason)

    def _refresh_preview(self) -> None:
        if not self._editor.active:
            return
        preview = tuple(self._editor.preview())
        if preview == self._last_preview:
            return
        self._last_preview = preview
        self._store().set_preview(preview, replace_base=True)
        self.request_repaint()

    def _key_hint(self, action: str) -> str:
        keys = self.context.keys
        if keys is None:
            return action
        try:
            return keys.binding(action).describe()
        except Exception:  # pragma: no cover - defensive
            return action

    # -- keyboard --------------------------------------------------------
    def _install_hook(self) -> None:
        hook = self._hook
        service = self.context.service
        if hook is None:
            if service is None:
                return
            factory = getattr(service, "create_keyboard_hook", None)
            if not callable(factory):
                self.logger.warning(
                    "the window service cannot capture shortcuts; editing keys "
                    "are unavailable"
                )
                return
            hook = factory()
            self._hook = hook
        keys = self.context.keys or KeyMap.default()
        bindings = {name: keys.binding(name) for name in binding_names()}
        try:
            hook.install(bindings)
        except Exception:
            self.logger.exception("could not install the keyboard hook")

    def _uninstall_hook(self) -> None:
        hook = self._hook
        if hook is None:
            return
        try:
            hook.uninstall()
        except Exception:  # pragma: no cover - defensive
            self.logger.exception("could not remove the keyboard hook")

    def _consume_key_events(self) -> None:
        hook = self._hook
        if hook is None or not self._editor.active:
            return
        try:
            events = hook.take_events()
        except Exception:
            self.logger.exception("could not read keyboard events")
            return
        for event in events:
            try:
                self._handle_key_event(event)
            except Exception:
                self.logger.exception("failed to handle key event %r", event)

    def _handle_key_event(self, event: KeyEvent) -> None:
        if event.kind == "up":
            # End of a key burst: persist deferred arrow-key nudges.
            if self._editor.flush():
                self.logger.debug("saved deferred rectangle changes")
            return
        action = event.action
        if not action:
            return
        if action in ("cancel", "confirm"):
            self._end_mode("finished editing")
            return
        if action == "delete":
            if self._editor.delete_selected():
                self._after_edit()
            return
        if action == "undo":
            if self._editor.undo():
                self._after_edit()
            return
        if action == "redo":
            if self._editor.redo():
                self._after_edit()
            return
        if action in ("select_next", "select_prev"):
            if self._editor.select_next(1 if action == "select_next" else -1):
                self._after_edit()
            return
        steps = {
            "move_left": (-1, 0),
            "move_right": (1, 0),
            "move_up": (0, -1),
            "move_down": (0, 1),
            "move_left_fast": (-_FAST_NUDGE, 0),
            "move_right_fast": (_FAST_NUDGE, 0),
            "move_up_fast": (0, -_FAST_NUDGE),
            "move_down_fast": (0, _FAST_NUDGE),
        }
        step = steps.get(action)
        if step is None:
            return
        if self._editor.nudge(step[0], step[1]):
            self._after_edit()

    def _after_edit(self) -> None:
        self._refresh_preview()
        self._update_properties()
        self._refresh_status()

    # -- pointer events --------------------------------------------------
    def _consume_capture_events(self) -> None:
        capture = self._capture
        if capture is None:
            return
        for event in capture.take_events():
            try:
                self._handle_pointer(event)
            except Exception:
                self.logger.exception("failed to handle pointer event %r", event)

    def _handle_pointer(self, event: PointerEvent) -> None:
        if not self._editor.active:
            return
        if event.kind == "down":
            self._editor.pointer_down(event.x, event.y)
            self._after_edit()
            self._refresh_cursor(event.x, event.y)
            return
        if event.kind == "move":
            if self._editor.pointer_move(event.x, event.y):
                self._refresh_preview()
                self._update_properties()
            self._refresh_cursor(event.x, event.y)
            return
        if event.kind == "up":
            if self._editor.pointer_up(event.x, event.y):
                self._after_edit()
            else:
                self._refresh_preview()
                self._update_properties()
            self._refresh_cursor(event.x, event.y)
            return
        if event.kind == "cancel":
            if self._editor.cancel_drag():
                self._after_edit()

    def _refresh_cursor(self, x: int, y: int) -> None:
        self._set_cursor(self._editor.cursor_at(x, y))

    # -- properties panel ------------------------------------------------
    def _properties_status(self) -> PanelStatus | None:
        shape = self._editor.selected_shape()
        if shape is None:
            return None
        hint = "  ·  ".join(
            [
                f"{self._key_hint('delete')} 删除",
                f"{self._key_hint('undo')} 撤回",
            ]
        )
        return PanelStatus(
            title="矩形属性",
            name=shape.name or "-",
            color=shape.color.upper(),
            position=f"{shape.x}, {shape.y}",
            size=f"{shape.width} × {shape.height}",
            opacity=f"{shape.opacity:.2f}",
            hint=_truncate(hint, 30),
        )

    def _update_properties(self) -> None:
        if not self._properties_enabled or not self._editor.active:
            self._destroy_properties()
            return
        status = self._properties_status()
        if status is None:
            self._destroy_properties()
            return
        scale = self._scale_for(self._monitor())
        x, y = self._properties_position(scale)
        if self._properties is None:
            service = self.context.service
            if service is None:
                return
            factory = getattr(service, "create_properties_panel", None)
            if not callable(factory):
                return
            try:
                self._properties = factory(
                    x=x, y=y, status=status, scale=scale
                )
            except Exception:
                self.logger.exception("could not create the properties panel")
                self._properties = None
                self._properties_enabled = False
                return
            x, y = self._properties_position(scale, width=self._properties.width)
            self._properties.update(status, x=x, y=y)
            return
        if not self._properties.is_alive():
            self._properties = None
            self._update_properties()
            return
        self._properties.update(status, x=x, y=y)

    def _properties_position(
        self, scale: float, *, width: int | None = None
    ) -> tuple[int, int]:
        shape = self._editor.selected_shape()
        target = self._target
        if shape is None or target is None:
            return (0, 0)
        panel_width = width or int(round(_PROPERTIES_WIDTH * scale))
        panel_height = int(round(_PROPERTIES_HEIGHT * scale))
        gap = int(round(10 * scale))
        bounds = target.bounds
        x = bounds.x + shape.x + shape.width + gap
        if x + panel_width > bounds.x + bounds.width:
            x = bounds.x + shape.x - panel_width - gap
        x = max(bounds.x, min(x, bounds.x + bounds.width - panel_width))
        y = bounds.y + shape.y
        y = max(bounds.y, min(y, bounds.y + bounds.height - panel_height))
        return int(x), int(y)

    def _destroy_properties(self) -> None:
        panel = self._properties
        self._properties = None
        if panel is not None:
            try:
                panel.destroy()
            except Exception:  # pragma: no cover - defensive
                self.logger.exception("could not destroy the properties panel")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _boolean_option(
    options: Mapping[str, Any], key: str, default: bool, instance: str
) -> bool:
    value = options.get(key, default)
    if not isinstance(value, bool):
        raise FeatureError(f"feature {instance!r}: '{key}' must be true or false")
    return value


def _integer_option(
    options: Mapping[str, Any],
    key: str,
    default: int,
    instance: str,
    *,
    minimum: int | None = None,
) -> int:
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FeatureError(f"feature {instance!r}: '{key}' must be an integer")
    if minimum is not None and value < minimum:
        raise FeatureError(f"feature {instance!r}: '{key}' must be >= {minimum}")
    return value


def _string_option(
    options: Mapping[str, Any], key: str, default: str, instance: str
) -> str:
    value = options.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise FeatureError(f"feature {instance!r}: '{key}' must be a non-empty string")
    return value.strip()


def _hex_option(
    options: Mapping[str, Any], key: str, default: str, instance: str
) -> str:
    value = options.get(key, default)
    if not isinstance(value, str):
        raise FeatureError(
            f"feature {instance!r}: '{key}' must be a string like '#FF3B30'"
        )
    try:
        Color.from_hex(value, 1.0)
    except ValueError as exc:
        raise FeatureError(f"feature {instance!r}: '{key}': {exc}") from exc
    return value.upper()
