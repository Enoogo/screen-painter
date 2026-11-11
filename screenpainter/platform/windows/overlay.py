"""Native per-pixel-alpha overlay window used to paint over another window.

The overlay is a WS_POPUP window *owned* by the target.  That gives us two
useful properties for free:

* it follows the target's z-order instead of floating above unrelated apps;
* Windows minimizes/hides it together with the owner.

We still poll and move it explicitly while the target is being dragged.
"""

from __future__ import annotations

import ctypes
import logging

from screenpainter.core.surface import PixelSurface
from screenpainter.errors import OverlayError
from screenpainter.logging_setup import get_logger

from . import win32

_logger = get_logger("overlay")

_CLICK_THROUGH_HWNDS: set[int] = set()
_CLASS_REGISTERED = False
_OVERLAY_WNDPROC_REF: object | None = None


def _overlay_wndproc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
    hwnd_int = int(hwnd or 0)
    if message == win32.WM_NCHITTEST and hwnd_int in _CLICK_THROUGH_HWNDS:
        return win32.HTTRANSPARENT
    if message == win32.WM_MOUSEACTIVATE:
        return win32.MA_NOACTIVATE
    if message == win32.WM_ERASEBKGND:
        return 1
    if message == win32.WM_PAINT:
        paint = win32.PAINTSTRUCT()
        dc = win32.user32.BeginPaint(hwnd, ctypes.byref(paint))
        if dc:
            win32.user32.EndPaint(hwnd, ctypes.byref(paint))
        return 0
    if message == win32.WM_DESTROY:
        _CLICK_THROUGH_HWNDS.discard(hwnd_int)
    return int(win32.user32.DefWindowProcW(hwnd, message, wparam, lparam))


# Keep the callback alive for the lifetime of the process.  A WNDCLASS only
# stores a C function pointer without owning a Python reference.
_OVERLAY_WNDPROC_REF = win32.WNDPROC(_overlay_wndproc)


def _ensure_window_class() -> None:
    global _CLASS_REGISTERED
    if _CLASS_REGISTERED:
        return
    window_class = win32.WNDCLASSW()
    window_class.lpfnWndProc = _OVERLAY_WNDPROC_REF  # type: ignore[assignment]
    window_class.hInstance = win32.wt.HINSTANCE(win32.get_module_handle())
    window_class.lpszClassName = win32.OVERLAY_CLASS_NAME
    atom = win32.user32.RegisterClassW(ctypes.byref(window_class))
    if not atom:
        error = ctypes.get_last_error()
        if error != win32.ERROR_CLASS_ALREADY_EXISTS:
            raise win32.winapi_error("RegisterClassW failed", error)
    _CLASS_REGISTERED = True


class LayeredOverlay:
    """A click-through, per-pixel-alpha window bound to an owner HWND."""

    def __init__(
        self,
        owner_hwnd: int,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        click_through: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        if width <= 0 or height <= 0:
            raise OverlayError(f"overlay size must be positive, got {width}x{height}")
        _ensure_window_class()
        self.logger = logger or _logger
        self.owner_hwnd = int(owner_hwnd)
        self._x = int(x)
        self._y = int(y)
        self._width = int(width)
        self._height = int(height)
        self._hwnd = 0
        self._mem_dc = 0
        self._bitmap = 0
        self._old_bitmap = 0
        self._bits: int | None = None
        self._visible = False
        self._click_through = bool(click_through)
        self._destroyed = False

        extended_style = (
            win32.WS_EX_LAYERED
            | win32.WS_EX_TOOLWINDOW
            | win32.WS_EX_NOACTIVATE
        )
        if self._click_through:
            extended_style |= win32.WS_EX_TRANSPARENT

        try:
            self._hwnd = int(
                win32.user32.CreateWindowExW(
                    extended_style,
                    win32.OVERLAY_CLASS_NAME,
                    win32.OVERLAY_WINDOW_TITLE,
                    win32.WS_POPUP,
                    self._x,
                    self._y,
                    self._width,
                    self._height,
                    self.owner_hwnd,
                    None,
                    win32.wt.HINSTANCE(win32.get_module_handle()),
                    None,
                )
                or 0
            )
            if not self._hwnd:
                raise win32.winapi_error("CreateWindowExW(overlay) failed")

            _CLICK_THROUGH_HWNDS.discard(self._hwnd)
            if self._click_through:
                _CLICK_THROUGH_HWNDS.add(self._hwnd)

            # A memory DC alone is enough; ScreenPainter deliberately does not
            # keep a screen DC open (that would pin GDI/DWM state for the whole
            # lifetime of the process).
            self._mem_dc = int(win32.gdi32.CreateCompatibleDC(None) or 0)
            if not self._mem_dc:
                raise win32.winapi_error("CreateCompatibleDC failed")
            self._ensure_dib(self._width, self._height)
        except Exception:
            self.destroy()
            raise

    # -- properties ------------------------------------------------------
    @property
    def hwnd(self) -> int:
        return self._hwnd

    @property
    def x(self) -> int:
        return self._x

    @property
    def y(self) -> int:
        return self._y

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def is_alive(self) -> bool:
        return bool(self._hwnd) and win32.is_window(self._hwnd)

    # -- drawing ---------------------------------------------------------
    def _ensure_dib(self, width: int, height: int) -> None:
        if self._bitmap:
            if self._old_bitmap:
                win32.gdi32.SelectObject(self._mem_dc, self._old_bitmap)
            win32.gdi32.DeleteObject(self._bitmap)
            self._bitmap = 0
            self._old_bitmap = 0
            self._bits = None

        bitmap_info = win32.BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(win32.BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = int(width)
        bitmap_info.bmiHeader.biHeight = -int(height)  # top-down rows
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = win32.BI_RGB
        bitmap_info.bmiHeader.biSizeImage = int(width) * int(height) * 4

        bits = ctypes.c_void_p()
        bitmap = int(
            win32.gdi32.CreateDIBSection(
                None,
                ctypes.byref(bitmap_info),
                win32.DIB_RGB_COLORS,
                ctypes.byref(bits),
                None,
                0,
            )
            or 0
        )
        if not bitmap or not bits.value:
            raise win32.winapi_error("CreateDIBSection failed")
        old_bitmap = win32.gdi32.SelectObject(self._mem_dc, bitmap)
        if not old_bitmap or int(old_bitmap) == -1:
            win32.gdi32.DeleteObject(bitmap)
            raise win32.winapi_error("SelectObject(DIB) failed")
        self._bitmap = bitmap
        self._old_bitmap = int(old_bitmap)
        self._bits = int(bits.value)
        self._width = int(width)
        self._height = int(height)
        ctypes.memset(self._bits, 0, self._width * self._height * 4)

    def render(self, surface: PixelSurface) -> None:
        """Upload a complete surface and resize the window to match it."""
        if not self.is_alive():
            raise OverlayError("cannot render: overlay window is no longer alive")
        if surface.width != self._width or surface.height != self._height:
            self._ensure_dib(surface.width, surface.height)
        assert self._bits is not None
        buffer = surface.buffer
        if len(buffer) != self._width * self._height * 4:
            raise OverlayError("surface buffer size does not match its dimensions")
        source = (ctypes.c_char * len(buffer)).from_buffer(buffer)
        ctypes.memmove(self._bits, source, len(buffer))
        self._update_layered_window()

    def _update_layered_window(self) -> None:
        if not self.is_alive():
            raise OverlayError("cannot update: overlay window is no longer alive")
        destination = win32.wt.POINT(self._x, self._y)
        size = win32.wt.SIZE(self._width, self._height)
        source = win32.wt.POINT(0, 0)
        blend = win32.BLENDFUNCTION(
            win32.AC_SRC_OVER,
            0,
            255,
            win32.AC_SRC_ALPHA,
        )
        success = win32.user32.UpdateLayeredWindow(
            self._hwnd,
            None,
            ctypes.byref(destination),
            ctypes.byref(size),
            self._mem_dc,
            ctypes.byref(source),
            0,
            ctypes.byref(blend),
            win32.ULW_ALPHA,
        )
        if not success:
            raise win32.winapi_error("UpdateLayeredWindow failed")

    # -- positioning/visibility -----------------------------------------
    def move(self, x: int, y: int) -> None:
        if not self.is_alive():
            raise OverlayError("cannot move: overlay window is no longer alive")
        x = int(x)
        y = int(y)
        if x == self._x and y == self._y:
            return
        success = win32.user32.SetWindowPos(
            self._hwnd,
            None,
            x,
            y,
            0,
            0,
            win32.SWP_NOSIZE
            | win32.SWP_NOZORDER
            | win32.SWP_NOACTIVATE,
        )
        if not success:
            raise win32.winapi_error("SetWindowPos(overlay) failed")
        self._x = x
        self._y = y

    def show(self) -> None:
        if not self.is_alive():
            raise OverlayError("cannot show: overlay window is no longer alive")
        if self._visible and win32.is_window_visible(self._hwnd):
            return
        win32.user32.ShowWindow(self._hwnd, win32.SW_SHOWNOACTIVATE)
        if not win32.is_window_visible(self._hwnd):
            raise win32.winapi_error("ShowWindow(overlay) failed")
        self._visible = True

    def hide(self) -> None:
        if self._hwnd and win32.is_window(self._hwnd):
            win32.user32.ShowWindow(self._hwnd, win32.SW_HIDE)
        self._visible = False

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        hwnd = self._hwnd
        self._hwnd = 0
        _CLICK_THROUGH_HWNDS.discard(int(hwnd or 0))
        if hwnd and win32.is_window(hwnd):
            win32.user32.ShowWindow(hwnd, win32.SW_HIDE)
            win32.user32.DestroyWindow(hwnd)

        if self._mem_dc and self._bitmap:
            if self._old_bitmap:
                win32.gdi32.SelectObject(self._mem_dc, self._old_bitmap)
            win32.gdi32.DeleteObject(self._bitmap)
        self._bitmap = 0
        self._old_bitmap = 0
        self._bits = None
        if self._mem_dc:
            win32.gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = 0
        self._visible = False
