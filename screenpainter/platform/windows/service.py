"""Window discovery and target scoring for the Windows backend."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes as wt
from typing import Sequence

from screenpainter.core.display import MonitorInfo
from screenpainter.core.geometry import Rect
from screenpainter.core.ui import ButtonSpec, PanelStatus
from screenpainter.core.window import (
    CoordinateSpace,
    WindowInfo,
    WindowProfile,
    WindowTarget,
)
from screenpainter.errors import OverlayError
from screenpainter.logging_setup import get_logger

from . import win32
from .interactive import ButtonPanel, PropertiesPanel, ScreenCapture
from .overlay import LayeredOverlay
from .winevent import KeyboardHook, TargetEventListener


def _rect_from_win32(rect: object | None) -> Rect | None:
    if rect is None:
        return None
    left = int(getattr(rect, "left"))
    top = int(getattr(rect, "top"))
    right = int(getattr(rect, "right"))
    bottom = int(getattr(rect, "bottom"))
    if right <= left or bottom <= top:
        return None
    return Rect.from_ltrb(left, top, right, bottom)


class WindowsWindowService:
    """Enumerates windows with ctypes and creates native layered overlays."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        win32.enable_dpi_awareness()
        self.logger = logger or get_logger("windows")
        self._process_cache: dict[int, tuple[str | None, str | None]] = {}

    # -- process metadata ------------------------------------------------
    def _process_info(self, pid: int) -> tuple[str | None, str | None]:
        cached = self._process_cache.get(pid)
        if cached is not None:
            return cached
        executable = win32.get_process_executable(pid)
        name = win32.process_name_from_path(executable)
        value = (name, executable)
        self._process_cache[pid] = value
        return value

    # -- geometry --------------------------------------------------------
    def _window_bounds(self, hwnd: int) -> Rect | None:
        if win32.is_iconic(hwnd):
            # DWM reports a bogus -32000 style rect for minimized windows.
            # Normal placement still gives useful dimensions for selection.
            bounds = _rect_from_win32(win32.get_normal_placement_bounds(hwnd))
            if bounds is not None:
                return bounds
        bounds = _rect_from_win32(win32.get_dwm_frame_bounds(hwnd))
        if bounds is not None:
            return bounds
        return _rect_from_win32(win32.get_raw_window_rect(hwnd))

    def _client_bounds(self, hwnd: int) -> Rect | None:
        # GetClientRect/ClientToScreen can return off-screen garbage for
        # minimized windows.  Callers fall back to normal window bounds; the
        # app hides the overlay while minimized anyway.
        if win32.is_iconic(hwnd):
            return None
        client = wt.RECT()
        if not win32.user32.GetClientRect(hwnd, ctypes.byref(client)):
            return None
        width = int(client.right - client.left)
        height = int(client.bottom - client.top)
        if width <= 0 or height <= 0:
            return None
        origin = wt.POINT(0, 0)
        if not win32.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        return Rect(int(origin.x), int(origin.y), width, height)

    # -- public API ------------------------------------------------------
    def pump_events(self) -> None:
        """Dispatch messages for overlays, the toolbar and capture windows."""
        win32.pump_window_messages()

    def wait_for_events(self, timeout_ms: int) -> bool:
        """Sleep until a message arrives (or the timeout elapses), then pump.

        This is what keeps the idle application from polling: window messages,
        WinEvent callbacks and low level keyboard hooks all wake this call.
        """
        woke = win32.wait_for_messages(timeout_ms)
        win32.pump_window_messages()
        return woke

    def list_monitors(self) -> list[MonitorInfo]:
        """Enumerate the connected displays, primary first is not guaranteed."""
        monitors: list[MonitorInfo] = []
        for hmonitor, device, monitor_rect, work_rect, primary in win32.enum_monitors():
            bounds = _rect_from_win32(monitor_rect)
            if bounds is None:
                continue
            work_area = _rect_from_win32(work_rect) or bounds
            monitors.append(
                MonitorInfo(
                    index=len(monitors),
                    device=device,
                    bounds=bounds,
                    work_area=work_area,
                    primary=primary,
                    dpi=win32.monitor_dpi(hmonitor),
                )
            )
        return monitors

    def virtual_desktop(self) -> Rect:
        rect = win32.virtual_desktop_rect()
        return Rect.from_ltrb(rect.left, rect.top, rect.right, rect.bottom)

    def create_button_panel(
        self,
        *,
        x: int,
        y: int,
        items: Sequence[ButtonSpec],
        title: str,
        status: str = "",
        scale: float = 1.0,
    ) -> ButtonPanel:
        return ButtonPanel(
            x=x,
            y=y,
            items=items,
            title=title,
            status=status,
            scale=scale,
            logger=self.logger,
        )

    def create_screen_capture(
        self, rect: Rect, *, cursor: str = "cross"
    ) -> ScreenCapture:
        return ScreenCapture(rect, cursor=cursor, logger=self.logger)

    def create_properties_panel(
        self,
        *,
        x: int,
        y: int,
        status: PanelStatus,
        scale: float = 1.0,
    ) -> PropertiesPanel:
        return PropertiesPanel(
            x=x,
            y=y,
            status=status,
            scale=scale,
            logger=self.logger,
        )

    def create_keyboard_hook(self) -> KeyboardHook:
        return KeyboardHook(logger=self.logger)

    def create_target_listener(self) -> TargetEventListener:
        return TargetEventListener(logger=self.logger)

    def list_windows(self) -> list[WindowInfo]:
        own_pid = win32.current_process_id()
        windows: list[WindowInfo] = []
        for hwnd in win32.enum_windows():
            if not win32.is_window_visible(hwnd):
                continue
            if not win32.is_top_level(hwnd):
                continue
            pid = win32.get_window_pid(hwnd)
            if pid == own_pid:
                continue
            window_bounds = self._window_bounds(hwnd)
            if window_bounds is None:
                continue
            title = win32.get_window_title(hwnd)
            class_name = win32.get_class_name(hwnd)
            if class_name == win32.OVERLAY_CLASS_NAME:
                continue
            process_name, executable = self._process_info(pid)
            windows.append(
                WindowInfo(
                    hwnd=hwnd,
                    pid=pid,
                    process_name=process_name,
                    executable=executable,
                    title=title,
                    class_name=class_name,
                    window_bounds=window_bounds,
                    client_bounds=self._client_bounds(hwnd) or window_bounds,
                    minimized=win32.is_iconic(hwnd),
                    cloaked=win32.is_window_cloaked(hwnd),
                )
            )
        return windows

    def _matching_windows(
        self, profile: WindowProfile
    ) -> list[WindowInfo]:
        return [
            info
            for info in self.list_windows()
            if not info.cloaked and info.matches(profile)
        ]

    def find_target(
        self, profile: WindowProfile, coordinate_space: CoordinateSpace = "window"
    ) -> WindowTarget | None:
        matches = self._matching_windows(profile)
        if not matches:
            return None
        if profile.select == "first":
            chosen = matches[0]
        else:
            chosen = max(
                matches,
                key=lambda item: (
                    item.window_bounds.width * item.window_bounds.height,
                    -item.window_bounds.y,
                    -item.window_bounds.x,
                ),
            )
        return self._make_target(chosen, coordinate_space)

    def refresh_target(
        self,
        hwnd: int,
        profile: WindowProfile,
        coordinate_space: CoordinateSpace = "window",
    ) -> WindowTarget | None:
        if not win32.is_window(hwnd):
            return None
        if not win32.is_window_visible(hwnd):
            return None
        if not win32.is_top_level(hwnd):
            return None
        if win32.is_window_cloaked(hwnd):
            return None

        window_bounds = self._window_bounds(hwnd)
        if window_bounds is None:
            return None
        pid = win32.get_window_pid(hwnd)
        if pid == win32.current_process_id():
            return None
        process_name, executable = self._process_info(pid)
        title = win32.get_window_title(hwnd)
        class_name = win32.get_class_name(hwnd)
        info = WindowInfo(
            hwnd=hwnd,
            pid=pid,
            process_name=process_name,
            executable=executable,
            title=title,
            class_name=class_name,
            window_bounds=window_bounds,
            client_bounds=self._client_bounds(hwnd) or window_bounds,
            minimized=win32.is_iconic(hwnd),
            cloaked=False,
        )
        if not info.matches(profile):
            return None
        return self._make_target(info, coordinate_space)

    def _make_target(
        self, info: WindowInfo, coordinate_space: CoordinateSpace
    ) -> WindowTarget:
        if coordinate_space == "client":
            bounds = info.client_bounds
            if bounds.width <= 0 or bounds.height <= 0:
                bounds = info.window_bounds
        else:
            bounds = info.window_bounds
        return WindowTarget(
            hwnd=info.hwnd,
            pid=info.pid,
            process_name=info.process_name,
            executable=info.executable,
            title=info.title,
            class_name=info.class_name,
            bounds=bounds,
            window_bounds=info.window_bounds,
            client_bounds=info.client_bounds,
            minimized=info.minimized,
            foreground=win32.get_foreground_window() == info.hwnd,
            coordinate_space=coordinate_space,
        )

    def create_overlay(
        self, target: WindowTarget, *, click_through: bool = True
    ) -> LayeredOverlay:
        bounds = target.bounds
        if bounds.width <= 0 or bounds.height <= 0:
            raise OverlayError(
                f"target window has an empty overlay area: {target.summary()}"
            )
        return LayeredOverlay(
            owner_hwnd=target.hwnd,
            x=bounds.x,
            y=bounds.y,
            width=bounds.width,
            height=bounds.height,
            click_through=click_through,
        )
