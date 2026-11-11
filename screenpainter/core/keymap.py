"""Configurable keyboard shortcuts.

The configuration file is the single source of truth for every key the editing
modes listen to; this module parses those strings into a small, platform
neutral value object.  The Windows backend maps the canonical key names onto
virtual-key codes (see ``platform/windows/windows_keys.py``).

A binding looks like ``"Ctrl+Shift+Z"``, ``"Delete"`` or ``"Shift+Left"``:

* modifiers may appear in any order and are case insensitive
  (``ctrl``/``control``, ``shift``, ``alt``, ``win``/``super``);
* the last component is the key name: a single printable character, a function
  key (``F1``..``F24``) or one of the named keys below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

__all__ = [
    "ACTIONS",
    "ACTION_HELP",
    "ACTION_LABELS",
    "DEFAULT_BINDINGS",
    "KeyBinding",
    "KeyBindingError",
    "KeyMap",
    "binding_names",
    "format_binding",
    "parse_binding",
]


class KeyBindingError(ValueError):
    """Raised when a configured shortcut cannot be parsed."""


# -- key names --------------------------------------------------------------

#: Named (non printable) keys understood by the parser; values are the
#: canonical spellings used in diagnostics and in the settings window.
NAMED_KEYS: dict[str, str] = {
    "escape": "escape",
    "esc": "escape",
    "enter": "enter",
    "return": "enter",
    "space": "space",
    "spacebar": "space",
    "tab": "tab",
    "backspace": "backspace",
    "back": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "ins": "insert",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pgup": "pageup",
    "pagedown": "pagedown",
    "pgdn": "pagedown",
    "left": "left",
    "arrowleft": "left",
    "right": "right",
    "arrowright": "right",
    "up": "up",
    "arrowup": "up",
    "down": "down",
    "arrowdown": "down",
    "minus": "-",
    "plus": "+",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "semicolon": ";",
    "quote": "'",
    "grave": "`",
    "leftbracket": "[",
    "rightbracket": "]",
}

_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "menu": "alt",
    "win": "win",
    "super": "win",
    "meta": "win",
}

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
_MODIFIER_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}

_NAMED_DISPLAY = {
    "escape": "Esc",
    "enter": "Enter",
    "space": "Space",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
}

#: Characters that may be used as a binding key without a name.
_CHARACTER_KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789-=[]\\;',./`")
_FUNCTION_KEYS = {f"f{index}" for index in range(1, 25)}


@dataclass(frozen=True, slots=True)
class KeyBinding:
    """One parsed shortcut: a canonical key plus its modifiers."""

    key: str
    ctrl: bool = False
    shift: bool = False
    alt: bool = False
    win: bool = False

    @property
    def modifiers(self) -> frozenset[str]:
        return frozenset(
            name for name in _MODIFIER_ORDER if getattr(self, name)
        )

    def describe(self) -> str:
        parts = [
            _MODIFIER_LABELS[name]
            for name in _MODIFIER_ORDER
            if getattr(self, name)
        ]
        parts.append(_display_key(self.key))
        return "+".join(parts)

    def to_config(self) -> str:
        return self.describe()

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.describe()


def _display_key(key: str) -> str:
    named = _NAMED_DISPLAY.get(key)
    if named is not None:
        return named
    if key in _FUNCTION_KEYS:
        return key.upper()
    if len(key) == 1 and key.isalpha():
        return key.upper()
    return key


def _canonical_key(token: str) -> str:
    stripped = token.strip()
    lowered = stripped.lower()
    if not stripped:
        raise KeyBindingError("the key name is empty")
    named = NAMED_KEYS.get(lowered)
    if named is not None:
        return named
    if lowered in _FUNCTION_KEYS:
        return lowered
    if len(stripped) == 1 and lowered in _CHARACTER_KEYS:
        return lowered
    raise KeyBindingError(
        f"unknown key name {stripped!r}; use a letter, a digit, a punctuation "
        "character, F1..F24 or a named key such as Delete/Left/Escape"
    )


def parse_binding(text: object) -> KeyBinding:
    """Parse ``"Ctrl+Shift+Z"`` into a :class:`KeyBinding`."""
    if not isinstance(text, str):
        raise KeyBindingError(
            f"shortcut must be a string, got {type(text).__name__}"
        )
    raw = text.strip()
    if not raw:
        raise KeyBindingError("shortcut must not be empty")

    # A trailing bare '+' is the plus key itself, e.g. "Ctrl++" or "+".
    if raw == "+":
        parts = ["plus"]
    elif raw.endswith("+"):
        parts = [item for item in raw[:-1].split("+")[:-1]] + ["plus"]
    else:
        parts = raw.split("+")

    modifiers: set[str] = set()
    key_token = ""
    for part in parts:
        token = part.strip()
        if not token:
            continue
        modifier = _MODIFIER_ALIASES.get(token.lower())
        if modifier is not None and not key_token:
            modifiers.add(modifier)
            continue
        if key_token:
            raise KeyBindingError(
                f"shortcut {text!r} has more than one key; expected modifiers "
                "followed by exactly one key"
            )
        key_token = token
    if not key_token:
        raise KeyBindingError(f"shortcut {text!r} does not name a key")
    key = _canonical_key(key_token)
    return KeyBinding(
        key=key,
        ctrl="ctrl" in modifiers,
        shift="shift" in modifiers,
        alt="alt" in modifiers,
        win="win" in modifiers,
    )


def format_binding(binding: KeyBinding) -> str:
    return binding.describe()


# -- actions ----------------------------------------------------------------

#: ``action -> (default shortcut, human readable label, help text)``
ACTIONS: dict[str, tuple[str, str, str]] = {
    "cancel": ("Escape", "退出编辑", "结束当前的编辑/移动模式"),
    "confirm": ("Enter", "确认编辑", "结束当前的编辑/移动模式并保存"),
    "delete": ("Delete", "删除矩形", "删除当前选中的矩形"),
    "undo": ("Ctrl+Z", "撤回", "撤销上一次矩形修改"),
    "redo": ("Ctrl+Shift+Z", "重做", "重做被撤销的修改"),
    "select_next": ("Tab", "选中下一个", "循环选择下一个矩形"),
    "select_prev": ("Shift+Tab", "选中上一个", "循环选择上一个矩形"),
    "move_left": ("Left", "左移 1 像素", "把选中的矩形向左移动一个像素"),
    "move_right": ("Right", "右移 1 像素", "把选中的矩形向右移动一个像素"),
    "move_up": ("Up", "上移 1 像素", "把选中的矩形向上移动一个像素"),
    "move_down": ("Down", "下移 1 像素", "把选中的矩形向下移动一个像素"),
    "move_left_fast": ("Shift+Left", "左移 10 像素", "把选中的矩形向左快速移动"),
    "move_right_fast": ("Shift+Right", "右移 10 像素", "把选中的矩形向右快速移动"),
    "move_up_fast": ("Shift+Up", "上移 10 像素", "把选中的矩形向上快速移动"),
    "move_down_fast": ("Shift+Down", "下移 10 像素", "把选中的矩形向下快速移动"),
}

ACTION_LABELS: dict[str, str] = {name: item[1] for name, item in ACTIONS.items()}
ACTION_HELP: dict[str, str] = {name: item[2] for name, item in ACTIONS.items()}
DEFAULT_BINDINGS: dict[str, str] = {name: item[0] for name, item in ACTIONS.items()}


def binding_names() -> tuple[str, ...]:
    """Action names in definition order (used by the settings window)."""
    return tuple(ACTIONS)


@dataclass(frozen=True, slots=True)
class KeyMap:
    """All configured shortcuts, parsed and validated."""

    cancel: KeyBinding
    confirm: KeyBinding
    delete: KeyBinding
    undo: KeyBinding
    redo: KeyBinding
    select_next: KeyBinding
    select_prev: KeyBinding
    move_left: KeyBinding
    move_right: KeyBinding
    move_up: KeyBinding
    move_down: KeyBinding
    move_left_fast: KeyBinding
    move_right_fast: KeyBinding
    move_up_fast: KeyBinding
    move_down_fast: KeyBinding

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None) -> "KeyMap":
        """Build a key map from ``[keys]``, filling in the defaults."""
        supplied = dict(values or {})
        unknown = sorted(set(supplied) - set(ACTIONS))
        if unknown:
            raise KeyBindingError(
                "unknown key binding name(s): "
                + ", ".join(repr(item) for item in unknown)
                + "; available: "
                + ", ".join(sorted(ACTIONS))
            )
        parsed: dict[str, KeyBinding] = {}
        for name in ACTIONS:
            raw = supplied.get(name, DEFAULT_BINDINGS[name])
            try:
                parsed[name] = parse_binding(raw)
            except KeyBindingError as exc:
                raise KeyBindingError(f"keys.{name}: {exc}") from exc
        clash = _find_clash(parsed)
        if clash:
            raise KeyBindingError(clash)
        return cls(**parsed)

    @classmethod
    def default(cls) -> "KeyMap":
        return cls.from_mapping({})

    def binding(self, action: str) -> KeyBinding:
        if action not in ACTIONS:
            raise KeyError(action)
        return getattr(self, action)  # type: ignore[no-any-return]

    def action_for(self, binding: KeyBinding) -> str | None:
        for name in ACTIONS:
            if getattr(self, name) == binding:
                return name
        return None

    def describe(self) -> dict[str, str]:
        return {name: getattr(self, name).describe() for name in ACTIONS}

    def to_mapping(self) -> dict[str, str]:
        return self.describe()


def _find_clash(bindings: Mapping[str, KeyBinding]) -> str:
    seen: dict[KeyBinding, str] = {}
    clashes: list[str] = []
    for name in ACTIONS:
        binding = bindings[name]
        other = seen.get(binding)
        if other is not None:
            clashes.append(
                f"{other} and {name} both use {binding.describe()}"
            )
        else:
            seen[binding] = name
    if not clashes:
        return ""
    return "duplicate shortcut(s) in [keys]: " + "; ".join(clashes)
