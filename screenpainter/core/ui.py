"""Platform-neutral models for the floating toolbar and pointer capture.

These small value objects are what features exchange with the platform layer,
so a feature never has to know about Win32 messages, control ids or hwnds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PointerKind = Literal["down", "move", "up", "cancel"]
ButtonEventKind = Literal["button", "moved"]
KeyEventKind = Literal["down", "up"]


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    """One button in the floating panel."""

    key: str
    label: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ButtonEvent:
    """A click on one of the panel's buttons."""

    key: str


@dataclass(frozen=True, slots=True)
class PanelEvent:
    """Something that happened in the floating panel."""

    kind: ButtonEventKind
    key: str = ""
    x: int = 0
    y: int = 0


@dataclass(frozen=True, slots=True)
class PointerEvent:
    """A mouse event in the capture window (client coordinates)."""

    kind: PointerKind
    x: int = 0
    y: int = 0


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """A keyboard event captured while an editing mode is active.

    ``action`` is the configured action name (``"delete"``, ``"undo"``, ...)
    resolved by the platform layer; ``vk`` and the modifier flags are kept for
    diagnostics.  Features never see raw virtual-key codes.
    """

    kind: KeyEventKind
    action: str = ""
    vk: int = 0
    ctrl: bool = False
    shift: bool = False
    alt: bool = False
    win: bool = False

    @property
    def modifiers(self) -> frozenset[str]:
        names = []
        if self.ctrl:
            names.append("ctrl")
        if self.shift:
            names.append("shift")
        if self.alt:
            names.append("alt")
        if self.win:
            names.append("win")
        return frozenset(names)


@dataclass(frozen=True, slots=True)
class PanelStatus:
    """Content of the small properties panel shown for a selected rectangle."""

    title: str
    name: str
    color: str
    position: str
    size: str
    opacity: str
    hint: str = ""


__all__ = [
    "ButtonEvent",
    "ButtonSpec",
    "KeyEvent",
    "PanelEvent",
    "PanelStatus",
    "PointerEvent",
]
