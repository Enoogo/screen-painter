"""Platform-neutral interfaces for window discovery and native windows."""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence

from screenpainter.core.display import MonitorInfo
from screenpainter.core.geometry import Rect
from screenpainter.core.keymap import KeyBinding
from screenpainter.core.surface import PixelSurface
from screenpainter.core.ui import (
    ButtonSpec,
    KeyEvent,
    PanelEvent,
    PanelStatus,
    PointerEvent,
)
from screenpainter.core.window import CoordinateSpace, WindowInfo, WindowProfile, WindowTarget


class Overlay(Protocol):
    @property
    def hwnd(self) -> int: ...

    def is_alive(self) -> bool: ...

    def move(self, x: int, y: int) -> None: ...

    def render(self, surface: PixelSurface) -> None: ...

    def show(self) -> None: ...

    def hide(self) -> None: ...

    def destroy(self) -> None: ...


class ButtonPanel(Protocol):
    """A draggable, always-on-top panel of native buttons."""

    @property
    def hwnd(self) -> int: ...

    @property
    def x(self) -> int: ...

    @property
    def y(self) -> int: ...

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    def is_alive(self) -> bool: ...

    def set_items(self, items: Sequence[ButtonSpec]) -> None: ...

    def set_title(self, text: str) -> None: ...

    def set_status(self, text: str) -> None: ...

    def move(self, x: int, y: int) -> None: ...

    def show(self) -> None: ...

    def hide(self) -> None: ...

    def raise_above(self) -> None: ...

    def take_events(self) -> list[PanelEvent]: ...

    def destroy(self) -> None: ...


class ScreenCapture(Protocol):
    """A transparent input window placed over the target while editing."""

    @property
    def hwnd(self) -> int: ...

    @property
    def rect(self) -> Rect: ...

    def is_alive(self) -> bool: ...

    def move_to(self, rect: Rect) -> None: ...

    def set_cursor(self, cursor: str) -> None: ...

    def show(self) -> None: ...

    def hide(self) -> None: ...

    def take_events(self) -> list[PointerEvent]: ...

    def destroy(self) -> None: ...


class PropertiesPanel(Protocol):
    """A small always-on-top panel that describes the selected rectangle."""

    @property
    def hwnd(self) -> int: ...

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    def is_alive(self) -> bool: ...

    def update(
        self, status: PanelStatus, *, x: int | None = None, y: int | None = None
    ) -> None: ...

    def move(self, x: int, y: int) -> None: ...

    def raise_above(self) -> None: ...

    def destroy(self) -> None: ...


class KeyboardHook(Protocol):
    """Reports (and swallows) configured shortcuts while editing."""

    @property
    def installed(self) -> bool: ...

    def install(self, bindings: "Mapping[str, KeyBinding]") -> bool: ...

    def uninstall(self) -> None: ...

    def set_bindings(self, bindings: "Mapping[str, KeyBinding]") -> None: ...

    def take_events(self) -> list[KeyEvent]: ...


class TargetListener(Protocol):
    """Event source for one target window's geometry/visibility changes."""

    @property
    def watched_hwnd(self) -> int: ...

    @property
    def active(self) -> bool: ...

    def watch(self, hwnd: int) -> bool: ...

    def release(self) -> None: ...

    def take_events(self) -> set[str]: ...


class WindowService(Protocol):
    def pump_events(self) -> None: ...

    def wait_for_events(self, timeout_ms: int) -> bool: ...

    def list_windows(self) -> list[WindowInfo]: ...

    def list_monitors(self) -> list[MonitorInfo]: ...

    def virtual_desktop(self) -> Rect: ...

    def find_target(
        self, profile: WindowProfile, coordinate_space: CoordinateSpace = "window"
    ) -> WindowTarget | None: ...

    def refresh_target(
        self,
        hwnd: int,
        profile: WindowProfile,
        coordinate_space: CoordinateSpace = "window",
    ) -> WindowTarget | None: ...

    def create_overlay(
        self, target: WindowTarget, *, click_through: bool = True
    ) -> Overlay: ...

    def create_button_panel(
        self,
        *,
        x: int,
        y: int,
        items: Sequence[ButtonSpec],
        title: str,
        status: str = "",
        scale: float = 1.0,
    ) -> ButtonPanel: ...

    def create_screen_capture(
        self, rect: Rect, *, cursor: str = "cross"
    ) -> ScreenCapture: ...

    def create_properties_panel(
        self,
        *,
        x: int,
        y: int,
        status: PanelStatus,
        scale: float = 1.0,
    ) -> PropertiesPanel: ...

    def create_keyboard_hook(self) -> KeyboardHook: ...

    def create_target_listener(self) -> TargetListener: ...
