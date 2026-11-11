"""Load and validate the TOML configuration.

Only Python's standard library is used (``tomllib`` requires Python 3.11+).
Validation is deliberately strict: typos in keys should fail loudly at startup
instead of silently producing a half-configured overlay.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Pattern

from .core.display import (
    RESOLUTION_MATCHES,
    RESOLUTION_MODES,
    ResolutionError,
    normalize_resolution,
)
from .core.keymap import KeyBindingError, KeyMap
from .core.layout import LayoutGroup, LayoutSettings, Shape
from .core.surface import Color
from .core.window import CoordinateSpace, SelectStrategy, WindowProfile
from .errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.toml"

SCHEMA_VERSION = 3
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_SELECT_STRATEGIES = {"largest", "first"}
_COORDINATE_SPACES = {"window", "client"}
_GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

_MISSING = object()


@dataclass(frozen=True, slots=True)
class AppConfig:
    name: str = "ScreenPainter"
    # Tracking is event driven; this is only the fallback re-check interval.
    verify_ms: int = 1000
    # How often the target window is searched for while it is absent.
    idle_tick_ms: int = 500
    debug: bool = False
    debug_interval_ms: int = 400


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str = "INFO"
    console: bool = True
    file: str | None = "logs/screenpainter.log"
    max_bytes: int = 1_000_000
    backup_count: int = 3


@dataclass(frozen=True, slots=True)
class OverlayConfig:
    coordinate_space: CoordinateSpace = "window"
    click_through: bool = True


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    name: str
    type: str
    enabled: bool
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Config:
    path: Path
    app: AppConfig
    logging: LoggingConfig
    target_profile_name: str
    target_profile: WindowProfile
    window_profiles: Mapping[str, WindowProfile]
    overlay: OverlayConfig
    features: tuple[FeatureConfig, ...]
    layout: LayoutSettings
    groups: tuple[LayoutGroup, ...]
    keys: KeyMap = field(default_factory=KeyMap.default)
    project_root: Path = PROJECT_ROOT

    def find_group(self, name: str) -> LayoutGroup | None:
        for group in self.groups:
            if group.name == name:
                return group
        return None


def load_config(path: str | Path | None = None) -> Config:
    """Read, parse and validate a config file."""
    config_path = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    else:
        config_path = config_path.resolve()

    if not config_path.exists():
        raise ConfigError(
            f"configuration file not found: {config_path}\n"
            f"Start from the example at {DEFAULT_CONFIG_PATH}"
        )
    if not config_path.is_file():
        raise ConfigError(f"configuration path is not a file: {config_path}")

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
        raise ConfigError("configuration root must be a TOML table")

    _reject_unknown(
        raw,
        {
            "version",
            "app",
            "logging",
            "target",
            "overlay",
            "window_profiles",
            "features",
            "layout",
            "groups",
            "keys",
        },
        "root",
    )

    version = raw.get("version", SCHEMA_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError(
            f"'version' must be the integer {SCHEMA_VERSION} (got {version!r})"
        )
    if version != SCHEMA_VERSION:
        hint = ""
        if version == 1:
            hint = (
                "\nVersion 1 stored rectangles inside [features.<name>]; version 2 "
                "moves them into [groups.<name>] and adds [layout].  See docs/CONFIG.md "
                "for the new schema, or restore config/config.toml from the example."
            )
        elif version == 2:
            hint = (
                "\nVersion 3 adds the [keys] table for every editable shortcut and "
                "replaces app.tick_ms with app.verify_ms (tracking is event "
                "driven).  See docs/CONFIG.md, or restore config/config.toml "
                "from the example."
            )
        raise ConfigError(
            f"'version' must be {SCHEMA_VERSION} (got {version}); this build only "
            f"understands schema version {SCHEMA_VERSION}.{hint}"
        )

    app = _parse_app(raw.get("app", {}))
    keys = _parse_keys(raw.get("keys", {}))
    logging_config = _parse_logging(raw.get("logging", {}))
    overlay = _parse_overlay(raw.get("overlay", {}))
    groups = _parse_groups(raw.get("groups", {}))
    layout = _parse_layout(raw.get("layout", {}), groups)

    profiles_table = _require_table(
        raw.get("window_profiles"), "window_profiles", allow_missing=False
    )
    if not profiles_table:
        raise ConfigError("'window_profiles' must contain at least one window profile")
    profiles: dict[str, WindowProfile] = {}
    for profile_name, profile_value in profiles_table.items():
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ConfigError("window profile names must be non-empty strings")
        profiles[profile_name] = _parse_profile(profile_name, profile_value)

    target_table = _require_table(raw.get("target"), "target", allow_missing=False)
    _reject_unknown(target_table, {"window"}, "target")
    target_name = target_table.get("window")
    if not isinstance(target_name, str) or not target_name.strip():
        raise ConfigError("'target.window' must name an entry under [window_profiles]")
    if target_name not in profiles:
        choices = ", ".join(sorted(profiles)) or "<none>"
        raise ConfigError(
            f"target.window is {target_name!r}, but [window_profiles] defines: {choices}"
        )

    features = _parse_features(raw.get("features", {}))
    _validate_feature_shape(features)

    return Config(
        path=config_path,
        app=app,
        logging=logging_config,
        target_profile_name=target_name,
        target_profile=profiles[target_name],
        window_profiles=profiles,
        overlay=overlay,
        features=features,
        layout=layout,
        groups=groups,
        keys=keys,
    )


# ---------------------------------------------------------------------------
# Section parsers


def _parse_app(value: Any) -> AppConfig:
    table = _require_table(value, "app", allow_missing=True)
    _reject_unknown(
        table,
        {"name", "verify_ms", "idle_tick_ms", "debug", "debug_interval_ms"},
        "app",
    )
    name = _string(table.get("name", "ScreenPainter"), "app.name")
    if not name.strip():
        raise ConfigError("'app.name' must not be empty")
    verify_ms = _integer(
        table.get("verify_ms", 1000), "app.verify_ms", minimum=100, maximum=10000
    )
    idle_tick_ms = _integer(
        table.get("idle_tick_ms", 500), "app.idle_tick_ms", minimum=50, maximum=10000
    )
    debug = _boolean(table.get("debug", False), "app.debug")
    debug_interval_ms = _integer(
        table.get("debug_interval_ms", 400),
        "app.debug_interval_ms",
        minimum=100,
        maximum=5000,
    )
    return AppConfig(
        name=name,
        verify_ms=verify_ms,
        idle_tick_ms=idle_tick_ms,
        debug=debug,
        debug_interval_ms=debug_interval_ms,
    )


def _parse_keys(value: Any) -> KeyMap:
    table = _require_table(value, "keys", allow_missing=True)
    try:
        return KeyMap.from_mapping(table)
    except KeyBindingError as exc:
        raise ConfigError(str(exc)) from exc


def _parse_logging(value: Any) -> LoggingConfig:
    table = _require_table(value, "logging", allow_missing=True)
    _reject_unknown(
        table, {"level", "console", "file", "max_bytes", "backup_count"}, "logging"
    )
    level = _string(table.get("level", "INFO"), "logging.level").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError(
            f"'logging.level' must be one of {', '.join(sorted(_LOG_LEVELS))} (got {level!r})"
        )
    console = _boolean(table.get("console", True), "logging.console")
    raw_file = table.get("file", "logs/screenpainter.log")
    if not isinstance(raw_file, str):
        raise ConfigError("'logging.file' must be a string (empty string disables file logging)")
    normalized_file = raw_file.strip() or None
    max_bytes = _integer(
        table.get("max_bytes", 1_000_000), "logging.max_bytes", minimum=1024, maximum=1_000_000_000
    )
    backup_count = _integer(
        table.get("backup_count", 3), "logging.backup_count", minimum=0, maximum=100
    )
    return LoggingConfig(
        level=level,
        console=console,
        file=normalized_file,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )


def _parse_overlay(value: Any) -> OverlayConfig:
    table = _require_table(value, "overlay", allow_missing=True)
    _reject_unknown(table, {"coordinate_space", "click_through"}, "overlay")
    space = _string(table.get("coordinate_space", "window"), "overlay.coordinate_space")
    if space not in _COORDINATE_SPACES:
        raise ConfigError(
            "'overlay.coordinate_space' must be 'window' or 'client' "
            f"(got {space!r})"
        )
    click_through = _boolean(table.get("click_through", True), "overlay.click_through")
    return OverlayConfig(coordinate_space=space, click_through=click_through)  # type: ignore[arg-type]


_RECTANGLE_KEYS = {"name", "x", "y", "width", "height", "color", "opacity"}
_GROUP_KEYS = {"description", "resolution", "rectangles"}


def _parse_groups(value: Any) -> tuple[LayoutGroup, ...]:
    table = _require_table(value, "groups", allow_missing=True)
    groups: list[LayoutGroup] = []
    for group_name, group_value in table.items():
        if not isinstance(group_name, str) or not _GROUP_NAME_RE.match(group_name):
            raise ConfigError(
                f"group name {group_name!r} must contain only letters, digits, "
                "'-' and '_' (letters/digits first)"
            )
        location = f"groups.{group_name}"
        section = _require_table(group_value, location, allow_missing=False)
        _reject_unknown(section, _GROUP_KEYS, location)
        description = _string(section.get("description", ""), f"{location}.description")
        raw_resolution = section.get("resolution", _MISSING)
        if raw_resolution is _MISSING:
            raise ConfigError(
                f"[{location}] is missing required key 'resolution' (for example "
                '"1920x1080"); it is what the automatic group matching compares'
            )
        try:
            resolution = normalize_resolution(
                _string(raw_resolution, f"{location}.resolution")
            )
        except ResolutionError as exc:
            raise ConfigError(f"'{location}.resolution': {exc}") from exc
        shapes = _parse_rectangles(section.get("rectangles", []), location)
        groups.append(
            LayoutGroup(
                name=group_name,
                resolution=resolution,
                description=description,
                shapes=shapes,
            )
        )
    return tuple(groups)


def _parse_rectangles(value: Any, location: str) -> tuple[Shape, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"'{location}.rectangles' must be an array of tables")
    shapes: list[Shape] = []
    for index, raw in enumerate(value):
        item_location = f"{location}.rectangles[{index}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"'{item_location}' must be a TOML table")
        unknown = sorted(set(raw) - _RECTANGLE_KEYS)
        if unknown:
            raise ConfigError(
                f"unknown key(s) at '{item_location}': "
                + ", ".join(repr(item) for item in unknown)
                + f"; allowed: {', '.join(sorted(_RECTANGLE_KEYS))}"
            )

        name_value = raw.get("name", f"rectangle_{index + 1}")
        if not isinstance(name_value, str) or not name_value.strip():
            raise ConfigError(f"'{item_location}.name' must be a non-empty string")

        def require_int(key: str, *, minimum: int | None = None) -> int:
            item = raw.get(key)
            if isinstance(item, bool) or not isinstance(item, int):
                raise ConfigError(
                    f"'{item_location}.{key}' must be an integer (got {item!r})"
                )
            if minimum is not None and item < minimum:
                raise ConfigError(
                    f"'{item_location}.{key}' must be >= {minimum} (got {item})"
                )
            return item

        x = require_int("x")
        y = require_int("y")
        width = require_int("width", minimum=1)
        height = require_int("height", minimum=1)

        color_text = raw.get("color", "#000000")
        if not isinstance(color_text, str):
            raise ConfigError(f"'{item_location}.color' must be a string like '#000000'")
        opacity = raw.get("opacity", 1.0)
        if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
            raise ConfigError(
                f"'{item_location}.opacity' must be a number between 0 and 1"
            )
        if not (0.0 <= float(opacity) <= 1.0):
            raise ConfigError(f"'{item_location}.opacity' must be between 0 and 1")
        try:
            Color.from_hex(color_text, float(opacity))
        except ValueError as exc:
            raise ConfigError(f"'{item_location}': {exc}") from exc

        shapes.append(
            Shape(
                x=x,
                y=y,
                width=width,
                height=height,
                name=name_value.strip(),
                color=color_text,
                opacity=float(opacity),
            )
        )
    return tuple(shapes)


def _parse_layout(value: Any, groups: tuple[LayoutGroup, ...]) -> LayoutSettings:
    table = _require_table(value, "layout", allow_missing=True)
    _reject_unknown(table, {"mode", "match", "group", "hidden"}, "layout")

    mode = _string(table.get("mode", "auto"), "layout.mode")
    if mode not in RESOLUTION_MODES:
        raise ConfigError(
            "'layout.mode' must be 'auto' or 'manual' (got "
            f"{mode!r}); 'auto' picks a group by resolution, 'manual' uses "
            "'layout.group'"
        )
    match = _string(table.get("match", "monitor"), "layout.match")
    if match not in RESOLUTION_MATCHES:
        raise ConfigError(
            "'layout.match' must be 'monitor' or 'window' (got "
            f"{match!r}); 'monitor' compares the display resolution, 'window' "
            "compares the target window size"
        )
    group = _string(table.get("group", ""), "layout.group").strip()
    hidden = _boolean(table.get("hidden", False), "layout.hidden")

    if mode == "manual":
        if not group:
            raise ConfigError(
                "'layout.group' must name a [groups.<name>] table when "
                "layout.mode = 'manual'"
            )
        if not any(item.name == group for item in groups):
            available = ", ".join(item.name for item in groups) or "<none>"
            raise ConfigError(
                f"layout.group is {group!r}, but [groups] defines: {available}"
            )
    elif group and not any(item.name == group for item in groups):
        available = ", ".join(item.name for item in groups) or "<none>"
        raise ConfigError(
            f"layout.group is {group!r}, but [groups] defines: {available}; "
            "leave it empty or fix the name"
        )

    return LayoutSettings(mode=mode, match=match, group=group, hidden=hidden)  # type: ignore[arg-type]


_PROFILE_KEYS = {
    "description",
    "process_names",
    "title_patterns",
    "class_names",
    "exclude_title_patterns",
    "min_width",
    "min_height",
    "select",
}


def _parse_profile(name: str, value: Any) -> WindowProfile:
    location = f"window_profiles.{name}"
    table = _require_table(value, location, allow_missing=False)
    _reject_unknown(table, _PROFILE_KEYS, location)

    description = _string(table.get("description", ""), f"{location}.description")
    process_names = _string_list(
        table.get("process_names", []), f"{location}.process_names"
    )
    class_names = _string_list(table.get("class_names", []), f"{location}.class_names")
    title_patterns = _pattern_list(table.get("title_patterns", []), f"{location}.title_patterns")
    exclude_title_patterns = _pattern_list(
        table.get("exclude_title_patterns", []), f"{location}.exclude_title_patterns"
    )
    if not process_names and not class_names and not title_patterns:
        raise ConfigError(
            f"[{location}] needs at least one of process_names, class_names or title_patterns; "
            "an empty profile would match every visible window"
        )

    min_width = _integer(table.get("min_width", 0), f"{location}.min_width", minimum=0)
    min_height = _integer(table.get("min_height", 0), f"{location}.min_height", minimum=0)
    select = _string(table.get("select", "largest"), f"{location}.select")
    if select not in _SELECT_STRATEGIES:
        raise ConfigError(
            f"'{location}.select' must be 'largest' or 'first' (got {select!r})"
        )

    return WindowProfile(
        name=name,
        description=description,
        process_names=tuple(process_names),
        title_patterns=tuple(title_patterns),
        class_names=tuple(class_names),
        exclude_title_patterns=tuple(exclude_title_patterns),
        min_width=min_width,
        min_height=min_height,
        select=select,  # type: ignore[arg-type]
    )


def _parse_features(value: Any) -> tuple[FeatureConfig, ...]:
    table = _require_table(value, "features", allow_missing=True)
    features: list[FeatureConfig] = []
    for instance_name, instance_value in table.items():
        if not isinstance(instance_name, str) or not instance_name.strip():
            raise ConfigError("feature instance names must be non-empty strings")
        location = f"features.{instance_name}"
        instance = _require_table(instance_value, location, allow_missing=False)
        feature_type = instance.get("type", _MISSING)
        if feature_type is _MISSING:
            raise ConfigError(f"[{location}] is missing required key 'type'")
        if not isinstance(feature_type, str) or not feature_type.strip():
            raise ConfigError(f"'{location}.type' must be a non-empty string")
        enabled = _boolean(instance.get("enabled", True), f"{location}.enabled")
        options = {
            key: val
            for key, val in instance.items()
            if key not in {"type", "enabled"}
        }
        features.append(
            FeatureConfig(
                name=instance_name,
                type=feature_type.strip(),
                enabled=enabled,
                options=options,
            )
        )
    return tuple(features)


def _validate_feature_shape(features: tuple[FeatureConfig, ...]) -> None:
    # The actual type registry is checked after feature packages are imported;
    # this generic check simply catches duplicate instance names early.
    seen: set[str] = set()
    for feature in features:
        if feature.name in seen:
            raise ConfigError(f"duplicate feature instance {feature.name!r}")
        seen.add(feature.name)


# ---------------------------------------------------------------------------
# Validation helpers


def _require_table(value: Any, location: str, *, allow_missing: bool) -> dict[str, Any]:
    if value is _MISSING or value is None:
        if allow_missing:
            return {}
        raise ConfigError(f"missing required table [{location}]")
    if not isinstance(value, dict):
        raise ConfigError(f"[{location}] must be a TOML table")
    return value


def _reject_unknown(table: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        joined = ", ".join(repr(item) for item in unknown)
        raise ConfigError(
            f"unknown key(s) in [{location}]: {joined}; allowed: {', '.join(sorted(allowed))}"
        )


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"'{location}' must be a string (got {type(value).__name__})")
    return value


def _boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"'{location}' must be true or false (got {value!r})")
    return value


def _integer(
    value: Any,
    location: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{location}' must be an integer (got {value!r})")
    if minimum is not None and value < minimum:
        raise ConfigError(f"'{location}' must be >= {minimum} (got {value})")
    if maximum is not None and value > maximum:
        raise ConfigError(f"'{location}' must be <= {maximum} (got {value})")
    return value


def _string_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"'{location}' must be an array of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"'{location}[{index}]' must be a non-empty string")
        result.append(item.strip())
    return result


def _pattern_list(value: Any, location: str) -> list[Pattern[str]]:
    items = _string_list(value, location)
    result: list[Pattern[str]] = []
    for index, pattern_text in enumerate(items):
        try:
            result.append(re.compile(pattern_text))
        except re.error as exc:
            raise ConfigError(
                f"invalid regular expression at '{location}[{index}]': "
                f"{pattern_text!r}: {exc}"
            ) from exc
    return result
