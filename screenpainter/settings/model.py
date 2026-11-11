"""Headless model behind the ScreenPainter settings window.

The settings window has to edit *every* value of the TOML configuration file
without destroying the hand written comments, and it has to guarantee that an
edit can never leave the file in a state the application cannot load.  This
module owns all of that logic and deliberately does **not** import tkinter, so
the complete load / stage / validate / save pipeline is unit testable without a
desktop.

How an edit session works:

1. :meth:`SettingsDraft.from_path` reads the file with :mod:`tomllib` into a
   nested dict (``draft.raw``) and keeps an untouched snapshot.
2. The window stages changes in ``draft.raw`` through :meth:`SettingsDraft.set`
   and the section helpers.  Nothing is written yet.
3. :meth:`SettingsDraft.save` plans the difference between the snapshot and the
   working copy, applies *only* those values to a fresh
   :class:`screenpainter.toml_edit.TomlDocument` (which preserves comments),
   serialises the result and validates it by writing a temporary file next to
   the configuration and calling
   :func:`screenpainter.config.load_config` on it.  The real file is written
   only after that validation succeeded; a rejected state raises
   :class:`SettingsValidationError` and the original file stays untouched.

The document is therefore never rewritten as a whole: scalars and string arrays
go through :meth:`TomlDocument.set_value`, rectangles through
:meth:`TomlDocument.set_inline_table_array`, and added / removed tables through
:meth:`TomlDocument.create_section` / :meth:`TomlDocument.remove_section`.
"""

from __future__ import annotations

import copy
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, MutableMapping, Sequence

from screenpainter.config import DEFAULT_CONFIG_PATH, load_config
from screenpainter.core.display import RESOLUTION_MATCHES, RESOLUTION_MODES
from screenpainter.errors import ConfigError, ConfigWriteError, ScreenPainterError
from screenpainter.toml_edit import TomlDocument, format_value

__all__ = [
    "COORDINATE_SPACES",
    "Field",
    "FieldKind",
    "HEX_COLOR_RE",
    "LAYOUT_MATCHES",
    "LAYOUT_MODES",
    "LOG_LEVELS",
    "SELECT_STRATEGIES",
    "SettingsDraft",
    "SettingsValidationError",
    "SettingsValueError",
    "TOOLBAR_BUTTONS",
    "TOOLBAR_OPTION_DEFAULTS",
    "TOOLBAR_OPTION_KINDS",
    "collect_fields",
    "field_kind",
    "format_toml_value",
    "load_raw",
    "parse_toml_value",
    "validate_group_name",
    "validate_table_name",
]


# ---------------------------------------------------------------------------
# Schema knowledge
#
# These constants mirror the validation rules in screenpainter.config.  They are
# duplicated here (instead of importing private helpers) so the window can offer
# combobox choices without reaching into another module's internals.  Values that
# config.py exports publicly are derived from it directly to avoid drift.

#: ``logging.level`` choices, in log severity order.
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
#: ``overlay.coordinate_space`` choices.
COORDINATE_SPACES: tuple[str, ...] = ("window", "client")
#: ``<profile>.select`` choices.
SELECT_STRATEGIES: tuple[str, ...] = ("largest", "first")
#: ``layout.mode`` choices.
LAYOUT_MODES: tuple[str, ...] = tuple(sorted(RESOLUTION_MODES))
#: ``layout.match`` choices.
LAYOUT_MATCHES: tuple[str, ...] = tuple(sorted(RESOLUTION_MATCHES))

#: Sections that only contain scalar / array values.
SCALAR_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("app",),
    ("logging",),
    ("target",),
    ("overlay",),
    ("layout",),
    ("keys",),
)

#: Sections whose children are ``[<parent>.<name>]`` tables.
TABLE_PARENTS: tuple[str, ...] = ("groups", "window_profiles", "features")

#: ``[features.toolbar] buttons`` choices, in the canonical display order.
TOOLBAR_BUTTONS: tuple[str, ...] = ("edit", "move", "hide", "settings")

FieldKind = Literal[
    "str",
    "int",
    "float",
    "bool",
    "str_list",
    "number_list",
    "table_array",
    "table",
    "other",
]

#: Type of every option the built in ``toolbar`` feature understands.  Unknown
#: option keys are still editable in the window through a generic TOML value
#: field, so a feature that grows options stays fully editable.
TOOLBAR_OPTION_KINDS: dict[str, FieldKind] = {
    "buttons": "str_list",
    "auto_position": "bool",
    "x": "int",
    "y": "int",
    "default_color": "str",
    "default_opacity": "float",
    "min_drag_size": "int",
    "title": "str",
    "outline_color": "str",
    "outline_width": "int",
    "selection_color": "str",
    "selection_width": "int",
    "handle_size": "int",
    "handle_tolerance": "int",
    "properties_panel": "bool",
}

#: Display defaults used when a *new* toolbar instance does not define an option
#: yet.  They mirror ``config/config.toml``; the window only writes a value when
#: the user actually changes the field.
TOOLBAR_OPTION_DEFAULTS: dict[str, Any] = {
    "buttons": list(TOOLBAR_BUTTONS),
    "auto_position": False,
    "x": 0,
    "y": 0,
    "default_color": "#000000",
    "default_opacity": 1.0,
    "min_drag_size": 3,
    "title": "屏幕遮罩",
    "outline_color": "#FF3B30",
    "outline_width": 1,
    "selection_color": "#0A84FF",
    "selection_width": 3,
    "handle_size": 7,
    "handle_tolerance": 6,
    "properties_panel": True,
}

#: ``[groups.<name>]`` names must stay valid TOML keys (config.py rule).
GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
#: ``[window_profiles.<name>]`` / ``[features.<name>]`` names.  Dots are allowed
#: because the editor quotes non bare keys; quotes and brackets are not.
TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
#: ``#RRGGBB`` (config.py / ``Color.from_hex`` rule).
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_MISSING = object()


# ---------------------------------------------------------------------------
# Errors


class SettingsValueError(ScreenPainterError, ValueError):
    """A value entered in the settings window cannot be stored in the config.

    The message is shown verbatim to the user, so it names the offending key.
    """


class SettingsValidationError(ConfigError):
    """The staged configuration does not pass ``load_config`` validation.

    The original configuration file is guaranteed to be untouched when this is
    raised; the message is the exact ``load_config`` error with the temporary
    validation file path replaced by the real configuration path.
    """


# ---------------------------------------------------------------------------
# Field introspection


@dataclass(frozen=True, slots=True)
class Field:
    """One editable configuration value.

    ``section`` is the table path without the key, e.g. ``("app",)`` or
    ``("groups", "wechat_1920x1080")``; ``key`` is the last path component.
    """

    section: tuple[str, ...]
    key: str
    value: Any
    kind: FieldKind = "other"

    @property
    def path(self) -> str:
        """Dotted path, for example ``"app.verify_ms"``."""
        return ".".join((*self.section, self.key))


def field_kind(value: Any) -> FieldKind:
    """Classify one configuration value for the generic editors.

    Empty arrays are reported as ``"str_list"`` because TOML itself loses the
    element type; the settings window knows that ``rectangles`` is a table
    array from the section it lives in.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, Mapping):
        return "table"
    if isinstance(value, (list, tuple)):
        items = list(value)
        if not items:
            return "str_list"
        if all(isinstance(item, Mapping) for item in items):
            return "table_array"
        if all(isinstance(item, str) for item in items):
            return "str_list"
        if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in items):
            return "number_list"
        return "other"
    return "other"


def collect_fields(raw: Mapping[str, Any]) -> tuple[Field, ...]:
    """Return every value of *raw* with its section path, key and type.

    The root ``version`` key is included for completeness; the settings window
    displays it but never edits it (it is schema metadata, not a preference).
    """
    fields: list[Field] = []
    version = raw.get("version", _MISSING)
    if version is not _MISSING:
        fields.append(Field((), "version", version, field_kind(version)))
    for section in SCALAR_SECTIONS:
        table = raw.get(section[0])
        if not isinstance(table, Mapping):
            continue
        for key, value in table.items():
            if isinstance(value, Mapping):
                # Nested tables (for example [features.x.y]) are not exposed as
                # flat fields; their parent table is handled below.
                continue
            fields.append(Field(section, str(key), value, field_kind(value)))
    for parent in TABLE_PARENTS:
        tables = raw.get(parent)
        if not isinstance(tables, Mapping):
            continue
        for name, table in tables.items():
            if not isinstance(table, Mapping):
                continue
            for key, value in table.items():
                fields.append(
                    Field((parent, str(name)), str(key), value, field_kind(value))
                )
    return tuple(fields)


# ---------------------------------------------------------------------------
# Generic TOML value helpers (used for option keys the window does not know)

def format_toml_value(value: Any) -> str:
    """Render *value* as TOML source text, including inline tables.

    :func:`screenpainter.toml_edit.format_value` only understands scalars and
    arrays, which is all the application writes.  The settings window also has
    to show option values it does not know (arrays of inline tables, nested
    tables), so this wrapper adds inline tables around it.
    """
    if isinstance(value, Mapping):
        parts = []
        for key, item in value.items():
            name = str(key)
            rendered = name if _BARE_KEY_RE.match(name) else format_value(name)
            parts.append(f"{rendered} = {format_toml_value(item)}")
        return "{ " + ", ".join(parts) + " }"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_toml_value(item) for item in value) + "]"
    return format_value(value)


def parse_toml_value(text: str) -> Any:
    """Parse a single TOML value (``42``, ``"x"``, ``["a"]``, ``{ a = 1 }``)."""
    stripped = str(text).strip()
    if not stripped:
        raise SettingsValueError("value must not be empty")
    try:
        # A one line inline expression is a valid TOML document on its own.
        return tomllib.loads(f"value = {stripped}")["value"]
    except tomllib.TOMLDecodeError as exc:
        raise SettingsValueError(f"cannot parse TOML value {stripped!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Name validation


def validate_group_name(name: str) -> str:
    """Validate a ``[groups.<name>]`` table name and return it stripped."""
    stripped = str(name).strip()
    if not GROUP_NAME_RE.match(stripped):
        raise SettingsValueError(
            f"invalid rectangle group name {stripped!r}: use letters, digits, "
            "'-' and '_', starting with a letter or digit"
        )
    return stripped


def validate_table_name(name: str, parent: str = "window_profiles") -> str:
    """Validate a ``[<parent>.<name>]`` table name and return it stripped."""
    stripped = str(name).strip()
    if not TABLE_NAME_RE.match(stripped):
        raise SettingsValueError(
            f"invalid [{parent}] table name {stripped!r}: use letters, digits, "
            "'-', '_' and '.', starting with a letter or digit"
        )
    return stripped


# ---------------------------------------------------------------------------
# Raw loading


def load_raw(path: str | Path | None = None) -> dict[str, Any]:
    """Read a TOML configuration file into a nested dict.

    UTF-8 with an optional BOM is accepted (same rule as
    :func:`screenpainter.config.load_config`).  ``path=None`` uses the project's
    default configuration file.
    """
    config_path = (
        Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
    )
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")
    try:
        raw_bytes = config_path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read configuration file {config_path}: {exc}") from exc
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"configuration file is not valid UTF-8: {config_path}: {exc}"
        ) from exc
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}:\n{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"configuration root in {config_path} must be a TOML table")
    return raw


# ---------------------------------------------------------------------------
# Internal helpers


def _dotted(section: Sequence[str], key: str) -> str:
    return ".".join((*section, key))


def _table_or_empty(raw: Mapping[str, Any], section: Sequence[str]) -> Mapping[str, Any]:
    table: Any = raw
    for part in section:
        if not isinstance(table, Mapping):
            return {}
        table = table.get(part, {})
    return table if isinstance(table, Mapping) else {}


@dataclass(frozen=True, slots=True)
class _ChangePlan:
    """Exactly what a save would write, computed from the staged difference."""

    writes: tuple[tuple[tuple[str, ...], str, Any], ...] = ()
    removals: tuple[tuple[tuple[str, ...], str], ...] = ()
    sections_added: tuple[tuple[str, ...], ...] = ()
    sections_removed: tuple[tuple[str, ...], ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.writes or self.removals or self.sections_added or self.sections_removed
        )

    def paths(self) -> tuple[str, ...]:
        paths = {_dotted(section, key) for section, key, _value in self.writes}
        paths |= {_dotted(section, key) for section, key in self.removals}
        paths |= {".".join(section) for section in self.sections_added}
        paths |= {".".join(section) for section in self.sections_removed}
        return tuple(sorted(paths))


# ---------------------------------------------------------------------------
# The draft


class SettingsDraft:
    """A staged, comment preserving edit session for one configuration file.

    Typical use::

        draft = SettingsDraft.from_path(path)
        draft.set(("app",), "name", "Masker")
        draft.set_rectangles("wechat_1920x1080", rows)
        draft.save()          # validates first, then writes

    ``draft.raw`` is the mutable working copy; the snapshot taken by
    :meth:`from_path` / :meth:`reload` is what :meth:`changed` and :meth:`save`
    compare against.
    """

    def __init__(self, path: Path, raw: dict[str, Any]) -> None:
        """Use :meth:`from_path`; this constructor expects already loaded data."""
        self._path = Path(path)
        self._raw = raw
        self._base = copy.deepcopy(raw)
        # Keys the user removed on purpose; a plain dict difference cannot tell
        # "removed" from "never existed", and the editor must not delete keys
        # the window never showed.
        self._deletions: set[tuple[tuple[str, ...], str]] = set()

    # -- construction / lifecycle ---------------------------------------
    @classmethod
    def from_path(cls, path: str | Path | None = None) -> "SettingsDraft":
        """Load *path* (default: ``config/config.toml``) into a new draft."""
        config_path = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
        config_path = config_path.resolve()
        return cls(config_path, load_raw(config_path))

    @property
    def path(self) -> Path:
        """Absolute path of the edited configuration file."""
        return self._path

    @property
    def raw(self) -> MutableMapping[str, Any]:
        """The mutable working copy of the parsed configuration."""
        return self._raw

    def reload(self) -> None:
        """Discard every staged change and re-read the file from disk."""
        raw = load_raw(self._path)
        self._raw = raw
        self._base = copy.deepcopy(raw)
        self._deletions.clear()

    # -- reading ---------------------------------------------------------
    def get(self, section: Sequence[str], key: str, default: Any = None) -> Any:
        """Return ``section.key`` from the staged state (or *default*)."""
        table = _table_or_empty(self._raw, section)
        return table.get(key, default)

    def get_table(self, section: Sequence[str]) -> Mapping[str, Any]:
        """Return a shallow copy of one table in the staged state."""
        return dict(_table_or_empty(self._raw, section))

    def table_names(self, parent: str) -> tuple[str, ...]:
        """Names of the ``[<parent>.<name>]`` tables, in file order."""
        tables = self._raw.get(parent)
        if not isinstance(tables, Mapping):
            return ()
        return tuple(str(name) for name, value in tables.items() if isinstance(value, Mapping))

    def group_names(self) -> tuple[str, ...]:
        """Names of every ``[groups.<name>]`` table."""
        return self.table_names("groups")

    def profile_names(self) -> tuple[str, ...]:
        """Names of every ``[window_profiles.<name>]`` table."""
        return self.table_names("window_profiles")

    def feature_names(self) -> tuple[str, ...]:
        """Names of every ``[features.<name>]`` table."""
        return self.table_names("features")

    def rectangles(self, group: str) -> list[dict[str, Any]]:
        """Rectangles of one group as a list of plain dicts (a deep copy)."""
        value = self.get(("groups", group), "rectangles", [])
        if not isinstance(value, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping):
                rows.append(dict(item))
        return rows

    # -- staging scalar values ------------------------------------------
    def set(self, section: Sequence[str], key: str, value: Any) -> None:
        """Stage one value (any TOML value the editor can write)."""
        table = self._table(section, create=True)
        table[key] = value
        self._deletions.discard((tuple(section), key))

    def update_table(self, section: Sequence[str], values: Mapping[str, Any]) -> None:
        """Stage several scalar values of one table at once."""
        for key, value in values.items():
            self.set(section, key, value)

    def delete(self, section: Sequence[str], key: str) -> None:
        """Stage the removal of one key (written as a real key deletion)."""
        table = _table_or_empty(self._raw, section)
        if key in table:
            target = self._table(section)
            if target is not None:
                del target[key]
        self._deletions.add((tuple(section), key))

    def set_rectangles(self, group: str, rows: Sequence[Mapping[str, Any]]) -> None:
        """Stage the complete rectangle list of one group."""
        self.set(("groups", group), "rectangles", [dict(row) for row in rows])

    # -- staging structural changes --------------------------------------
    def add_group(
        self,
        name: str,
        *,
        resolution: str,
        description: str = "",
        rectangles: Sequence[Mapping[str, Any]] = (),
    ) -> str:
        """Create a new empty (or prefilled) ``[groups.<name>]`` table."""
        clean = validate_group_name(name)
        groups = self._table(("groups",), create=True)
        if clean in groups:
            raise SettingsValueError(f"[groups.{clean}] already exists")
        groups[clean] = {
            "description": description,
            "resolution": resolution,
            "rectangles": [dict(row) for row in rectangles],
        }
        return clean

    def remove_group(self, name: str) -> None:
        """Remove a whole ``[groups.<name>]`` table."""
        groups = self._table(("groups",))
        if groups is not None:
            groups.pop(name, None)

    def rename_group(self, old: str, new: str) -> str:
        """Rename ``[groups.<old>]`` to ``[groups.<new>]`` (comment loss is documented)."""
        clean = validate_group_name(new)
        self._rename_table("groups", old, clean)
        return clean

    def add_profile(
        self,
        name: str,
        *,
        description: str = "",
        process_names: Sequence[str] = (),
        class_names: Sequence[str] = (),
        title_patterns: Sequence[str] = (),
        exclude_title_patterns: Sequence[str] = (),
        min_width: int = 0,
        min_height: int = 0,
        select: str = "largest",
    ) -> str:
        """Create a new ``[window_profiles.<name>]`` table.

        Selectors default to empty, which ``load_config`` rejects on save: the
        window then shows that message so the user knows a selector is needed.
        """
        clean = validate_table_name(name, "window_profiles")
        profiles = self._table(("window_profiles",), create=True)
        if clean in profiles:
            raise SettingsValueError(f"[window_profiles.{clean}] already exists")
        profiles[clean] = {
            "description": description,
            "process_names": [str(item) for item in process_names],
            "title_patterns": [str(item) for item in title_patterns],
            "class_names": [str(item) for item in class_names],
            "exclude_title_patterns": [str(item) for item in exclude_title_patterns],
            "min_width": int(min_width),
            "min_height": int(min_height),
            "select": select,
        }
        return clean

    def remove_profile(self, name: str) -> None:
        """Remove a whole ``[window_profiles.<name>]`` table."""
        profiles = self._table(("window_profiles",))
        if profiles is not None:
            profiles.pop(name, None)

    def rename_profile(self, old: str, new: str) -> str:
        """Rename ``[window_profiles.<old>]`` to ``[window_profiles.<new>]``."""
        clean = validate_table_name(new, "window_profiles")
        self._rename_table("window_profiles", old, clean)
        return clean

    def add_feature(
        self,
        name: str,
        *,
        feature_type: str,
        enabled: bool = True,
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Create a new ``[features.<name>]`` table."""
        clean = validate_table_name(name, "features")
        features = self._table(("features",), create=True)
        if clean in features:
            raise SettingsValueError(f"[features.{clean}] already exists")
        table: dict[str, Any] = {"type": feature_type, "enabled": bool(enabled)}
        for key, value in (options or {}).items():
            table[str(key)] = copy.deepcopy(value)
        features[clean] = table
        return clean

    def remove_feature(self, name: str) -> None:
        """Remove a whole ``[features.<name>]`` table."""
        features = self._table(("features",))
        if features is not None:
            features.pop(name, None)

    def rename_feature(self, old: str, new: str) -> str:
        """Rename ``[features.<old>]`` to ``[features.<new>]``."""
        clean = validate_table_name(new, "features")
        self._rename_table("features", old, clean)
        return clean

    # -- change detection / saving ---------------------------------------
    def changed(self) -> bool:
        """True when the staged state would write something to the file."""
        return not self._plan().is_empty()

    def changes(self) -> tuple[str, ...]:
        """Dotted paths of every value a save would write, sorted."""
        return self._plan().paths()

    def preview_text(self) -> str:
        """Return the TOML text the staged state would write (nothing written)."""
        document = TomlDocument.from_path(self._path)
        self.apply_to_document(document)
        return document.text()

    def validate(self) -> None:
        """Validate the staged state without writing the real file.

        Raises :class:`SettingsValidationError` (a :class:`ConfigError`) when
        the edited configuration would not load.
        """
        self._validate_text(self.preview_text())

    def save(self) -> bool:
        """Validate the staged edits and, if valid, write them to the file.

        Returns ``True`` when the file was written, ``False`` when there was
        nothing to write.  Raises :class:`SettingsValidationError` when the
        edited configuration is invalid -- the original file is untouched in
        that case -- and :class:`ConfigWriteError` / :class:`ConfigError` when
        the file cannot be read or replaced.

        The document is re-read from disk before the changes are applied so
        values another writer changed in the meantime (the toolbar writes the
        same file) are preserved for every field the settings window did not
        touch.
        """
        if not self.changed():
            return False
        document = TomlDocument.from_path(self._path)
        self.apply_to_document(document)
        text = document.text()
        self._validate_text(text)
        written = document.write(self._path)
        self.reload()
        return written

    # -- applying the plan to a TomlDocument -----------------------------
    def apply_to_document(self, document: TomlDocument) -> bool:
        """Apply the staged difference to *document*; return True if it changed.

        Only values that differ from the loaded snapshot are touched, which is
        what keeps the surrounding comments and formatting intact.
        """
        plan = self._plan()
        changed = False
        for section in plan.sections_removed:
            changed = document.remove_section(section) or changed
        for section in plan.sections_added:
            changed = document.create_section(section, {}) or changed
        for section, key, value in plan.writes:
            changed = self._write_value(document, section, key, value) or changed
        for section, key in plan.removals:
            changed = document.remove_key(section, key) or changed
        return changed

    # -- internals -------------------------------------------------------
    def _table(
        self, section: Sequence[str], *, create: bool = False
    ) -> MutableMapping[str, Any] | None:
        table: Any = self._raw
        walked: list[str] = []
        for part in section:
            if not isinstance(table, MutableMapping):
                raise SettingsValueError(
                    f"[{'.'.join(walked) or '<root>'}] is not a table in the configuration"
                )
            walked.append(part)
            nxt = table.get(part)
            if nxt is None:
                if not create:
                    return None
                nxt = {}
                table[part] = nxt
            if not isinstance(nxt, MutableMapping):
                raise SettingsValueError(
                    f"[{'.'.join(walked)}] is not a table in the configuration"
                )
            table = nxt
        return table  # type: ignore[return-value]

    def _rename_table(self, parent: str, old: str, new: str) -> None:
        if old == new:
            return
        table = self._table((parent,))
        if table is None or old not in table:
            raise SettingsValueError(f"[{parent}.{old}] does not exist")
        if new in table:
            raise SettingsValueError(f"[{parent}.{new}] already exists")
        items = list(table.items())
        table.clear()
        for key, value in items:
            table[new if key == old else key] = value

    def _plan(self) -> _ChangePlan:
        writes: list[tuple[tuple[str, ...], str, Any]] = []
        removals: list[tuple[tuple[str, ...], str]] = []
        added: list[tuple[str, ...]] = []
        removed: list[tuple[str, ...]] = []

        for section in SCALAR_SECTIONS:
            base = _table_or_empty(self._base, section)
            current = _table_or_empty(self._raw, section)
            for key, value in current.items():
                if isinstance(value, Mapping):
                    continue
                if key not in base or base[key] != value:
                    writes.append((section, str(key), value))
            for key in base:
                if key not in current and (section, key) in self._deletions:
                    removals.append((section, str(key)))

        for parent in TABLE_PARENTS:
            base_tables = _table_or_empty(self._base, (parent,))
            current_tables = _table_or_empty(self._raw, (parent,))
            for name, current_table in current_tables.items():
                if not isinstance(current_table, Mapping):
                    continue
                section = (parent, str(name))
                if name not in base_tables:
                    added.append(section)
                    for key, value in current_table.items():
                        writes.append((section, str(key), value))
                    continue
                base_table = base_tables.get(name)
                if not isinstance(base_table, Mapping):
                    base_table = {}
                for key, value in current_table.items():
                    if key not in base_table or base_table[key] != value:
                        writes.append((section, str(key), value))
                for key in base_table:
                    if key not in current_table and (section, key) in self._deletions:
                        removals.append((section, str(key)))
            for name in base_tables:
                if name not in current_tables:
                    removed.append((parent, str(name)))

        return _ChangePlan(
            writes=tuple(writes),
            removals=tuple(removals),
            sections_added=tuple(added),
            sections_removed=tuple(removed),
        )

    @staticmethod
    def _write_value(
        document: TomlDocument, section: Sequence[str], key: str, value: Any
    ) -> bool:
        if isinstance(value, (list, tuple)) and value and all(
            isinstance(row, Mapping) for row in value
        ):
            empty_comment = (
                "no rectangles yet; draw one with the floating toolbar"
                if key == "rectangles"
                else None
            )
            return document.set_inline_table_array(
                section,
                key,
                [dict(row) for row in value],
                empty_comment=empty_comment,
            )
        if isinstance(value, Mapping):
            raise SettingsValueError(
                f"cannot edit nested table '{_dotted(section, key)}' from the "
                "settings window; edit the TOML file instead"
            )
        if isinstance(value, tuple):
            value = list(value)
        return document.set_value(section, key, value)

    def _validate_text(self, text: str) -> None:
        """Validate *text* through a temporary file next to the configuration."""
        path = self._path
        handle = -1
        temp_path: Path | None = None
        try:
            try:
                handle, temp_name = tempfile.mkstemp(
                    dir=str(path.parent),
                    prefix=f"{path.name}.settings-check.",
                    suffix=".toml",
                )
            except OSError as exc:
                raise ConfigWriteError(
                    f"cannot create a temporary file next to {path}: {exc}"
                ) from exc
            temp_path = Path(temp_name)
            stream = os.fdopen(handle, "w", encoding="utf-8", newline="\n")
            handle = -1
            with stream:
                stream.write(text)
            try:
                load_config(temp_path)
            except ConfigError as exc:
                message = str(exc).replace(str(temp_path), str(path))
                raise SettingsValidationError(message) from exc
        finally:
            if handle != -1:
                try:
                    os.close(handle)
                except OSError:
                    pass
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
