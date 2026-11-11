"""End-to-end smoke test using a synthetic native target window.

This test does not touch the real WeChat window.  It creates its own white
Win32 target window, starts ``run.py`` with a temporary config that matches it,
and verifies through screen pixels and Win32 metadata that:

* the overlay is created as a click-through layered owned window;
* black rectangles are rendered;
* the transparent parts of the overlay show the target underneath;
* moving the target moves the overlay (event driven, no 60 Hz polling);
* resizing the target updates the overlay extent.

It uses only the Python standard library and the project's own ctypes helpers.
Run from the project root:

    python tools/smoke_test.py
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes as wt
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from screenpainter.core.geometry import Rect  # noqa: E402
from screenpainter.platform.windows import win32  # noqa: E402

# Extra ctypes declarations needed only by the test harness.
user32 = win32.user32
gdi32 = win32.gdi32
kernel32 = win32.kernel32

FillRect = user32.FillRect
FillRect.restype = ctypes.c_int
FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]

SetForegroundWindow = user32.SetForegroundWindow
SetForegroundWindow.restype = wt.BOOL
SetForegroundWindow.argtypes = [wt.HWND]

AttachThreadInput = user32.AttachThreadInput
AttachThreadInput.restype = wt.BOOL
AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]

GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.restype = wt.DWORD
GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

GetCurrentThreadId = kernel32.GetCurrentThreadId
GetCurrentThreadId.restype = wt.DWORD

WindowFromPoint = user32.WindowFromPoint
WindowFromPoint.restype = wt.HWND
WindowFromPoint.argtypes = [wt.POINT]

UpdateWindow = user32.UpdateWindow
UpdateWindow.restype = wt.BOOL
UpdateWindow.argtypes = [wt.HWND]

GetCursorPos = user32.GetCursorPos
GetCursorPos.restype = wt.BOOL
GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]

SetCursorPos = user32.SetCursorPos
SetCursorPos.restype = wt.BOOL
SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

mouse_event = user32.mouse_event
mouse_event.restype = None
mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
WM_LBUTTONDOWN = 0x0201
SMOKE_BM_CLICK = 0x00F5

WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WS_OVERLAPPEDWINDOW = 0x00CF0000
SW_MINIMIZE = 6
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

WHITE_BRUSH = 0
TARGET_CLASS = "ScreenPainterSmokeTarget"
TARGET_TITLE = "ScreenPainter smoke target"
TARGET_X = 220
TARGET_Y = 150
TARGET_WIDTH = 760
TARGET_HEIGHT = 480
RECT_LOCAL = (60, 100, 140, 100)  # x, y, width, height inside overlay


class _TestError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise _TestError(message)


def _pump() -> None:
    win32.pump_window_messages()


def _pump_for(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _pump()
        time.sleep(0.01)


def _wait_until(predicate: Any, timeout: float, description: str) -> Any:
    end = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < end:
        _pump()
        try:
            value = predicate()
        except _TestError as exc:
            last_error = exc
        else:
            if value:
                return value
        time.sleep(0.03)
    detail = f": {last_error}" if last_error is not None else ""
    raise _TestError(f"timed out waiting for {description}{detail}")


def _force_foreground(hwnd: int) -> None:
    foreground = win32.get_foreground_window()
    foreground_tid = _window_thread_id(foreground)
    target_tid = _window_thread_id(hwnd)
    current_tid = int(GetCurrentThreadId())
    AttachThreadInput(current_tid, foreground_tid, True)
    AttachThreadInput(current_tid, target_tid, True)
    try:
        SetForegroundWindow(hwnd)
    finally:
        AttachThreadInput(current_tid, foreground_tid, False)
        AttachThreadInput(current_tid, target_tid, False)
    _pump_for(0.15)


def _window_thread_id(hwnd: int) -> int:
    pid = wt.DWORD(0)
    return int(GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))


def _screen_pixel(x: int, y: int) -> tuple[int, int, int]:
    pixel = win32.get_screen_pixel(x, y)
    if pixel is None:
        raise _TestError(f"could not read screen pixel at ({x}, {y})")
    return pixel


def _is_black(pixel: tuple[int, int, int], tolerance: int = 12) -> bool:
    return all(channel <= tolerance for channel in pixel)


def _is_white(pixel: tuple[int, int, int], tolerance: int = 12) -> bool:
    return all(channel >= 255 - tolerance for channel in pixel)


def _simulate_click(x: int, y: int) -> None:
    """Click a screen point, then restore the mouse position."""
    previous = wt.POINT()
    had_previous = bool(GetCursorPos(ctypes.byref(previous)))
    SetCursorPos(x, y)
    _pump_for(0.05)
    mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    _pump_for(0.03)
    mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    _pump_for(0.2)
    if had_previous:
        SetCursorPos(previous.x, previous.y)
        _pump_for(0.05)


def _window_rect(hwnd: int) -> wt.RECT:
    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise win32.winapi_error(f"GetWindowRect(0x{hwnd:X}) failed")
    return rect


def _rect_tuple(rect: wt.RECT) -> tuple[int, int, int, int]:
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def _equal_rect(rect: wt.RECT, bounds: Any, tolerance: int = 1) -> bool:
    return (
        abs(int(rect.left) - bounds.x) <= tolerance
        and abs(int(rect.top) - bounds.y) <= tolerance
        and abs(int(rect.right) - bounds.right) <= tolerance
        and abs(int(rect.bottom) - bounds.bottom) <= tolerance
    )


def _find_child_overlay(child_pid: int) -> int | None:
    for hwnd in win32.enum_windows():
        if win32.get_window_pid(hwnd) != child_pid:
            continue
        if win32.get_class_name(hwnd) == win32.OVERLAY_CLASS_NAME:
            return hwnd
    return None


class _NativeTarget:
    def __init__(self) -> None:
        self.class_name = TARGET_CLASS
        self._wndproc = win32.WNDPROC(self._proc)
        self._brush = int(gdi32.GetStockObject(WHITE_BRUSH))
        self.hwnd = 0
        self.clicks = 0
        self._register_and_create()

    def _proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == WM_LBUTTONDOWN:
            self.clicks += 1
            return 0
        if message == WM_PAINT:
            paint = win32.PAINTSTRUCT()
            dc = user32.BeginPaint(hwnd, ctypes.byref(paint))
            if dc:
                rect = wt.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                FillRect(dc, ctypes.byref(rect), self._brush)
                user32.EndPaint(hwnd, ctypes.byref(paint))
            return 0
        if message == WM_ERASEBKGND:
            return 1
        return int(user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _register_and_create(self) -> None:
        window_class = win32.WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = wt.HINSTANCE(win32.get_module_handle())
        window_class.hbrBackground = self._brush
        window_class.lpszClassName = self.class_name
        atom = user32.RegisterClassW(ctypes.byref(window_class))
        if not atom:
            error = ctypes.get_last_error()
            if error != win32.ERROR_CLASS_ALREADY_EXISTS:
                raise win32.winapi_error("RegisterClassW(smoke target) failed", error)
        hwnd = int(
            user32.CreateWindowExW(
                0,
                self.class_name,
                TARGET_TITLE,
                WS_OVERLAPPEDWINDOW,
                TARGET_X,
                TARGET_Y,
                TARGET_WIDTH,
                TARGET_HEIGHT,
                None,
                None,
                wt.HINSTANCE(win32.get_module_handle()),
                None,
            )
            or 0
        )
        if not hwnd:
            raise win32.winapi_error("CreateWindowExW(smoke target) failed")
        self.hwnd = hwnd
        user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
        _pump_for(0.25)
        _force_foreground(hwnd)
        UpdateWindow(hwnd)
        _pump_for(0.2)

    def move(self, dx: int, dy: int) -> None:
        rect = _window_rect(self.hwnd)
        user32.SetWindowPos(
            self.hwnd,
            None,
            rect.left + dx,
            rect.top + dy,
            0,
            0,
            win32.SWP_NOSIZE | win32.SWP_NOZORDER | win32.SWP_NOACTIVATE,
        )
        _pump_for(0.1)

    def resize(self, dw: int, dh: int) -> None:
        rect = _window_rect(self.hwnd)
        user32.SetWindowPos(
            self.hwnd,
            None,
            rect.left,
            rect.top,
            max(200, rect.right - rect.left + dw),
            max(150, rect.bottom - rect.top + dh),
            win32.SWP_NOZORDER | win32.SWP_NOACTIVATE,
        )
        _pump_for(0.1)

    def minimize(self) -> None:
        user32.ShowWindow(self.hwnd, SW_MINIMIZE)
        _pump_for(0.2)
        print(
            "[smoke] target minimized state:",
            "visible=", win32.is_window_visible(self.hwnd),
            "iconic=", win32.is_iconic(self.hwnd),
            "cloaked=", win32.is_window_cloaked(self.hwnd),
            "rect=", _rect_tuple(_window_rect(self.hwnd)),
        )

    def restore(self) -> None:
        user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
        _pump_for(0.2)
        _force_foreground(self.hwnd)
        UpdateWindow(self.hwnd)
        _pump_for(0.15)
        print(
            "[smoke] target restored state:",
            "visible=", win32.is_window_visible(self.hwnd),
            "iconic=", win32.is_iconic(self.hwnd),
            "cloaked=", win32.is_window_cloaked(self.hwnd),
            "title=", repr(win32.get_window_title(self.hwnd)),
            "rect=", _rect_tuple(_window_rect(self.hwnd)),
        )

    def force_front(self) -> None:
        """Best-effort activation, plus topmost as a capture-only fallback."""
        _force_foreground(self.hwnd)
        user32.SetWindowPos(
            self.hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32.SWP_NOMOVE | win32.SWP_NOSIZE | win32.SWP_NOACTIVATE,
        )
        _pump_for(0.1)
        print(
            "[smoke] foreground:",
            hex(win32.get_foreground_window()),
            "target:",
            hex(self.hwnd),
        )

    def drop_topmost(self) -> None:
        if self.hwnd and win32.is_window(self.hwnd):
            user32.SetWindowPos(
                self.hwnd,
                HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                win32.SWP_NOMOVE | win32.SWP_NOSIZE | win32.SWP_NOACTIVATE,
            )

    def destroy(self) -> None:
        if self.hwnd and win32.is_window(self.hwnd):
            user32.DestroyWindow(self.hwnd)
        self.hwnd = 0


def _write_temp_config(path: Path, *, color: str = "#000000", toolbar: bool = False) -> None:
    config = f"""\
version = 3

[app]
verify_ms = 1000
idle_tick_ms = 150
debug = true
debug_interval_ms = 150

[keys]
cancel = "Escape"
confirm = "Enter"
delete = "Delete"
undo = "Ctrl+Z"
redo = "Ctrl+Shift+Z"
select_next = "Tab"
select_prev = "Shift+Tab"
move_left = "Left"
move_right = "Right"
move_up = "Up"
move_down = "Down"
move_left_fast = "Shift+Left"
move_right_fast = "Shift+Right"
move_up_fast = "Shift+Up"
move_down_fast = "Shift+Down"

[logging]
level = "DEBUG"
console = true
file = ""
backup_count = 0

[target]
window = "smoke"

[overlay]
coordinate_space = "window"
click_through = true

[layout]
mode = "auto"
match = "monitor"
group = ""
hidden = false

[groups.smoke]
description = "smoke test rectangles"
resolution = "{_display_resolution()}"
rectangles = [
  {{ name = "smoke_rect", x = {RECT_LOCAL[0]}, y = {RECT_LOCAL[1]}, width = {RECT_LOCAL[2]}, height = {RECT_LOCAL[3]}, color = "{color}", opacity = 1.0 }},
]

[window_profiles.smoke]
description = "ScreenPainter smoke test synthetic window"
class_names = ["{TARGET_CLASS}"]
title_patterns = ["^ScreenPainter smoke target$"]
min_width = 200
min_height = 100
select = "first"

[features.occluder]
type = "black_rectangles"
enabled = true

[features.toolbar]
type = "toolbar"
enabled = {str(toolbar).lower()}
auto_position = true
buttons = ["edit", "move", "hide"]
"""
    path.write_text(config, encoding="utf-8")


def _display_resolution() -> str:
    """Resolution signature of the display hosting the synthetic target."""
    monitors = win32.enum_monitors()
    for _hmonitor, _device, monitor_rect, _work, _primary in monitors:
        if monitor_rect.left <= TARGET_X < monitor_rect.right:
            return f"{monitor_rect.right - monitor_rect.left}x{monitor_rect.bottom - monitor_rect.top}"
    if monitors:
        _hmonitor, _device, monitor_rect, _work, _primary = monitors[0]
        return f"{monitor_rect.right - monitor_rect.left}x{monitor_rect.bottom - monitor_rect.top}"
    return "1920x1080"


_CHILD_OUTPUT: list[str] = []


def _drain_child_output(stream: Any) -> None:
    """Keep reading the child's output so its pipe can never fill up."""
    try:
        for line in stream:
            _CHILD_OUTPUT.append(line)
    except (ValueError, OSError):
        pass


def _start_child(config_path: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    _CHILD_OUTPUT.clear()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "run.py"),
            "--config",
            str(config_path),
            "--duration",
            "120",
            "--log-level",
            "DEBUG",
        ],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    thread = threading.Thread(
        target=_drain_child_output, args=(proc.stdout,), daemon=True
    )
    thread.start()
    return proc


def _find_child_window(
    child_pid: int, class_name: str, *, visible_only: bool = False
) -> int | None:
    for hwnd in win32.enum_windows():
        if win32.get_window_pid(hwnd) != child_pid:
            continue
        if win32.get_class_name(hwnd) != class_name:
            continue
        if visible_only and not win32.is_window_visible(hwnd):
            continue
        return hwnd
    return None


def _panel_buttons(panel_hwnd: int) -> dict[str, int]:
    """The panel assigns control ids 1..N in the configured button order."""
    buttons: dict[str, int] = {}
    for index, key in enumerate(("edit", "move", "hide"), start=1):
        handle = int(win32.user32.GetDlgItem(panel_hwnd, index) or 0)
        if handle:
            buttons[key] = handle
    return buttons


KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_DELETE = 0x2E

user32.keybd_event.restype = None
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]


def _key_event(vk: int, *, up: bool) -> None:
    user32.keybd_event(
        wt.BYTE(vk), wt.BYTE(0), KEYEVENTF_KEYUP if up else 0, 0
    )


def _send_key(vk: int, *, ctrl: bool = False) -> None:
    """Send a real key press so the app's low level hook sees it."""
    win32.pump_window_messages()
    if ctrl:
        _key_event(VK_CONTROL, up=False)
        _pump_for(0.02)
    _key_event(vk, up=False)
    _pump_for(0.03)
    _key_event(vk, up=True)
    if ctrl:
        _pump_for(0.02)
        _key_event(VK_CONTROL, up=True)
    _pump_for(0.25)


def _click_button(handle: int) -> None:
    if not handle or not win32.is_window(handle):
        raise _TestError(f"button 0x{handle:X} does not exist")
    win32.user32.SendMessageW(handle, SMOKE_BM_CLICK, 0, 0)
    _pump_for(0.1)


def _drag(start: tuple[int, int], end: tuple[int, int], *, steps: int = 8) -> None:
    """Press at ``start``, move in steps and release at ``end`` (real mouse)."""
    previous = wt.POINT()
    had_previous = bool(GetCursorPos(ctypes.byref(previous)))
    SetCursorPos(start[0], start[1])
    _pump_for(0.08)
    mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    _pump_for(0.05)
    for step in range(1, steps + 1):
        x = start[0] + (end[0] - start[0]) * step // steps
        y = start[1] + (end[1] - start[1]) * step // steps
        SetCursorPos(x, y)
        _pump_for(0.03)
    mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    _pump_for(0.15)
    if had_previous:
        SetCursorPos(previous.x, previous.y)
        _pump_for(0.05)


def _read_config(path: Path) -> dict[str, Any]:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def _wait_for_config(
    path: Path, predicate: Any, timeout: float, description: str
) -> Any:
    def _check() -> Any:
        try:
            data = _read_config(path)
        except Exception as exc:  # noqa: BLE001 - the file may be mid-write
            raise _TestError(f"cannot read the configuration yet: {exc}") from exc
        return predicate(data)

    return _wait_until(_check, timeout, description)


def _collect_child_output(proc: subprocess.Popen[str]) -> str:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            pass
    return "".join(_CHILD_OUTPUT)


def run_smoke_test() -> int:
    win32.enable_dpi_awareness()
    target = _NativeTarget()
    proc: subprocess.Popen[str] | None = None
    config_path: Path | None = None
    try:
        print("[smoke] target window:", hex(target.hwnd), _rect_tuple(_window_rect(target.hwnd)))
        _force_foreground(target.hwnd)
        target.force_front()
        fd, config_name = tempfile.mkstemp(prefix="screenpainter-smoke-", suffix=".toml")
        os.close(fd)
        config_path = Path(config_name)
        _write_temp_config(config_path, toolbar=True)
        print("[smoke] temporary config:", config_path)
        proc = _start_child(config_path)
        print("[smoke] child pid:", proc.pid)

        def _overlay_ready() -> int:
            overlay = _find_child_overlay(proc.pid)
            _require(overlay is not None, "overlay window not created yet")
            _require(win32.is_window_visible(overlay), "overlay is not visible")
            target_bounds = win32.get_dwm_frame_bounds(target.hwnd)
            _require(target_bounds is not None, "target has no DWM frame bounds")
            expected = Rect.from_ltrb(
                target_bounds.left,
                target_bounds.top,
                target_bounds.right,
                target_bounds.bottom,
            )
            rect = _window_rect(overlay)
            _require(
                _equal_rect(rect, expected),
                f"overlay rect does not match target: overlay={_rect_tuple(rect)}, target={expected}",
            )
            black_x = rect.left + RECT_LOCAL[0] + RECT_LOCAL[2] // 2
            black_y = rect.top + RECT_LOCAL[1] + RECT_LOCAL[3] // 2
            _require(
                _is_black(_screen_pixel(black_x, black_y)),
                f"expected black rectangle at ({black_x}, {black_y}), "
                f"got {_screen_pixel(black_x, black_y)}",
            )
            white_x = rect.left + 300
            white_y = rect.top + 220
            _require(
                _is_white(_screen_pixel(white_x, white_y)),
                f"expected transparent overlay area to show white target at "
                f"({white_x}, {white_y}), got {_screen_pixel(white_x, white_y)}",
            )
            ex_style = win32.get_window_long_ptr(overlay, win32.GWL_EXSTYLE)
            _require(ex_style & win32.WS_EX_LAYERED, "overlay is missing WS_EX_LAYERED")
            _require(ex_style & win32.WS_EX_TRANSPARENT, "overlay is missing WS_EX_TRANSPARENT")
            _require(ex_style & win32.WS_EX_NOACTIVATE, "overlay is missing WS_EX_NOACTIVATE")
            wfp = WindowFromPoint(wt.POINT(black_x, black_y))
            _require(
                int(wfp) == target.hwnd,
                f"WindowFromPoint at the black area is 0x{int(wfp):X}, not the target",
            )
            print(
                "[smoke] overlay:",
                hex(overlay),
                "rect=",
                _rect_tuple(rect),
                "exstyle=",
                hex(ex_style),
                "WindowFromPoint=",
                hex(int(wfp)),
            )
            return overlay

        overlay_hwnd = _wait_until(_overlay_ready, 10.0, "overlay to render")
        _force_foreground(target.hwnd)
        target.force_front()

        overlay_rect = _window_rect(overlay_hwnd)
        click_x = overlay_rect.left + RECT_LOCAL[0] + RECT_LOCAL[2] // 2
        click_y = overlay_rect.top + RECT_LOCAL[1] + RECT_LOCAL[3] // 2
        target.clicks = 0
        _simulate_click(click_x, click_y)
        _require(
            target.clicks > 0,
            "a real mouse click on the covered rectangle did not reach the target window; "
            "click-through is broken",
        )
        print("[smoke] click-through PASS")

        print("[smoke] moving target by (30, 20) and waiting for overlay to follow")
        old_black = None
        rect_before = _window_rect(overlay_hwnd)
        old_black = (
            rect_before.left + RECT_LOCAL[0] + 5,
            rect_before.top + RECT_LOCAL[1] + 5,
        )
        target.move(30, 20)

        def _overlay_followed() -> bool:
            target_bounds = win32.get_dwm_frame_bounds(target.hwnd)
            _require(target_bounds is not None, "target has no bounds after move")
            rect = _window_rect(overlay_hwnd)
            expected_left = target_bounds.left
            expected_top = target_bounds.top
            _require(
                abs(rect.left - expected_left) <= 1 and abs(rect.top - expected_top) <= 1,
                f"overlay has not followed target yet: overlay={_rect_tuple(rect)}, "
                f"target=({target_bounds.left},{target_bounds.top})",
            )
            black_x = rect.left + RECT_LOCAL[0] + RECT_LOCAL[2] // 2
            black_y = rect.top + RECT_LOCAL[1] + RECT_LOCAL[3] // 2
            _require(
                _is_black(_screen_pixel(black_x, black_y)),
                f"black rectangle missing after move at ({black_x}, {black_y}): "
                f"{_screen_pixel(black_x, black_y)}",
            )
            old_pixel = _screen_pixel(*old_black)
            _require(
                _is_white(old_pixel),
                f"old rectangle area should be white target after move at {old_black}: {old_pixel}",
            )
            return True

        _wait_until(_overlay_followed, 5.0, "overlay movement")
        print("[smoke] movement PASS")

        print("[smoke] resizing target by (+80, +50) and waiting for overlay resize")
        target.resize(80, 50)

        def _overlay_resized() -> bool:
            target_bounds = win32.get_dwm_frame_bounds(target.hwnd)
            _require(target_bounds is not None, "target has no bounds after resize")
            rect = _window_rect(overlay_hwnd)
            _require(
                _equal_rect(rect, Rect.from_ltrb(
                    target_bounds.left,
                    target_bounds.top,
                    target_bounds.right,
                    target_bounds.bottom,
                )),
                f"overlay size did not follow resize: overlay={_rect_tuple(rect)}, "
                f"target={(target_bounds.left, target_bounds.top, target_bounds.right, target_bounds.bottom)}",
            )
            black_x = rect.left + RECT_LOCAL[0] + RECT_LOCAL[2] // 2
            black_y = rect.top + RECT_LOCAL[1] + RECT_LOCAL[3] // 2
            _require(
                _is_black(_screen_pixel(black_x, black_y)),
                f"black rectangle missing after resize at ({black_x}, {black_y})",
            )
            return True

        _wait_until(_overlay_resized, 5.0, "overlay resize")
        print("[smoke] resize PASS")

        print("[smoke] minimizing target and waiting for overlay to hide")
        target.minimize()

        def _overlay_hidden() -> bool:
            visible = [
                hwnd
                for hwnd in win32.enum_windows()
                if win32.get_window_pid(hwnd) == proc.pid
                and win32.get_class_name(hwnd) == win32.OVERLAY_CLASS_NAME
                and win32.is_window_visible(hwnd)
            ]
            _require(
                not visible,
                f"overlay is still visible while the target is minimized: {visible}",
            )
            return True

        _wait_until(_overlay_hidden, 5.0, "overlay to hide after minimize")
        print("[smoke] minimize PASS")

        print("[smoke] restoring target and waiting for overlay to reappear")
        target.restore()
        target.force_front()

        def _overlay_restored() -> bool:
            current_overlay = _find_child_overlay(proc.pid)
            _require(current_overlay is not None, "overlay window does not exist after restore")
            _require(
                win32.is_window_visible(current_overlay),
                "overlay did not become visible again",
            )
            target_bounds = win32.get_dwm_frame_bounds(target.hwnd)
            _require(target_bounds is not None, "target has no bounds after restore")
            rect = _window_rect(current_overlay)
            _require(
                _equal_rect(rect, Rect.from_ltrb(
                    target_bounds.left,
                    target_bounds.top,
                    target_bounds.right,
                    target_bounds.bottom,
                )),
                f"overlay geometry wrong after restore: overlay={_rect_tuple(rect)}",
            )
            black_x = rect.left + RECT_LOCAL[0] + RECT_LOCAL[2] // 2
            black_y = rect.top + RECT_LOCAL[1] + RECT_LOCAL[3] // 2
            _require(
                _is_black(_screen_pixel(black_x, black_y)),
                f"black rectangle missing after restore at ({black_x}, {black_y})",
            )
            return True

        _wait_until(_overlay_restored, 5.0, "overlay restore")
        print("[smoke] restore PASS")

        # ------------------------------------------------------------------
        # Debug mode: hot reload of the configuration file
        # ------------------------------------------------------------------
        print("[smoke] debug mode: rewriting the config with a red rectangle")

        def _rectangle_pixel(color_center: tuple[int, int]) -> tuple[int, int, int]:
            current = _find_child_overlay(proc.pid)
            _require(current is not None, "overlay disappeared")
            rect = _window_rect(current)
            return _screen_pixel(rect.left + color_center[0], rect.top + color_center[1])

        center = (RECT_LOCAL[0] + RECT_LOCAL[2] // 2, RECT_LOCAL[1] + RECT_LOCAL[3] // 2)

        def _is_red(pixel: tuple[int, int, int], tolerance: int = 40) -> bool:
            return pixel[0] >= 255 - tolerance and pixel[1] <= tolerance and pixel[2] <= tolerance

        _write_temp_config(config_path, color="#FF0000", toolbar=True)

        def _became_red() -> bool:
            pixel = _rectangle_pixel(center)
            _require(_is_red(pixel), f"rectangle is still {pixel}, hot reload did not apply")
            return True

        _wait_until(_became_red, 6.0, "hot reload to paint the rectangle red")
        print("[smoke] hot reload PASS")

        _write_temp_config(config_path, color="#000000", toolbar=True)

        def _became_black() -> bool:
            pixel = _rectangle_pixel(center)
            _require(_is_black(pixel), f"rectangle did not go back to black: {pixel}")
            return True

        _wait_until(_became_black, 6.0, "hot reload to paint the rectangle black again")
        print("[smoke] hot reload revert PASS")

        # ------------------------------------------------------------------
        # Floating toolbar: 编辑 / 移动 / 隐藏
        # ------------------------------------------------------------------
        print("[smoke] waiting for the floating toolbar")

        def _toolbar_ready() -> int:
            panel = _find_child_window(
                proc.pid, win32.PANEL_CLASS_NAME, visible_only=True
            )
            _require(panel is not None, "toolbar panel was not created")
            ex_style = win32.get_window_long_ptr(panel, win32.GWL_EXSTYLE)
            _require(ex_style & win32.WS_EX_TOPMOST, "toolbar is not topmost")
            _require(ex_style & win32.WS_EX_TOOLWINDOW, "toolbar is missing WS_EX_TOOLWINDOW")
            _require(ex_style & win32.WS_EX_NOACTIVATE, "toolbar is missing WS_EX_NOACTIVATE")
            buttons = _panel_buttons(panel)
            for key in ("edit", "move", "hide"):
                _require(key in buttons, f"toolbar button {key!r} is missing")
            return panel

        panel_hwnd = _wait_until(_toolbar_ready, 10.0, "toolbar panel to appear")
        panel_rect = _window_rect(panel_hwnd)
        print(
            "[smoke] toolbar:",
            hex(panel_hwnd),
            "rect=",
            _rect_tuple(panel_rect),
            "buttons=",
            {key: hex(value) for key, value in _panel_buttons(panel_hwnd).items()},
        )
        target.drop_topmost()
        _force_foreground(target.hwnd)
        print("[smoke] toolbar PASS")

        # -- edit mode: draw, resize, delete, undo -------------------------
        print("[smoke] toolbar edit: clicking 编辑 and dragging a new rectangle")
        buttons = _panel_buttons(panel_hwnd)
        _click_button(buttons["edit"])

        def _capture_ready() -> int:
            capture = _find_child_window(
                proc.pid, win32.CAPTURE_CLASS_NAME, visible_only=True
            )
            _require(capture is not None, "capture window was not created")
            return capture

        capture_hwnd = _wait_until(_capture_ready, 5.0, "capture window to appear")
        print("[smoke] capture window:", hex(capture_hwnd), _rect_tuple(_window_rect(capture_hwnd)))
        current_overlay = _find_child_overlay(proc.pid)
        _require(current_overlay is not None, "overlay disappeared before editing")
        overlay_rect = _window_rect(current_overlay)
        draw_start = (overlay_rect.left + 300, overlay_rect.top + 250)
        draw_end = (overlay_rect.left + 400, overlay_rect.top + 330)
        _drag(draw_start, draw_end)

        def _rectangle_saved(data: dict[str, Any]) -> bool:
            shapes = data["groups"]["smoke"]["rectangles"]
            _require(
                len(shapes) == 2,
                f"expected 2 rectangles in the config after drawing, got {len(shapes)}",
            )
            shape = shapes[1]
            _require(
                (shape["x"], shape["y"], shape["width"], shape["height"])
                == (300, 250, 100, 80),
                f"the saved rectangle has unexpected geometry: {shape}",
            )
            return True

        _wait_for_config(config_path, _rectangle_saved, 8.0, "the drawn rectangle to be saved")

        def _drawn_pixel() -> bool:
            pixel = _rectangle_pixel((350, 290))
            _require(_is_black(pixel), f"the drawn rectangle is not visible on screen: {pixel}")
            return True

        _wait_until(_drawn_pixel, 5.0, "the drawn rectangle to render")
        print("[smoke] edit-create PASS")

        # The new rectangle is selected, so the colour/properties panel appears.
        def _properties_ready() -> int:
            panel = _find_child_window(
                proc.pid, win32.PROPERTIES_CLASS_NAME, visible_only=True
            )
            _require(panel is not None, "the rectangle properties panel was not created")
            ex_style = win32.get_window_long_ptr(panel, win32.GWL_EXSTYLE)
            _require(ex_style & win32.WS_EX_TOPMOST, "properties panel is not topmost")
            _require(
                ex_style & win32.WS_EX_NOACTIVATE,
                "properties panel is missing WS_EX_NOACTIVATE",
            )
            rect = _window_rect(panel)
            _require(
                rect.right - rect.left > 100 and rect.bottom - rect.top > 100,
                f"properties panel has an unexpected size: {_rect_tuple(rect)}",
            )
            return panel

        properties_hwnd = _wait_until(_properties_ready, 5.0, "the rectangle properties panel")
        print("[smoke] properties panel:", hex(properties_hwnd), _rect_tuple(_window_rect(properties_hwnd)))
        print("[smoke] properties PASS")

        print("[smoke] toolbar edit: dragging the right edge to resize the selection")
        before = _read_config(config_path)["groups"]["smoke"]["rectangles"][1]
        edge_x = overlay_rect.left + before["x"] + before["width"]
        edge_y = overlay_rect.top + before["y"] + before["height"] // 2
        _drag((edge_x, edge_y), (edge_x + 40, edge_y))

        def _resized(data: dict[str, Any]) -> bool:
            shape = data["groups"]["smoke"]["rectangles"][1]
            _require(
                shape["width"] == before["width"] + 40,
                f"the rectangle was not resized: {shape}",
            )
            _require(
                shape["height"] == before["height"],
                f"resizing an edge changed the height: {shape}",
            )
            return True

        _wait_for_config(config_path, _resized, 8.0, "the resized rectangle to be saved")
        print("[smoke] edit-resize PASS")

        print("[smoke] toolbar edit: Delete removes the selection, Ctrl+Z restores it")
        _send_key(VK_DELETE, ctrl=False)

        def _deleted(data: dict[str, Any]) -> bool:
            shapes = data["groups"]["smoke"]["rectangles"]
            _require(
                len(shapes) == 1,
                f"Delete did not remove the selected rectangle (got {len(shapes)})",
            )
            return True

        _wait_for_config(config_path, _deleted, 6.0, "Delete to remove the rectangle")

        def _deleted_pixel() -> bool:
            pixel = _rectangle_pixel((350, 290))
            _require(_is_white(pixel), f"the deleted rectangle is still painted: {pixel}")
            return True

        _wait_until(_deleted_pixel, 5.0, "the deleted rectangle to disappear")

        _send_key(ord("Z"), ctrl=True)

        def _undone(data: dict[str, Any]) -> bool:
            shapes = data["groups"]["smoke"]["rectangles"]
            _require(len(shapes) == 2, f"Ctrl+Z did not restore the rectangle (got {len(shapes)})")
            _require(
                (shapes[1]["x"], shapes[1]["y"]) == (300, 250),
                f"the restored rectangle has the wrong position: {shapes[1]}",
            )
            return True

        _wait_for_config(config_path, _undone, 6.0, "Ctrl+Z to restore the rectangle")
        print("[smoke] edit-delete/undo PASS")

        # Leave edit mode first: the selected rectangle's properties panel is a
        # topmost window and would cover the pixels checked below.
        _click_button(buttons["edit"])  # labelled 完成 while editing
        _pump_for(0.4)

        # -- hiding every rectangle ---------------------------------------
        print("[smoke] toolbar hide: all rectangles should disappear and be saved")
        _click_button(buttons["hide"])

        def _hidden_saved(data: dict[str, Any]) -> bool:
            _require(data["layout"]["hidden"] is True, "[layout] hidden was not saved")
            return True

        _wait_for_config(config_path, _hidden_saved, 5.0, "[layout] hidden to be saved")

        def _shapes_gone() -> bool:
            pixel = _rectangle_pixel((350, 290))
            _require(_is_white(pixel), f"the rectangle is still visible after hiding: {pixel}")
            return True

        _wait_until(_shapes_gone, 5.0, "rectangles to be hidden")
        _click_button(buttons["hide"])  # 显示

        def _shown_again() -> bool:
            data = _read_config(config_path)
            _require(data["layout"]["hidden"] is False, "[layout] hidden was not cleared")
            pixel = _rectangle_pixel((350, 290))
            _require(_is_black(pixel), f"the rectangle did not come back: {pixel}")
            return True

        _wait_until(_shown_again, 6.0, "rectangles to be shown again")
        print("[smoke] hide PASS")

        # -- moving a rectangle -------------------------------------------
        print("[smoke] toolbar move: selecting and dragging the first rectangle by (60, 40)")
        _click_button(buttons["move"])

        def _capture_ready_again() -> int:
            capture = _find_child_window(
                proc.pid, win32.CAPTURE_CLASS_NAME, visible_only=True
            )
            _require(capture is not None, "capture window was not recreated for move mode")
            return capture

        _wait_until(_capture_ready_again, 5.0, "capture window for move mode")
        current_overlay = _find_child_overlay(proc.pid)
        _require(current_overlay is not None, "overlay disappeared before moving")
        overlay_rect = _window_rect(current_overlay)
        move_start = (overlay_rect.left + 100, overlay_rect.top + 150)
        _drag(move_start, (move_start[0] + 60, move_start[1] + 40))
        _click_button(buttons["move"])  # now labelled 完成

        def _move_saved(data: dict[str, Any]) -> bool:
            shapes = data["groups"]["smoke"]["rectangles"]
            first = shapes[0]
            _require(
                (first["x"], first["y"]) == (120, 140),
                f"the moved rectangle was not saved: {first}",
            )
            return True

        _wait_for_config(config_path, _move_saved, 8.0, "the moved rectangle to be saved")

        def _move_visible() -> bool:
            moved = _rectangle_pixel((160, 180))
            _require(_is_black(moved), f"the moved rectangle is not visible: {moved}")
            old = _rectangle_pixel((100, 150))
            _require(_is_white(old), f"the old rectangle position is still painted: {old}")
            return True

        _wait_until(_move_visible, 5.0, "the moved rectangle to render")
        print("[smoke] move PASS")

        # -- dragging the toolbar itself ----------------------------------
        print("[smoke] toolbar drag: moving the button panel by (-60, 40)")
        panel_rect = _window_rect(panel_hwnd)
        drag_start = (
            (panel_rect.left + panel_rect.right) // 2,
            panel_rect.top + 8,
        )
        _drag(drag_start, (drag_start[0] - 60, drag_start[1] + 40))

        def _panel_position_saved(data: dict[str, Any]) -> bool:
            toolbar = data["features"]["toolbar"]
            _require(
                toolbar["auto_position"] is False,
                "the toolbar position was not saved (auto_position is still true)",
            )
            _require(
                "x" in toolbar and "y" in toolbar,
                "the toolbar saved position is missing x/y",
            )
            return (toolbar["x"], toolbar["y"])

        saved_position = _wait_for_config(
            config_path, _panel_position_saved, 8.0, "the toolbar position to be saved"
        )
        panel_rect_after = _window_rect(panel_hwnd)
        _require(
            abs(panel_rect_after.left - saved_position[0]) <= 1
            and abs(panel_rect_after.top - saved_position[1]) <= 1,
            "the saved toolbar position does not match the window position: "
            f"{panel_rect_after} vs {saved_position}",
        )
        _require(
            panel_rect_after.left != panel_rect.left,
            "the toolbar did not move at all",
        )
        print("[smoke] toolbar drag PASS")

        print("[smoke] all checks passed")
        return 0
    except _TestError as exc:
        print(f"[smoke] FAILED: {exc}", file=sys.stderr)
        if proc is not None and config_path is not None:
            print("[smoke] child output follows:", file=sys.stderr)
            proc.terminate()
            print(_collect_child_output(proc), file=sys.stderr)
        return 1
    except Exception:
        import traceback

        traceback.print_exc()
        return 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        target.drop_topmost()
        target.destroy()
        _pump_for(0.1)
        try:
            if config_path is not None:
                config_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(run_smoke_test())
