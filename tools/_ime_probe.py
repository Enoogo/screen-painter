"""Temporary diagnostic: measure IME candidate-panel latency with/without the app.

This script is a throw-away investigation tool (not part of the product).  It:

1. starts a target application (Notepad or a dedicated Edge window);
2. focuses its text field so the system IME (Rime/Weasel) composes there;
3. types pinyin letters with ``SendInput`` and measures, with a screen-pixel
   watcher, how long the IME candidate panel takes to appear / update;
4. repeats the same measurement while ScreenPainter runs against that window.

All keystrokes go to a throw-away window; nothing is sent to WeChat.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from ctypes import wintypes as wt
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from screenpainter.platform.windows import win32  # noqa: E402

user32 = win32.user32
gdi32 = win32.gdi32
kernel32 = win32.kernel32

# ---------------------------------------------------------------------------
# ctypes declarations

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
ULONG_PTR = ctypes.c_size_t

user32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.SetForegroundWindow.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.AttachThreadInput.restype = wt.BOOL
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetFocus.restype = wt.HWND
user32.GetFocus.argtypes = []
user32.SetFocus.restype = wt.HWND
user32.SetFocus.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetForegroundWindow.argtypes = []
user32.FindWindowExW.restype = wt.HWND
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.GetGUIThreadInfo.restype = wt.BOOL
user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.c_void_p]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.EnumWindows.restype = wt.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wt.HDC,
    ctypes.POINTER(win32.BITMAPINFO),
    wt.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wt.HANDLE,
    wt.DWORD,
]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.BitBlt.restype = wt.BOOL
gdi32.BitBlt.argtypes = [
    wt.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wt.DWORD,
]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]

kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.GetCurrentThreadId.argtypes = []
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE,
    wt.DWORD,
    wt.LPWSTR,
    ctypes.POINTER(wt.DWORD),
]

SRCCOPY = 0x00CC0020
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
GW_OWNER = 4

WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008

VK_ESCAPE = 0x1B


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hwndActive", wt.HWND),
        ("hwndFocus", wt.HWND),
        ("hwndCapture", wt.HWND),
        ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND),
        ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


# ---------------------------------------------------------------------------
# helpers


def process_name(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def enum_windows() -> list[int]:
    handles: list[int] = []

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        handles.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return handles


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def ex_style(hwnd: int) -> int:
    return int(user32.GetWindowLongPtrW(hwnd, -20)) & 0xFFFFFFFF


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def find_weasel_panel() -> int | None:
    """The IME candidate panel: WeaselServer layered topmost popup."""
    skip = {"TrayIconClass", "GDI+ Hook Window Class", "WeaselIPCWindow_1.0"}
    candidates: list[tuple[int, int]] = []
    for hwnd in enum_windows():
        if process_name(window_pid(hwnd)).lower() != "weaselserver.exe":
            continue
        cls = class_name(hwnd)
        if cls in skip:
            continue
        style = ex_style(hwnd)
        if style & WS_EX_LAYERED and style & WS_EX_TOPMOST:
            left, top, width, height = rect_of(hwnd)
            candidates.append((width * height, hwnd))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def type_key(vk: int) -> None:
    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].u.ki.wVk = vk
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].u.ki.wVk = vk
    inputs[1].u.ki.dwFlags = KEYEVENTF_KEYUP
    user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))


def force_foreground(hwnd: int) -> None:
    foreground = int(user32.GetForegroundWindow() or 0)
    foreground_tid = window_pid_thread(foreground)
    target_tid = window_pid_thread(hwnd)
    current_tid = int(kernel32.GetCurrentThreadId())
    user32.AttachThreadInput(current_tid, foreground_tid, True)
    user32.AttachThreadInput(current_tid, target_tid, True)
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(current_tid, foreground_tid, False)
        user32.AttachThreadInput(current_tid, target_tid, False)
    time.sleep(0.15)


def window_pid_thread(hwnd: int) -> int:
    if not hwnd:
        return 0
    pid = wt.DWORD(0)
    return int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))


def focus_hwnd(hwnd: int, child: int) -> bool:
    """Give keyboard focus to ``child`` of the (already foreground) ``hwnd``."""
    thread_id = window_pid_thread(hwnd)
    current_tid = int(kernel32.GetCurrentThreadId())
    user32.AttachThreadInput(current_tid, thread_id, True)
    try:
        user32.SetFocus(child)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        ok = bool(user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)))
        return ok and int(info.hwndFocus or 0) == child
    finally:
        user32.AttachThreadInput(current_tid, thread_id, False)


class ScreenRegion:
    """A DIB-backed screen grabber for one fixed screen rectangle."""

    def __init__(self, left: int, top: int, width: int, height: int) -> None:
        self.left = left
        self.top = top
        self.width = max(1, width)
        self.height = max(1, height)
        self._screen = int(user32.GetDC(None) or 0)
        self._mem = int(gdi32.CreateCompatibleDC(self._screen) or 0)
        info = win32.BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(win32.BITMAPINFOHEADER)
        info.bmiHeader.biWidth = self.width
        info.bmiHeader.biHeight = -self.height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = win32.BI_RGB
        info.bmiHeader.biSizeImage = self.width * self.height * 4
        bits = ctypes.c_void_p()
        self._bitmap = int(
            gdi32.CreateDIBSection(
                self._screen,
                ctypes.byref(info),
                win32.DIB_RGB_COLORS,
                ctypes.byref(bits),
                None,
                0,
            )
            or 0
        )
        self._bits = int(bits.value)
        gdi32.SelectObject(self._mem, self._bitmap)
        self._size = self.width * self.height * 4

    def grab(self) -> bytes:
        gdi32.BitBlt(
            self._mem,
            0,
            0,
            self.width,
            self.height,
            self._screen,
            self.left,
            self.top,
            SRCCOPY,
        )
        return ctypes.string_at(self._bits, self._size)

    def close(self) -> None:
        if self._mem and self._bitmap:
            gdi32.DeleteObject(self._bitmap)
            self._bitmap = 0
        if self._mem:
            gdi32.DeleteDC(self._mem)
            self._mem = 0
        if self._screen:
            user32.ReleaseDC(None, self._screen)
            self._screen = 0


# ---------------------------------------------------------------------------
# measurement


def _stable_image(region: ScreenRegion, timeout: float = 0.6) -> bytes:
    """Wait until the captured region stops changing, then return it."""
    previous = region.grab()
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        time.sleep(0.02)
        current = region.grab()
        if current == previous:
            return current
        previous = current
    return previous


def _diff_rows(older: bytes, newer: bytes, width: int, height: int) -> list[int]:
    """Number of changed pixels per row (raw BGRA buffers of the same size)."""
    counts = [0] * height
    stride = width * 4
    for y in range(height):
        start = y * stride
        old_row = older[start : start + stride]
        new_row = newer[start : start + stride]
        if old_row == new_row:
            continue
        count = 0
        for x in range(0, stride, 4):
            if old_row[x : x + 4] != new_row[x : x + 4]:
                count += 1
        counts[y] = count
    return counts


def find_panel_region(panel_search: ScreenRegion) -> tuple[int, int, int, int] | None:
    """Derive the candidate-panel rectangle from a hidden/visible screenshot pair."""
    type_key(VK_ESCAPE)
    time.sleep(0.35)
    hidden = _stable_image(panel_search)
    for letter in "nihao":
        type_key(ord(letter.upper()))
        time.sleep(0.06)
    time.sleep(0.7)
    visible = panel_search.grab()
    width, height = panel_search.width, panel_search.height
    rows = _diff_rows(hidden, visible, width, height)
    interesting = [y for y, count in enumerate(rows) if count >= 8]
    if not interesting:
        return None
    top = min(interesting)
    bottom = max(interesting)
    # Column extent inside that band.
    stride = width * 4
    left = width
    right = 0
    for y in range(top, bottom + 1):
        start = y * stride
        old_row = hidden[start : start + stride]
        new_row = visible[start : start + stride]
        for x in range(0, stride, 4):
            if old_row[x : x + 4] != new_row[x : x + 4]:
                column = x // 4
                if column < left:
                    left = column
                if column > right:
                    right = column
    if right <= left or bottom <= top:
        return None
    return (
        panel_search.left + max(0, left - 2),
        panel_search.top + max(0, top - 2),
        min(width, right - left + 5),
        min(height, bottom - top + 5),
    )


def measure_latency(
    region: ScreenRegion,
    letters: str,
    *,
    rounds: int,
    label: str,
    verbose: bool,
) -> list[tuple[str, float]]:
    """Type ``letters`` and measure how long the candidate panel takes to change."""
    samples: list[tuple[str, float]] = []
    for _round_index in range(rounds):
        type_key(VK_ESCAPE)
        time.sleep(0.3)
        baseline = _stable_image(region)
        for letter in letters:
            start = time.perf_counter()
            type_key(ord(letter.upper()))
            observed: float | None = None
            deadline = start + 3.0
            while time.perf_counter() < deadline:
                image = region.grab()
                if image != baseline:
                    observed = (time.perf_counter() - start) * 1000.0
                    baseline = image
                    break
            if observed is None:
                observed = float("nan")
                baseline = region.grab()
            samples.append(("panel", observed))
            if verbose:
                print(f"    {label}: {observed:7.1f} ms")
            time.sleep(0.12)
        type_key(VK_ESCAPE)
        time.sleep(0.25)
    values = [value for _, value in samples if value == value]
    if not values:
        print(f"  [{label}] IME panel never changed - is the IME in Chinese mode?")
        return samples
    ordered = sorted(values)
    print(
        f"  [{label}] n={len(values)} median={ordered[len(ordered) // 2]:6.1f} ms "
        f"p90={ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]:6.1f} ms "
        f"max={ordered[-1]:6.1f} ms"
    )
    return samples


# ---------------------------------------------------------------------------
# app config


def current_monitor_resolution() -> str:
    for _hmon, _device, monitor_rect, _work, _primary in win32.enum_monitors():
        return f"{monitor_rect.right - monitor_rect.left}x{monitor_rect.bottom - monitor_rect.top}"
    return "1920x1080"


def write_config(
    path: Path,
    *,
    process: str,
    title_pattern: str,
    class_pattern: str = "",
    hidden: bool,
    toolbar: bool,
    tick_ms: int,
    log_path: Path,
) -> None:
    resolution = current_monitor_resolution()
    class_names = f'class_names = ["{class_pattern}"]' if class_pattern else ""
    text = f"""version = 3

[app]
name = "ScreenPainterProbe"
verify_ms = {max(100, tick_ms)}
idle_tick_ms = 400
debug = false
debug_interval_ms = 400

[logging]
level = "INFO"
console = false
file = "{log_path.as_posix()}"
max_bytes = 2000000
backup_count = 1

[target]
window = "probe"

[overlay]
coordinate_space = "window"
click_through = true

[layout]
mode = "auto"
match = "monitor"
group = ""
hidden = {str(hidden).lower()}

[groups.probe_group]
description = "probe group"
resolution = "{resolution}"
rectangles = [
  {{ name = "probe", x = 40, y = 40, width = 200, height = 80, color = "#000000", opacity = 1.0 }},
]

[window_profiles.probe]
description = "probe target"
process_names = ["{process}"]
title_patterns = ["{title_pattern}"]
{class_names}
min_width = 100
min_height = 100
select = "largest"

[features.occluder]
type = "black_rectangles"
enabled = true

[features.toolbar]
type = "toolbar"
enabled = {str(toolbar).lower()}
auto_position = true
buttons = ["edit", "move", "hide"]
default_color = "#000000"
default_opacity = 1.0
"""
    path.write_text(text, encoding="utf-8")


def _app_window_exists() -> bool:
    wanted = {"ScreenPainterOverlayWindow", "ScreenPainterButtonPanel"}
    for hwnd in enum_windows():
        if class_name(hwnd) in wanted:
            return True
    return False


class AppRun:
    def __init__(self, config: Path, log: Path) -> None:
        self.config = config
        self.log = log
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        started = time.time()
        self.process = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "run.py"), "--config", str(self.config)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = started + 20
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise SystemExit(f"ScreenPainter exited early:\n{self._log_text()}")
            if _app_window_exists():
                print(f"  (app windows in {time.time() - started:.2f}s)")
                return
            time.sleep(0.05)
        raise SystemExit(f"ScreenPainter did not start in time:\n{self._log_text()}")

    def _log_text(self) -> str:
        try:
            return self.log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None


def launch_target() -> tuple[int, int, subprocess.Popen[bytes]]:
    process = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "tools" / "_ime_target.py")],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        for hwnd in enum_windows():
            if class_name(hwnd) != "ScreenPainterImeTarget":
                continue
            if not user32.IsWindowVisible(hwnd):
                continue
            for child in child_windows(hwnd):
                if class_name(child).upper() == "EDIT":
                    return hwnd, child, process
        if process.poll() is not None:
            error = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
            raise SystemExit(f"target window process exited: {error}")
        time.sleep(0.1)
    raise SystemExit("could not find the synthetic target window")


def child_windows(hwnd: int) -> list[int]:
    children: list[int] = []

    @WNDENUMPROC
    def callback(child, _lparam):
        children.append(int(child))
        return True

    user32.EnumChildWindows.argtypes = [wt.HWND, WNDENUMPROC, wt.LPARAM]
    user32.EnumChildWindows.restype = wt.BOOL
    user32.EnumChildWindows(hwnd, callback, 0)
    return children


def launch_notepad() -> tuple[int, subprocess.Popen[bytes]]:
    process = subprocess.Popen(["notepad.exe", "Untitled"])
    deadline = time.time() + 10
    while time.time() < deadline:
        for hwnd in enum_windows():
            if process_name(window_pid(hwnd)).lower() != "notepad.exe":
                continue
            if not user32.IsWindowVisible(hwnd):
                continue
            if "Notepad" not in class_name(hwnd):
                continue
            for child in child_windows(hwnd):
                if class_name(child).lower().startswith(("edit", "richedit")):
                    return hwnd, process
        time.sleep(0.1)
    raise SystemExit("could not find the Notepad window/edit control")


def wait_for_desktop_idle(seconds: float, timeout: float = 60.0) -> None:
    """Wait until the user has not touched mouse/keyboard for a moment."""

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wt.BOOL
    kernel32.GetTickCount.restype = wt.DWORD
    kernel32.GetTickCount.argtypes = []
    start = time.time()
    while time.time() - start < timeout:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        user32.GetLastInputInfo(ctypes.byref(info))
        idle_ms = int(kernel32.GetTickCount()) - int(info.dwTime)
        if idle_ms >= seconds * 1000:
            return
        time.sleep(0.2)


def launch_edge(html_path: Path) -> tuple[int, subprocess.Popen[bytes]]:  # pragma: no cover
    raise SystemExit("the Edge target was removed; use the synthetic Win32 target")


HTML = ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--letters", default="nihao")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    win32.enable_dpi_awareness()
    original_foreground = int(user32.GetForegroundWindow() or 0)
    wait_for_desktop_idle(2.5)
    temp_dir = Path(tempfile.mkdtemp(prefix="sp-ime-probe-"))

    app: AppRun | None = None
    target_process: subprocess.Popen[bytes] | None = None
    region: ScreenRegion | None = None
    try:
        hwnd, focus_target, target_process = launch_target()
        process_name_text = "python.exe"
        title_pattern = "^ScreenPainter IME target"
        target_class = "ScreenPainterImeTarget"

        force_foreground(hwnd)
        focus_hwnd(hwnd, focus_target)
        time.sleep(0.3)

        search = ScreenRegion(110, 90, 1000, 700)
        found = find_panel_region(search)
        search.close()
        if found is None:
            raise SystemExit(
                "could not derive the IME candidate panel rectangle - is the IME "
                "in Chinese mode and composing?"
            )
        left, top, width, height = found
        print(f"IME panel region: {left},{top} {width}x{height}")
        region = ScreenRegion(left, top, width, height)
        type_key(VK_ESCAPE)
        time.sleep(0.4)

        print("condition A: no ScreenPainter")
        measure_latency(region, args.letters, rounds=args.rounds, label="baseline", verbose=args.verbose)

        config_path = temp_dir / "probe.toml"
        log_path = temp_dir / "probe.log"
        write_config(
            config_path,
            process=process_name_text,
            title_pattern=title_pattern,
            class_pattern=target_class,
            hidden=False,
            toolbar=True,
            tick_ms=16,
            log_path=log_path,
        )
        app = AppRun(config_path, log_path)
        app.start()
        time.sleep(1.5)
        force_foreground(hwnd)
        focus_hwnd(hwnd, focus_target)
        time.sleep(0.4)
        print("condition B: ScreenPainter running, rectangles visible")
        measure_latency(region, args.letters, rounds=args.rounds, label="visible", verbose=args.verbose)

        app.stop()
        app = None

        write_config(
            config_path,
            process=process_name_text,
            title_pattern=title_pattern,
            class_pattern=target_class,
            hidden=True,
            toolbar=True,
            tick_ms=16,
            log_path=log_path,
        )
        app = AppRun(config_path, log_path)
        app.start()
        time.sleep(1.5)
        force_foreground(hwnd)
        focus_hwnd(hwnd, focus_target)
        time.sleep(0.4)
        print("condition C: ScreenPainter running, rectangles hidden")
        measure_latency(region, args.letters, rounds=args.rounds, label="hidden", verbose=args.verbose)

        app.stop()
        app = None

        write_config(
            config_path,
            process=process_name_text,
            title_pattern=title_pattern,
            class_pattern=target_class,
            hidden=True,
            toolbar=False,
            tick_ms=1000,
            log_path=log_path,
        )
        app = AppRun(config_path, log_path)
        app.start()
        time.sleep(1.5)
        force_foreground(hwnd)
        focus_hwnd(hwnd, focus_target)
        time.sleep(0.4)
        print("condition D: ScreenPainter, no toolbar, 1 Hz poll")
        measure_latency(region, args.letters, rounds=args.rounds, label="slow-poll", verbose=args.verbose)
    finally:
        if app is not None:
            app.stop()
        if region is not None:
            region.close()
        if target_process is not None:
            target_process.terminate()
            try:
                target_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                target_process.kill()
        if original_foreground and user32.IsWindow(original_foreground):
            try:
                force_foreground(original_foreground)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
