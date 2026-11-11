"""Tkinter settings window for the ScreenPainter configuration file.

The window edits the TOML configuration directly (it does not need the overlay
application to be running) and touches only the values the user actually
changed.  Every save is validated with the same rules the application uses
before the file is written, so a half-edited configuration can never reach the
running overlay.

Entry point::

    python run.py --settings          # wired up by the command line
    python -m screenpainter.settings.app [config.toml]

``main()`` returns ``0`` on success and ``2`` when the configuration cannot be
loaded (or the window cannot be created).

Tabs:

* 常规       - ``[app]``
* 目标窗口   - ``[target]`` / ``[overlay]``
* 日志       - ``[logging]``
* 布局与分组 - ``[layout]``, ``[groups.<name>]`` including every rectangle
* 窗口配置   - ``[window_profiles.<name>]``
* 功能       - ``[features.<name>]`` including the toolbar options
* 快捷键     - ``[keys]``, with a key capture field per action
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, messagebox, simpledialog, ttk
from typing import Any, Callable, Mapping, Sequence

from screenpainter.config import DEFAULT_CONFIG_PATH, load_config
from screenpainter.core.display import normalize_resolution
from screenpainter.core.feature import registered_feature_types
from screenpainter.core.keymap import (
    ACTION_LABELS,
    ACTIONS,
    DEFAULT_BINDINGS,
    KeyBindingError,
    parse_binding,
)
from screenpainter.errors import ConfigError, ConfigWriteError, ScreenPainterError
from screenpainter.features import load_builtin_features
from screenpainter.settings.model import (
    COORDINATE_SPACES,
    HEX_COLOR_RE,
    LAYOUT_MATCHES,
    LAYOUT_MODES,
    LOG_LEVELS,
    SELECT_STRATEGIES,
    TOOLBAR_BUTTONS,
    TOOLBAR_OPTION_DEFAULTS,
    TOOLBAR_OPTION_KINDS,
    SettingsDraft,
    SettingsValidationError,
    SettingsValueError,
    format_toml_value,
    parse_toml_value,
)

__all__ = ["SettingsWindow", "keysym_to_binding", "main"]

WINDOW_TITLE = "ScreenPainter 设置"
MIN_WIDTH = 980
MIN_HEIGHT = 720
INITIAL_SIZE = "1060x800"

_MONOSPACE = ("Consolas", 9)
_HINT_COLOR = "#666666"
_ERROR_COLOR = "#B00020"
_OK_COLOR = "#006400"
_MISSING = object()

_PUNCTUATION = set("abcdefghijklmnopqrstuvwxyz0123456789-=[]\\;',./`")
_FUNCTION_KEYS = frozenset(f"f{index}" for index in range(1, 25))

#: Tk keysyms of the modifier keys themselves: pressing one alone is not a
#: shortcut, so the capture field simply ignores it.
_MODIFIER_KEYSYMS = frozenset(
    {
        "shift_l",
        "shift_r",
        "control_l",
        "control_r",
        "alt_l",
        "alt_r",
        "win_l",
        "win_r",
        "super_l",
        "super_r",
        "meta_l",
        "meta_r",
        "caps_lock",
        "num_lock",
        "scroll_lock",
        "iso_level3_shift",
    }
)

#: Tk keysyms that are not a single character but have a canonical keymap token.
_KEYSYM_TOKENS: dict[str, str] = {
    "escape": "escape",
    "return": "enter",
    "KP_Enter": "enter",
    "space": "space",
    "tab": "tab",
    "ISO_Left_Tab": "tab",
    "backspace": "backspace",
    "delete": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "prior": "pageup",
    "next": "pagedown",
    "left": "left",
    "right": "right",
    "up": "up",
    "down": "down",
    "minus": "minus",
    "equal": "equal",
    "bracketleft": "leftbracket",
    "bracketright": "rightbracket",
    "backslash": "backslash",
    "semicolon": "semicolon",
    "apostrophe": "quote",
    "comma": "comma",
    "period": "period",
    "slash": "slash",
    "grave": "grave",
    "plus": "plus",
    "KP_Add": "plus",
    "KP_Subtract": "minus",
    "KP_Decimal": "period",
}

# Tk event.state bits (Windows and X11 agree on these three).
_STATE_SHIFT = 0x0001
_STATE_CONTROL = 0x0004
_STATE_ALT_MOD1 = 0x0008
_STATE_ALT_WIN32 = 0x20000


# ---------------------------------------------------------------------------
# Small pure helpers


def _keysym_token(keysym: str) -> str | None:
    """Translate one Tk keysym into a keymap key token (``None`` if unknown)."""
    if not keysym:
        return None
    lowered = keysym.lower()
    if lowered in _MODIFIER_KEYSYMS:
        return None
    mapped = _KEYSYM_TOKENS.get(keysym) or _KEYSYM_TOKENS.get(lowered)
    if mapped is not None:
        return mapped
    if lowered in _FUNCTION_KEYS:
        return lowered
    if len(keysym) == 1 and lowered in _PUNCTUATION:
        return lowered
    return None


def keysym_to_binding(keysym: str, state: int = 0) -> str | None:
    """Return the canonical shortcut for a Tk key event, or ``None``.

    ``state`` is ``event.state``; the result is what
    :mod:`screenpainter.core.keymap` would parse back, for example
    ``"Ctrl+Shift+Z"``.  A bare modifier press returns ``None`` so the capture
    field ignores it.

    The Windows key cannot be detected reliably through Tk on Windows; a
    combination that needs ``Win`` has to be typed into the TOML file.
    """
    token = _keysym_token(keysym)
    if token is None:
        return None
    parts: list[str] = []
    if state & _STATE_CONTROL:
        parts.append("Ctrl")
    if state & (_STATE_ALT_MOD1 | _STATE_ALT_WIN32):
        parts.append("Alt")
    if state & _STATE_SHIFT:
        parts.append("Shift")
    parts.append(token)
    try:
        return parse_binding("+".join(parts)).describe()
    except KeyBindingError:
        return None


def display_binding(value: object) -> str:
    """Canonical display form of a stored shortcut; invalid values stay visible."""
    try:
        return parse_binding(value).describe()
    except KeyBindingError:
        return "" if value is None else str(value)


def _toml_text(value: Any) -> str:
    """Render any value as a one line TOML snippet for the generic editors."""
    try:
        return format_toml_value(value)
    except (ScreenPainterError, TypeError, ValueError):
        return str(value)


def _number_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return format_toml_value(value)
    return str(value)


def _split_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _option_cache_value(key: str, kind: str | None, value: Any) -> Any:
    """Normalise an option value for the widget cache.

    Booleans stay booleans, the toolbar ``buttons`` list becomes a canonical
    list, numbers/strings become their text form and unknown options become a
    TOML snippet that :func:`parse_toml_value` can parse again.
    """
    if kind == "bool":
        return bool(value)
    if kind == "str_list":
        items = [str(item) for item in value] if isinstance(value, (list, tuple)) else []
        ordered = [button for button in TOOLBAR_BUTTONS if button in items]
        ordered += [item for item in items if item not in TOOLBAR_BUTTONS]
        return ordered
    if kind == "int" or kind == "float":
        return _number_text(value)
    if kind == "str":
        return str(value)
    return _toml_text(value)


# ---------------------------------------------------------------------------
# Widgets


class _KeyCaptureEntry(ttk.Entry):
    """An entry that records the next key combination pressed inside it."""

    def __init__(
        self,
        master: tk.Misc,
        on_binding: Callable[[str], None],
        on_message: Callable[[str], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(master, width=18, justify="center", **kwargs)
        self._on_binding = on_binding
        self._on_message = on_message
        self.bind("<KeyPress>", self._handle_key_press)

    def _handle_key_press(self, event: tk.Event) -> str:
        keysym = getattr(event, "keysym", "") or ""
        state = int(getattr(event, "state", 0) or 0)
        binding = keysym_to_binding(keysym, state)
        if binding is None:
            if keysym.lower() not in _MODIFIER_KEYSYMS:
                self._on_message(
                    "无法识别这个按键；请使用字母、数字、F1–F24 或 Delete、Left 等命名按键。"
                )
            return "break"
        self._on_binding(binding)
        return "break"


class _ScalarRow:
    """One staged scalar field: a Tk variable plus how to convert it back."""

    __slots__ = ("section", "key", "kind", "initial", "var", "label", "minimum", "maximum")

    def __init__(
        self,
        section: tuple[str, ...],
        key: str,
        kind: str,
        initial: Any,
        var: tk.Variable,
        label: str,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> None:
        self.section = section
        self.key = key
        self.kind = kind
        self.initial = initial
        self.var = var
        self.label = label
        self.minimum = minimum
        self.maximum = maximum

    def collect(self, draft: SettingsDraft, errors: list[str]) -> None:
        value = self._value(errors)
        if value is _MISSING:
            return
        if value == self.initial:
            return
        try:
            draft.set(self.section, self.key, value)
        except SettingsValueError as exc:
            errors.append(f"{self.label}：{exc}")

    def _value(self, errors: list[str]) -> Any:
        if self.kind == "bool":
            return bool(self.var.get())
        text = str(self.var.get()).strip()
        if self.kind == "str":
            return text
        if self.kind == "int":
            try:
                number: float = int(text)
            except ValueError:
                errors.append(f"{self.label}：需要整数（当前为 {text!r}）")
                return _MISSING
        elif self.kind == "float":
            try:
                number = float(text)
            except ValueError:
                errors.append(f"{self.label}：需要数字（当前为 {text!r}）")
                return _MISSING
        else:
            return text
        if self.minimum is not None and number < self.minimum:
            errors.append(f"{self.label}：必须 ≥ {self.minimum:g}（当前为 {number:g}）")
            return _MISSING
        if self.maximum is not None and number > self.maximum:
            errors.append(f"{self.label}：必须 ≤ {self.maximum:g}（当前为 {number:g}）")
            return _MISSING
        return number


# ---------------------------------------------------------------------------
# Tab base class


class _Tab(ttk.Frame):
    """Common behaviour for the notebook pages."""

    def __init__(self, master: ttk.Notebook, window: "SettingsWindow") -> None:
        super().__init__(master, padding=10)
        self.window = window
        self.draft = window.draft
        self._rows: list[_ScalarRow] = []

    # -- helpers ---------------------------------------------------------
    @property
    def root(self) -> tk.Tk:
        return self.window.root

    def get(self, section: Sequence[str], key: str, default: Any = None) -> Any:
        return self.draft.get(section, key, default)

    def status(self, message: str) -> None:
        self.window.set_status(message)

    def error(self, message: str, title: str = "设置") -> None:
        self.window.show_error(title, message)

    def _add_scalar(
        self,
        parent: tk.Misc,
        row: int,
        label: str,
        section: tuple[str, ...],
        key: str,
        kind: str = "str",
        *,
        default: Any = None,
        minimum: float | None = None,
        maximum: float | None = None,
        width: int = 24,
        hint: str | None = None,
    ) -> tk.Widget:
        raw = self.get(section, key, _MISSING)
        displayed = default if raw is _MISSING else raw
        if kind == "bool":
            var: tk.Variable = tk.BooleanVar(value=bool(displayed))
            widget: tk.Widget = ttk.Checkbutton(parent, text=label, variable=var)
            widget.grid(row=row, column=0, columnspan=3, sticky="w", pady=3)
        else:
            var = tk.StringVar(
                value="" if displayed is None else self._text(displayed)
            )
            ttk.Label(parent, text=label, width=18, anchor="w").grid(
                row=row, column=0, sticky="w", pady=3
            )
            widget = ttk.Entry(parent, textvariable=var, width=width)
            widget.grid(row=row, column=1, sticky="w", pady=3)
            if hint:
                ttk.Label(parent, text=hint, foreground=_HINT_COLOR).grid(
                    row=row, column=2, sticky="w", padx=(10, 0)
                )
        self._rows.append(
            _ScalarRow(
                section,
                key,
                kind,
                displayed,
                var,
                label,
                minimum=minimum,
                maximum=maximum,
            )
        )
        return widget

    def _add_choice(
        self,
        parent: tk.Misc,
        row: int,
        label: str,
        section: tuple[str, ...],
        key: str,
        choices: Sequence[str],
        *,
        default: Any = None,
        width: int = 22,
    ) -> ttk.Combobox:
        raw = self.get(section, key, _MISSING)
        displayed = None if raw is _MISSING else raw
        text = "" if displayed is None else str(displayed)
        values = list(choices)
        if text and text not in values:
            values.append(text)
        var = tk.StringVar(value=text)
        ttk.Label(parent, text=label, width=18, anchor="w").grid(
            row=row, column=0, sticky="w", pady=3
        )
        combo = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
        combo.grid(row=row, column=1, sticky="w", pady=3)
        self._rows.append(_ScalarRow(section, key, "str", displayed, var, label))
        return combo

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return format_toml_value(value)
        return str(value)

    # -- lifecycle -------------------------------------------------------
    def collect(self, errors: list[str]) -> None:
        """Stage this page's values into the draft (called on save)."""
        for row in self._rows:
            row.collect(self.draft, errors)

    def on_shown(self) -> None:
        """Called when the page becomes visible; refreshes dynamic choices."""


# ---------------------------------------------------------------------------
# 常规


class _GeneralTab(_Tab):
    def __init__(self, master: ttk.Notebook, window: "SettingsWindow") -> None:
        super().__init__(master, window)
        box = ttk.LabelFrame(self, text="应用程序 [app]", padding=10)
        box.pack(fill="x")
        self._add_scalar(box, 0, "应用名称", ("app",), "name", "str", default="ScreenPainter")
        self._add_scalar(
            box,
            1,
            "跟踪校验间隔 (ms)",
            ("app",),
            "verify_ms",
            "int",
            default=1000,
            minimum=100,
            maximum=10000,
            hint="100 – 10000",
        )
        self._add_scalar(
            box,
            2,
            "空闲轮询间隔 (ms)",
            ("app",),
            "idle_tick_ms",
            "int",
            default=500,
            minimum=50,
            maximum=10000,
            hint="50 – 10000",
        )
        self._add_scalar(box, 3, "调试模式（热重载）", ("app",), "debug", "bool", default=False)
        self._add_scalar(
            box,
            4,
            "调试检查间隔 (ms)",
            ("app",),
            "debug_interval_ms",
            "int",
            default=400,
            minimum=100,
            maximum=5000,
            hint="100 – 5000",
        )

        version = self.get((), "version", "?")
        info = ttk.LabelFrame(self, text="说明", padding=10)
        info.pack(fill="x", pady=(10, 0))
        ttk.Label(
            info,
            text=(
                f"配置版本：{version}（schema 版本由程序决定，窗口里不能修改）\n"
                "调试模式打开后，运行中的 ScreenPainter 会在文件变化时自动重新加载配置。"
            ),
            justify="left",
            foreground=_HINT_COLOR,
        ).pack(anchor="w")


# ---------------------------------------------------------------------------
# 目标窗口


class _TargetTab(_Tab):
    def __init__(self, master: ttk.Notebook, window: "SettingsWindow") -> None:
        super().__init__(master, window)
        target_box = ttk.LabelFrame(self, text="目标窗口 [target]", padding=10)
        target_box.pack(fill="x")
        current = str(self.get(("target",), "window", ""))
        values = list(self.draft.profile_names())
        if current and current not in values:
            values.append(current)
        self.var_profile = tk.StringVar(value=current)
        ttk.Label(target_box, text="窗口配置", width=18, anchor="w").grid(
            row=0, column=0, sticky="w", pady=3
        )
        self.profile_combo = ttk.Combobox(
            target_box,
            textvariable=self.var_profile,
            values=values,
            state="readonly",
            width=24,
        )
        self.profile_combo.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(
            target_box,
            text="target.window 必须是 [window_profiles] 里某个表的名称",
            foreground=_HINT_COLOR,
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))

        overlay_box = ttk.LabelFrame(self, text="覆盖层 [overlay]", padding=10)
        overlay_box.pack(fill="x", pady=(10, 0))
        self._add_choice(
            overlay_box,
            0,
            "坐标空间",
            ("overlay",),
            "coordinate_space",
            COORDINATE_SPACES,
            default="window",
        )
        self._add_scalar(
            overlay_box,
            1,
            "鼠标点击穿透",
            ("overlay",),
            "click_through",
            "bool",
            default=True,
        )
        ttk.Label(
            self,
            text=(
                "window = DWM 可见外框（含标题栏）；client = 内容区（不含标题栏）。\n"
                "修改坐标空间不会移动已保存的矩形，矩形坐标以覆盖层左上角为原点。"
            ),
            justify="left",
            foreground=_HINT_COLOR,
        ).pack(anchor="w", pady=(10, 0))

    def on_shown(self) -> None:
        values = list(self.draft.profile_names())
        current = self.var_profile.get().strip()
        if current and current not in values:
            values.append(current)
        self.profile_combo.configure(values=values)

    def collect(self, errors: list[str]) -> None:
        profile = self.var_profile.get().strip()
        if profile:
            self.draft.set(("target",), "window", profile)
        else:
            errors.append("目标窗口：必须选择一个 [window_profiles] 配置")
        super().collect(errors)


# ---------------------------------------------------------------------------
# 日志


class _LoggingTab(_Tab):
    def __init__(self, master: ttk.Notebook, window: "SettingsWindow") -> None:
        super().__init__(master, window)
        box = ttk.LabelFrame(self, text="日志 [logging]", padding=10)
        box.pack(fill="x")
        self._add_choice(box, 0, "日志级别", ("logging",), "level", LOG_LEVELS, default="INFO")
        self._add_scalar(box, 1, "输出到控制台", ("logging",), "console", "bool", default=True)
        self._add_scalar(
            box,
            2,
            "日志文件",
            ("logging",),
            "file",
            "str",
            default="logs/screenpainter.log",
            width=40,
            hint="相对项目根目录；留空表示关闭文件日志",
        )
        self._add_scalar(
            box,
            3,
            "单文件最大字节",
            ("logging",),
            "max_bytes",
            "int",
            default=1_000_000,
            minimum=1024,
            maximum=1_000_000_000,
            hint="≥ 1024",
        )
        self._add_scalar(
            box,
            4,
            "备份数量",
            ("logging",),
            "backup_count",
            "int",
            default=3,
            minimum=0,
            maximum=100,
            hint="0 – 100",
        )
        ttk.Label(
            self,
            text="日志级别的改动需要重启 ScreenPainter 才会生效（热重载不会替换日志处理器）。",
            foreground=_HINT_COLOR,
        ).pack(anchor="w", pady=(10, 0))


# ---------------------------------------------------------------------------
# 布局与分组


def _rect_row_text(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "name": str(row.get("name", "")),
        "x": _number_text(row.get("x", 0)),
        "y": _number_text(row.get("y", 0)),
        "width": _number_text(row.get("width", 1)),
        "height": _number_text(row.get("height", 1)),
        "color": str(row.get("color", "#000000")),
        "opacity": _number_text(row.get("opacity", 1.0)),
    }


def _default_rect_row(index: int, color: str) -> dict[str, str]:
    return {
        "name": f"rect_{index}",
        "x": "0",
        "y": "0",
        "width": "100",
        "height": "30",
        "color": color,
        "opacity": "1.0",
    }


class _LayoutGroupsTab(_Tab):
    """``[layout]`` plus group and rectangle management."""

    RECT_COLUMNS = ("name", "x", "y", "width", "height", "color", "opacity")
    RECT_HEADINGS = {
        "name": "名称",
        "x": "x",
        "y": "y",
        "width": "宽",
        "height": "高",
        "color": "颜色",
        "opacity": "不透明度",
    }

    def __init__(self, master: ttk.Notebook, window: "SettingsWindow") -> None:
        super().__init__(master, window)
        self._current_group: str | None = None
        self._rect_index: int | None = None
        self._loading = False
        self._group_meta: dict[str, tuple[str, str]] = {}
        self._rect_rows: dict[str, list[dict[str, str]]] = {}

        layout_box = ttk.LabelFrame(self, text="布局 [layout]", padding=8)
        layout_box.pack(fill="x")
        layout_box.columnconfigure(3, weight=1)
        self._add_choice(layout_box, 0, "模式", ("layout",), "mode", LAYOUT_MODES, default="auto")
        self.var_group = tk.StringVar(value=str(self.get(("layout",), "group", "")))
        ttk.Label(layout_box, text="手动矩形组", width=18, anchor="w").grid(
            row=0, column=2, sticky="w", padx=(24, 0), pady=3
        )
        self.group_combo = ttk.Combobox(
            layout_box, textvariable=self.var_group, values=[], state="readonly", width=24
        )
        self.group_combo.grid(row=0, column=3, sticky="w", pady=3)
        self._add_choice(layout_box, 1, "匹配对象", ("layout",), "match", LAYOUT_MATCHES, default="monitor")
        self._add_scalar(layout_box, 2, "隐藏所有矩形", ("layout",), "hidden", "bool", default=False)
        ttk.Label(
            layout_box,
            text="mode = manual 时必须选择一个矩形组；mode = auto 时留空表示按分辨率自动匹配。",
            foreground=_HINT_COLOR,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(8, 0))

        left = ttk.LabelFrame(panes, text="矩形组", padding=8)
        right = ttk.LabelFrame(panes, text="组内容", padding=8)
        panes.add(left, weight=1)
        panes.add(right, weight=3)

        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True)
        self.group_tree = ttk.Treeview(
            list_frame, columns=("name", "resolution"), show="headings", height=6, selectmode="browse"
        )
        self.group_tree.heading("name", text="名称")
        self.group_tree.heading("resolution", text="分辨率")
        self.group_tree.column("name", width=130, stretch=True)
        self.group_tree.column("resolution", width=90, stretch=False)
        group_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.group_tree.yview)
        self.group_tree.configure(yscrollcommand=group_scroll.set)
        self.group_tree.pack(side="left", fill="both", expand=True)
        group_scroll.pack(side="right", fill="y")
        self.group_tree.bind("<<TreeviewSelect>>", self._on_group_select)

        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="新建组", command=self._add_group).pack(side="left")
        ttk.Button(buttons, text="重命名", command=self._rename_group).pack(side="left", padx=4)
        ttk.Button(buttons, text="删除组", command=self._remove_group).pack(side="left")

        meta = ttk.Frame(right)
        meta.pack(fill="x")
        ttk.Label(meta, text="说明", width=6, anchor="w").grid(row=0, column=0, sticky="w")
        self.var_group_description = tk.StringVar()
        ttk.Entry(meta, textvariable=self.var_group_description, width=44).grid(
            row=0, column=1, sticky="we", padx=(2, 12)
        )
        ttk.Label(meta, text="分辨率", anchor="w").grid(row=0, column=2, sticky="w")
        self.var_group_resolution = tk.StringVar()
        ttk.Entry(meta, textvariable=self.var_group_resolution, width=14).grid(
            row=0, column=3, sticky="w", padx=(2, 0)
        )
        meta.columnconfigure(1, weight=1)

        rect_frame = ttk.Frame(right)
        rect_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.rect_tree = ttk.Treeview(
            rect_frame, columns=self.RECT_COLUMNS, show="headings", height=7, selectmode="browse"
        )
        widths = {
            "name": 110,
            "x": 60,
            "y": 60,
            "width": 60,
            "height": 60,
            "color": 90,
            "opacity": 80,
        }
        for column in self.RECT_COLUMNS:
            self.rect_tree.heading(column, text=self.RECT_HEADINGS[column])
            self.rect_tree.column(column, width=widths[column], stretch=(column == "name"))
        rect_scroll = ttk.Scrollbar(rect_frame, orient="vertical", command=self.rect_tree.yview)
        self.rect_tree.configure(yscrollcommand=rect_scroll.set)
        self.rect_tree.pack(side="left", fill="both", expand=True)
        rect_scroll.pack(side="right", fill="y")
        self.rect_tree.bind("<<TreeviewSelect>>", self._on_rect_select)

        rect_buttons = ttk.Frame(right)
        rect_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(rect_buttons, text="新增矩形", command=self._add_rect).pack(side="left")
        ttk.Button(rect_buttons, text="复制矩形", command=self._duplicate_rect).pack(side="left", padx=4)
        ttk.Button(rect_buttons, text="删除矩形", command=self._remove_rect).pack(side="left")

        editor = ttk.LabelFrame(right, text="选中的矩形", padding=8)
        editor.pack(fill="x", pady=(8, 0))
        self.var_rect_name = tk.StringVar()
        self.var_rect_x = tk.StringVar()
        self.var_rect_y = tk.StringVar()
        self.var_rect_width = tk.StringVar()
        self.var_rect_height = tk.StringVar()
        self.var_rect_color = tk.StringVar()
        self.var_rect_opacity = tk.StringVar()

        first_row = ttk.Frame(editor)
        first_row.pack(fill="x")
        _inline_entry(first_row, "名称", self.var_rect_name, width=14)
        _inline_entry(first_row, "x", self.var_rect_x, width=7)
        _inline_entry(first_row, "y", self.var_rect_y, width=7)
        second_row = ttk.Frame(editor)
        second_row.pack(fill="x", pady=(4, 0))
        _inline_entry(second_row, "宽", self.var_rect_width, width=7)
        _inline_entry(second_row, "高", self.var_rect_height, width=7)
        _inline_entry(second_row, "不透明度", self.var_rect_opacity, width=7)

        third_row = ttk.Frame(editor)
        third_row.pack(fill="x", pady=(4, 0))
        ttk.Label(third_row, text="颜色").pack(side="left")
        ttk.Entry(third_row, textvariable=self.var_rect_color, width=10).pack(
            side="left", padx=(2, 6)
        )
        self.swatch = tk.Label(third_row, width=3, text=" ", relief="solid", borderwidth=1, bg="#000000")
        self.swatch.pack(side="left", padx=(0, 8))
        ttk.Button(third_row, text="选择颜色...", command=self._choose_rect_color).pack(side="left")
        ttk.Label(third_row, text='格式 "#RRGGBB"', foreground=_HINT_COLOR).pack(
            side="left", padx=(8, 0)
        )

        for var in (
            self.var_rect_name,
            self.var_rect_x,
            self.var_rect_y,
            self.var_rect_width,
            self.var_rect_height,
            self.var_rect_color,
            self.var_rect_opacity,
        ):
            var.trace_add("write", self._on_rect_field_changed)

        self._refresh_group_choices()
        self._refresh_group_list()
        groups = self.draft.group_names()
        if groups:
            self._select_group(groups[0])

    # -- group list ------------------------------------------------------
    def _refresh_group_choices(self) -> None:
        names = list(self.draft.group_names())
        values = [""] + names
        current = self.var_group.get().strip()
        if current and current not in values:
            values.append(current)
        self.group_combo.configure(values=values)

    def _refresh_group_list(self, select: str | None = None) -> None:
        for item in self.group_tree.get_children():
            self.group_tree.delete(item)
        for name in self.draft.group_names():
            resolution = str(self.draft.get(("groups", name), "resolution", ""))
            self.group_tree.insert("", "end", iid=name, values=(name, resolution))
        target = select or self._current_group
        names = self.draft.group_names()
        if target not in names:
            target = names[0] if names else None
        if target is not None:
            self._select_group(target)
        else:
            self._select_group(None)
        self._refresh_group_choices()

    def _selected_group(self) -> str | None:
        selection = self.group_tree.selection()
        if not selection:
            return None
        return str(selection[0])

    def _select_group(self, name: str | None) -> None:
        if name != self._current_group:
            # Switching groups must not lose the description/resolution of the
            # group that is currently on screen.
            self._store_group_widgets()
        if name is None:
            self.group_tree.selection_remove(self.group_tree.selection())
            self._current_group = None
            self._load_group(None)
            return
        self.group_tree.selection_set(name)
        self.group_tree.focus(name)
        self.group_tree.see(name)
        self._current_group = name
        self._load_group(name)

    def _on_group_select(self, _event: object = None) -> None:
        name = self._selected_group()
        if name == self._current_group:
            return
        self._store_group_widgets()
        self._current_group = name
        self._load_group(name)

    def _meta_for(self, name: str) -> tuple[str, str]:
        if name not in self._group_meta:
            self._group_meta[name] = (
                str(self.draft.get(("groups", name), "description", "")),
                str(self.draft.get(("groups", name), "resolution", "")),
            )
        return self._group_meta[name]

    def _rects_for(self, name: str) -> list[dict[str, str]]:
        if name not in self._rect_rows:
            self._rect_rows[name] = [
                _rect_row_text(row) for row in self.draft.rectangles(name)
            ]
        return self._rect_rows[name]

    def _load_group(self, name: str | None) -> None:
        self._loading = True
        try:
            if name is None:
                self.var_group_description.set("")
                self.var_group_resolution.set("")
                for item in self.rect_tree.get_children():
                    self.rect_tree.delete(item)
                self._rect_index = None
                self._load_rect_vars(None)
            else:
                description, resolution = self._meta_for(name)
                self.var_group_description.set(description)
                self.var_group_resolution.set(resolution)
                self._fill_rect_tree(name)
        finally:
            self._loading = False

    def _fill_rect_tree(self, name: str) -> None:
        rows = self._rects_for(name)
        for item in self.rect_tree.get_children():
            self.rect_tree.delete(item)
        for index, row in enumerate(rows):
            self.rect_tree.insert(
                "",
                "end",
                iid=str(index),
                values=tuple(row[column] for column in self.RECT_COLUMNS),
            )
        self._rect_index = None
        if rows:
            self._select_rect(0)
        else:
            self._load_rect_vars(None)

    def _select_rect(self, index: int | None) -> None:
        previous_loading = self._loading
        self._loading = True
        try:
            if index is None or not self.rect_tree.get_children():
                self._rect_index = None
                self.rect_tree.selection_remove(self.rect_tree.selection())
                self._load_rect_vars(None)
                return
            children = self.rect_tree.get_children()
            index = max(0, min(index, len(children) - 1))
            item = children[index]
            # Setting a selection Tk already has still emits <<TreeviewSelect>>;
            # only touch it when it actually changes, otherwise the handler and
            # this method would keep re-triggering each other.
            if item not in self.rect_tree.selection():
                self.rect_tree.selection_set(item)
            self.rect_tree.focus(item)
            self.rect_tree.see(item)
            self._rect_index = index
            self._load_rect_vars(index)
        finally:
            self._loading = previous_loading

    def _load_rect_vars(self, index: int | None) -> None:
        variables = (
            (self.var_rect_name, "name"),
            (self.var_rect_x, "x"),
            (self.var_rect_y, "y"),
            (self.var_rect_width, "width"),
            (self.var_rect_height, "height"),
            (self.var_rect_color, "color"),
            (self.var_rect_opacity, "opacity"),
        )
        if index is None or self._current_group is None:
            for var, _key in variables:
                var.set("")
            self._update_swatch()
            return
        rows = self._rects_for(self._current_group)
        if not (0 <= index < len(rows)):
            return
        row = rows[index]
        for var, key in variables:
            var.set(row[key])
        self._update_swatch()

    def _on_rect_select(self, _event: object = None) -> None:
        if self._loading:
            return
        selection = self.rect_tree.selection()
        if not selection:
            if self._rect_index is not None:
                self._select_rect(None)
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if index != self._rect_index:
            self._select_rect(index)

    def _on_rect_field_changed(self, *_args: object) -> None:
        if self._loading or self._current_group is None or self._rect_index is None:
            return
        rows = self._rects_for(self._current_group)
        if not (0 <= self._rect_index < len(rows)):
            return
        row = rows[self._rect_index]
        row.update(
            {
                "name": self.var_rect_name.get(),
                "x": self.var_rect_x.get(),
                "y": self.var_rect_y.get(),
                "width": self.var_rect_width.get(),
                "height": self.var_rect_height.get(),
                "color": self.var_rect_color.get(),
                "opacity": self.var_rect_opacity.get(),
            }
        )
        self._update_rect_item(self._rect_index)
        self._update_swatch()

    def _update_rect_item(self, index: int) -> None:
        if self._current_group is None:
            return
        rows = self._rects_for(self._current_group)
        if not (0 <= index < len(rows)):
            return
        item = str(index)
        if self.rect_tree.exists(item):
            self.rect_tree.item(
                item, values=tuple(rows[index][column] for column in self.RECT_COLUMNS)
            )

    def _update_swatch(self) -> None:
        color = self.var_rect_color.get().strip()
        if HEX_COLOR_RE.match(color):
            try:
                self.swatch.configure(bg=color, text=" ")
            except tk.TclError:
                self.swatch.configure(bg="#FFFFFF", text="?")
        else:
            self.swatch.configure(bg="#FFFFFF", text="?")

    def _choose_rect_color(self) -> None:
        current = self.var_rect_color.get().strip()
        initial = current if HEX_COLOR_RE.match(current) else "#000000"
        try:
            _rgb, hex_value = colorchooser.askcolor(
                color=initial, parent=self.root, title="选择矩形颜色"
            )
        except tk.TclError:
            return
        if hex_value:
            self.var_rect_color.set(hex_value.upper())

    # -- structural edits ------------------------------------------------
    def _store_group_widgets(self) -> None:
        """Remember the widget state of the selected group before switching."""
        if self._loading or self._current_group is None:
            return
        self._group_meta[self._current_group] = (
            self.var_group_description.get(),
            self.var_group_resolution.get(),
        )

    def _flush(self) -> bool:
        """Stage pending group edits so structural changes see them."""
        self._store_group_widgets()
        errors: list[str] = []
        self._collect_groups(errors)
        if errors:
            self.error("请先修正分组设置：\n\n" + "\n".join(f"• {item}" for item in errors))
            return False
        return True

    def _suggested_resolution(self) -> str:
        if self._current_group is not None:
            return str(self.draft.get(("groups", self._current_group), "resolution", ""))
        names = self.draft.group_names()
        if names:
            return str(self.draft.get(("groups", names[0]), "resolution", ""))
        return ""

    def _add_group(self) -> None:
        if not self._flush():
            return
        name = simpledialog.askstring(
            "新建矩形组", "组名（只能包含字母、数字、- 和 _）：", parent=self.root
        )
        if not name:
            return
        resolution = simpledialog.askstring(
            "新建矩形组",
            "分辨率（例如 1920x1080）：",
            initialvalue=self._suggested_resolution(),
            parent=self.root,
        )
        if resolution is None:
            return
        try:
            clean_resolution = normalize_resolution(resolution)
            created = self.draft.add_group(name, resolution=clean_resolution, description="")
        except (SettingsValueError, ValueError) as exc:
            self.error(f"无法新建矩形组：{exc}")
            return
        self._group_meta[created] = ("", clean_resolution)
        self._rect_rows[created] = []
        self._refresh_group_list(select=created)
        self.status(f"已新建矩形组 {created}（保存后写入文件）")

    def _rename_group(self) -> None:
        name = self._selected_group()
        if name is None:
            self.status("请先在列表里选择一个矩形组")
            return
        if not self._flush():
            return
        new_name = simpledialog.askstring(
            "重命名矩形组", "新的组名：", initialvalue=name, parent=self.root
        )
        if not new_name or new_name == name:
            return
        try:
            renamed = self.draft.rename_group(name, new_name)
        except SettingsValueError as exc:
            self.error(f"无法重命名：{exc}")
            return
        if name in self._group_meta:
            self._group_meta[renamed] = self._group_meta.pop(name)
        if name in self._rect_rows:
            self._rect_rows[renamed] = self._rect_rows.pop(name)
        if str(self.draft.get(("layout",), "group", "")) == name:
            self.draft.set(("layout",), "group", renamed)
            self.var_group.set(renamed)
        self._current_group = renamed
        self._refresh_group_list(select=renamed)
        self.status(f"已重命名为 {renamed}（保存后写入文件）")

    def _remove_group(self) -> None:
        name = self._selected_group()
        if name is None:
            self.status("请先在列表里选择一个矩形组")
            return
        if not messagebox.askyesno(
            "删除矩形组",
            f"确定删除 [groups.{name}] 及其全部矩形吗？",
            parent=self.root,
        ):
            return
        self.draft.remove_group(name)
        self._group_meta.pop(name, None)
        self._rect_rows.pop(name, None)
        if str(self.draft.get(("layout",), "group", "")) == name:
            self.draft.set(("layout",), "group", "")
            self.var_group.set("")
        self._current_group = None
        self._refresh_group_list()
        self.status(f"已删除矩形组 {name}（保存后写入文件）")

    # -- rectangle edits -------------------------------------------------
    def _default_rect_color(self) -> str:
        color = str(self.get(("features", "toolbar"), "default_color", "#000000"))
        return color if HEX_COLOR_RE.match(color) else "#000000"

    def _add_rect(self) -> None:
        if self._current_group is None:
            self.status("请先选择一个矩形组")
            return
        rows = self._rects_for(self._current_group)
        rows.append(_default_rect_row(len(rows) + 1, self._default_rect_color()))
        self._loading = True
        try:
            self._fill_rect_tree(self._current_group)
            self._select_rect(len(rows) - 1)
        finally:
            self._loading = False

    def _duplicate_rect(self) -> None:
        if self._current_group is None or self._rect_index is None:
            self.status("请先选择一个矩形")
            return
        rows = self._rects_for(self._current_group)
        if not (0 <= self._rect_index < len(rows)):
            return
        copy = dict(rows[self._rect_index])
        copy["name"] = f"{copy['name']}_copy" if copy["name"] else "copy"
        index = self._rect_index + 1
        rows.insert(index, copy)
        self._loading = True
        try:
            self._fill_rect_tree(self._current_group)
            self._select_rect(index)
        finally:
            self._loading = False

    def _remove_rect(self) -> None:
        if self._current_group is None or self._rect_index is None:
            self.status("请先选择一个矩形")
            return
        rows = self._rects_for(self._current_group)
        if not (0 <= self._rect_index < len(rows)):
            return
        index = self._rect_index
        del rows[index]
        self._loading = True
        try:
            self._fill_rect_tree(self._current_group)
            if rows:
                self._select_rect(min(index, len(rows) - 1))
        finally:
            self._loading = False

    # -- lifecycle -------------------------------------------------------
    def on_shown(self) -> None:
        self._refresh_group_choices()

    def collect(self, errors: list[str]) -> None:
        self._store_group_widgets()
        self.draft.set(("layout",), "group", self.var_group.get().strip())
        super().collect(errors)
        self._collect_groups(errors)

    def _collect_groups(self, errors: list[str]) -> None:
        names = set(self.draft.group_names())
        for name, (description, resolution) in self._group_meta.items():
            if name not in names:
                continue
            self.draft.set(("groups", name), "description", description)
            self.draft.set(("groups", name), "resolution", resolution.strip())
        for name, rows in self._rect_rows.items():
            if name not in names:
                continue
            normalized: list[dict[str, Any]] = []
            failed = False
            for index, row in enumerate(rows):
                location = f"[groups.{name}] 第 {index + 1} 个矩形"
                values: dict[str, Any] = {}
                ok = True
                try:
                    values["name"] = row["name"].strip() or f"rectangle_{index + 1}"
                except AttributeError:
                    errors.append(f"{location}：名称无效")
                    ok = False
                for field, label, minimum in (
                    ("x", "x", None),
                    ("y", "y", None),
                    ("width", "宽", 1),
                    ("height", "高", 1),
                ):
                    try:
                        number = int(str(row[field]).strip())
                    except ValueError:
                        errors.append(f"{location} 的 {label}：需要整数")
                        ok = False
                        continue
                    if minimum is not None and number < minimum:
                        errors.append(f"{location} 的 {label}：必须 ≥ {minimum}")
                        ok = False
                        continue
                    values[field] = number
                color = row["color"].strip().upper()
                if not HEX_COLOR_RE.match(color):
                    errors.append(f'{location} 的颜色：需要 "#RRGGBB" 形式（当前为 {row["color"]!r}）')
                    ok = False
                else:
                    values["color"] = color
                try:
                    opacity = float(str(row["opacity"]).strip())
                except ValueError:
                    errors.append(f"{location} 的不透明度：需要 0.0 – 1.0 之间的数字")
                    ok = False
                else:
                    if not 0.0 <= opacity <= 1.0:
                        errors.append(f"{location} 的不透明度：必须在 0.0 – 1.0 之间")
                        ok = False
                    else:
                        values["opacity"] = opacity
                if not ok:
                    failed = True
                    continue
                normalized.append(values)
            if failed:
                continue
            try:
                if normalized != self.draft.rectangles(name):
                    self.draft.set_rectangles(name, normalized)
            except SettingsValueError as exc:
                errors.append(f"[groups.{name}] 的矩形：{exc}")


def _inline_entry(
    parent: tk.Misc, label: str, variable: tk.Variable, *, width: int = 10
) -> ttk.Entry:
    ttk.Label(parent, text=label).pack(side="left")
    entry = ttk.Entry(parent, textvariable=variable, width=width)
    entry.pack(side="left", padx=(2, 10))
    return entry


# ---------------------------------------------------------------------------
# 窗口配置


_PROFILE_FIELDS = (
    "description",
    "process_names",
    "title_patterns",
    "class_names",
    "exclude_title_patterns",
    "min_width",
    "min_height",
    "select",
)


def _new_profile_values() -> dict[str, str]:
    return {
        "description": "",
        "process_names": "",
        "title_patterns": "",
        "class_names": "",
        "exclude_title_patterns": "",
        "min_width": "0",
        "min_height": "0",
        "select": "largest",
    }


class _ProfilesTab(_Tab):
    """``[window_profiles.<name>]`` management."""

    def __init__(self, master: ttk.Notebook, window: "SettingsWindow") -> None:
        super().__init__(master, window)
        self._current_profile: str | None = None
        self._profile_values: dict[str, dict[str, str]] = {}
        self._profile_initial: dict[str, dict[str, str]] = {}

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.LabelFrame(panes, text="窗口配置", padding=8)
        right = ttk.LabelFrame(panes, text="匹配规则", padding=8)
        panes.add(left, weight=1)
        panes.add(right, weight=3)

        self.profile_tree = ttk.Treeview(
            left, columns=("name", "detail"), show="headings", selectmode="browse", height=12
        )
        self.profile_tree.heading("name", text="名称")
        self.profile_tree.heading("detail", text="进程 / 标题")
        self.profile_tree.column("name", width=120, stretch=False)
        self.profile_tree.column("detail", width=180, stretch=True)
        self.profile_tree.pack(fill="both", expand=True)
        self.profile_tree.bind("<<TreeviewSelect>>", self._on_profile_select)

        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="新建", command=self._add_profile).pack(side="left")
        ttk.Button(buttons, text="重命名", command=self._rename_profile).pack(side="left", padx=4)
        ttk.Button(buttons, text="删除", command=self._remove_profile).pack(side="left")

        self.var_description = tk.StringVar()
        self.var_process_names = tk.StringVar()
        self.var_title_patterns = tk.StringVar()
        self.var_class_names = tk.StringVar()
        self.var_exclude_titles = tk.StringVar()
        self.var_min_width = tk.StringVar()
        self.var_min_height = tk.StringVar()
        self.var_select = tk.StringVar()

        right.columnconfigure(1, weight=1)
        label_width = 20
        ttk.Label(right, text="说明", width=label_width, anchor="w").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(right, textvariable=self.var_description, width=50).grid(
            row=0, column=1, sticky="we", pady=3
        )
        for row, (label, variable, hint) in enumerate(
            (
                ("进程名（逗号分隔）", self.var_process_names, "例如 Weixin.exe, WeChat.exe"),
                ("标题正则（逗号分隔）", self.var_title_patterns, "Python 正则，re.search 语义"),
                ("窗口类名（逗号分隔）", self.var_class_names, "Win32 类名，区分大小写"),
                ("排除标题正则（逗号分隔）", self.var_exclude_titles, "命中任意一条即排除"),
            ),
            start=1,
        ):
            ttk.Label(right, text=label, width=label_width, anchor="w").grid(
                row=row, column=0, sticky="w", pady=3
            )
            ttk.Entry(right, textvariable=variable, width=50).grid(
                row=row, column=1, sticky="we", pady=3
            )
            ttk.Label(right, text=hint, foreground=_HINT_COLOR).grid(
                row=row, column=2, sticky="w", padx=(10, 0)
            )

        ttk.Label(right, text="最小宽度", width=label_width, anchor="w").grid(
            row=5, column=0, sticky="w", pady=3
        )
        ttk.Entry(right, textvariable=self.var_min_width, width=10).grid(
            row=5, column=1, sticky="w", pady=3
        )
        ttk.Label(right, text="最小高度", width=label_width, anchor="w").grid(
            row=6, column=0, sticky="w", pady=3
        )
        ttk.Entry(right, textvariable=self.var_min_height, width=10).grid(
            row=6, column=1, sticky="w", pady=3
        )
        ttk.Label(right, text="多窗口选择", width=label_width, anchor="w").grid(
            row=7, column=0, sticky="w", pady=3
        )
        ttk.Combobox(
            right,
            textvariable=self.var_select,
            values=list(SELECT_STRATEGIES),
            state="readonly",
            width=12,
        ).grid(row=7, column=1, sticky="w", pady=3)

        ttk.Label(
            right,
            text=(
                "同一个列表内的值之间是“或”，不同列表之间是“与”；\n"
                "process_names / class_names / title_patterns 至少要填写一个。\n"
                "逗号分隔的列表里不能包含逗号（正则里的逗号请写成 \\x2c）。"
            ),
            justify="left",
            foreground=_HINT_COLOR,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self._refresh_profile_list()
        names = self.draft.profile_names()
        if names:
            self._select_profile(names[0])

    # -- list / values ---------------------------------------------------
    def _refresh_profile_list(self, select: str | None = None) -> None:
        for item in self.profile_tree.get_children():
            self.profile_tree.delete(item)
        for name in self.draft.profile_names():
            table = self.draft.get_table(("window_profiles", name))
            processes = table.get("process_names") or []
            patterns = table.get("title_patterns") or []
            detail = ", ".join(str(item) for item in [*processes, *patterns])
            self.profile_tree.insert("", "end", iid=name, values=(name, detail))
        names = self.draft.profile_names()
        target = select or self._current_profile
        if target not in names:
            target = names[0] if names else None
        if target is None:
            self._current_profile = None
            self._select_profile(None)
            return
        if target == self._current_profile:
            # Rebuilding the tree dropped the selection; restore it without
            # reloading the entry fields (the user may be typing in them).
            self.profile_tree.selection_set(target)
            self.profile_tree.focus(target)
            return
        self._current_profile = None
        self._select_profile(target)

    def _values_for(self, name: str) -> dict[str, str]:
        if name not in self._profile_values:
            table = self.draft.get_table(("window_profiles", name))
            values = {
                "description": str(table.get("description", "")),
                "process_names": ", ".join(str(item) for item in table.get("process_names", [])),
                "title_patterns": ", ".join(str(item) for item in table.get("title_patterns", [])),
                "class_names": ", ".join(str(item) for item in table.get("class_names", [])),
                "exclude_title_patterns": ", ".join(
                    str(item) for item in table.get("exclude_title_patterns", [])
                ),
                "min_width": str(table.get("min_width", 0)),
                "min_height": str(table.get("min_height", 0)),
                "select": str(table.get("select", "largest")),
            }
            self._profile_values[name] = dict(values)
            self._profile_initial[name] = dict(values)
        return self._profile_values[name]

    def _variables(self) -> dict[str, tk.StringVar]:
        return {
            "description": self.var_description,
            "process_names": self.var_process_names,
            "title_patterns": self.var_title_patterns,
            "class_names": self.var_class_names,
            "exclude_title_patterns": self.var_exclude_titles,
            "min_width": self.var_min_width,
            "min_height": self.var_min_height,
            "select": self.var_select,
        }

    def _store_widgets(self) -> None:
        name = self._current_profile
        if name is None or name not in self.draft.profile_names():
            return
        values = self._values_for(name)
        for field, var in self._variables().items():
            values[field] = var.get()

    def _select_profile(self, name: str | None) -> None:
        if name == self._current_profile and name is not None:
            return
        self._current_profile = name
        if name is None:
            for var in self._variables().values():
                var.set("")
            return
        values = self._values_for(name)
        for field, var in self._variables().items():
            var.set(values.get(field, ""))
        self.profile_tree.selection_set(name)
        self.profile_tree.focus(name)

    def _on_profile_select(self, _event: object = None) -> None:
        selection = self.profile_tree.selection()
        if not selection:
            return
        name = str(selection[0])
        if name == self._current_profile:
            return
        self._store_widgets()
        self._current_profile = None
        self._select_profile(name)

    def _selected_profile(self) -> str | None:
        selection = self.profile_tree.selection()
        return str(selection[0]) if selection else None

    # -- structural edits ------------------------------------------------
    def _add_profile(self) -> None:
        self._store_widgets()
        name = simpledialog.askstring(
            "新建窗口配置", "配置名称（例如 notepad）：", parent=self.root
        )
        if not name:
            return
        try:
            created = self.draft.add_profile(name)
        except SettingsValueError as exc:
            self.error(f"无法新建窗口配置：{exc}")
            return
        self._profile_values[created] = _new_profile_values()
        self._profile_initial[created] = _new_profile_values()
        self._current_profile = None
        self._refresh_profile_list(select=created)
        self.status(f"已新建窗口配置 {created}：请至少填写进程名、类名或标题正则之一")

    def _rename_profile(self) -> None:
        name = self._selected_profile()
        if name is None:
            self.status("请先选择一个窗口配置")
            return
        self._store_widgets()
        new_name = simpledialog.askstring(
            "重命名窗口配置", "新的名称：", initialvalue=name, parent=self.root
        )
        if not new_name or new_name == name:
            return
        try:
            renamed = self.draft.rename_profile(name, new_name)
        except SettingsValueError as exc:
            self.error(f"无法重命名：{exc}")
            return
        if name in self._profile_values:
            self._profile_values[renamed] = self._profile_values.pop(name)
            self._profile_initial[renamed] = self._profile_initial.pop(name, {})
        if str(self.draft.get(("target",), "window", "")) == name:
            self.draft.set(("target",), "window", renamed)
        self._current_profile = None
        self._refresh_profile_list(select=renamed)
        self.status(f"已重命名为 {renamed}（保存后写入文件）")

    def _remove_profile(self) -> None:
        name = self._selected_profile()
        if name is None:
            self.status("请先选择一个窗口配置")
            return
        remaining = [item for item in self.draft.profile_names() if item != name]
        if not remaining:
            self.error("至少要保留一个窗口配置：ScreenPainter 需要一个目标窗口。")
            return
        if not messagebox.askyesno(
            "删除窗口配置", f"确定删除 [window_profiles.{name}] 吗？", parent=self.root
        ):
            return
        self.draft.remove_profile(name)
        self._profile_values.pop(name, None)
        self._profile_initial.pop(name, None)
        if str(self.draft.get(("target",), "window", "")) == name:
            self.draft.set(("target",), "window", remaining[0])
        self._current_profile = None
        self._refresh_profile_list()
        self.status(f"已删除窗口配置 {name}（保存后写入文件）")

    # -- lifecycle -------------------------------------------------------
    def on_shown(self) -> None:
        self._store_widgets()
        self._refresh_profile_list()

    def collect(self, errors: list[str]) -> None:
        self._store_widgets()
        super().collect(errors)
        names = set(self.draft.profile_names())
        for name, values in self._profile_values.items():
            if name not in names:
                continue
            initial = self._profile_initial.get(name, {})
            for field in _PROFILE_FIELDS:
                text = str(values.get(field, "")).strip()
                if initial.get(field, "").strip() == text:
                    continue
                if field in ("min_width", "min_height"):
                    try:
                        number = int(text or "0")
                    except ValueError:
                        errors.append(f"[window_profiles.{name}] 的 {field}：需要整数")
                        continue
                    if number < 0:
                        errors.append(f"[window_profiles.{name}] 的 {field}：必须 ≥ 0")
                        continue
                    self.draft.set(("window_profiles", name), field, number)
                elif field == "select":
                    if text not in SELECT_STRATEGIES:
                        errors.append(
                            f"[window_profiles.{name}] 的 select：只能是 "
                            + " / ".join(SELECT_STRATEGIES)
                        )
                        continue
                    self.draft.set(("window_profiles", name), field, text)
                elif field == "description":
                    self.draft.set(("window_profiles", name), field, text)
                else:
                    self.draft.set(("window_profiles", name), field, _split_list(text))


# ---------------------------------------------------------------------------
# 功能


def _feature_types(configured: Sequence[str] = ()) -> tuple[str, ...]:
    """Registered feature types plus any type the file already references."""
    try:
        load_builtin_features()
    except Exception:  # pragma: no cover - a broken feature package must not break the window
        pass
    types = set(registered_feature_types())
    types.update(str(item) for item in configured if str(item).strip())
    return tuple(sorted(types))


class _FeaturesTab(_Tab):
    """``[features.<name>]`` management, including the toolbar options."""

    def __init__(self, master: ttk.Notebook, window: "SettingsWindow") -> None:
        super().__init__(master, window)
        self._current_feature: str | None = None
        self._feature_cache: dict[str, dict[str, Any]] = {}
        self._option_order: list[str] = []
        self._option_vars: dict[str, tk.Variable] = {}
        self._option_generic: dict[str, tk.StringVar] = {}
        self._button_vars: dict[str, tk.BooleanVar] = {}
        self._button_extras: list[str] = []
        self._option_initial: dict[str, Any] = {}
        configured = [
            str(self.draft.get(("features", name), "type", ""))
            for name in self.draft.feature_names()
        ]
        self._types = _feature_types(configured)

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.LabelFrame(panes, text="功能实例", padding=8)
        right = ttk.LabelFrame(panes, text="功能设置", padding=8)
        panes.add(left, weight=1)
        panes.add(right, weight=3)

        self.feature_tree = ttk.Treeview(
            left, columns=("name", "type"), show="headings", selectmode="browse", height=10
        )
        self.feature_tree.heading("name", text="名称")
        self.feature_tree.heading("type", text="类型")
        self.feature_tree.column("name", width=120, stretch=True)
        self.feature_tree.column("type", width=110, stretch=False)
        self.feature_tree.pack(fill="both", expand=True)
        self.feature_tree.bind("<<TreeviewSelect>>", self._on_feature_select)

        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="新建", command=self._add_feature).pack(side="left")
        ttk.Button(buttons, text="删除", command=self._remove_feature).pack(side="left", padx=4)

        header = ttk.Frame(right)
        header.pack(fill="x")
        ttk.Label(header, text="名称", width=8, anchor="w").grid(row=0, column=0, sticky="w", pady=3)
        self.var_feature_name = tk.StringVar()
        ttk.Entry(header, textvariable=self.var_feature_name, width=24).grid(
            row=0, column=1, sticky="w", pady=3
        )
        ttk.Button(header, text="重命名", command=self._rename_feature).grid(
            row=0, column=2, sticky="w", padx=(6, 0)
        )
        ttk.Label(header, text="类型", width=8, anchor="w").grid(
            row=1, column=0, sticky="w", pady=3
        )
        self.var_feature_type = tk.StringVar()
        self.type_combo = ttk.Combobox(
            header,
            textvariable=self.var_feature_type,
            values=list(self._types),
            state="readonly",
            width=22,
        )
        self.type_combo.grid(row=1, column=1, sticky="w", pady=3)
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_changed)
        self.var_feature_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(header, text="启用这个功能实例", variable=self.var_feature_enabled).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=3
        )

        self.options_frame = ttk.LabelFrame(right, text="选项", padding=8)
        self.options_frame.pack(fill="both", expand=True, pady=(8, 0))

        self._refresh_feature_list()
        names = self.draft.feature_names()
        if names:
            self._select_feature(names[0])

    # -- cache -----------------------------------------------------------
    def _load_feature(self, name: str) -> dict[str, Any]:
        table = self.draft.get_table(("features", name))
        feature_type = str(table.get("type", ""))
        options: dict[str, Any] = {}
        for key, value in table.items():
            if key in ("type", "enabled"):
                continue
            kind = TOOLBAR_OPTION_KINDS.get(key) if feature_type == "toolbar" else None
            options[key] = _option_cache_value(key, kind, value)
        return {
            "type": feature_type,
            "enabled": bool(table.get("enabled", True)),
            "options": options,
            "initial": dict(options),
        }

    def _entry_for(self, name: str) -> dict[str, Any]:
        if name not in self._feature_cache:
            self._feature_cache[name] = self._load_feature(name)
        return self._feature_cache[name]

    def _refresh_feature_list(self, select: str | None = None) -> None:
        for item in self.feature_tree.get_children():
            self.feature_tree.delete(item)
        for name in self.draft.feature_names():
            cached = self._feature_cache.get(name)
            feature_type = (
                str(cached["type"])
                if cached is not None
                else str(self.draft.get(("features", name), "type", ""))
            )
            self.feature_tree.insert("", "end", iid=name, values=(name, feature_type))
        names = self.draft.feature_names()
        target = select or self._current_feature
        if target not in names:
            target = names[0] if names else None
        if target is not None:
            self._select_feature(target)
        else:
            self._select_feature(None)

    def _select_feature(self, name: str | None) -> None:
        self._current_feature = name
        if name is None:
            self.var_feature_name.set("")
            self.var_feature_type.set("")
            self.var_feature_enabled.set(False)
            self._build_options()
            return
        entry = self._entry_for(name)
        self.var_feature_name.set(name)
        self.var_feature_type.set(str(entry["type"]))
        self.var_feature_enabled.set(bool(entry["enabled"]))
        self.feature_tree.selection_set(name)
        self.feature_tree.focus(name)
        self._build_options()

    def _on_feature_select(self, _event: object = None) -> None:
        selection = self.feature_tree.selection()
        if not selection:
            return
        name = str(selection[0])
        if name == self._current_feature:
            return
        self._flush_widgets()
        self._select_feature(name)

    def _on_type_changed(self, _event: object = None) -> None:
        if self._current_feature is None:
            return
        entry = self._entry_for(self._current_feature)
        # The options panel was built for the type it rendered; comparing
        # against that (not against the freshly flushed value) is what makes a
        # type change rebuild the panel.
        rendered_type = str(entry.get("rendered_type", entry["type"]))
        self._flush_widgets()
        new_type = self.var_feature_type.get().strip()
        if not new_type or new_type == rendered_type:
            return
        entry["type"] = new_type
        if new_type == "toolbar":
            for key, default in TOOLBAR_OPTION_DEFAULTS.items():
                entry["options"].setdefault(
                    key, list(default) if isinstance(default, list) else default
                )
        self._build_options()
        self.status(f"已把 {self._current_feature} 的类型改为 {new_type}（保存后生效）")

    def _flush_widgets(self) -> None:
        if self._current_feature is None:
            return
        entry = self._feature_cache.get(self._current_feature)
        if entry is None:
            return
        typed = self.var_feature_type.get().strip()
        if typed:
            entry["type"] = typed
        entry["enabled"] = bool(self.var_feature_enabled.get())
        options = entry["options"]
        is_toolbar = entry["type"] == "toolbar"
        for key in list(self._option_order):
            if is_toolbar and key == "buttons" and self._button_vars:
                checked = [button for button in TOOLBAR_BUTTONS if self._button_vars[button].get()]
                options[key] = checked + [item for item in self._button_extras if item not in checked]
                continue
            kind = TOOLBAR_OPTION_KINDS.get(key) if is_toolbar else None
            var = self._option_vars.get(key)
            if var is not None and kind == "bool":
                options[key] = bool(var.get())
                continue
            if var is not None:
                options[key] = str(var.get())
                continue
            generic = self._option_generic.get(key)
            if generic is not None:
                options[key] = str(generic.get())

    # -- options UI ------------------------------------------------------
    def _build_options(self) -> None:
        for child in self.options_frame.winfo_children():
            child.destroy()
        self._option_order = []
        self._option_vars = {}
        self._option_generic = {}
        self._button_vars = {}
        self._button_extras = []
        self._option_initial = {}
        name = self._current_feature
        if name is None:
            ttk.Label(
                self.options_frame, text="请选择一个功能实例", foreground=_HINT_COLOR
            ).pack(anchor="w")
            return
        entry = self._entry_for(name)
        is_toolbar = entry["type"] == "toolbar"
        entry["rendered_type"] = str(entry["type"])
        options = entry["options"]
        if is_toolbar:
            for key, default in TOOLBAR_OPTION_DEFAULTS.items():
                options.setdefault(key, list(default) if isinstance(default, list) else default)
        if is_toolbar:
            ordered = [key for key in TOOLBAR_OPTION_KINDS if key in options]
            ordered += [key for key in options if key not in TOOLBAR_OPTION_KINDS]
        else:
            ordered = list(options)
        self._option_order = ordered

        row = 0
        for key in ordered:
            value = options.get(key)
            kind = TOOLBAR_OPTION_KINDS.get(key) if is_toolbar else None
            if key == "buttons" and is_toolbar:
                self._build_buttons_row(row, key, value)
            elif kind == "bool":
                var = tk.BooleanVar(value=bool(value))
                ttk.Checkbutton(self.options_frame, text=key, variable=var).grid(
                    row=row, column=0, columnspan=3, sticky="w", pady=2
                )
                self._option_vars[key] = var
            elif kind in ("int", "float", "str"):
                text = _number_text(value) if kind in ("int", "float") else str(value)
                var = tk.StringVar(value=text)
                ttk.Label(self.options_frame, text=key, width=20, anchor="w").grid(
                    row=row, column=0, sticky="w", pady=2
                )
                width = 40 if kind == "str" else 12
                ttk.Entry(self.options_frame, textvariable=var, width=width).grid(
                    row=row, column=1, sticky="w", pady=2
                )
                if key.endswith("color"):
                    ttk.Label(
                        self.options_frame, text='"#RRGGBB"', foreground=_HINT_COLOR
                    ).grid(row=row, column=2, sticky="w", padx=(8, 0))
                self._option_vars[key] = var
            else:
                text = _toml_text(value)
                var = tk.StringVar(value=text)
                ttk.Label(self.options_frame, text=key, width=20, anchor="w").grid(
                    row=row, column=0, sticky="w", pady=2
                )
                ttk.Entry(self.options_frame, textvariable=var, width=52).grid(
                    row=row, column=1, sticky="we", pady=2
                )
                ttk.Label(
                    self.options_frame, text="TOML 值", foreground=_HINT_COLOR
                ).grid(row=row, column=2, sticky="w", padx=(8, 0))
                self._option_generic[key] = var
            self._option_initial[key] = _option_cache_value(key, kind, value)
            row += 1
        self.options_frame.columnconfigure(1, weight=1)
        if not ordered:
            ttk.Label(
                self.options_frame,
                text="这个功能类型没有选项；如需添加功能，请在 [features] 下新建实例。",
                foreground=_HINT_COLOR,
            ).grid(row=0, column=0, sticky="w")
        entry["initial"] = dict(self._option_initial)

    def _build_buttons_row(self, row: int, key: str, value: Any) -> None:
        items = [str(item) for item in value] if isinstance(value, (list, tuple)) else []
        self._button_extras = [item for item in items if item not in TOOLBAR_BUTTONS]
        ttk.Label(self.options_frame, text=key, width=20, anchor="w").grid(
            row=row, column=0, sticky="w", pady=2
        )
        holder = ttk.Frame(self.options_frame)
        holder.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        self._button_vars = {}
        for button in TOOLBAR_BUTTONS:
            var = tk.BooleanVar(value=button in items)
            ttk.Checkbutton(holder, text=button, variable=var).pack(side="left", padx=(0, 6))
            self._button_vars[button] = var
        if self._button_extras:
            ttk.Label(
                holder,
                text="（文件里还有：" + ", ".join(self._button_extras) + "，保存时保留）",
                foreground=_HINT_COLOR,
            ).pack(side="left", padx=(6, 0))

    # -- structural edits ------------------------------------------------
    def _selected_feature(self) -> str | None:
        selection = self.feature_tree.selection()
        return str(selection[0]) if selection else None

    def _add_feature(self) -> None:
        self._flush_widgets()
        name = simpledialog.askstring(
            "新建功能实例", "实例名称（例如 toolbar）：", parent=self.root
        )
        if not name:
            return
        feature_type = self._types[0] if self._types else "black_rectangles"
        try:
            created = self.draft.add_feature(name, feature_type=feature_type)
        except SettingsValueError as exc:
            self.error(f"无法新建功能实例：{exc}")
            return
        self._feature_cache[created] = {
            "type": feature_type,
            "enabled": True,
            "options": {},
            "initial": {},
        }
        self._refresh_feature_list(select=created)
        self.status(f"已新建功能实例 {created}（类型 {feature_type}）")

    def _rename_feature(self) -> None:
        name = self._selected_feature()
        if name is None:
            self.status("请先选择一个功能实例")
            return
        self._flush_widgets()
        new_name = simpledialog.askstring(
            "重命名功能实例", "新的名称：", initialvalue=name, parent=self.root
        )
        if not new_name or new_name == name:
            return
        self._rename_current(name, new_name)

    def _rename_current(self, new_name: str, errors: list[str] | None = None) -> str | None:
        old = self._current_feature
        if old is None:
            return None
        try:
            renamed = self.draft.rename_feature(old, new_name)
        except SettingsValueError as exc:
            if errors is not None:
                errors.append(f"功能名称：{exc}")
            else:
                self.error(f"无法重命名：{exc}")
            return None
        self._feature_cache[renamed] = self._feature_cache.pop(old)
        self._current_feature = renamed
        self.var_feature_name.set(renamed)
        self._refresh_feature_list(select=renamed)
        return renamed

    def _remove_feature(self) -> None:
        name = self._selected_feature()
        if name is None:
            self.status("请先选择一个功能实例")
            return
        if not messagebox.askyesno(
            "删除功能实例", f"确定删除 [features.{name}] 吗？", parent=self.root
        ):
            return
        self.draft.remove_feature(name)
        self._feature_cache.pop(name, None)
        self._current_feature = None
        self._refresh_feature_list()
        self.status(f"已删除功能实例 {name}（保存后写入文件）")

    # -- lifecycle -------------------------------------------------------
    def on_shown(self) -> None:
        self._flush_widgets()
        self._refresh_feature_list()

    def collect(self, errors: list[str]) -> None:
        self._flush_widgets()
        super().collect(errors)
        names = set(self.draft.feature_names())
        for name, entry in self._feature_cache.items():
            if name not in names:
                continue
            section = ("features", name)
            feature_type = str(entry["type"])
            self.draft.set(section, "type", feature_type)
            self.draft.set(section, "enabled", bool(entry["enabled"]))
            is_toolbar = feature_type == "toolbar"
            initial = entry.get("initial", {})
            for key, value in entry["options"].items():
                if key in initial and initial[key] == value:
                    continue
                kind = TOOLBAR_OPTION_KINDS.get(key) if is_toolbar else None
                try:
                    if kind == "bool":
                        parsed: Any = bool(value)
                    elif kind == "str_list":
                        parsed = (
                            [str(item) for item in value]
                            if isinstance(value, (list, tuple))
                            else _split_list(str(value))
                        )
                    elif kind == "int":
                        parsed = int(str(value).strip())
                    elif kind == "float":
                        parsed = float(str(value).strip())
                    elif kind == "str":
                        parsed = str(value)
                    else:
                        parsed = parse_toml_value(str(value))
                except (TypeError, ValueError):
                    errors.append(f"[features.{name}] 的 {key}：需要数字")
                    continue
                except SettingsValueError as exc:
                    errors.append(f"[features.{name}] 的 {key}：{exc}")
                    continue
                self.draft.set(section, key, parsed)
        # A name typed into the entry field renames the section.  It is applied
        # last so the option edits above were staged under the old name and move
        # with the table.
        typed = self.var_feature_name.get().strip()
        if self._current_feature is not None and typed and typed != self._current_feature:
            self._rename_current(typed, errors)


# ---------------------------------------------------------------------------
# 快捷键


class _KeysTab(_Tab):
    """One capture field per action in ``[keys]``."""

    def __init__(self, master: ttk.Notebook, window: "SettingsWindow") -> None:
        super().__init__(master, window)
        self._vars: dict[str, tk.StringVar] = {}
        header = ttk.Frame(self)
        header.pack(fill="x")
        for column, (text, width) in enumerate(
            (("动作", 16), ("说明", 14), ("用途", 34), ("按键（点击后按下组合键）", 24))
        ):
            ttk.Label(header, text=text, width=width, anchor="w").grid(
                row=0, column=column, sticky="w", padx=(0, 6)
            )

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(4, 0))
        for row, (action, (default, label, help_text)) in enumerate(ACTIONS.items(), start=1):
            ttk.Label(body, text=action, width=16, anchor="w", font=_MONOSPACE).grid(
                row=row, column=0, sticky="w", padx=(0, 6), pady=2
            )
            ttk.Label(body, text=label, width=14, anchor="w").grid(
                row=row, column=1, sticky="w", padx=(0, 6), pady=2
            )
            ttk.Label(
                body, text=help_text, width=34, anchor="w", foreground=_HINT_COLOR
            ).grid(row=row, column=2, sticky="w", padx=(0, 6), pady=2)
            var = tk.StringVar(value=display_binding(self.get(("keys",), action, default)))
            entry = _KeyCaptureEntry(
                body,
                on_binding=lambda text, name=action: self._record(name, text),
                on_message=self.status,
            )
            entry.configure(textvariable=var)
            entry.grid(row=row, column=3, sticky="w", padx=(0, 6), pady=2)
            ttk.Button(
                body,
                text="清除",
                command=lambda name=action: self._reset(name),
            ).grid(row=row, column=4, sticky="w", pady=2)
            self._vars[action] = var

        ttk.Label(
            self,
            text=(
                "点击按键输入框后直接按下组合键即可记录（单独按修饰键会被忽略）；"
                "“清除”恢复默认值。\n"
                "Win 组合键无法在这里捕获，请直接在 TOML 里写（例如 \"Win+Z\"）。"
            ),
            justify="left",
            foreground=_HINT_COLOR,
        ).pack(anchor="w", pady=(8, 0))

    def _record(self, action: str, text: str) -> None:
        try:
            binding = parse_binding(text)
        except KeyBindingError as exc:
            self.status(f"这个组合键无法使用：{exc}")
            return
        canonical = binding.to_config()
        self._vars[action].set(canonical)
        self.draft.set(("keys",), action, canonical)
        self.status(f"已记录 {ACTION_LABELS.get(action, action)} = {canonical}（保存后生效）")

    def _reset(self, action: str) -> None:
        default = DEFAULT_BINDINGS[action]
        self._vars[action].set(display_binding(default))
        try:
            canonical = parse_binding(default).to_config()
        except KeyBindingError:  # pragma: no cover - defaults are always valid
            canonical = default
        self.draft.set(("keys",), action, canonical)
        self.status(f"已恢复默认快捷键 {ACTION_LABELS.get(action, action)} = {canonical}（保存后生效）")

    def collect(self, errors: list[str]) -> None:
        # Bindings are staged immediately when captured; nothing to collect.
        super().collect(errors)


# ---------------------------------------------------------------------------
# The window


class SettingsWindow:
    """The whole settings window: notebook, bottom bar, save/reload logic."""

    def __init__(self, draft: SettingsDraft, *, startup_warning: str | None = None) -> None:
        self.draft = draft
        self.startup_warning = startup_warning
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        # No theme is forced: the platform default is used.
        self.status = tk.StringVar(value="就绪")
        self._tabs: list[_Tab] = []
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.root.geometry(INITIAL_SIZE)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self._build()

    # -- construction ----------------------------------------------------
    def _build(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        self._tabs = []
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        hint = ttk.Label(
            outer,
            text=(
                f"直接编辑：{self.draft.path}\n"
                "保存前会用与主程序相同的规则校验；保存后如果 ScreenPainter 正在运行并且 "
                "[app] debug = true，配置会自动热重载，否则重启程序后生效。"
            ),
            justify="left",
            foreground=_HINT_COLOR,
            wraplength=980,
        )
        hint.pack(fill="x")

        if self.startup_warning:
            warning = ttk.Label(
                outer,
                text="当前配置未通过校验：" + self.startup_warning.strip().splitlines()[0],
                justify="left",
                foreground=_ERROR_COLOR,
                wraplength=980,
            )
            warning.pack(fill="x", pady=(4, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True, pady=(8, 8))
        for tab_class, title in (
            (_GeneralTab, "常规"),
            (_TargetTab, "目标窗口"),
            (_LoggingTab, "日志"),
            (_LayoutGroupsTab, "布局与分组"),
            (_ProfilesTab, "窗口配置"),
            (_FeaturesTab, "功能"),
            (_KeysTab, "快捷键"),
        ):
            tab = tab_class(self.notebook, self)
            self.notebook.add(tab, text=title)
            self._tabs.append(tab)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        ttk.Button(bar, text="保存", command=self.save).pack(side="left")
        ttk.Button(bar, text="重新载入", command=self.reload).pack(side="left", padx=6)
        ttk.Button(bar, text="关闭", command=self.close).pack(side="right")
        ttk.Label(bar, textvariable=self.status, foreground=_OK_COLOR).pack(
            side="left", padx=12
        )

    def _on_tab_changed(self, _event: object = None) -> None:
        try:
            widget = self.notebook.nametowidget(self.notebook.select())
        except (tk.TclError, KeyError):
            return
        on_shown = getattr(widget, "on_shown", None)
        if callable(on_shown):
            on_shown()

    # -- status / errors -------------------------------------------------
    def set_status(self, message: str) -> None:
        first_line = message.strip().splitlines()[0] if message.strip() else ""
        self.status.set(first_line[:240])

    def show_error(self, title: str, message: str) -> None:
        self.set_status(f"{title}：{message}".replace("\n", " "))
        print(f"[settings] {title}: {message}", file=sys.stderr)
        try:
            messagebox.showerror(title, message, parent=self.root)
        except tk.TclError:  # pragma: no cover - headless fallback
            pass

    # -- commands --------------------------------------------------------
    def save(self) -> None:
        errors: list[str] = []
        for tab in self._tabs:
            try:
                tab.collect(errors)
            except SettingsValueError as exc:
                errors.append(str(exc))
            except Exception as exc:  # pragma: no cover - a GUI bug must not crash the window
                errors.append(f"{type(tab).__name__}：{exc}")
        if errors:
            self.show_error("无法保存", "请先修正以下问题：\n\n" + "\n".join(f"• {item}" for item in errors))
            return
        try:
            written = self.draft.save()
        except SettingsValidationError as exc:
            self.show_error("保存失败：配置校验未通过", str(exc))
            return
        except (ConfigWriteError, ConfigError, ScreenPainterError) as exc:
            self.show_error("保存失败", str(exc))
            return
        except OSError as exc:  # pragma: no cover - filesystem problem
            self.show_error("保存失败", str(exc))
            return
        self.startup_warning = None
        self._build()
        self.status.set("已保存" if written else "已保存（没有需要写入的改动）")
        print("[settings] 已保存配置；运行中的 ScreenPainter 会在热重载后应用（否则重启生效）。")

    def reload(self) -> None:
        if self.draft.changed() and not messagebox.askyesno(
            "重新载入", "放弃所有未保存的修改并重新从磁盘读取？", parent=self.root
        ):
            return
        try:
            self.draft.reload()
        except ConfigError as exc:
            self.show_error("无法重新载入", str(exc))
            return
        self.startup_warning = None
        self._build()
        self.status.set("已重新载入（未保存的修改已丢弃）")

    def close(self) -> None:
        if self.draft.changed() and not messagebox.askyesno(
            "关闭", "还有未保存的修改，确定关闭吗？", parent=self.root
        ):
            return
        self.root.destroy()

    # -- main loop -------------------------------------------------------
    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry point


def _report_startup_error(message: str) -> None:
    print(f"[settings] 无法读取配置：\n{message}", file=sys.stderr)
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(WINDOW_TITLE, f"无法读取配置文件：\n\n{message}")
        root.destroy()
    except tk.TclError:  # pragma: no cover - no display
        pass


def main(config_path: Path | str | None = None) -> int:
    """Open the settings window and block until it is closed.

    Returns ``0`` after a normal close and ``2`` when the configuration file
    cannot be loaded or the window cannot be created.  The file is edited
    directly, so a running ScreenPainter picks the change up through hot reload
    (``[app] debug = true``) or after a restart.
    """
    path = Path(config_path).expanduser() if config_path is not None else DEFAULT_CONFIG_PATH
    print(f"[settings] 配置文件：{path.resolve()}")
    print(
        "[settings] 保存后：如果 ScreenPainter 正在运行并且 [app] debug = true，配置会自动"
        "热重载；否则重启程序后生效。"
    )
    try:
        draft = SettingsDraft.from_path(path)
    except ConfigError as exc:
        _report_startup_error(str(exc))
        return 2
    except OSError as exc:  # pragma: no cover - unexpected filesystem problem
        _report_startup_error(str(exc))
        return 2

    warning: str | None = None
    try:
        load_config(path)
    except ConfigError as exc:
        warning = str(exc)

    try:
        window = SettingsWindow(draft, startup_warning=warning)
    except tk.TclError as exc:  # pragma: no cover - no display
        print(f"[settings] 无法创建窗口：{exc}", file=sys.stderr)
        return 2
    window.run()
    return 0


if __name__ == "__main__":  # pragma: no cover - manual launch
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    raise SystemExit(main(source))
