"""Temporary target application for the IME latency probe.

Creates a plain Win32 window with a focused multiline EDIT control so the
system IME composes into an ordinary application.  Started by
``tools/_ime_probe.py``; prints the window handle and then pumps messages.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes as wt
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from screenpainter.platform.windows import win32  # noqa: E402

user32 = win32.user32
kernel32 = win32.kernel32

user32.SetFocus.argtypes = [wt.HWND]
user32.SetFocus.restype = wt.HWND
user32.UpdateWindow.argtypes = [wt.HWND]
user32.UpdateWindow.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t
)
CLASS_NAME = "ScreenPainterImeTarget"
TITLE = "ScreenPainter IME target"
EDIT_CLASS = "EDIT"


def main() -> int:
    win32.enable_dpi_awareness()

    @WNDPROC
    def _proc(hwnd, message, wparam, lparam):
        if message == 0x0010:  # WM_CLOSE
            user32.DestroyWindow(hwnd)
            return 0
        return int(user32.DefWindowProcW(hwnd, message, wparam, lparam))

    window_class = win32.WNDCLASSW()
    window_class.lpfnWndProc = _proc
    window_class.hInstance = wt.HINSTANCE(win32.get_module_handle())
    window_class.lpszClassName = CLASS_NAME
    window_class.hbrBackground = wt.HBRUSH(6)  # COLOR_WINDOW+1
    atom = user32.RegisterClassW(ctypes.byref(window_class))
    if not atom:
        error = ctypes.get_last_error()
        if error != win32.ERROR_CLASS_ALREADY_EXISTS:
            print(f"RegisterClassW failed: {error}", flush=True)
            return 1

    hwnd = int(
        user32.CreateWindowExW(
            0,
            CLASS_NAME,
            TITLE,
            wt.DWORD(0x00CF0000),  # WS_OVERLAPPEDWINDOW
            120,
            90,
            1100,
            760,
            None,
            None,
            wt.HINSTANCE(win32.get_module_handle()),
            None,
        )
        or 0
    )
    if not hwnd:
        print("CreateWindowExW failed", flush=True)
        return 1

    edit = int(
        user32.CreateWindowExW(
            0x00000200,  # WS_EX_CLIENTEDGE
            EDIT_CLASS,
            "",
            wt.DWORD(0x50301144),  # WS_CHILD|WS_VISIBLE|WS_VSCROLL|ES_MULTILINE|...
            20,
            20,
            1040,
            660,
            wt.HWND(hwnd),
            None,
            wt.HINSTANCE(win32.get_module_handle()),
            None,
        )
        or 0
    )
    font = int(win32.gdi32.GetStockObject(17) or 0)
    if font:
        user32.SendMessageW(edit, win32.WM_SETFONT, font, 1)

    user32.ShowWindow(hwnd, 5)  # SW_SHOW
    user32.UpdateWindow(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(edit)
    print(f"ready hwnd=0x{hwnd:X} edit=0x{edit:X}", flush=True)

    while True:
        win32.pump_window_messages()
        if not win32.is_window(hwnd):
            return 0
        ctypes.windll.kernel32.Sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
