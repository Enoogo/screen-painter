"""Map platform neutral key bindings onto Windows virtual-key codes."""

from __future__ import annotations

from screenpainter.core.keymap import KeyBinding

__all__ = [
    "VK_BY_KEY",
    "binding_signature",
    "event_signature",
    "shift_is_significant",
    "vk_for_key",
]

_LETTER_VKS = {chr(code): code - 32 for code in range(ord("a"), ord("z") + 1)}
_DIGIT_VKS = {str(index): 0x30 + index for index in range(10)}
_FUNCTION_VKS = {f"f{index}": 0x70 + index - 1 for index in range(1, 25)}

_NAMED_VKS = {
    "escape": 0x1B,
    "enter": 0x0D,
    "space": 0x20,
    "tab": 0x09,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "right": 0x27,
    "up": 0x26,
    "down": 0x28,
}

#: US layout OEM keys.  These share a key with a shifted symbol, so Shift is
#: not part of the matching signature for them.
_OEM_VKS = {
    "-": 0xBD,
    "=": 0xBB,
    "+": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
}

VK_BY_KEY: dict[str, int] = {
    **_LETTER_VKS,
    **_DIGIT_VKS,
    **_FUNCTION_VKS,
    **_NAMED_VKS,
    **_OEM_VKS,
}

MOD_CTRL = 0x01
MOD_SHIFT = 0x02
MOD_ALT = 0x04
MOD_WIN = 0x08


def vk_for_key(key: str) -> int | None:
    return VK_BY_KEY.get(str(key).lower())


def shift_is_significant(vk: int) -> bool:
    """Shift is part of the shortcut for letters, digits, F-keys and named keys.

    OEM symbol keys already require Shift for their shifted character, so a
    binding on ``-`` must not demand "Shift+-".
    """
    if 0x41 <= vk <= 0x5A:  # A-Z
        return True
    if 0x30 <= vk <= 0x39:  # 0-9
        return True
    if 0x70 <= vk <= 0x87:  # F1-F24
        return True
    return vk in _NAMED_VKS.values()


def binding_signature(binding: KeyBinding) -> tuple[int, int] | None:
    vk = vk_for_key(binding.key)
    if vk is None:
        return None
    mask = 0
    if binding.ctrl:
        mask |= MOD_CTRL
    if binding.alt:
        mask |= MOD_ALT
    if binding.win:
        mask |= MOD_WIN
    if binding.shift and shift_is_significant(vk):
        mask |= MOD_SHIFT
    return int(vk), mask


def event_signature(
    vk: int, *, ctrl: bool, shift: bool, alt: bool, win: bool
) -> tuple[int, int]:
    mask = 0
    if ctrl:
        mask |= MOD_CTRL
    if alt:
        mask |= MOD_ALT
    if win:
        mask |= MOD_WIN
    if shift and shift_is_significant(int(vk)):
        mask |= MOD_SHIFT
    return int(vk), mask
