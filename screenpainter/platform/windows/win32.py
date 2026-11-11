"""Low-level Win32 bindings used by the Windows backend.

There is deliberately no ``pywin32`` dependency.  All ctypes prototypes are
declared centrally and explicitly because ctypes otherwise assumes 32-bit
``int`` return values on 64-bit Windows, which truncates handles and window
messages in confusing ways.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as wt
from pathlib import Path

from screenpainter.errors import OverlayError

# -- constants --------------------------------------------------------------
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9

WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
WS_CLIPCHILDREN = 0x02000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_RGB = 0

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_SETCURSOR = 0x0020
WM_MOUSEACTIVATE = 0x0021
WM_NCHITTEST = 0x0084
WM_COMMAND = 0x0111
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_CAPTURECHANGED = 0x0215
WM_EXITSIZEMOVE = 0x0232
WM_SETFONT = 0x0030
WM_NCLBUTTONDBLCLK = 0x00A3
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
HTTRANSPARENT = -1
MA_NOACTIVATE = 3
PM_REMOVE = 0x0001
ERROR_CLASS_ALREADY_EXISTS = 1410

# Event loop / global hooks
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x0004
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
WH_KEYBOARD_LL = 13
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80

# Window classes / stock objects
BUTTON_CLASS_NAME = "BUTTON"
STATIC_CLASS_NAME = "STATIC"
BS_PUSHBUTTON = 0x00000000
BN_CLICKED = 0
DEFAULT_GUI_FONT = 17
COLOR_BTNFACE = 15

# System cursors
IDC_ARROW = 32512
IDC_CROSS = 32515
IDC_SIZENS = 32645
IDC_SIZEWE = 32644
IDC_SIZENWSE = 32642
IDC_SIZENESW = 32643
IDC_SIZEALL = 32646
IDC_HAND = 32649

# System metrics
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CMONITORS = 80

MONITORINFOF_PRIMARY = 1

# Fonts (CreateFontW)
FW_NORMAL = 400
FW_BOLD = 700
DEFAULT_CHARSET = 1
CLEARTYPE_QUALITY = 5
DEFAULT_PITCH = 0
FF_DONTCARE = 0

GA_ROOT = 2
GWL_STYLE = -16
GWL_EXSTYLE = -20
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

OVERLAY_CLASS_NAME = "ScreenPainterOverlayWindow"
OVERLAY_WINDOW_TITLE = "ScreenPainter overlay"
CAPTURE_CLASS_NAME = "ScreenPainterCaptureWindow"
CAPTURE_WINDOW_TITLE = "ScreenPainter input capture"
PANEL_CLASS_NAME = "ScreenPainterButtonPanel"
PANEL_WINDOW_TITLE = "ScreenPainter toolbar"
PROPERTIES_CLASS_NAME = "ScreenPainterPropertiesPanel"
PROPERTIES_WINDOW_TITLE = "ScreenPainter rectangle properties"

# -- ctypes aliases/structures ---------------------------------------------
LRESULT = ctypes.c_ssize_t
LONG_PTR = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    wt.HANDLE,
    wt.DWORD,
    wt.HWND,
    ctypes.c_long,
    ctypes.c_long,
    wt.DWORD,
    wt.DWORD,
)
HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wt.HDC),
        ("fErase", wt.BOOL),
        ("rcPaint", wt.RECT),
        ("fRestore", wt.BOOL),
        ("fIncUpdate", wt.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wt.UINT),
        ("flags", wt.UINT),
        ("showCmd", wt.UINT),
        ("ptMinPosition", wt.POINT),
        ("ptMaxPosition", wt.POINT),
        ("rcNormalPosition", wt.RECT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), LPARAM
)


# -- DLLs and prototypes ----------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)


def _declare(dll: ctypes.WinDLL, name: str, restype: object, *argtypes: object) -> None:
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = list(argtypes)


# User32
_declare(user32, "EnumWindows", wt.BOOL, WNDENUMPROC, LPARAM)
_declare(user32, "IsWindow", wt.BOOL, wt.HWND)
_declare(user32, "IsWindowVisible", wt.BOOL, wt.HWND)
_declare(user32, "IsIconic", wt.BOOL, wt.HWND)
_declare(user32, "GetWindowThreadProcessId", wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
_declare(user32, "GetWindowTextLengthW", ctypes.c_int, wt.HWND)
_declare(user32, "GetWindowTextW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_declare(user32, "GetClassNameW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_declare(user32, "GetAncestor", wt.HWND, wt.HWND, wt.UINT)
_declare(user32, "GetWindowRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
_declare(user32, "GetClientRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
_declare(user32, "ClientToScreen", wt.BOOL, wt.HWND, ctypes.POINTER(wt.POINT))
_declare(user32, "GetForegroundWindow", wt.HWND)
_declare(user32, "GetWindowPlacement", wt.BOOL, wt.HWND, ctypes.POINTER(WINDOWPLACEMENT))
_declare(user32, "GetDC", wt.HDC, wt.HWND)
_declare(user32, "ReleaseDC", ctypes.c_int, wt.HWND, wt.HDC)
_declare(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
_declare(user32, "GetCursorPos", wt.BOOL, ctypes.POINTER(wt.POINT))
_declare(user32, "SetCursorPos", wt.BOOL, ctypes.c_int, ctypes.c_int)
_declare(user32, "SetCapture", wt.HWND, wt.HWND)
_declare(user32, "ReleaseCapture", wt.BOOL)
_declare(user32, "GetCapture", wt.HWND)
_declare(user32, "LoadCursorW", wt.HANDLE, wt.HINSTANCE, ctypes.c_void_p)
_declare(user32, "SetCursor", wt.HANDLE, wt.HANDLE)
_declare(user32, "SendMessageW", LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
_declare(user32, "EnableWindow", wt.BOOL, wt.HWND, wt.BOOL)
_declare(user32, "MoveWindow", wt.BOOL, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.BOOL)
_declare(user32, "SetWindowTextW", wt.BOOL, wt.HWND, wt.LPCWSTR)
_declare(user32, "GetDlgItem", wt.HWND, wt.HWND, ctypes.c_int)
_declare(
    user32,
    "EnumDisplayMonitors",
    wt.BOOL,
    wt.HDC,
    ctypes.POINTER(wt.RECT),
    MONITORENUMPROC,
    LPARAM,
)
_declare(user32, "GetMonitorInfoW", wt.BOOL, wt.HMONITOR, ctypes.c_void_p)
_declare(user32, "RegisterClassW", wt.ATOM, ctypes.POINTER(WNDCLASSW))
_declare(
    user32,
    "CreateWindowExW",
    wt.HWND,
    wt.DWORD,
    wt.LPCWSTR,
    wt.LPCWSTR,
    wt.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.HWND,
    wt.HMENU,
    wt.HINSTANCE,
    wt.LPVOID,
)
_declare(user32, "DestroyWindow", wt.BOOL, wt.HWND)
_declare(user32, "ShowWindow", wt.BOOL, wt.HWND, ctypes.c_int)
_declare(
    user32,
    "SetWindowPos",
    wt.BOOL,
    wt.HWND,
    wt.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.UINT,
)
_declare(user32, "DefWindowProcW", LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
_declare(user32, "PeekMessageW", wt.BOOL, ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT)
_declare(
    user32,
    "MsgWaitForMultipleObjectsEx",
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
)
_declare(
    user32,
    "SetWinEventHook",
    wt.HANDLE,
    wt.UINT,
    wt.UINT,
    wt.HMODULE,
    WINEVENTPROC,
    wt.DWORD,
    wt.DWORD,
    wt.UINT,
)
_declare(user32, "UnhookWinEvent", wt.BOOL, wt.HANDLE)
_declare(
    user32,
    "SetWindowsHookExW",
    wt.HANDLE,
    ctypes.c_int,
    HOOKPROC,
    wt.HMODULE,
    wt.DWORD,
)
_declare(user32, "UnhookWindowsHookEx", wt.BOOL, wt.HANDLE)
_declare(user32, "CallNextHookEx", LRESULT, wt.HANDLE, ctypes.c_int, WPARAM, LPARAM)
_declare(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
_declare(user32, "GetKeyState", ctypes.c_short, ctypes.c_int)
_declare(user32, "InvalidateRect", wt.BOOL, wt.HWND, ctypes.c_void_p, wt.BOOL)
_declare(user32, "UpdateWindow", wt.BOOL, wt.HWND)
_declare(user32, "FillRect", ctypes.c_int, wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH)
_declare(user32, "TranslateMessage", wt.BOOL, ctypes.POINTER(wt.MSG))
_declare(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(wt.MSG))
_declare(user32, "BeginPaint", wt.HDC, wt.HWND, ctypes.POINTER(PAINTSTRUCT))
_declare(user32, "EndPaint", wt.BOOL, wt.HWND, ctypes.POINTER(PAINTSTRUCT))
_declare(
    user32,
    "UpdateLayeredWindow",
    wt.BOOL,
    wt.HWND,
    wt.HDC,
    ctypes.POINTER(wt.POINT),
    ctypes.POINTER(wt.SIZE),
    wt.HDC,
    ctypes.POINTER(wt.POINT),
    wt.DWORD,
    ctypes.POINTER(BLENDFUNCTION),
    wt.DWORD,
)

if hasattr(user32, "GetWindowLongPtrW"):
    _declare(user32, "GetWindowLongPtrW", LONG_PTR, wt.HWND, ctypes.c_int)
else:  # pragma: no cover - only relevant on 32-bit Windows
    _declare(user32, "GetWindowLongW", LONG_PTR, wt.HWND, ctypes.c_int)

if hasattr(user32, "SetProcessDpiAwarenessContext"):
    _declare(user32, "SetProcessDpiAwarenessContext", wt.BOOL, ctypes.c_void_p)
if hasattr(user32, "SetProcessDPIAware"):
    _declare(user32, "SetProcessDPIAware", wt.BOOL)

# GDI32
_declare(gdi32, "CreateCompatibleDC", wt.HDC, wt.HDC)
_declare(
    gdi32,
    "CreateDIBSection",
    wt.HBITMAP,
    wt.HDC,
    ctypes.POINTER(BITMAPINFO),
    wt.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wt.HANDLE,
    wt.DWORD,
)
_declare(gdi32, "SelectObject", wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_declare(gdi32, "DeleteObject", wt.BOOL, wt.HGDIOBJ)
_declare(gdi32, "DeleteDC", wt.BOOL, wt.HDC)
_declare(gdi32, "GetStockObject", wt.HGDIOBJ, ctypes.c_int)
_declare(gdi32, "CreateSolidBrush", wt.HBRUSH, wt.COLORREF)
_declare(gdi32, "SetTextColor", wt.COLORREF, wt.HDC, wt.COLORREF)
_declare(gdi32, "SetBkMode", ctypes.c_int, wt.HDC, ctypes.c_int)
_declare(
    gdi32,
    "TextOutW",
    wt.BOOL,
    wt.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wt.LPCWSTR,
    ctypes.c_int,
)
_declare(
    gdi32,
    "GetTextExtentPoint32W",
    wt.BOOL,
    wt.HDC,
    wt.LPCWSTR,
    ctypes.c_int,
    ctypes.POINTER(wt.SIZE),
)
_declare(gdi32, "GetPixel", wt.COLORREF, wt.HDC, ctypes.c_int, ctypes.c_int)
_declare(
    gdi32,
    "CreateFontW",
    wt.HFONT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.LPCWSTR,
)

# Kernel32
_declare(kernel32, "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)
_declare(kernel32, "OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_declare(kernel32, "CloseHandle", wt.BOOL, wt.HANDLE)
_declare(
    kernel32,
    "CreateEventW",
    wt.HANDLE,
    ctypes.c_void_p,
    wt.BOOL,
    wt.BOOL,
    wt.LPCWSTR,
)
_declare(
    kernel32,
    "QueryFullProcessImageNameW",
    wt.BOOL,
    wt.HANDLE,
    wt.DWORD,
    wt.LPWSTR,
    ctypes.POINTER(wt.DWORD),
)

# DWM
_declare(
    dwmapi,
    "DwmGetWindowAttribute",
    ctypes.c_long,
    wt.HWND,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
)


# -- DPI --------------------------------------------------------------------
_dpi_awareness_set = False


def enable_dpi_awareness() -> bool:
    """Make this process per-monitor DPI aware before any HWND is created.

    Without this, Windows virtualises coordinates for DPI-unaware processes and
    overlays drift away from native windows on scaled monitors.
    """
    global _dpi_awareness_set
    if _dpi_awareness_set:
        return True

    try:
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
                _dpi_awareness_set = True
                return True
    except OSError:
        pass

    # Windows 8.1 fallback.
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.restype = ctypes.c_long
        set_awareness.argtypes = [ctypes.c_int]
        if set_awareness(2) in (0, -2147024891):  # S_OK or E_ACCESSDENIED (already set)
            _dpi_awareness_set = True
            return True
    except (OSError, AttributeError):
        pass

    try:
        if user32.SetProcessDPIAware():
            _dpi_awareness_set = True
            return True
    except OSError:
        pass
    return False


# -- Error handling ---------------------------------------------------------
def winapi_error(prefix: str, error: int | None = None) -> OverlayError:
    code = ctypes.get_last_error() if error is None else error
    return OverlayError(f"{prefix} (Win32 error {code}: {ctypes.FormatError(code).strip()})")


# -- Window helpers ---------------------------------------------------------
def enum_windows() -> list[int]:
    handles: list[int] = []

    @WNDENUMPROC
    def _callback(hwnd: int, _lparam: int) -> bool:
        handles.append(int(hwnd))
        return True

    if not user32.EnumWindows(_callback, 0):
        raise winapi_error("EnumWindows failed")
    return handles


def is_window(hwnd: int) -> bool:
    return bool(hwnd and user32.IsWindow(hwnd))


def is_window_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


def is_iconic(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def is_top_level(hwnd: int) -> bool:
    return int(user32.GetAncestor(hwnd, GA_ROOT)) == int(hwnd)


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def get_window_pid(hwnd: int) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_thread_id(hwnd: int) -> int:
    pid = wt.DWORD(0)
    return int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))


def get_window_title(hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 2)
    copied = int(user32.GetWindowTextW(hwnd, buffer, len(buffer)))
    if copied <= 0:
        return ""
    return buffer.value[:copied]


def get_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    copied = int(user32.GetClassNameW(hwnd, buffer, len(buffer)))
    if copied <= 0:
        return ""
    return buffer.value[:copied]


def get_window_long_ptr(hwnd: int, index: int) -> int:
    if hasattr(user32, "GetWindowLongPtrW"):
        return int(user32.GetWindowLongPtrW(hwnd, index))
    return int(user32.GetWindowLongW(hwnd, index))


def get_process_executable(pid: int) -> str | None:
    if pid <= 0:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        capacity = wt.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(capacity)
        ):
            return buffer.value or None
        return None
    finally:
        kernel32.CloseHandle(handle)


def process_name_from_path(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).name


def get_raw_window_rect(hwnd: int) -> wt.RECT | None:
    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect


def get_dwm_frame_bounds(hwnd: int) -> wt.RECT | None:
    if is_iconic(hwnd):
        # Minimized windows report a bogus off-screen rectangle through DWM.
        return None
    rect = wt.RECT()
    result = dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if result != 0:
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return rect


def get_normal_placement_bounds(hwnd: int) -> wt.RECT | None:
    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
        return None
    rect = placement.rcNormalPosition
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return rect


def is_window_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    result = dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_CLOAKED,
        ctypes.byref(cloaked),
        ctypes.sizeof(cloaked),
    )
    return result == 0 and cloaked.value != 0


def pump_window_messages() -> None:
    """Pump this thread's window messages without blocking."""
    message = wt.MSG()
    while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


_WAIT_EVENT: int | None = None


def _wait_event() -> int:
    """A never-signalled event used just to make the wait count non-zero.

    ``MsgWaitForMultipleObjectsEx`` needs at least one object handle to behave
    as a real wait; with ``nCount = 0`` it returns immediately on some builds.
    """
    global _WAIT_EVENT
    if _WAIT_EVENT is None:
        _WAIT_EVENT = int(
            kernel32.CreateEventW(None, False, False, None) or 0
        )
    return _WAIT_EVENT or 0


def wait_for_messages(timeout_ms: int) -> bool:
    """Sleep until this thread receives a message or ``timeout_ms`` elapses.

    ``MsgWaitForMultipleObjectsEx`` also wakes for messages the system queues
    for us, which is what makes out-of-context WinEvent hooks and low level
    keyboard hooks work without polling.  Returns True when input was waiting.
    """
    event = _wait_event()
    handles = (wt.HANDLE * 1)(event) if event else None
    result = int(
        user32.MsgWaitForMultipleObjectsEx(
            1 if event else 0,
            ctypes.cast(handles, ctypes.c_void_p) if handles else None,
            max(0, int(timeout_ms)),
            QS_ALLINPUT,
            MWMO_INPUTAVAILABLE,
        )
    )
    # WAIT_OBJECT_0 for the (never signalled) event, or WAIT_OBJECT_0 + 1 when
    # a message was waiting.  WAIT_TIMEOUT / WAIT_FAILED mean "really idle".
    return result in (0, 1)


def get_async_key_state(vk: int) -> int:
    return int(user32.GetAsyncKeyState(int(vk)))


def invalidate_rect(hwnd: int, *, erase: bool = False) -> None:
    if hwnd and is_window(hwnd):
        user32.InvalidateRect(hwnd, None, bool(erase))


def get_screen_pixel(x: int, y: int) -> tuple[int, int, int] | None:
    """Read one physical screen pixel; primarily useful for smoke tests."""
    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return None
    try:
        color = int(gdi32.GetPixel(screen_dc, int(x), int(y)))
        if color == 0xFFFFFFFF:
            return None
        return (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)
    finally:
        user32.ReleaseDC(None, screen_dc)


def get_module_handle() -> int:
    return int(kernel32.GetModuleHandleW(None) or 0)


def current_process_id() -> int:
    return os.getpid()


# -- Monitors ---------------------------------------------------------------
def enum_monitors() -> list[tuple[int, str, wt.RECT, wt.RECT, bool]]:
    """Return ``(hmonitor, device, monitor_rect, work_rect, primary)`` tuples."""
    monitors: list[tuple[int, str, wt.RECT, wt.RECT, bool]] = []

    @MONITORENUMPROC
    def _callback(hmonitor: int, _hdc: int, _rect: object, _lparam: int) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            monitors.append(
                (
                    int(hmonitor or 0),
                    str(info.szDevice),
                    info.rcMonitor,
                    info.rcWork,
                    bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return True

    if not user32.EnumDisplayMonitors(None, None, _callback, 0) and not monitors:
        raise winapi_error("EnumDisplayMonitors failed")
    return monitors


def virtual_desktop_rect() -> wt.RECT:
    """Bounding rectangle that covers every monitor."""
    left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    return wt.RECT(left, top, left + width, top + height)


def _load_shcore():
    try:
        return ctypes.WinDLL("shcore", use_last_error=True)
    except OSError:  # pragma: no cover - only on very old Windows
        return None


_SHARED_DC_DPI: dict[int, int] = {}


def monitor_dpi(hmonitor: int) -> int:
    """Effective DPI of one monitor; 96 when the API is unavailable."""
    cached = _SHARED_DC_DPI.get(int(hmonitor))
    if cached is not None:
        return cached
    dpi = 96
    shcore = _load_shcore()
    if shcore is not None:
        try:
            function = shcore.GetDpiForMonitor
            function.restype = ctypes.c_long
            function.argtypes = [
                wt.HMONITOR,
                ctypes.c_int,
                ctypes.POINTER(wt.UINT),
                ctypes.POINTER(wt.UINT),
            ]
            dpi_x = wt.UINT(0)
            dpi_y = wt.UINT(0)
            if function(int(hmonitor), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                dpi = int(dpi_x.value) or 96
        except (OSError, AttributeError):  # pragma: no cover
            dpi = 96
    _SHARED_DC_DPI[int(hmonitor)] = dpi
    return dpi


# -- Cursor / mouse capture -------------------------------------------------
def cursor_position() -> tuple[int, int] | None:
    point = wt.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return (int(point.x), int(point.y))


def set_cursor_position(x: int, y: int) -> bool:
    return bool(user32.SetCursorPos(int(x), int(y)))


def load_system_cursor(cursor_id: int) -> int:
    return int(user32.LoadCursorW(None, ctypes.c_void_p(int(cursor_id))) or 0)


def set_capture(hwnd: int) -> int:
    return int(user32.SetCapture(hwnd) or 0)


def release_capture() -> None:
    user32.ReleaseCapture()


def get_capture() -> int:
    return int(user32.GetCapture() or 0)
