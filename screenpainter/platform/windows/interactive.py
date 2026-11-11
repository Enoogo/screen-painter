"""Interactive native windows: a floating button panel and a pointer capture box.

The production overlay (``overlay.py``) is deliberately click-through and owned
by the target window.  The toolbar needs the opposite: windows that receive real
mouse input and float above everything.  They live here, in a separate module,
so the overlay's invariants are untouched:

* :class:`ButtonPanel` - a topmost ``WS_POPUP`` window with native ``BUTTON``
  children.  It is draggable (the panel moves with the mouse, no title bar) and
  never takes the keyboard focus away from the user's application
  (``WS_EX_NOACTIVATE``).
* :class:`ScreenCapture` - a topmost, fully transparent layered window that sits
  exactly over the target window while the user draws or moves rectangles.  It
  has no ``WS_EX_TRANSPARENT`` (unlike the overlay), so it receives the drag.
  It renders nothing itself: previews are painted by the drawing feature through
  the regular surface, which keeps the repaint path as cheap as possible.
* :class:`PropertiesPanel` - a small self-painted topmost window that shows the
  selected rectangle's colour (hex), geometry and the configured shortcuts.

All windows are created on the application thread and pumped by
``WindowService.pump_events``; input is queued and consumed by features on the
same thread, so there is no locking anywhere.
"""

from __future__ import annotations

import ctypes
import logging
from typing import Sequence

from ctypes import wintypes as wt

from screenpainter.core.geometry import Rect
from screenpainter.core.ui import ButtonSpec, PanelEvent, PanelStatus, PointerEvent
from screenpainter.errors import OverlayError
from screenpainter.logging_setup import get_logger

from . import win32

_logger = get_logger("interactive")

_REGISTERED_CLASSES: set[str] = set()
_WINDOWS: dict[int, "_NativeWindow"] = {}
_WNDPROC_REF: object | None = None


def _window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
    hwnd_int = int(hwnd or 0)
    window = _WINDOWS.get(hwnd_int)
    if window is not None:
        try:
            result = window.handle_message(message, wparam, lparam)
        except Exception:  # pragma: no cover - defensive, keeps the app alive
            _logger.exception("window message handler failed for 0x%X", hwnd_int)
            result = None
        if result is not None:
            return int(result)

    if message == win32.WM_MOUSEACTIVATE:
        return win32.MA_NOACTIVATE
    if message == win32.WM_ERASEBKGND:
        if window is not None and window.erase_background:
            return 1
        return int(win32.user32.DefWindowProcW(hwnd, message, wparam, lparam))
    if message == win32.WM_PAINT:
        paint = win32.PAINTSTRUCT()
        dc = win32.user32.BeginPaint(hwnd, ctypes.byref(paint))
        if dc:
            win32.user32.EndPaint(hwnd, ctypes.byref(paint))
        return 0
    if message == win32.WM_DESTROY:
        _WINDOWS.pop(hwnd_int, None)
    return int(win32.user32.DefWindowProcW(hwnd, message, wparam, lparam))


# The C callback must outlive the window classes that reference it.
_WNDPROC_REF = win32.WNDPROC(_window_proc)


def _ensure_window_class(name: str, *, background: int | None = None) -> None:
    if name in _REGISTERED_CLASSES:
        return
    window_class = win32.WNDCLASSW()
    window_class.lpfnWndProc = _WNDPROC_REF  # type: ignore[assignment]
    window_class.hInstance = wt.HINSTANCE(win32.get_module_handle())
    window_class.lpszClassName = name
    if background is not None:
        window_class.hbrBackground = wt.HBRUSH(background)
    atom = win32.user32.RegisterClassW(ctypes.byref(window_class))
    if not atom:
        error = ctypes.get_last_error()
        if error != win32.ERROR_CLASS_ALREADY_EXISTS:
            raise win32.winapi_error(f"RegisterClassW({name}) failed", error)
    _REGISTERED_CLASSES.add(name)


def _point_from_lparam(lparam: int) -> tuple[int, int]:
    x = ctypes.c_short(int(lparam) & 0xFFFF).value
    y = ctypes.c_short((int(lparam) >> 16) & 0xFFFF).value
    return int(x), int(y)


class _NativeWindow:
    """Common plumbing for the windows in this module."""

    erase_background = True

    def __init__(self) -> None:
        self._hwnd = 0
        self._x = 0
        self._y = 0
        self._visible = False

    @property
    def hwnd(self) -> int:
        return self._hwnd

    @property
    def x(self) -> int:
        return self._x

    @property
    def y(self) -> int:
        return self._y

    def is_alive(self) -> bool:
        return bool(self._hwnd) and win32.is_window(self._hwnd)

    def handle_message(self, message: int, wparam: int, lparam: int) -> int | None:
        return None

    def _register_instance(self) -> None:
        _WINDOWS[self._hwnd] = self

    def move(self, x: int, y: int) -> None:
        if not self.is_alive():
            raise OverlayError("cannot move: the window no longer exists")
        if int(x) == self._x and int(y) == self._y:
            return
        if not win32.user32.SetWindowPos(
            self._hwnd,
            None,
            int(x),
            int(y),
            0,
            0,
            win32.SWP_NOSIZE | win32.SWP_NOZORDER | win32.SWP_NOACTIVATE,
        ):
            raise win32.winapi_error("SetWindowPos(window) failed")
        self._x = int(x)
        self._y = int(y)

    def show(self) -> None:
        if not self.is_alive():
            raise OverlayError("cannot show: the window no longer exists")
        if self._visible and win32.is_window_visible(self._hwnd):
            return
        win32.user32.ShowWindow(self._hwnd, win32.SW_SHOWNOACTIVATE)
        self._visible = True

    def hide(self) -> None:
        if self.is_alive():
            win32.user32.ShowWindow(self._hwnd, win32.SW_HIDE)
        self._visible = False

    def raise_above(self) -> None:
        """Put this window on top of every other window, without activating."""
        if not self.is_alive():
            return
        win32.user32.SetWindowPos(
            self._hwnd,
            wt.HWND(win32.HWND_TOPMOST),
            0,
            0,
            0,
            0,
            win32.SWP_NOMOVE | win32.SWP_NOSIZE | win32.SWP_NOACTIVATE,
        )

    def destroy(self) -> None:
        hwnd = self._hwnd
        self._hwnd = 0
        self._visible = False
        _WINDOWS.pop(int(hwnd or 0), None)
        if hwnd and win32.is_window(hwnd):
            win32.user32.ShowWindow(hwnd, win32.SW_HIDE)
            win32.user32.DestroyWindow(hwnd)


class ScreenCapture(_NativeWindow):
    """A transparent, topmost input window placed over the target window.

    The window paints nothing; it exists to receive mouse input while the user
    draws or drags rectangles.  Coordinates in :class:`PointerEvent` are client
    coordinates, which equal overlay coordinates because the window is created
    with the target's overlay bounds.
    """

    _CURSORS = {
        "cross": win32.IDC_CROSS,
        "move": win32.IDC_SIZEALL,
        "arrow": win32.IDC_ARROW,
    }

    def __init__(
        self,
        rect: Rect,
        *,
        cursor: str = "cross",
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__()
        if rect.width <= 0 or rect.height <= 0:
            raise OverlayError(f"capture window needs a positive size, got {rect}")
        _ensure_window_class(win32.CAPTURE_CLASS_NAME)
        self.logger = logger or _logger
        self._rect = rect
        self._events: list[PointerEvent] = []
        self._mem_dc = 0
        self._bitmap = 0
        self._old_bitmap = 0
        self._bits: int | None = None
        self._capturing = False
        self._cursor_name = cursor if cursor in self._CURSORS else "cross"
        self._cursor_handle = win32.load_system_cursor(self._CURSORS[self._cursor_name])

        extended_style = (
            win32.WS_EX_LAYERED
            | win32.WS_EX_TOOLWINDOW
            | win32.WS_EX_NOACTIVATE
            | win32.WS_EX_TOPMOST
        )
        try:
            self._hwnd = int(
                win32.user32.CreateWindowExW(
                    extended_style,
                    win32.CAPTURE_CLASS_NAME,
                    win32.CAPTURE_WINDOW_TITLE,
                    win32.WS_POPUP,
                    rect.x,
                    rect.y,
                    rect.width,
                    rect.height,
                    None,
                    None,
                    wt.HINSTANCE(win32.get_module_handle()),
                    None,
                )
                or 0
            )
            if not self._hwnd:
                raise win32.winapi_error("CreateWindowExW(capture) failed")
            self._register_instance()
            self._mem_dc = int(win32.gdi32.CreateCompatibleDC(None) or 0)
            if not self._mem_dc:
                raise win32.winapi_error("CreateCompatibleDC failed")
            self._ensure_dib(rect.width, rect.height)
            self._upload()
            self.show()
        except Exception:
            self.destroy()
            raise

    # -- properties ------------------------------------------------------
    @property
    def rect(self) -> Rect:
        return self._rect

    # -- geometry --------------------------------------------------------
    def move_to(self, rect: Rect) -> None:
        if rect.width <= 0 or rect.height <= 0:
            return
        if rect == self._rect:
            return
        if not self.is_alive():
            raise OverlayError("cannot move: the capture window no longer exists")
        size_changed = rect.width != self._rect.width or rect.height != self._rect.height
        self._rect = rect
        if size_changed:
            self._ensure_dib(rect.width, rect.height)
            self._upload()
        else:
            self.move(rect.x, rect.y)

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
        bitmap_info.bmiHeader.biHeight = -int(height)
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
            raise win32.winapi_error("CreateDIBSection(capture) failed")
        old_bitmap = win32.gdi32.SelectObject(self._mem_dc, bitmap)
        if not old_bitmap or int(old_bitmap) == -1:
            win32.gdi32.DeleteObject(bitmap)
            raise win32.winapi_error("SelectObject(capture DIB) failed")
        self._bitmap = bitmap
        self._old_bitmap = int(old_bitmap)
        self._bits = int(bits.value)
        # Windows 11 hit-tests layered windows per pixel: pixels with alpha 0 do
        # not receive mouse input.  The capture window must therefore be filled
        # with the lowest visible alpha instead of complete transparency, which
        # is imperceptible (1/255 black) but makes the whole area clickable.
        pixel = b"\x00\x00\x00\x01"
        payload = pixel * (int(width) * int(height))
        ctypes.memmove(self._bits, payload, len(payload))

    def _upload(self) -> None:
        if not self.is_alive():
            raise OverlayError("cannot upload: the capture window no longer exists")
        destination = wt.POINT(self._rect.x, self._rect.y)
        size = wt.SIZE(self._rect.width, self._rect.height)
        source = wt.POINT(0, 0)
        blend = win32.BLENDFUNCTION(win32.AC_SRC_OVER, 0, 255, win32.AC_SRC_ALPHA)
        if not win32.user32.UpdateLayeredWindow(
            self._hwnd,
            None,
            ctypes.byref(destination),
            ctypes.byref(size),
            self._mem_dc,
            ctypes.byref(source),
            0,
            ctypes.byref(blend),
            win32.ULW_ALPHA,
        ):
            raise win32.winapi_error("UpdateLayeredWindow(capture) failed")

    # -- cursor / events -------------------------------------------------
    def set_cursor(self, cursor: str) -> None:
        if cursor not in self._CURSORS:
            return
        self._cursor_name = cursor
        self._cursor_handle = win32.load_system_cursor(self._CURSORS[cursor])
        if self.is_alive() and win32.get_capture() == self._hwnd:
            win32.user32.SetCursor(wt.HANDLE(self._cursor_handle))

    def take_events(self) -> list[PointerEvent]:
        events = self._events
        self._events = []
        return events

    def _emit(self, kind: str, lparam: int) -> None:
        x, y = _point_from_lparam(lparam)
        self._events.append(PointerEvent(kind=kind, x=x, y=y))  # type: ignore[arg-type]

    # -- messages --------------------------------------------------------
    def handle_message(self, message: int, wparam: int, lparam: int) -> int | None:
        if message == win32.WM_LBUTTONDOWN:
            win32.set_capture(self._hwnd)
            self._capturing = True
            self._emit("down", lparam)
            return 0
        if message == win32.WM_MOUSEMOVE:
            if self._capturing:
                self._emit("move", lparam)
            return 0
        if message == win32.WM_LBUTTONUP:
            if self._capturing:
                self._capturing = False
                win32.release_capture()
                self._emit("up", lparam)
            return 0
        if message == win32.WM_RBUTTONDOWN:
            self._cancel()
            return 0
        if message == win32.WM_CAPTURECHANGED:
            if self._capturing:
                self._cancel()
            return 0
        if message == win32.WM_SETCURSOR:
            if self._cursor_handle:
                win32.user32.SetCursor(wt.HANDLE(self._cursor_handle))
            return 1
        if message == win32.WM_NCHITTEST:
            return 1  # HTCLIENT: capture every click inside the target window
        return None

    def _cancel(self) -> None:
        if self._capturing:
            self._capturing = False
            win32.release_capture()
            self._events.append(PointerEvent(kind="cancel"))

    def destroy(self) -> None:
        self._events = []
        super().destroy()
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


class ButtonPanel(_NativeWindow):
    """A small topmost panel of native buttons that can be dragged around."""

    # Base metrics at 96 DPI; scaled by the monitor DPI so the panel stays
    # readable and clickable on high-DPI displays.
    _WIDTH = 176
    _PAD = 10
    _TITLE_HEIGHT = 20
    _STATUS_HEIGHT = 16
    _HEADER_GAP = 8
    _BUTTON_HEIGHT = 30
    _BUTTON_GAP = 6

    def __init__(
        self,
        *,
        x: int,
        y: int,
        items: Sequence[ButtonSpec],
        title: str,
        status: str = "",
        scale: float = 1.0,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__()
        _ensure_window_class(
            win32.PANEL_CLASS_NAME, background=win32.COLOR_BTNFACE + 1
        )
        self.logger = logger or _logger
        self._items: tuple[ButtonSpec, ...] = tuple(items)
        self._events: list[PanelEvent] = []
        self._title = title
        self._status = status
        self._scale = max(0.5, float(scale))
        self._title_static = 0
        self._status_static = 0
        self._buttons: dict[str, int] = {}
        self._button_ids: dict[int, str] = {}
        self._next_id = 1
        self._dragging = False
        self._drag_cursor = (0, 0)
        self._drag_origin = (0, 0)
        self._font = self._create_font()

        width, height = self._measure()
        self._width = width
        self._height = height
        try:
            self._hwnd = int(
                win32.user32.CreateWindowExW(
                    win32.WS_EX_TOOLWINDOW
                    | win32.WS_EX_NOACTIVATE
                    | win32.WS_EX_TOPMOST,
                    win32.PANEL_CLASS_NAME,
                    win32.PANEL_WINDOW_TITLE,
                    win32.WS_POPUP | win32.WS_CLIPCHILDREN,
                    int(x),
                    int(y),
                    width,
                    height,
                    None,
                    None,
                    wt.HINSTANCE(win32.get_module_handle()),
                    None,
                )
                or 0
            )
            if not self._hwnd:
                raise win32.winapi_error("CreateWindowExW(panel) failed")
            self._x = int(x)
            self._y = int(y)
            self._register_instance()
            self._create_children()
            self.show()
        except Exception:
            self.destroy()
            raise

    # -- construction helpers -------------------------------------------
    @property
    def erase_background(self) -> bool:  # type: ignore[override]
        # Let DefWindowProc paint the class brush: native buttons look right.
        return False

    @property
    def items(self) -> tuple[ButtonSpec, ...]:
        return self._items

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * self._scale)))

    def _measure(self) -> tuple[int, int]:
        width = self._s(self._WIDTH)
        header = self._s(self._PAD // 2) + self._s(self._TITLE_HEIGHT) + self._s(
            self._STATUS_HEIGHT
        ) + self._s(self._HEADER_GAP)
        buttons = len(self._items)
        body = buttons * self._s(self._BUTTON_HEIGHT)
        if buttons > 1:
            body += (buttons - 1) * self._s(self._BUTTON_GAP)
        height = header + body + self._s(self._PAD)
        return width, max(height, self._s(60))

    def _create_font(self) -> int:
        height = -self._s(13)
        face = "Microsoft YaHei UI"
        font = int(
            win32.gdi32.CreateFontW(
                height,
                0,
                0,
                0,
                win32.FW_NORMAL,
                0,
                0,
                0,
                win32.DEFAULT_CHARSET,
                0,
                0,
                win32.CLEARTYPE_QUALITY,
                win32.DEFAULT_PITCH | win32.FF_DONTCARE,
                face,
            )
            or 0
        )
        return font or int(win32.gdi32.GetStockObject(win32.DEFAULT_GUI_FONT) or 0)

    def _create_children(self) -> None:
        width, _ = self._measure()
        pad = self._s(self._PAD)
        self._title_static = self._create_static(
            self._title,
            pad,
            self._s(self._PAD // 2),
            width - 2 * pad,
            self._s(self._TITLE_HEIGHT),
        )
        y = self._s(self._PAD // 2) + self._s(self._TITLE_HEIGHT)
        self._status_static = self._create_static(
            self._status,
            pad,
            y,
            width - 2 * pad,
            self._s(self._STATUS_HEIGHT),
        )
        y += self._s(self._STATUS_HEIGHT) + self._s(self._HEADER_GAP)
        for item in self._items:
            button = self._create_button(
                item,
                pad,
                y,
                width - 2 * pad,
                self._s(self._BUTTON_HEIGHT),
            )
            self._buttons[item.key] = button
            self._button_ids[self._next_id] = item.key
            self._next_id += 1
            y += self._s(self._BUTTON_HEIGHT) + self._s(self._BUTTON_GAP)

    def _create_static(self, text: str, x: int, y: int, width: int, height: int) -> int:
        hwnd = int(
            win32.user32.CreateWindowExW(
                0,
                win32.STATIC_CLASS_NAME,
                text,
                win32.WS_CHILD | win32.WS_VISIBLE,
                x,
                y,
                width,
                height,
                self._hwnd,
                None,
                wt.HINSTANCE(win32.get_module_handle()),
                None,
            )
            or 0
        )
        if hwnd and self._font:
            win32.user32.SendMessageW(hwnd, win32.WM_SETFONT, self._font, 1)
        return hwnd

    def _create_button(
        self, item: ButtonSpec, x: int, y: int, width: int, height: int
    ) -> int:
        control_id = self._next_id
        hwnd = int(
            win32.user32.CreateWindowExW(
                0,
                win32.BUTTON_CLASS_NAME,
                item.label,
                win32.WS_CHILD | win32.WS_VISIBLE | win32.WS_TABSTOP,
                x,
                y,
                width,
                height,
                self._hwnd,
                wt.HMENU(control_id),
                wt.HINSTANCE(win32.get_module_handle()),
                None,
            )
            or 0
        )
        if not hwnd:
            raise win32.winapi_error("CreateWindowExW(panel button) failed")
        if self._font:
            win32.user32.SendMessageW(hwnd, win32.WM_SETFONT, self._font, 1)
        if not item.enabled:
            win32.user32.EnableWindow(hwnd, False)
        return hwnd

    def _destroy_children(self) -> None:
        for hwnd in list(self._buttons.values()) + [
            self._title_static,
            self._status_static,
        ]:
            if hwnd and win32.is_window(hwnd):
                win32.user32.DestroyWindow(hwnd)
        self._buttons = {}
        self._button_ids = {}
        self._next_id = 1
        self._title_static = 0
        self._status_static = 0

    # -- public API ------------------------------------------------------
    def set_items(self, items: Sequence[ButtonSpec]) -> None:
        items = tuple(items)
        same_keys = [item.key for item in items] == [item.key for item in self._items]
        self._items = items
        if not self.is_alive():
            return
        if same_keys:
            for item in items:
                hwnd = self._buttons.get(item.key)
                if hwnd and win32.is_window(hwnd):
                    win32.user32.SetWindowTextW(hwnd, item.label)
                    win32.user32.EnableWindow(hwnd, item.enabled)
            return
        self._destroy_children()
        self._create_children()
        self._width, self._height = self._measure()
        win32.user32.MoveWindow(
            self._hwnd, self._x, self._y, self._width, self._height, True
        )

    def set_title(self, text: str) -> None:
        if text == self._title:
            return
        self._title = text
        if self._title_static and win32.is_window(self._title_static):
            win32.user32.SetWindowTextW(self._title_static, text)

    def set_status(self, text: str) -> None:
        if text == self._status:
            return
        self._status = text
        if self._status_static and win32.is_window(self._status_static):
            win32.user32.SetWindowTextW(self._status_static, text)

    def take_events(self) -> list[PanelEvent]:
        events = self._events
        self._events = []
        return events

    # -- messages --------------------------------------------------------
    def handle_message(self, message: int, wparam: int, lparam: int) -> int | None:
        if message == win32.WM_COMMAND:
            control_id = int(wparam) & 0xFFFF
            notification = (int(wparam) >> 16) & 0xFFFF
            key = self._button_ids.get(control_id)
            if key is not None and notification == win32.BN_CLICKED:
                self._events.append(PanelEvent(kind="button", key=key))
            return 0
        if message == win32.WM_LBUTTONDOWN:
            self._start_drag()
            return 0
        if message == win32.WM_MOUSEMOVE:
            self._track_drag()
            return 0
        if message == win32.WM_LBUTTONUP:
            self._finish_drag()
            return 0
        if message == win32.WM_CAPTURECHANGED:
            self._dragging = False
            return None
        if message == win32.WM_NCLBUTTONDBLCLK:
            return 0
        return None

    def _start_drag(self) -> None:
        cursor = win32.cursor_position()
        if cursor is None:
            return
        self._dragging = True
        self._drag_cursor = cursor
        self._drag_origin = (self._x, self._y)
        win32.set_capture(self._hwnd)

    def _track_drag(self) -> None:
        if not self._dragging:
            return
        cursor = win32.cursor_position()
        if cursor is None:
            return
        dx = cursor[0] - self._drag_cursor[0]
        dy = cursor[1] - self._drag_cursor[1]
        self.move(self._drag_origin[0] + dx, self._drag_origin[1] + dy)

    def _finish_drag(self) -> None:
        if not self._dragging:
            return
        self._dragging = False
        win32.release_capture()
        self._events.append(PanelEvent(kind="moved", x=self._x, y=self._y))

    def destroy(self) -> None:
        self._events = []
        self._destroy_children()
        super().destroy()
        if self._font:
            win32.gdi32.DeleteObject(self._font)
            self._font = 0


# -- properties panel -------------------------------------------------------

_PROPERTY_ROWS = ("名称", "颜色", "位置", "大小", "不透明度")


class PropertiesPanel(_NativeWindow):
    """A small self-painted panel describing the selected rectangle."""

    erase_background = True
    _WIDTH = 218
    _PAD = 12
    _TITLE_HEIGHT = 24
    _ROW_HEIGHT = 22
    _LABEL_WIDTH = 52
    _HINT_HEIGHT = 18
    _SWATCH_WIDTH = 46
    _SWATCH_HEIGHT = 14

    _COLOR_BACKGROUND = 0x00F9F7F4
    _COLOR_BORDER = 0x00D3CBC6
    _COLOR_TITLE = 0x00141211
    _COLOR_LABEL = 0x00736B6B
    _COLOR_VALUE = 0x001E1C1C
    _COLOR_HINT = 0x00938C8C
    _COLOR_SWATCH_BORDER = 0x00A89F9A

    def __init__(
        self,
        *,
        x: int,
        y: int,
        status: PanelStatus,
        scale: float = 1.0,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__()
        _ensure_window_class(win32.PROPERTIES_CLASS_NAME)
        self.logger = logger or _logger
        self._status = status
        self._scale = max(0.5, float(scale))
        self._title_font = self._create_font(13, bold=True)
        self._body_font = self._create_font(12)
        self._hint_font = self._create_font(11)
        self._brushes: dict[int, int] = {}
        width, height = self._measure()
        self._width = width
        self._height = height
        try:
            self._hwnd = int(
                win32.user32.CreateWindowExW(
                    win32.WS_EX_TOOLWINDOW
                    | win32.WS_EX_NOACTIVATE
                    | win32.WS_EX_TOPMOST,
                    win32.PROPERTIES_CLASS_NAME,
                    win32.PROPERTIES_WINDOW_TITLE,
                    win32.WS_POPUP,
                    int(x),
                    int(y),
                    width,
                    height,
                    None,
                    None,
                    wt.HINSTANCE(win32.get_module_handle()),
                    None,
                )
                or 0
            )
            if not self._hwnd:
                raise win32.winapi_error("CreateWindowExW(properties) failed")
            self._x = int(x)
            self._y = int(y)
            self._register_instance()
            self.show()
        except Exception:
            self.destroy()
            raise

    # -- construction helpers -------------------------------------------
    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * self._scale)))

    def _measure(self) -> tuple[int, int]:
        height = (
            self._s(self._PAD) * 2
            + self._s(self._TITLE_HEIGHT)
            + len(_PROPERTY_ROWS) * self._s(self._ROW_HEIGHT)
            + self._s(self._HINT_HEIGHT)
        )
        return self._s(self._WIDTH), height

    def _create_font(self, size: int, *, bold: bool = False) -> int:
        font = int(
            win32.gdi32.CreateFontW(
                -self._s(size),
                0,
                0,
                0,
                win32.FW_BOLD if bold else win32.FW_NORMAL,
                0,
                0,
                0,
                win32.DEFAULT_CHARSET,
                0,
                0,
                win32.CLEARTYPE_QUALITY,
                win32.DEFAULT_PITCH | win32.FF_DONTCARE,
                "Microsoft YaHei UI",
            )
            or 0
        )
        return font or int(win32.gdi32.GetStockObject(win32.DEFAULT_GUI_FONT) or 0)

    def _brush(self, color: int) -> int:
        cached = self._brushes.get(color)
        if cached:
            return cached
        brush = int(win32.gdi32.CreateSolidBrush(color) or 0)
        if brush:
            self._brushes[color] = brush
        return brush

    # -- public API ------------------------------------------------------
    def update(self, status: PanelStatus, *, x: int | None = None, y: int | None = None) -> None:
        changed = status != self._status
        self._status = status
        if x is not None or y is not None:
            target_x = self._x if x is None else int(x)
            target_y = self._y if y is None else int(y)
            if (target_x, target_y) != (self._x, self._y):
                self.move(target_x, target_y)
                changed = True
        if changed and self.is_alive():
            win32.invalidate_rect(self._hwnd)

    # -- painting --------------------------------------------------------
    def handle_message(self, message: int, wparam: int, lparam: int) -> int | None:
        if message == win32.WM_PAINT:
            self._paint()
            return 0
        return None

    def _paint(self) -> None:
        paint = win32.PAINTSTRUCT()
        hdc = win32.user32.BeginPaint(self._hwnd, ctypes.byref(paint))
        if not hdc:
            return
        try:
            self._paint_content(int(hdc))
        finally:
            win32.user32.EndPaint(self._hwnd, ctypes.byref(paint))

    def _paint_content(self, hdc: int) -> None:
        client = wt.RECT()
        win32.user32.GetClientRect(self._hwnd, ctypes.byref(client))
        width = max(1, int(client.right))
        height = max(1, int(client.bottom))
        fill = win32.user32.FillRect
        bg = self._brush(self._COLOR_BACKGROUND)
        rect = wt.RECT(0, 0, width, height)
        if bg:
            fill(hdc, ctypes.byref(rect), bg)
        border = self._brush(self._COLOR_BORDER)
        if border:
            for edge in (
                wt.RECT(0, 0, width, 1),
                wt.RECT(0, height - 1, width, height),
                wt.RECT(0, 0, 1, height),
                wt.RECT(width - 1, 0, width, height),
            ):
                fill(hdc, ctypes.byref(edge), border)

        pad = self._s(self._PAD)
        win32.gdi32.SetBkMode(hdc, 1)  # TRANSPARENT

        y = pad
        win32.gdi32.SetTextColor(hdc, self._COLOR_TITLE)
        old = win32.gdi32.SelectObject(hdc, self._title_font)
        self._text(hdc, pad, y, self._status.title)
        win32.gdi32.SelectObject(hdc, old)
        y += self._s(self._TITLE_HEIGHT)

        values = (
            self._status.name,
            self._status.color,
            self._status.position,
            self._status.size,
            self._status.opacity,
        )
        body_old = win32.gdi32.SelectObject(hdc, self._body_font)
        label_x = pad
        value_x = pad + self._s(self._LABEL_WIDTH)
        for index, (label, value) in enumerate(zip(_PROPERTY_ROWS, values)):
            if index == 1:
                y += self._paint_swatch(hdc, value_x, y)
            win32.gdi32.SetTextColor(hdc, self._COLOR_LABEL)
            self._text(hdc, label_x, y, label)
            if index == 1:
                win32.gdi32.SetTextColor(hdc, self._COLOR_VALUE)
                self._text(
                    hdc,
                    value_x + self._s(self._SWATCH_WIDTH) + self._s(6),
                    y,
                    value,
                )
            else:
                win32.gdi32.SetTextColor(hdc, self._COLOR_VALUE)
                self._text(hdc, value_x, y, value)
            y += self._s(self._ROW_HEIGHT)
        win32.gdi32.SelectObject(hdc, body_old)

        if self._status.hint:
            hint_old = win32.gdi32.SelectObject(hdc, self._hint_font)
            win32.gdi32.SetTextColor(hdc, self._COLOR_HINT)
            self._text(hdc, pad, y + self._s(2), self._status.hint)
            win32.gdi32.SelectObject(hdc, hint_old)

    def _paint_swatch(self, hdc: int, x: int, y: int) -> int:
        color = _parse_hex_color(self._status.color)
        if color is None:
            return 0
        top = y + max(0, (self._s(self._ROW_HEIGHT) - self._s(self._SWATCH_HEIGHT)) // 2)
        box = wt.RECT(
            x,
            top,
            x + self._s(self._SWATCH_WIDTH),
            top + self._s(self._SWATCH_HEIGHT),
        )
        frame = self._brush(self._COLOR_SWATCH_BORDER)
        if frame:
            win32.user32.FillRect(hdc, ctypes.byref(box), frame)
        inner = wt.RECT(box.left + 1, box.top + 1, box.right - 1, box.bottom - 1)
        brush = self._brush(_colorref(color))
        if brush:
            win32.user32.FillRect(hdc, ctypes.byref(inner), brush)
        return 0

    def _text(self, hdc: int, x: int, y: int, text: str) -> None:
        if not text:
            return
        win32.gdi32.TextOutW(hdc, int(x), int(y), str(text), len(str(text)))

    # -- teardown --------------------------------------------------------
    def destroy(self) -> None:
        super().destroy()
        for brush in self._brushes.values():
            try:
                win32.gdi32.DeleteObject(brush)
            except Exception:  # pragma: no cover - defensive
                pass
        self._brushes.clear()
        for name in ("_title_font", "_body_font", "_hint_font"):
            font = getattr(self, name, 0)
            if font:
                try:
                    win32.gdi32.DeleteObject(font)
                except Exception:  # pragma: no cover - defensive
                    pass
                setattr(self, name, 0)


def _parse_hex_color(text: str) -> tuple[int, int, int] | None:
    if not isinstance(text, str) or len(text) != 7 or not text.startswith("#"):
        return None
    try:
        return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
    except ValueError:
        return None


def _colorref(rgb: tuple[int, int, int]) -> int:
    red, green, blue = rgb
    return int(red) | (int(green) << 8) | (int(blue) << 16)

