"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

from screenpainter import __version__
from screenpainter.app import ScreenPainterApp
from screenpainter.config import Config, DEFAULT_CONFIG_PATH, FeatureConfig, load_config
from screenpainter.core.display import (
    DisplayInfo,
    MonitorInfo,
    normalize_resolution,
    window_signature,
)
from screenpainter.core.layout import (
    LayoutGroup,
    save_app_values,
    save_feature_options,
    save_layout_values,
    save_new_group,
)
from screenpainter.errors import (
    ConfigError,
    ConfigWriteError,
    FeatureError,
    PlatformError,
    ScreenPainterError,
)
from screenpainter.logging_setup import configure_logging, get_logger
from screenpainter.platform import create_window_service
from screenpainter.platform.base import WindowService

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_TOOLBAR_TYPE = "toolbar"
_KNOWN_BUTTONS = ("edit", "move", "hide", "settings")
#: Older configurations used "draw"; it is accepted as an alias for "edit".
_BUTTON_ALIASES = {"draw": "edit"}

_ON_OFF = {"on", "off"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="screenpainter",
        description=(
            "Config-driven overlay that draws on a native Windows window. "
            "The default configuration targets WeChat/Weixin."
        ),
        epilog=(
            "Configuration commands (they edit the TOML file in place):\n"
            "  python run.py --list-monitors\n"
            "  python run.py --list-groups\n"
            "  python run.py --monitor 0\n"
            "  python run.py --monitor 0 --apply-group office\n"
            "  python run.py --new-group office --monitor 0\n"
            "  python run.py --layout auto\n"
            "  python run.py --shapes off\n"
            "  python run.py --toolbar off\n"
            "  python run.py --buttons draw,move,hide\n"
            "  python run.py --debug on\n"
            "  python run.py --settings\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"TOML configuration file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--list-windows",
        action="store_true",
        help="list visible top-level windows and exit (does not need a config)",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="resolve target.window from the config and exit without drawing",
    )
    parser.add_argument(
        "--duration",
        type=_positive_seconds,
        default=None,
        metavar="SECONDS",
        help="run for this many seconds then exit (useful for smoke tests)",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=_LOG_LEVELS,
        default=None,
        help="override logging.level from the configuration",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="open the settings window for every configuration value and exit",
    )

    admin = parser.add_argument_group("configuration commands")
    admin.add_argument(
        "--list-monitors",
        action="store_true",
        help="list the connected displays (index, device, resolution, DPI) and exit",
    )
    admin.add_argument(
        "--list-groups",
        action="store_true",
        help="list [groups.*] rectangle groups and which one matches the current display",
    )
    admin.add_argument(
        "--monitor",
        type=int,
        metavar="N",
        default=None,
        help="select display N (see --list-monitors); prints the groups for its resolution",
    )
    admin.add_argument(
        "--apply-group",
        metavar="NAME",
        default=None,
        help="use [groups.NAME] explicitly ([layout] mode = manual, group = NAME)",
    )
    admin.add_argument(
        "--new-group",
        metavar="NAME",
        default=None,
        help=(
            "create an empty [groups.NAME] for the selected display resolution and "
            "use it (several groups may share a resolution)"
        ),
    )
    admin.add_argument(
        "--layout",
        choices=("auto", "manual"),
        default=None,
        help="auto = pick the group by resolution, manual = keep [layout] group",
    )
    admin.add_argument(
        "--shapes",
        choices=("on", "off"),
        default=None,
        help="show or hide every saved rectangle ([layout] hidden)",
    )
    admin.add_argument(
        "--toolbar",
        choices=("on", "off"),
        default=None,
        help="show or hide the floating button panel ([features.toolbar] enabled)",
    )
    admin.add_argument(
        "--buttons",
        metavar="LIST",
        default=None,
        help="comma separated toolbar buttons, for example draw,move,hide",
    )
    admin.add_argument(
        "--debug",
        choices=("on", "off"),
        default=None,
        help="enable or disable configuration hot reload ([app] debug)",
    )
    return parser


def _positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from exc
    if not math.isfinite(parsed) or not parsed > 0:
        raise argparse.ArgumentTypeError("duration must be a finite number greater than zero")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.settings:
            return _open_settings(args.config or DEFAULT_CONFIG_PATH)
        if args.list_windows:
            return _list_windows()
        if args.list_monitors:
            return _list_monitors()
        config = load_config(args.config)
        if _wants_admin_action(args):
            return _run_admin(config, args)
        return _run_configured(config, args)
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2
    except ConfigWriteError as exc:
        print(f"Cannot update the configuration:\n{exc}", file=sys.stderr)
        return 2
    except PlatformError as exc:
        print(f"Platform error:\n{exc}", file=sys.stderr)
        return 2
    except FeatureError as exc:
        print(f"Feature error:\n{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 0


def _run_configured(
    config: Config, args: argparse.Namespace
) -> int:
    logger = configure_logging(
        config.logging,
        config.project_root,
        level_override=args.log_level,
    )
    try:
        service = create_window_service()
    except PlatformError as exc:
        logger.error("%s", exc)
        return 2

    if args.detect:
        return _detect(config, service, logger)

    app = ScreenPainterApp(config, service, logger=get_logger("app"))
    try:
        return app.run(duration=args.duration)
    except ScreenPainterError as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("unexpected fatal error")
        return 1


# ---------------------------------------------------------------------------
# Read-only inspection


def _open_settings(path: Path) -> int:
    """Open the Tkinter settings window for ``path`` (blocks until closed)."""
    try:
        from screenpainter.settings.app import main as settings_main
    except ImportError as exc:  # pragma: no cover - tkinter missing
        print(f"the settings window needs tkinter: {exc}", file=sys.stderr)
        return 2
    return int(settings_main(path))


def _list_windows() -> int:
    try:
        service = create_window_service()
    except PlatformError as exc:
        print(f"Platform error:\n{exc}", file=sys.stderr)
        return 2
    windows = service.list_windows()
    if not windows:
        print("No visible top-level windows were found.")
        return 0
    print("Visible top-level windows (z-order, topmost first; own process excluded)")
    print(
        f"{'HWND':>10}  {'PID':>7}  {'PROCESS':<20}  {'CLASS':<28}  "
        f"{'BOUNDS':<22}  {'FLAGS':<8}  TITLE"
    )
    for item in windows:
        flags = []
        if item.minimized:
            flags.append("min")
        if item.cloaked:
            flags.append("cloak")
        print(
            f"0x{item.hwnd:08X}  {item.pid:>7}  "
            f"{(item.process_name or '?')[:20]:<20}  "
            f"{item.class_name[:28]:<28}  "
            f"{str(item.window_bounds):<22}  "
            f"{','.join(flags):<8}  {item.title}"
        )
    return 0


def _display_info(service: WindowService) -> DisplayInfo:
    return DisplayInfo(
        monitors=tuple(service.list_monitors()),
        virtual_bounds=service.virtual_desktop(),
    )


def _list_monitors() -> int:
    try:
        service = create_window_service()
    except PlatformError as exc:
        print(f"Platform error:\n{exc}", file=sys.stderr)
        return 2
    displays = _display_info(service)
    if not displays.monitors:
        print("No displays were detected.")
        return 1
    print("Detected displays:")
    for monitor in displays.monitors:
        print(f"  {monitor.label}")
        print(
            f"      bounds {monitor.bounds}  work area {monitor.work_area}"
        )
    if displays.virtual_bounds is not None:
        print(f"Virtual desktop: {displays.virtual_bounds}")
    print()
    print("Use `python run.py --list-groups` to see which rectangle groups exist")
    print("for these resolutions, and `--monitor N` to inspect one of them.")
    return 0


def _describe_layout(config: Config, displays: DisplayInfo | None) -> None:
    settings = config.layout
    print(f"Layout mode:    {settings.mode} (match = {settings.match})")
    if settings.mode == "manual":
        print(f"Manual group:   {settings.group or '<none>'}")
    print(f"Shapes hidden:  {settings.hidden}")


def _print_groups(
    config: Config,
    *,
    display: MonitorInfo | None = None,
    signature: str = "",
) -> None:
    if not config.groups:
        print("No [groups.*] tables are defined in the configuration.")
        return
    if display is not None:
        print(f"Display {display.index} ({display.device}) has resolution {display.resolution}.")
    if signature:
        matching = [group for group in config.groups if group.resolution == signature]
        print(f"Groups for resolution {signature}: {len(matching)}")
        for group in matching:
            marker = " *" if group.name == config.layout.group else ""
            print(
                f"  {group.name:<28} {len(group.shapes):>3} rectangle(s)  "
                f"{group.description}{marker}"
            )
        if not matching:
            print(
                "  (none yet - create one with "
                f'`python run.py --new-group my_layout` for {signature})'
            )
        print()
    print("All rectangle groups:")
    for group in config.groups:
        print(
            f"  {group.name:<28} {group.resolution:<12} {len(group.shapes):>3} rectangle(s)"
            f"  {group.description}"
        )


def _detect(config: Config, service: WindowService, logger: logging.Logger) -> int:
    displays = _display_info(service)
    target = service.find_target(
        config.target_profile,
        config.overlay.coordinate_space,
    )
    print(f"Config:         {config.path}")
    print(
        f"Window profile: {config.target_profile_name} "
        f"({config.target_profile.describe()})"
    )
    print(f"Coordinate:     {config.overlay.coordinate_space}")
    _describe_layout(config, displays)
    if target is None:
        print("Target:         NOT FOUND")
        print("Run `python run.py --list-windows` to inspect visible windows.")
        logger.warning("target window was not found")
        return 1
    print("Target:         FOUND")
    print(f"  hwnd:        0x{target.hwnd:X} ({target.hwnd})")
    print(f"  pid:         {target.pid}")
    print(f"  process:     {target.process_name}")
    print(f"  executable:  {target.executable}")
    print(f"  title:       {target.title!r}")
    print(f"  class:       {target.class_name}")
    print(f"  overlay:     {target.bounds}")
    print(f"  window:      {target.window_bounds}")
    print(f"  client:      {target.client_bounds}")
    print(f"  minimized:   {target.minimized}")
    print(f"  foreground:  {target.foreground}")

    monitor = displays.monitor_for(target.bounds)
    signature = (
        window_signature(target.bounds)
        if config.layout.match == "window"
        else (monitor.resolution if monitor is not None else "")
    )
    print(f"  display:     {monitor.label if monitor is not None else '<unknown>'}")
    print(f"  resolution:  {signature} ({config.layout.match})")
    active = _active_group_for(config, signature)
    print(f"  group:       {active.name if active is not None else '<no match>'}")
    if active is None:
        print(
            "  hint:        create a group for this resolution with "
            "`python run.py --new-group my_layout`"
        )
    logger.info("target check succeeded: %s", target.summary())
    return 0


def _active_group_for(config: Config, signature: str) -> LayoutGroup | None:
    if config.layout.mode == "manual":
        return config.find_group(config.layout.group)
    for group in config.groups:
        if group.resolution == signature:
            return group
    return None


# ---------------------------------------------------------------------------
# Configuration commands


def _wants_admin_action(args: argparse.Namespace) -> bool:
    return args.list_groups or _changes_anything(args)


def _changes_anything(args: argparse.Namespace) -> bool:
    return any(
        value not in (None, False)
        for value in (
            args.monitor,
            args.apply_group,
            args.new_group,
            args.layout,
            args.shapes,
            args.toolbar,
            args.buttons,
            args.debug,
        )
    )


def _toolbar_instance(config: Config) -> FeatureConfig | None:
    for feature in config.features:
        if feature.type == _TOOLBAR_TYPE:
            return feature
    return None


def _current_signature(
    config: Config, service: WindowService, displays: DisplayInfo
) -> str:
    """Resolution signature of the target window's display (or the primary one)."""
    target = service.find_target(
        config.target_profile, config.overlay.coordinate_space
    )
    if config.layout.match == "window":
        return window_signature(target.bounds) if target is not None else ""
    monitor = None
    if target is not None:
        monitor = displays.monitor_for(target.bounds)
    monitor = monitor or displays.primary
    return monitor.resolution if monitor is not None else ""


def _resolution_for_group(
    config: Config,
    service: WindowService,
    selected: MonitorInfo | None,
    displays: DisplayInfo,
) -> str:
    """Resolution a brand new group should be created for."""
    if config.layout.match == "monitor":
        monitor = selected
        if monitor is None:
            target = service.find_target(
                config.target_profile, config.overlay.coordinate_space
            )
            if target is not None:
                monitor = displays.monitor_for(target.bounds)
        monitor = monitor or displays.primary
        if monitor is None:
            raise ConfigError("no displays were detected")
        return normalize_resolution(monitor.resolution)
    target = service.find_target(
        config.target_profile, config.overlay.coordinate_space
    )
    if target is None:
        raise ConfigError(
            "layout.match is 'window', so a new group needs the target window size, "
            "but the target window is not open.  Open it first, or switch "
            "layout.match to 'monitor'."
        )
    return window_signature(target.bounds)


def _run_admin(config: Config, args: argparse.Namespace) -> int:
    service = create_window_service()
    displays = _display_info(service)
    selected: MonitorInfo | None = None
    if args.monitor is not None:
        selected = displays.by_index(args.monitor)
        if selected is None:
            print(
                f"Display {args.monitor} does not exist. Available displays:",
                file=sys.stderr,
            )
            for monitor in displays.monitors:
                print(f"  {monitor.label}", file=sys.stderr)
            return 2

    if args.list_groups or not _changes_anything(args):
        _describe_layout(config, displays)
        print()
        if selected is not None:
            _print_groups(config, display=selected, signature=selected.resolution)
        else:
            _print_groups(
                config,
                signature=_current_signature(config, service, displays),
            )
        if not _changes_anything(args):
            return 0
        print()

    changed: list[str] = []

    if args.new_group:
        name = args.new_group.strip()
        if not name:
            raise ConfigError("--new-group needs a group name")
        if config.find_group(name) is not None:
            raise ConfigError(
                f"[groups.{name}] already exists; choose another name or use "
                f"--apply-group {name}"
            )
        resolution = _resolution_for_group(config, service, selected, displays)
        save_new_group(
            config.path,
            name=name,
            resolution=resolution,
            description=f"created from the command line for {resolution}",
        )
        save_layout_values(config.path, {"mode": "manual", "group": name})
        changed.append(f"created [groups.{name}] with resolution {resolution}")

    if args.apply_group:
        name = args.apply_group.strip()
        group = config.find_group(name)
        if group is None:
            available = ", ".join(item.name for item in config.groups) or "<none>"
            raise ConfigError(
                f"unknown rectangle group {name!r}; configured groups: {available}"
            )
        save_layout_values(config.path, {"mode": "manual", "group": name})
        changed.append(
            f"selected [groups.{name}] ({group.resolution}, "
            f"{len(group.shapes)} rectangle(s)) as the manual group"
        )

    if args.layout:
        if args.layout == "manual" and not config.layout.group:
            raise ConfigError(
                "--layout manual needs an existing group; run "
                "`python run.py --apply-group NAME` first"
            )
        save_layout_values(config.path, {"mode": args.layout})
        changed.append(f"layout mode is now {args.layout}")

    if args.shapes:
        hidden = args.shapes == "off"
        save_layout_values(config.path, {"hidden": hidden})
        changed.append(
            "all rectangles are now " + ("hidden" if hidden else "visible")
        )

    if args.debug:
        enabled = args.debug == "on"
        save_app_values(config.path, {"debug": enabled})
        changed.append("debug (hot reload) is now " + ("on" if enabled else "off"))

    if args.toolbar:
        enabled = args.toolbar == "on"
        instance = _toolbar_instance(config)
        if instance is None:
            if enabled:
                from screenpainter.toml_edit import TomlDocument

                document = TomlDocument.from_path(config.path)
                document.create_section(
                    ("features", _TOOLBAR_TYPE),
                    {"type": _TOOLBAR_TYPE, "enabled": True},
                )
                document.write(config.path)
                changed.append(
                    'added [features.toolbar] (type = "toolbar", enabled = true)'
                )
            else:
                print("The toolbar feature is not present in the configuration.")
        else:
            save_feature_options(config.path, instance.name, {"enabled": enabled})
            changed.append(
                f"the floating toolbar is now {'shown' if enabled else 'hidden'}"
            )

    if args.buttons:
        buttons = [item.strip() for item in args.buttons.split(",") if item.strip()]
        if not buttons:
            raise ConfigError("--buttons needs at least one button: draw, move, hide")
        buttons = [_BUTTON_ALIASES.get(item, item) for item in buttons]
        unknown = [item for item in buttons if item not in _KNOWN_BUTTONS]
        if unknown:
            raise ConfigError(
                "unknown button(s): "
                + ", ".join(unknown)
                + f"; available: {', '.join(_KNOWN_BUTTONS)}"
            )
        instance = _toolbar_instance(config)
        if instance is None:
            raise ConfigError(
                "the configuration has no toolbar feature; enable it first with "
                "`python run.py --toolbar on`"
            )
        save_feature_options(config.path, instance.name, {"buttons": buttons})
        changed.append("toolbar buttons: " + ", ".join(buttons))

    config = load_config(config.path)
    if not changed:
        print("Nothing to do. See --help for the configuration commands.")
        return 0
    for line in changed:
        print(f"Updated {config.path}: {line}")
    print()
    if config.app.debug:
        print("Debug mode is on: a running ScreenPainter picks this up automatically.")
    else:
        print(
            "Restart ScreenPainter to apply this, or enable hot reload with "
            "`python run.py --debug on`."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
