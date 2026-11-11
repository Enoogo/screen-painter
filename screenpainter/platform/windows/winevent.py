"""Event sources that keep the application loop event driven.

Two Windows facilities are wrapped here:

* :class:`TargetEventListener` - an out-of-context ``SetWinEventHook`` on the
  target window's thread.  It reports move/resize/minimize/restore/visibility
  changes as they happen, so the overlay no longer has to poll the target
  60 times per second.
* :class:`KeyboardHook` - a low level keyboard hook (``WH_KEYBOARD_LL``) that
  is installed only while an editing mode is active.  The capture window is
  ``WS_EX_NOACTIVATE`` and therefore never receives keyboard focus; the hook
  lets Delete/Ctrl+Z/arrow keys work without stealing focus from the target
  application, and it swallows exactly the configured shortcuts.

Both objects must be created, used and destroyed on the application thread:
out-of-context WinEvent callbacks and low level hooks are dispatched while that
thread pumps its message queue.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import replace
from typing import Iterable, Mapping

from screenpainter.core.keymap import KeyBinding
from screenpainter.core.ui import KeyEvent
from screenpainter.logging_setup import get_logger

from . import win32
from . import windows_keys

__all__ = ["KeyboardHook", "TargetEventListener", "TARGET_EVENTS"]

_OBJID_WINDOW = 0
_EVENT_OBJECT_DESTROY = 0x8001
_EVENT_OBJECT_SHOW = 0x8002
_EVENT_OBJECT_HIDE = 0x8003
_EVENT_OBJECT_LOCATIONCHANGE = 0x800B
_EVENT_OBJECT_NAMECHANGE = 0x800C
_EVENT_OBJECT_CLOAKED = 0x8017
_EVENT_OBJECT_UNCLOAKED = 0x8018
_EVENT_SYSTEM_MINIMIZESTART = 0x0016
_EVENT_SYSTEM_MINIMIZEEND = 0x0017

#: Event ranges hooked on the target thread.
_EVENT_RANGES: tuple[tuple[int, int], ...] = (
    (_EVENT_OBJECT_DESTROY, _EVENT_OBJECT_HIDE),
    (_EVENT_OBJECT_LOCATIONCHANGE, _EVENT_OBJECT_LOCATIONCHANGE),
    (_EVENT_OBJECT_NAMECHANGE, _EVENT_OBJECT_NAMECHANGE),
    (_EVENT_OBJECT_CLOAKED, _EVENT_OBJECT_UNCLOAKED),
    (_EVENT_SYSTEM_MINIMIZESTART, _EVENT_SYSTEM_MINIMIZEEND),
)

_FLAG_BY_EVENT = {
    _EVENT_OBJECT_DESTROY: "destroyed",
    _EVENT_OBJECT_SHOW: "shown",
    _EVENT_OBJECT_HIDE: "hidden",
    _EVENT_OBJECT_LOCATIONCHANGE: "location",
    _EVENT_OBJECT_NAMECHANGE: "title",
    _EVENT_OBJECT_CLOAKED: "cloaked",
    _EVENT_OBJECT_UNCLOAKED: "uncloaked",
    _EVENT_SYSTEM_MINIMIZESTART: "minimized",
    _EVENT_SYSTEM_MINIMIZEEND: "restored",
}

TARGET_EVENTS = frozenset(_FLAG_BY_EVENT.values())


class TargetEventListener:
    """Reports geometry/visibility changes of one window without polling."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or get_logger("winevent")
        self._hwnd = 0
        self._hooks: list[tuple[int, object]] = []
        self._events: set[str] = set()
        self._proc: object | None = None

    @property
    def watched_hwnd(self) -> int:
        return self._hwnd

    @property
    def active(self) -> bool:
        return bool(self._hooks)

    def watch(self, hwnd: int) -> bool:
        """Start watching ``hwnd``; returns False when no hook could be set."""
        hwnd = int(hwnd or 0)
        if hwnd == self._hwnd and self._hooks:
            return True
        self.release()
        if not hwnd or not win32.is_window(hwnd):
            return False
        thread_id = win32.window_thread_id(hwnd)
        if not thread_id:
            return False
        self._hwnd = hwnd
        if self._proc is None:
            self._proc = win32.WINEVENTPROC(self._on_event)
        proc = self._proc
        for event_min, event_max in _EVENT_RANGES:
            hook = int(
                win32.user32.SetWinEventHook(
                    event_min,
                    event_max,
                    None,
                    proc,  # type: ignore[arg-type]
                    0,
                    int(thread_id),
                    win32.WINEVENT_OUTOFCONTEXT | win32.WINEVENT_SKIPOWNPROCESS,
                )
                or 0
            )
            if hook:
                self._hooks.append((hook, proc))
        if not self._hooks:
            self.logger.warning(
                "could not install WinEvent hooks for hwnd=0x%X; "
                "falling back to periodic verification",
                hwnd,
            )
            self._hwnd = 0
            return False
        self.logger.debug(
            "watching hwnd=0x%X (thread %d) with %d WinEvent hook(s)",
            hwnd,
            thread_id,
            len(self._hooks),
        )
        return True

    def release(self) -> None:
        for hook, _proc in self._hooks:
            try:
                win32.user32.UnhookWinEvent(win32.wt.HANDLE(hook))
            except Exception:  # pragma: no cover - defensive
                self.logger.exception("could not remove a WinEvent hook")
        self._hooks.clear()
        self._hwnd = 0

    def take_events(self) -> set[str]:
        events = self._events
        self._events = set()
        return events

    # -- callback --------------------------------------------------------
    def _on_event(
        self,
        _hook: int,
        event: int,
        hwnd: int,
        id_object: int,
        id_child: int,
        _thread: int,
        _time: int,
    ) -> None:
        try:
            if int(hwnd or 0) != self._hwnd:
                return
            if int(id_object) != _OBJID_WINDOW or int(id_child) != 0:
                # Caret/layout/accessibility events for other objects are noise.
                return
            flag = _FLAG_BY_EVENT.get(int(event))
            if flag is not None:
                self._events.add(flag)
        except Exception:  # pragma: no cover - never break the message loop
            self.logger.exception("WinEvent callback failed")


class KeyboardHook:
    """Low level keyboard hook that reports and optionally swallows shortcuts.

    ``signatures`` is a set of ``(virtual_key, modifier_mask)`` pairs (see
    :func:`windows_keys.binding_signature`).  A matching key down is consumed
    (so the target application does not see Delete/Ctrl+Z while editing) and
    queued as a :class:`KeyEvent` for the feature to interpret.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or get_logger("keyboard")
        self._hook = 0
        self._proc: object | None = None
        self._actions: dict[tuple[int, int], str] = {}
        self._swallowed: set[tuple[int, int]] = set()
        self._events: list[KeyEvent] = []
        self._max_events = 512

    @property
    def installed(self) -> bool:
        return bool(self._hook)

    def install(self, bindings: Mapping[str, KeyBinding]) -> bool:
        self.set_bindings(bindings)
        if self._hook:
            return True
        if self._proc is None:
            self._proc = win32.HOOKPROC(self._on_key)
        hook = int(
            win32.user32.SetWindowsHookExW(
                win32.WH_KEYBOARD_LL,
                self._proc,  # type: ignore[arg-type]
                win32.wt.HMODULE(win32.get_module_handle()),
                0,
            )
            or 0
        )
        if not hook:
            self.logger.warning(
                "could not install the keyboard hook; editing shortcuts will "
                "not work"
            )
            return False
        self._hook = hook
        self._swallowed.clear()
        self.logger.debug("keyboard hook installed")
        return True

    def uninstall(self) -> None:
        if self._hook:
            try:
                win32.user32.UnhookWindowsHookEx(win32.wt.HANDLE(self._hook))
            except Exception:  # pragma: no cover - defensive
                self.logger.exception("could not remove the keyboard hook")
        self._hook = 0
        self._swallowed.clear()
        self._events.clear()

    def set_bindings(self, bindings: Mapping[str, KeyBinding]) -> None:
        actions: dict[tuple[int, int], str] = {}
        for action, binding in bindings.items():
            signature = windows_keys.binding_signature(binding)
            if signature is not None:
                actions[signature] = action
        self._actions = actions

    def take_events(self) -> list[KeyEvent]:
        events = self._events
        self._events = []
        return events

    # -- callback --------------------------------------------------------
    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        try:
            if int(code) < 0:
                return int(
                    win32.user32.CallNextHookEx(None, int(code), wparam, lparam)
                )
            info = ctypes.cast(
                lparam, ctypes.POINTER(win32.KBDLLHOOKSTRUCT)
            ).contents
            message = int(wparam)
            vk = int(info.vkCode)
            if message in (win32.WM_KEYDOWN, win32.WM_SYSKEYDOWN):
                event = self._build_event("down", vk, int(info.flags))
                signature = windows_keys.event_signature(
                    vk,
                    ctrl=event.ctrl,
                    shift=event.shift,
                    alt=event.alt,
                    win=event.win,
                )
                action = self._actions.get(signature)
                if action is not None:
                    self._swallowed.add(signature)
                    self._queue(replace(event, action=action))
                    return 1
                return int(
                    win32.user32.CallNextHookEx(None, int(code), wparam, lparam)
                )
            if message in (win32.WM_KEYUP, win32.WM_SYSKEYUP):
                event = self._build_event("up", vk, int(info.flags))
                signature = windows_keys.event_signature(
                    vk,
                    ctrl=event.ctrl,
                    shift=event.shift,
                    alt=event.alt,
                    win=event.win,
                )
                if signature in self._swallowed:
                    self._swallowed.discard(signature)
                    self._queue(replace(event, action=self._actions.get(signature, "")))
                    return 1
        except Exception:  # pragma: no cover - never break key delivery
            self.logger.exception("keyboard hook callback failed")
        return int(win32.user32.CallNextHookEx(None, int(code), wparam, lparam))

    def _build_event(self, kind: str, vk: int, flags: int) -> KeyEvent:
        control = win32.get_async_key_state(win32.VK_CONTROL) & 0x8000
        shift = win32.get_async_key_state(win32.VK_SHIFT) & 0x8000
        alt = (
            win32.get_async_key_state(win32.VK_MENU) & 0x8000
            or flags & win32.LLKHF_ALTDOWN
        )
        win_key = (
            win32.get_async_key_state(win32.VK_LWIN)
            | win32.get_async_key_state(win32.VK_RWIN)
        ) & 0x8000
        return KeyEvent(
            kind=kind,  # type: ignore[arg-type]
            vk=vk,
            ctrl=bool(control),
            shift=bool(shift),
            alt=bool(alt),
            win=bool(win_key),
        )

    def _queue(self, event: KeyEvent) -> None:
        if len(self._events) >= self._max_events:
            del self._events[: self._max_events // 2]
        self._events.append(event)
