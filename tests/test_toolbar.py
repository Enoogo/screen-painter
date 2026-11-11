"""Tests for the floating toolbar feature.

The feature never touches Win32 directly: it talks to the platform service
through the ``ButtonPanel`` / ``ScreenCapture`` / ``PropertiesPanel`` /
``KeyboardHook`` protocols.  These tests inject in-memory fakes, so the whole
edit / move / hide / undo state machine runs without a desktop or a real window.
"""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from typing import Any, Sequence

from screenpainter.config import load_config
from screenpainter.core.display import MonitorInfo
from screenpainter.core.feature import FeatureContext
from screenpainter.core.geometry import Rect
from screenpainter.core.keymap import KeyMap
from screenpainter.core.layout import LayoutStore, Shape
from screenpainter.core.ui import (
    ButtonSpec,
    KeyEvent,
    PanelEvent,
    PanelStatus,
    PointerEvent,
)
from screenpainter.core.window import WindowTarget
from screenpainter.errors import FeatureError
from screenpainter.features.toolbar.editor import (
    handle_shapes,
    hit_edges,
    resize_cursor,
    resize_shape,
)
from screenpainter.features.toolbar.feature import (
    ToolbarFeature,
    clamp_move,
    clamp_rect,
    outline_shapes,
    rubber_band,
)

try:  # ``python -m unittest discover -s tests`` imports this as a top module
    from .helpers import cleanup, config_text, make_config
except ImportError:  # pragma: no cover - depends on how the tests are run
    from helpers import cleanup, config_text, make_config

TOOLBAR_SECTION = """\
[features.toolbar]
type = "toolbar"
enabled = true
auto_position = true
buttons = ["edit", "move", "hide", "settings"]
default_color = "#112233"
default_opacity = 1.0
min_drag_size = 3
"""

LEGACY_TOOLBAR_SECTION = TOOLBAR_SECTION.replace(
    '["edit", "move", "hide", "settings"]', '["draw", "hide"]'
)

TARGET_BOUNDS = Rect(100, 200, 800, 600)


class FakePanel:
    def __init__(
        self,
        *,
        x: int,
        y: int,
        items: Sequence[ButtonSpec],
        title: str,
        status: str,
        scale: float,
    ) -> None:
        self._x = int(x)
        self._y = int(y)
        self.items = list(items)
        self.title = title
        self.status = status
        self.scale = scale
        self.width = 176
        self.height = 164
        self.events: list[PanelEvent] = []
        self.raised = 0
        self.destroyed = False
        self.visible = False

    @property
    def hwnd(self) -> int:
        return 0x9000

    @property
    def x(self) -> int:
        return self._x

    @property
    def y(self) -> int:
        return self._y

    def is_alive(self) -> bool:
        return not self.destroyed

    def set_items(self, items: Sequence[ButtonSpec]) -> None:
        self.items = list(items)

    def set_title(self, text: str) -> None:
        self.title = text

    def set_status(self, text: str) -> None:
        self.status = text

    def move(self, x: int, y: int) -> None:
        self._x = int(x)
        self._y = int(y)

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def raise_above(self) -> None:
        self.raised += 1

    def take_events(self) -> list[PanelEvent]:
        events = self.events
        self.events = []
        return events

    def destroy(self) -> None:
        self.destroyed = True

    # test helpers
    def click(self, key: str) -> None:
        self.events.append(PanelEvent(kind="button", key=key))

    def move_by_hand(self, x: int, y: int) -> None:
        self._x, self._y = x, y
        self.events.append(PanelEvent(kind="moved", x=x, y=y))

    def label(self, key: str) -> str:
        for item in self.items:
            if item.key == key:
                return item.label
        raise KeyError(key)

    @property
    def keys(self) -> list[str]:
        return [item.key for item in self.items]


class FakeCapture:
    def __init__(self, rect: Rect, cursor: str) -> None:
        self.rect = rect
        self.cursor = cursor
        self.cursors: list[str] = [cursor]
        self.events: list[PointerEvent] = []
        self.destroyed = False

    @property
    def hwnd(self) -> int:
        return 0x9100

    def is_alive(self) -> bool:
        return not self.destroyed

    def move_to(self, rect: Rect) -> None:
        self.rect = rect

    def set_cursor(self, cursor: str) -> None:
        self.cursor = cursor
        self.cursors.append(cursor)

    def show(self) -> None:
        pass

    def hide(self) -> None:
        pass

    def take_events(self) -> list[PointerEvent]:
        events = self.events
        self.events = []
        return events

    def destroy(self) -> None:
        self.destroyed = True

    # test helpers
    def pointer(self, kind: str, x: int = 0, y: int = 0) -> None:
        self.events.append(PointerEvent(kind=kind, x=x, y=y))  # type: ignore[arg-type]

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        self.pointer("down", *start)
        self.pointer("move", (start[0] + end[0]) // 2, (start[1] + end[1]) // 2)
        self.pointer("move", *end)
        self.pointer("up", *end)


class FakePropertiesPanel:
    def __init__(self, *, x: int, y: int, status: PanelStatus, scale: float) -> None:
        self._x = int(x)
        self._y = int(y)
        self.status = status
        self.scale = scale
        self.width = 218
        self.height = 172
        self.updates = 0
        self.destroyed = False
        self.raised = 0

    @property
    def hwnd(self) -> int:
        return 0x9200

    def is_alive(self) -> bool:
        return not self.destroyed

    def update(
        self, status: PanelStatus, *, x: int | None = None, y: int | None = None
    ) -> None:
        self.status = status
        self.updates += 1
        if x is not None:
            self._x = int(x)
        if y is not None:
            self._y = int(y)

    def move(self, x: int, y: int) -> None:
        self._x, self._y = int(x), int(y)

    def raise_above(self) -> None:
        self.raised += 1

    def destroy(self) -> None:
        self.destroyed = True


class FakeKeyboardHook:
    def __init__(self) -> None:
        self.bindings: dict[str, Any] = {}
        self.installed = False
        self.events: list[KeyEvent] = []

    def install(self, bindings: dict[str, Any]) -> bool:
        self.bindings = dict(bindings)
        self.installed = True
        return True

    def uninstall(self) -> None:
        self.installed = False
        self.events.clear()

    def set_bindings(self, bindings: dict[str, Any]) -> None:
        self.bindings = dict(bindings)

    def take_events(self) -> list[KeyEvent]:
        events = self.events
        self.events = []
        return events

    # test helpers
    def press(self, action: str) -> None:
        self.events.append(KeyEvent(kind="down", action=action))

    def release(self, action: str = "") -> None:
        self.events.append(KeyEvent(kind="up", action=action))


class FakeService:
    def __init__(self, monitors: Sequence[MonitorInfo] | None = None) -> None:
        self.monitors = list(monitors) if monitors is not None else [DEFAULT_MONITOR]
        self.virtual = Rect(0, 0, 1920, 1080)
        self.panels: list[FakePanel] = []
        self.captures: list[FakeCapture] = []
        self.properties: list[FakePropertiesPanel] = []
        self.hook = FakeKeyboardHook()
        self.repaint_calls = 0

    def note_repaint(self) -> None:
        self.repaint_calls += 1

    def list_monitors(self) -> list[MonitorInfo]:
        return list(self.monitors)

    def virtual_desktop(self) -> Rect:
        return self.virtual

    def create_button_panel(self, **kwargs: Any) -> FakePanel:
        panel = FakePanel(**kwargs)
        self.panels.append(panel)
        return panel

    def create_screen_capture(self, rect: Rect, *, cursor: str = "cross") -> FakeCapture:
        capture = FakeCapture(rect, cursor)
        self.captures.append(capture)
        return capture

    def create_properties_panel(
        self, *, x: int, y: int, status: PanelStatus, scale: float = 1.0
    ) -> FakePropertiesPanel:
        panel = FakePropertiesPanel(x=x, y=y, status=status, scale=scale)
        self.properties.append(panel)
        return panel

    def create_keyboard_hook(self) -> FakeKeyboardHook:
        return self.hook


DEFAULT_MONITOR = MonitorInfo(
    index=0,
    device="\\\\.\\DISPLAY1",
    bounds=Rect(0, 0, 1920, 1080),
    work_area=Rect(0, 0, 1920, 1040),
    primary=True,
    dpi=96,
)


def _target() -> WindowTarget:
    return WindowTarget(
        hwnd=0x1234,
        pid=4321,
        process_name="demo.exe",
        executable="C:/demo.exe",
        title="Demo",
        class_name="DemoWindow",
        bounds=TARGET_BOUNDS,
        window_bounds=TARGET_BOUNDS,
        client_bounds=TARGET_BOUNDS,
    )


class ToolbarFeatureTests(unittest.TestCase):
    def _make(
        self,
        options: dict[str, Any] | None = None,
        *,
        service: FakeService | None = None,
        attach_target: bool = True,
        layout: str | None = None,
        groups: str = "",
        toolbar_section: str = TOOLBAR_SECTION,
    ) -> tuple[ToolbarFeature, LayoutStore, FakeService, Path, FeatureContext]:
        kwargs: dict[str, Any] = {"features": toolbar_section, "groups": groups}
        if layout is not None:
            kwargs["layout"] = layout
        config, directory = make_config(config_text(**kwargs))
        self.addCleanup(cleanup, directory)
        store = LayoutStore(config)
        store.update_signature("1920x1080")
        fake_service = service or FakeService()
        context = FeatureContext(
            instance_name="toolbar",
            type_name="toolbar",
            project_root=directory,
            config_path=config.path,
            assets_dir=directory / "assets",
            logger=logging.getLogger("test.toolbar"),
            store=store,
            service=fake_service,  # type: ignore[arg-type]
            request_repaint=fake_service.note_repaint,
            keys=config.keys,
        )
        if options is None:
            instance = next(
                item for item in config.features if item.type == "toolbar"
            )
            options = dict(instance.options)
        feature = ToolbarFeature(context, options)
        self.addCleanup(feature.stop)
        feature.start()
        if attach_target:
            feature.on_target_changed(_target())
        feature.tick(0.0)
        return feature, store, fake_service, config.path, context

    # -- helpers --------------------------------------------------------
    def _panel(self, service: FakeService) -> FakePanel:
        return service.panels[-1]

    def _capture(self, service: FakeService) -> FakeCapture:
        return service.captures[-1]

    def _shapes(self, config_path: Path, group: str = "demo") -> tuple[Shape, ...]:
        config = load_config(config_path)
        found = config.find_group(group)
        assert found is not None
        return found.shapes

    def _enter_edit(self, feature: ToolbarFeature, service: FakeService) -> FakeCapture:
        self._panel(service).click("edit")
        feature.tick(0.0)
        return self._capture(service)

    # -- basic lifecycle -------------------------------------------------
    def test_start_creates_the_panel_with_configured_buttons(self) -> None:
        feature, _, service, _, _ = self._make()
        panel = self._panel(service)
        self.assertEqual(panel.keys, ["edit", "move", "hide", "settings"])
        self.assertEqual(panel.label("edit"), "编辑")
        self.assertEqual(panel.label("move"), "移动")
        self.assertEqual(panel.label("hide"), "隐藏")
        self.assertEqual(panel.label("settings"), "设置")
        self.assertEqual(panel.title, "屏幕遮罩")
        self.assertFalse(feature.keep_alive())

    def test_legacy_draw_button_is_accepted_as_edit(self) -> None:
        _, _, service, _, _ = self._make(toolbar_section=LEGACY_TOOLBAR_SECTION)
        panel = self._panel(service)
        self.assertEqual(panel.keys, ["edit", "hide"])

    def test_panel_is_docked_on_the_right_edge(self) -> None:
        _, _, service, _, _ = self._make()
        panel = self._panel(service)
        # 1920 - 176 - 16 (margin at 96 dpi)
        self.assertEqual(panel.x, 1920 - 176 - 16)
        self.assertEqual(panel.y, (1040 - panel.height) // 2)

    def test_hide_button_toggles_and_saves(self) -> None:
        feature, store, service, config_path, _ = self._make()
        panel = self._panel(service)
        panel.click("hide")
        feature.tick(0.0)
        self.assertTrue(store.hidden)
        self.assertEqual(panel.label("hide"), "显示")
        saved = load_config(config_path)
        self.assertTrue(saved.layout.hidden)
        panel.click("hide")
        feature.tick(0.0)
        self.assertFalse(store.hidden)

    def test_editing_shows_outlines_and_selects(self) -> None:
        feature, store, service, _, _ = self._make()
        panel = self._panel(service)
        panel.click("edit")
        feature.tick(0.0)
        self.assertEqual(panel.label("edit"), "完成")
        self.assertTrue(feature.keep_alive())
        self.assertTrue(service.hook.installed)
        self.assertEqual(len(service.captures), 1)
        self.assertIn("delete", service.hook.bindings)
        # Every rectangle gets an outline in the preview.
        preview = store.preview_shapes
        outline_count = sum(1 for shape in preview if shape.name.startswith("outline_"))
        self.assertGreaterEqual(outline_count, 4)

        capture = self._capture(service)
        capture.pointer("down", 20, 30)  # inside the demo rectangle
        capture.pointer("up", 20, 30)
        feature.tick(0.0)
        self.assertEqual(feature._editor.selected, 0)
        selected_outlines = [
            shape
            for shape in store.preview_shapes
            if shape.name.startswith("outline_") and shape.color.upper() == "#0A84FF"
        ]
        self.assertGreaterEqual(len(selected_outlines), 4)
        self.assertTrue(service.properties)
        status = service.properties[-1].status
        self.assertEqual(status.color, "#000000")
        self.assertEqual(status.position, "10, 20")
        self.assertEqual(status.size, "30 × 40")

    def test_escape_ends_the_mode(self) -> None:
        feature, store, service, _, _ = self._make()
        capture = self._enter_edit(feature, service)
        service.hook.press("cancel")
        feature.tick(0.0)
        self.assertFalse(service.hook.installed)
        self.assertTrue(capture.destroyed)
        self.assertEqual(store.preview_shapes, ())
        self.assertEqual(self._panel(service).label("edit"), "编辑")

    def test_losing_the_target_cancels_editing(self) -> None:
        feature, store, service, _, _ = self._make()
        self._enter_edit(feature, service)
        feature.on_target_changed(None)
        feature.tick(0.0)
        self.assertFalse(service.hook.installed)
        self.assertEqual(store.preview_shapes, ())

    # -- pointer editing -------------------------------------------------
    def test_move_mode_drag_updates_and_saves(self) -> None:
        feature, _, service, config_path, _ = self._make()
        panel = self._panel(service)
        panel.click("move")
        feature.tick(0.0)
        capture = self._capture(service)
        capture.drag((20, 30), (60, 50))
        feature.tick(0.0)
        shape = self._shapes(config_path)[0]
        self.assertEqual((shape.x, shape.y), (50, 40))

    def test_edit_mode_drag_inside_moves(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        capture.drag((20, 30), (25, 35))
        feature.tick(0.0)
        shape = self._shapes(config_path)[0]
        self.assertEqual((shape.x, shape.y), (15, 25))

    def test_edit_mode_edge_drag_resizes(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        capture.pointer("down", 20, 30)
        capture.pointer("up", 20, 30)
        feature.tick(0.0)
        # Grab the right edge of the selected rectangle (x=10..40, y=20..60).
        capture.drag((40, 40), (70, 40))
        feature.tick(0.0)
        shape = self._shapes(config_path)[0]
        self.assertEqual(shape.width, 60)
        self.assertEqual(shape.x, 10)

    def test_edit_mode_drag_on_empty_space_creates(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        capture.drag((200, 200), (260, 240))
        feature.tick(0.0)
        shapes = self._shapes(config_path)
        self.assertEqual(len(shapes), 2)
        created = shapes[-1]
        self.assertEqual((created.x, created.y, created.width, created.height), (200, 200, 60, 40))
        self.assertEqual(created.color, "#112233")
        self.assertEqual(feature._editor.selected, 1)

    def test_tiny_drag_does_not_create(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        capture.drag((300, 300), (301, 301))
        feature.tick(0.0)
        self.assertEqual(len(self._shapes(config_path)), 1)

    def test_move_mode_empty_drag_only_clears_the_selection(self) -> None:
        feature, _, service, config_path, _ = self._make()
        self._panel(service).click("move")
        feature.tick(0.0)
        capture = self._capture(service)
        capture.drag((300, 300), (360, 340))
        feature.tick(0.0)
        self.assertEqual(len(self._shapes(config_path)), 1)
        self.assertIsNone(feature._editor.selected)

    def test_resize_is_clipped_to_the_target(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        # Drag the left edge far outside the target.
        capture.drag((10, 40), (-200, 40))
        feature.tick(0.0)
        shape = self._shapes(config_path)[0]
        self.assertGreaterEqual(shape.x, 0)
        self.assertGreaterEqual(shape.width, 2)

    # -- keyboard editing ------------------------------------------------
    def test_delete_key_removes_and_saves(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        capture.pointer("down", 20, 30)
        capture.pointer("up", 20, 30)
        feature.tick(0.0)
        service.hook.press("delete")
        feature.tick(0.0)
        self.assertEqual(self._shapes(config_path), ())
        self.assertIsNone(feature._editor.selected)

    def test_undo_and_redo_restore_the_rectangles(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        capture.pointer("down", 20, 30)
        capture.pointer("up", 20, 30)
        feature.tick(0.0)
        service.hook.press("delete")
        feature.tick(0.0)
        self.assertEqual(self._shapes(config_path), ())

        service.hook.press("undo")
        feature.tick(0.0)
        restored = self._shapes(config_path)
        self.assertEqual(len(restored), 1)
        self.assertEqual((restored[0].x, restored[0].y), (10, 20))

        service.hook.press("redo")
        feature.tick(0.0)
        self.assertEqual(self._shapes(config_path), ())

    def test_undo_without_history_is_a_no_op(self) -> None:
        feature, _, service, config_path, _ = self._make()
        self._enter_edit(feature, service)
        service.hook.press("undo")
        feature.tick(0.0)
        self.assertEqual(len(self._shapes(config_path)), 1)

    def test_arrow_keys_nudge_one_pixel_and_shift_moves_ten(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        capture.pointer("down", 20, 30)
        capture.pointer("up", 20, 30)
        feature.tick(0.0)
        for _ in range(3):
            service.hook.press("move_right")
        feature.tick(0.0)
        service.hook.press("move_down_fast")
        feature.tick(0.0)
        # Deferred until the key is released, so the file only changes then.
        service.hook.release("move_down_fast")
        feature.tick(0.0)
        shape = self._shapes(config_path)[0]
        self.assertEqual((shape.x, shape.y), (13, 30))

    def test_nudge_is_coalesced_into_one_undo_step(self) -> None:
        feature, _, service, config_path, _ = self._make()
        capture = self._enter_edit(feature, service)
        capture.pointer("down", 20, 30)
        capture.pointer("up", 20, 30)
        feature.tick(0.0)
        for _ in range(4):
            service.hook.press("move_left")
        feature.tick(0.0)
        service.hook.release("move_left")
        feature.tick(0.0)
        self.assertEqual(self._shapes(config_path)[0].x, 6)
        service.hook.press("undo")
        feature.tick(0.0)
        self.assertEqual(self._shapes(config_path)[0].x, 10)

    def test_select_next_cycles_through_rectangles(self) -> None:
        extra = """\
[groups.extra]
resolution = "1920x1080"
rectangles = [
  { name = "first", x = 10, y = 20, width = 30, height = 40 },
  { name = "second", x = 200, y = 200, width = 40, height = 40 },
]
"""
        feature, _, service, _, _ = self._make(
            layout=(
                '[layout]\nmode = "manual"\nmatch = "monitor"\n'
                'group = "extra"\nhidden = false\n'
            ),
            groups=extra,
        )
        self._enter_edit(feature, service)
        service.hook.press("select_next")
        feature.tick(0.0)
        self.assertEqual(feature._editor.selected, 0)
        service.hook.press("select_next")
        feature.tick(0.0)
        self.assertEqual(feature._editor.selected, 1)
        service.hook.press("select_prev")
        feature.tick(0.0)
        self.assertEqual(feature._editor.selected, 0)

    # -- panel / settings ------------------------------------------------
    def test_dragging_the_panel_is_persisted(self) -> None:
        feature, _, service, config_path, _ = self._make()
        panel = self._panel(service)
        panel.move_by_hand(200, 100)
        feature.tick(0.0)
        config = load_config(config_path)
        toolbar = next(item for item in config.features if item.type == "toolbar")
        self.assertFalse(toolbar.options["auto_position"])
        self.assertEqual((toolbar.options["x"], toolbar.options["y"]), (200, 100))

    def test_settings_button_starts_the_settings_window(self) -> None:
        feature, _, service, _, _ = self._make()
        launched: list[list[str]] = []

        class FakeProcess:
            pid = 4242

            def poll(self) -> int | None:
                return 0

        def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
            launched.append(list(args))
            return FakeProcess()

        import screenpainter.features.toolbar.feature as module

        original = module.subprocess.Popen
        module.subprocess.Popen = fake_popen  # type: ignore[assignment]
        self.addCleanup(setattr, module.subprocess, "Popen", original)
        self._panel(service).click("settings")
        feature.tick(0.0)
        self.assertEqual(len(launched), 1)
        self.assertIn("--settings", launched[0])
        feature.tick(0.1)
        self.assertTrue(feature.context.request_config_reload is not None)

    # -- validation ------------------------------------------------------
    def test_invalid_options_are_rejected(self) -> None:
        for options in (
            {"unknown": 1},
            {"buttons": []},
            {"buttons": "draw"},
            {"buttons": ["nope"]},
            {"auto_position": "yes"},
            {"x": "left"},
            {"default_color": "black"},
            {"default_opacity": 2.0},
            {"min_drag_size": 0},
            {"title": ""},
            {"outline_color": "red"},
            {"selection_width": 0},
        ):
            with self.subTest(options=options):
                config, directory = make_config(config_text(features=TOOLBAR_SECTION))
                self.addCleanup(cleanup, directory)
                context = FeatureContext(
                    instance_name="toolbar",
                    type_name="toolbar",
                    project_root=directory,
                    config_path=config.path,
                    assets_dir=directory / "assets",
                    logger=logging.getLogger("test.toolbar"),
                    store=LayoutStore(config),
                    service=FakeService(),  # type: ignore[arg-type]
                )
                with self.assertRaises(FeatureError):
                    ToolbarFeature(context, options)

    def test_missing_service_is_reported(self) -> None:
        config, directory = make_config(config_text(features=TOOLBAR_SECTION))
        self.addCleanup(cleanup, directory)
        context = FeatureContext(
            instance_name="toolbar",
            type_name="toolbar",
            project_root=directory,
            config_path=config.path,
            assets_dir=directory / "assets",
            logger=logging.getLogger("test.toolbar"),
            store=LayoutStore(config),
            service=None,
        )
        feature = ToolbarFeature(context, {})
        with self.assertRaises(FeatureError):
            feature.start()


class GeometryHelperTests(unittest.TestCase):
    def test_outline_shapes(self) -> None:
        shapes = outline_shapes(Rect(10, 20, 100, 50), thickness=2)
        self.assertEqual(len(shapes), 4)
        top = shapes[0]
        self.assertEqual((top.x, top.y, top.width, top.height), (10, 20, 100, 2))
        left = shapes[2]
        self.assertEqual((left.x, left.y, left.width, left.height), (10, 20, 2, 50))

    def test_outline_of_a_tiny_rectangle(self) -> None:
        shapes = sorted(
            outline_shapes(Rect(0, 0, 1, 1), thickness=4),
            key=lambda shape: (shape.width, shape.height),
        )
        for shape in shapes:
            self.assertGreaterEqual(shape.width, 1)
            self.assertGreaterEqual(shape.height, 1)

    def test_rubber_band(self) -> None:
        shapes = rubber_band(Rect(0, 0, 100, 100))
        self.assertEqual(len(shapes), 5)
        self.assertEqual(shapes[0].name, "preview_fill")
        self.assertEqual(rubber_band(Rect(0, 0, 0, 10)), [])

    def test_clamp_move(self) -> None:
        bounds = Rect(0, 0, 100, 100)
        self.assertEqual(clamp_move(50, 50, 20, 20, bounds), (50, 50))
        self.assertEqual(clamp_move(-50, -50, 20, 20, bounds), (0, 0))
        self.assertEqual(clamp_move(500, 500, 20, 20, bounds), (80, 80))
        self.assertEqual(clamp_move(-500, -500, 200, 200, bounds), (-199, -199))
        self.assertEqual(clamp_move(500, 500, 200, 200, bounds), (99, 99))

    def test_clamp_rect(self) -> None:
        bounds = Rect(0, 0, 100, 100)
        self.assertEqual(clamp_rect(Rect(10, 10, 20, 20), bounds), Rect(10, 10, 20, 20))
        self.assertEqual(clamp_rect(Rect(-10, -10, 30, 30), bounds), Rect(0, 0, 20, 20))
        self.assertIsNone(clamp_rect(Rect(200, 200, 20, 20), bounds))

    def test_hit_edges(self) -> None:
        shape = Shape(x=10, y=20, width=100, height=50)
        self.assertTrue(hit_edges(shape, 10, 40, 6) & 1)  # left
        self.assertTrue(hit_edges(shape, 110, 40, 6) & 2)  # right
        self.assertTrue(hit_edges(shape, 60, 20, 6) & 4)  # top
        self.assertTrue(hit_edges(shape, 60, 70, 6) & 8)  # bottom
        self.assertEqual(hit_edges(shape, 60, 40, 6), 0)  # inside
        self.assertEqual(hit_edges(shape, 300, 300, 6), 0)  # outside

    def test_resize_shape(self) -> None:
        shape = Shape(x=10, y=20, width=100, height=50)
        bounds = Rect(0, 0, 800, 600)
        resized = resize_shape(shape, 2, 300, 40, bounds=bounds, min_size=2)
        self.assertEqual((resized.x, resized.width), (10, 290))
        moved_left = resize_shape(shape, 1, 50, 40, bounds=bounds, min_size=2)
        self.assertEqual((moved_left.x, moved_left.width), (50, 60))
        clamped = resize_shape(shape, 1, 500, 40, bounds=bounds, min_size=2)
        self.assertEqual(clamped.width, 2)

    def test_resize_cursor(self) -> None:
        self.assertEqual(resize_cursor(1), "sizewe")
        self.assertEqual(resize_cursor(4), "sizens")
        self.assertEqual(resize_cursor(5), "sizenwse")

    def test_handle_shapes(self) -> None:
        handles = handle_shapes(Rect(10, 20, 100, 50), color="#ffffff", size=7)
        self.assertEqual(len(handles), 4)
        for handle in handles:
            self.assertEqual((handle.width, handle.height), (7, 7))


if __name__ == "__main__":
    unittest.main()
