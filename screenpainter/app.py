"""Application orchestrator and main loop."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Sequence

from screenpainter import __version__
from screenpainter.config import Config, FeatureConfig, load_config
from screenpainter.core.display import DisplayInfo, resolve_signature
from screenpainter.core.feature import (
    Feature,
    FeatureContext,
    feature_class,
)
from screenpainter.core.geometry import Rect
from screenpainter.core.layout import LayoutStore
from screenpainter.core.surface import PixelSurface
from screenpainter.core.tracker import TargetTracker
from screenpainter.core.window import WindowTarget
from screenpainter.errors import ConfigError, FeatureError, OverlayError
from screenpainter.features import load_builtin_features
from screenpainter.logging_setup import get_logger
from screenpainter.platform.base import Overlay, WindowService

_MONITOR_REFRESH_SECONDS = 2.0
#: How long the loop stays in its fast (16 ms) cadence after any activity.
_ACTIVITY_WINDOW_SECONDS = 0.4
#: Cadence used while a feature holds the fast loop (an editing mode is open).
_BUSY_TIMEOUT_MS = 100
#: Fallback polling cadence used when WinEvent hooks are unavailable.
_FALLBACK_TICK_MS = 16


@dataclass(slots=True)
class _FeaturePlan:
    """Result of validating a configuration reload before it is applied."""

    specs: list[FeatureConfig] = field(default_factory=list)
    keep: dict[str, Feature] = field(default_factory=dict)
    constructed: list[tuple[FeatureConfig, Feature]] = field(default_factory=list)


class ScreenPainterApp:
    """Owns the tracker, overlay surface and feature lifecycle.

    All work happens on one thread.  Features must not block, because the same
    loop must keep up with the target window being dragged.
    """

    def __init__(
        self,
        config: Config,
        service: WindowService,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.service = service
        self.logger = logger or get_logger("app")
        self.tracker = TargetTracker(
            service,
            config.target_profile,
            coordinate_space=config.overlay.coordinate_space,
        )
        self.store = LayoutStore(
            config,
            logger=get_logger("layout"),
            notify_config_saved=self._note_config_saved,
        )

        self._features: list[Feature] = []
        self._prepared = False
        self._has_run = False
        self._shutdown_done = False
        self._stop_requested = False

        self._overlay: Overlay | None = None
        self._surface: PixelSurface | None = None
        self._applied_bounds: Rect | None = None
        self._overlay_shown = False
        self._notified_target_hwnd: int | None = None
        self._last_target: WindowTarget | None = None
        self._waiting_logged = False
        self._minimized_logged = False
        self._force_repaint = True
        self._repaint_requested = False
        self._overlay_retry_at = 0.0
        self._next_target_search_at = 0.0
        self._activity_until = 0.0
        self._listener = None
        self._reload_requested = False

        # Debug mode / configuration watching.
        self._config_stamp = self._read_config_stamp()
        self._rejected_stamp: tuple[int, int] | None = None
        self._reload_check_at = 0.0
        self.debug_override = False

        # Monitor cache: enumeration is only refreshed every few seconds.
        self._displays = DisplayInfo()
        self._displays_at = 0.0
        self._displays_failed = False
        self._last_signature = ""

    @property
    def features(self) -> Sequence[Feature]:
        return tuple(self._features)

    @property
    def overlay_hwnd(self) -> int | None:
        return self._overlay.hwnd if self._overlay is not None else None

    @property
    def resolution_signature(self) -> str:
        return self.store.signature

    def request_repaint(self) -> None:
        """Called by features when their output needs to be redrawn."""
        self._repaint_requested = True

    def request_stop(self) -> None:
        self._stop_requested = True

    def _request_config_reload(self) -> None:
        """Features (the settings window) ask for a reload from disk."""
        self._reload_requested = True

    # -- setup/teardown -------------------------------------------------
    def _prepare_features(self) -> None:
        load_builtin_features(self.logger)
        assets_dir = self.config.project_root / "assets"
        try:
            assets_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.logger.warning("cannot create assets directory %s: %s", assets_dir, exc)

        installed: list[Feature] = []
        try:
            for feature_config in self.config.features:
                if not feature_config.enabled:
                    self.logger.info(
                        "feature %s (%s) is disabled; skipping",
                        feature_config.name,
                        feature_config.type,
                    )
                    continue
                feature = self._construct_feature(feature_config, self.config)
                try:
                    feature.start()
                except Exception as exc:
                    self._stop_feature_quietly(feature)
                    raise FeatureError(
                        f"feature {feature_config.name!r} failed to start: {exc}"
                    ) from exc
                installed.append(feature)
                self.logger.info(
                    "feature %s (%s) started",
                    feature_config.name,
                    feature_config.type,
                )
        except Exception:
            # A later feature must not leave earlier features running when
            # startup fails.
            for started in reversed(installed):
                self._stop_feature_quietly(started)
            raise

        self._features = installed
        self._prepared = True
        if not any(feature.context.type_name == "black_rectangles" for feature in installed):
            self.logger.warning(
                "no black_rectangles feature is enabled; the overlay will stay transparent"
            )

    def _construct_feature(self, feature_config: FeatureConfig, config: Config) -> Feature:
        cls = self._resolve_feature_class(feature_config)
        context = FeatureContext(
            instance_name=feature_config.name,
            type_name=feature_config.type,
            project_root=config.project_root,
            config_path=config.path,
            assets_dir=config.project_root / "assets",
            logger=get_logger(f"features.{feature_config.name}"),
            request_repaint=self.request_repaint,
            store=self.store,
            service=self.service,
            notify_config_saved=self._note_config_saved,
            request_config_reload=self._request_config_reload,
            keys=config.keys,
        )
        try:
            return cls(context, feature_config.options)
        except FeatureError:
            raise
        except Exception as exc:
            raise FeatureError(
                f"failed to initialize feature {feature_config.name!r} "
                f"(type {feature_config.type!r}): {exc}"
            ) from exc

    def _resolve_feature_class(self, feature_config: FeatureConfig) -> type[Feature]:
        try:
            return feature_class(feature_config.type)
        except FeatureError as exc:
            raise FeatureError(
                f"feature {feature_config.name!r}: {exc}"
            ) from exc

    def _stop_feature_quietly(self, feature: Feature) -> None:
        try:
            feature.stop()
        except Exception:
            self.logger.exception("feature %r raised while stopping", feature)

    def _disable_feature(self, feature: Feature, action: str) -> None:
        self.logger.exception(
            "feature %s (%s) failed during %s; disabling it",
            feature.context.instance_name,
            feature.context.type_name,
            action,
        )
        feature_name = feature.context.instance_name
        try:
            feature.stop()
        except Exception:
            self.logger.exception("feature %s also failed while stopping", feature_name)
        self._features = [item for item in self._features if item is not feature]
        self._force_repaint = True

    # -- target handling -------------------------------------------------
    def _notify_target_changed(self, target: WindowTarget | None) -> None:
        for feature in list(self._features):
            try:
                feature.on_target_changed(target)
            except Exception:
                self._disable_feature(feature, "on_target_changed")

    def _tick_features(self, now: float) -> None:
        for feature in list(self._features):
            try:
                feature.tick(now)
            except Exception:
                self._disable_feature(feature, "tick")

    def _update_features(self, target: WindowTarget, now: float) -> None:
        for feature in list(self._features):
            try:
                feature.update(target, now)
            except Exception:
                self._disable_feature(feature, "update")

    def _draw_features(self, surface: PixelSurface, target: WindowTarget) -> None:
        for feature in list(self._features):
            try:
                feature.draw(surface, target)
            except Exception:
                self._disable_feature(feature, "draw")

    def _handle_target(self, target: WindowTarget | None, now: float) -> None:
        events = self._take_target_events()
        if "location" in events or "restored" in events:
            self._activity_until = max(self._activity_until, now + _ACTIVITY_WINDOW_SECONDS)
        if target is None:
            self._handle_no_target()
            return

        changed = self._notified_target_hwnd != target.hwnd
        if changed:
            if self._notified_target_hwnd is not None:
                self.logger.info(
                    "target changed from hwnd=0x%X to 0x%X",
                    self._notified_target_hwnd,
                    target.hwnd,
                )
            self._destroy_overlay()
            if self._notified_target_hwnd is not None:
                self._notify_target_changed(None)
            self._watch_target(target.hwnd)
            self._notify_target_changed(target)
            self._notified_target_hwnd = target.hwnd
            self._force_repaint = True
            self._last_target = target
            self._waiting_logged = False
            self._minimized_logged = False
            self._overlay_retry_at = 0.0
            self.logger.info("attached to target: %s", target.summary())

        self._last_target = target
        self._refresh_signature(target)

        if target.minimized:
            if self._overlay is not None:
                self._overlay.hide()
                self._overlay_shown = False
            if not self._minimized_logged:
                self.logger.info(
                    "target is minimized; overlay hidden until it is restored"
                )
                self._minimized_logged = True
                # While minimized the window must not be edited (a topmost input
                # window would otherwise keep covering the desktop), so features
                # see the target as temporarily unavailable.
                self._notify_target_changed(None)
            return
        if self._minimized_logged:
            self.logger.info("target restored; overlay visible again")
            self._minimized_logged = False
            self._notify_target_changed(target)
            # The overlay was explicitly hidden while minimized.  A repaint
            # both refreshes its content and calls show() again.
            self._force_repaint = True

        self._ensure_overlay(target, now)
        if self._overlay is None:
            return

        # Move first (cheap) so the surface update can use the new position.
        if self._applied_bounds is not None:
            if (
                self._applied_bounds.x != target.bounds.x
                or self._applied_bounds.y != target.bounds.y
            ):
                try:
                    self._overlay.move(target.bounds.x, target.bounds.y)
                except OverlayError:
                    self.logger.exception("failed to move overlay; recreating it")
                    self._destroy_overlay()
                    self._overlay_retry_at = now + 0.5
                    return
                self._applied_bounds = Rect(
                    target.bounds.x,
                    target.bounds.y,
                    self._applied_bounds.width,
                    self._applied_bounds.height,
                )

        if (
            self._surface is None
            or self._surface.width != target.bounds.width
            or self._surface.height != target.bounds.height
        ):
            self._surface = PixelSurface(target.bounds.width, target.bounds.height)
            self._force_repaint = True

        self._update_features(target, now)

        if not self._something_to_paint():
            # Nothing to paint (all rectangles hidden or the group is empty):
            # do not keep a transparent overlay window on top of the target.
            # This keeps the desktop untouched while the mask is hidden.
            self._repaint_requested = False
            if self._overlay_shown:
                self.logger.debug("nothing to paint; hiding the overlay")
                self._overlay.hide()
                self._overlay_shown = False
            self._force_repaint = True
            return

        if not self._overlay_shown:
            self._force_repaint = True
        if self._force_repaint or self._repaint_requested:
            self._repaint(target)

    def _something_to_paint(self) -> bool:
        store = self.store
        try:
            return bool(store.draw_shapes())
        except Exception:  # pragma: no cover - defensive
            self.logger.exception("could not read the shapes to paint")
            return True

    def _handle_no_target(self) -> None:
        if self._notified_target_hwnd is not None:
            last = self._last_target
            self._unwatch_target()
            self._notify_target_changed(None)
            self._notified_target_hwnd = None
            self._destroy_overlay()
            self._last_target = None
            self._minimized_logged = False
            self._overlay_retry_at = 0.0
            if last is not None:
                self.logger.info(
                    "target window lost (was hwnd=0x%X); waiting for it to reappear",
                    last.hwnd,
                )
        if not self._waiting_logged:
            profile_name = self.config.target_profile_name
            self.logger.info(
                "waiting for window profile %r (%s)",
                profile_name,
                self.config.target_profile.describe(),
            )
            self._waiting_logged = True

    def _ensure_overlay(self, target: WindowTarget, now: float) -> None:
        if self._overlay is not None and not self._overlay.is_alive():
            self.logger.info("overlay window disappeared; recreating")
            self._destroy_overlay()
            self._overlay_retry_at = 0.0
        if self._overlay is None:
            if now < self._overlay_retry_at:
                return
            try:
                self._overlay = self.service.create_overlay(
                    target,
                    click_through=self.config.overlay.click_through,
                )
            except OverlayError:
                self.logger.exception(
                    "failed to create overlay for %s; retrying in 1 second",
                    target.summary(),
                )
                self._overlay = None
                self._overlay_retry_at = now + 1.0
                return
            self._surface = None
            self._applied_bounds = target.bounds
            self._overlay_shown = False
            self._force_repaint = True
            self.logger.debug(
                "created overlay hwnd=0x%X at %s",
                self._overlay.hwnd,
                target.bounds,
            )

    def _repaint(self, target: WindowTarget) -> None:
        if self._overlay is None or self._surface is None:
            return
        # Consume the request before drawing.  A feature can request another
        # frame during update/draw (animation), and errors set force again.
        self._force_repaint = False
        self._repaint_requested = False
        self.logger.debug("repaint %s at %s", self._surface.width, target.bounds)
        self._surface.clear()
        self._draw_features(self._surface, target)
        try:
            self._overlay.render(self._surface)
            self._overlay.show()
        except OverlayError:
            self.logger.exception("failed to update overlay; recreating it")
            self._destroy_overlay()
            self._overlay_retry_at = time.monotonic() + 0.5
            return
        self._applied_bounds = target.bounds
        self._overlay_shown = True

    def _destroy_overlay(self) -> None:
        overlay = self._overlay
        self._overlay = None
        self._surface = None
        self._applied_bounds = None
        self._overlay_shown = False
        if overlay is not None:
            try:
                overlay.destroy()
            except Exception:
                self.logger.exception("failed to destroy overlay cleanly")

    # -- displays / resolution groups ------------------------------------
    def _refresh_displays(self) -> DisplayInfo:
        now = time.monotonic()
        if now - self._displays_at < _MONITOR_REFRESH_SECONDS:
            return self._displays
        self._displays_at = now
        try:
            monitors = tuple(self.service.list_monitors())
            virtual = self.service.virtual_desktop()
        except Exception:
            if not self._displays_failed:
                self._displays_failed = True
                self.logger.exception(
                    "could not enumerate monitors; keeping the last known layout"
                )
            return self._displays
        self._displays_failed = False
        self._displays = DisplayInfo(monitors=monitors, virtual_bounds=virtual)
        return self._displays

    def _refresh_signature(self, target: WindowTarget | None) -> None:
        displays = self._refresh_displays()
        signature = resolve_signature(
            self.config.layout.match,
            target.bounds if target is not None else None,
            displays,
        )
        if signature == self._last_signature and signature == self.store.signature:
            return
        self._last_signature = signature
        if self.store.update_signature(signature):
            self._force_repaint = True
            self.logger.info(
                "resolution %s -> rectangle group %s",
                signature or "<unknown>",
                self.store.active_group_name or "<none>",
            )

    # -- debug mode: configuration hot reload ---------------------------
    def _read_config_stamp(self) -> tuple[int, int] | None:
        try:
            stat = self.config.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _note_config_saved(self) -> None:
        """The app itself wrote the config; do not treat it as an external edit."""
        self._config_stamp = self._read_config_stamp()

    def _apply_reload_request(self) -> None:
        if not self._reload_requested:
            return
        self._reload_requested = False
        try:
            new_config = load_config(self.config.path)
        except ConfigError as exc:
            self.logger.warning(
                "configuration change rejected (the running configuration is "
                "kept):\n%s",
                exc,
            )
            self._config_stamp = self._read_config_stamp()
            return
        self._config_stamp = self._read_config_stamp()
        if self._apply_config(new_config):
            self.logger.info(
                "configuration reloaded from %s (requested by a feature)",
                self.config.path,
            )
        else:
            self.logger.warning(
                "configuration change could not be applied; keeping the running setup"
            )

    def _tick_config_watch(self, now: float) -> None:
        if not self.config.app.debug and not self.debug_override:
            return
        if now < self._reload_check_at:
            return
        self._reload_check_at = now + self.config.app.debug_interval_ms / 1000.0
        stamp = self._read_config_stamp()
        if stamp is None or stamp == self._config_stamp or stamp == self._rejected_stamp:
            return
        try:
            new_config = load_config(self.config.path)
        except ConfigError as exc:
            self._rejected_stamp = stamp
            self.logger.warning(
                "configuration change rejected (the running configuration is kept):\n%s",
                exc,
            )
            return
        self._rejected_stamp = None
        self._config_stamp = stamp
        if self._apply_config(new_config):
            self.logger.info("configuration reloaded from %s", self.config.path)
        else:
            self.logger.warning(
                "configuration change could not be applied; keeping the previous setup"
            )

    def _apply_config(self, new_config: Config) -> bool:
        old_config = self.config
        current_features = {
            feature.context.instance_name: feature for feature in self._features
        }
        # Validate and build first: a broken configuration must never tear down
        # the overlay that is currently working.
        try:
            plan = self._plan_features(new_config, current_features)
        except Exception:
            self.logger.exception("could not build the features of the new configuration")
            return False

        self.config = new_config
        self.store.apply_config(new_config)

        target_binding_changed = (
            old_config.target_profile_name != new_config.target_profile_name
            or old_config.overlay.coordinate_space != new_config.overlay.coordinate_space
            or old_config.window_profiles != new_config.window_profiles
        )
        if target_binding_changed:
            self._reset_target_binding(new_config)
        elif old_config.overlay.click_through != new_config.overlay.click_through:
            self._destroy_overlay()

        self._activate_features(plan, current_features)
        # Reused feature instances keep their identity but must see the new keys.
        for feature in self._features:
            feature.context.keys = new_config.keys
        if new_config.logging != old_config.logging:
            self.logger.info(
                "logging settings changed; restart the application to apply them"
            )
        if new_config.app.verify_ms != old_config.app.verify_ms:
            self.logger.info(
                "verify_ms is now %d ms", new_config.app.verify_ms
            )
        self._force_repaint = True
        self._last_signature = ""
        self._refresh_signature(self._last_target)
        return True

    def _reset_target_binding(self, new_config: Config) -> None:
        if self._notified_target_hwnd is not None:
            self._unwatch_target()
            self._notify_target_changed(None)
        self._notified_target_hwnd = None
        self._destroy_overlay()
        self._last_target = None
        self._minimized_logged = False
        self._waiting_logged = False
        self._next_target_search_at = 0.0
        self.tracker = TargetTracker(
            self.service,
            new_config.target_profile,
            coordinate_space=new_config.overlay.coordinate_space,
        )
        self.logger.info(
            "tracking target profile %r again after the configuration change",
            new_config.target_profile_name,
        )

    def _plan_features(
        self, config: Config, current: dict[str, Feature]
    ) -> _FeaturePlan:
        plan = _FeaturePlan()
        to_create: list[FeatureConfig] = []
        for feature_config in config.features:
            if not feature_config.enabled:
                continue
            plan.specs.append(feature_config)
            existing = current.get(feature_config.name)
            if (
                existing is not None
                and existing.context.type_name == feature_config.type
                and dict(existing.options) == dict(feature_config.options)
            ):
                plan.keep[feature_config.name] = existing
            else:
                to_create.append(feature_config)
        try:
            for feature_config in to_create:
                feature = self._construct_feature(feature_config, config)
                plan.constructed.append((feature_config, feature))
        except Exception:
            for _, feature in plan.constructed:
                self._stop_feature_quietly(feature)
            raise
        return plan

    def _activate_features(
        self, plan: _FeaturePlan, current: dict[str, Feature]
    ) -> None:
        for name, feature in current.items():
            if name not in plan.keep:
                self._stop_feature_quietly(feature)
        started: dict[str, Feature] = {}
        for feature_config, feature in plan.constructed:
            try:
                feature.start()
            except Exception:
                self.logger.exception(
                    "feature %r failed to start after the reload; skipping it",
                    feature_config.name,
                )
                self._stop_feature_quietly(feature)
                continue
            started[feature_config.name] = feature
            self.logger.info(
                "feature %s (%s) reloaded from the configuration",
                feature_config.name,
                feature_config.type,
            )
        ordered: list[Feature] = []
        for feature_config in plan.specs:
            feature = plan.keep.get(feature_config.name)
            if feature is None:
                feature = started.get(feature_config.name)
            if feature is not None:
                ordered.append(feature)
        self._features = ordered
        self._force_repaint = True

    # -- main loop -------------------------------------------------------
    def run(self, *, duration: float | None = None) -> int:
        if self._has_run:
            raise RuntimeError("ScreenPainterApp.run() can only be called once")
        self._has_run = True
        if not self._prepared:
            self._prepare_features()

        self.logger.info(
            "%s %s starting (config=%s, target=%s, coordinate_space=%s, "
            "click_through=%s, layout=%s/%s, debug=%s)",
            self.config.app.name,
            __version__,
            self.config.path,
            self.config.target_profile_name,
            self.config.overlay.coordinate_space,
            self.config.overlay.click_through,
            self.config.layout.mode,
            self.config.layout.match,
            self.config.app.debug or self.debug_override,
        )
        self.logger.info(
            "event-driven overlay loop: idle wake-up every %d ms (target) / "
            "%d ms (waiting); press Ctrl+C to stop",
            self.config.app.verify_ms,
            self.config.app.idle_tick_ms,
        )

        self._start_listener()
        deadline = time.monotonic() + duration if duration is not None else None
        interrupt = False
        try:
            while not self._stop_requested:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    break
                timeout_ms = self._wait_timeout_ms(now)
                if timeout_ms > 0:
                    woke = self._wait_for_events(timeout_ms)
                    if woke:
                        now = time.monotonic()
                        self._activity_until = now + _ACTIVITY_WINDOW_SECONDS
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    break
                self._tick_config_watch(now)
                self._tick_features(now)
                self._apply_reload_request()
                target = self._poll_target(now)
                self._handle_target(target, now)
        except KeyboardInterrupt:
            interrupt = True
            self.logger.info("interrupted by Ctrl+C; shutting down")
        finally:
            self._shutdown()
        if interrupt:
            return 0
        return 0

    # -- event driven scheduling -----------------------------------------
    def _start_listener(self) -> None:
        factory = getattr(self.service, "create_target_listener", None)
        if not callable(factory):
            self.logger.info(
                "window service has no event source; polling the target instead"
            )
            return
        try:
            self._listener = factory()
        except Exception:
            self.logger.exception("could not create the window event listener")
            self._listener = None
        if self._listener is not None:
            self.logger.info(
                "tracking is event driven (WinEvent hooks); no 60 Hz polling"
            )

    def _wait_for_events(self, timeout_ms: int) -> bool:
        waiter = getattr(self.service, "wait_for_events", None)
        if callable(waiter):
            return bool(waiter(timeout_ms))
        self.service.pump_events()
        if timeout_ms > 0:
            time.sleep(min(timeout_ms, _FALLBACK_TICK_MS) / 1000.0)
        return False

    def _wait_timeout_ms(self, now: float) -> int:
        """How long the loop may sleep before it has to look at the world again."""
        if now < self._activity_until:
            return _FALLBACK_TICK_MS
        has_target = self.tracker.cached_hwnd is not None
        timeout = (
            self.config.app.verify_ms if has_target else self.config.app.idle_tick_ms
        )
        if self._keep_alive():
            timeout = min(timeout, _BUSY_TIMEOUT_MS)
        if self.config.app.debug or self.debug_override:
            timeout = min(timeout, self.config.app.debug_interval_ms)
        if self._listener is not None and not self._listener.active and has_target:
            # Without hooks the target has to be polled for movement.
            timeout = min(timeout, _FALLBACK_TICK_MS)
        return max(1, int(timeout))

    def _take_target_events(self) -> set[str]:
        listener = self._listener
        if listener is None:
            return set()
        try:
            events = listener.take_events()
        except Exception:
            self.logger.exception("could not read window events")
            return set()
        if events:
            self._activity_until = time.monotonic() + _ACTIVITY_WINDOW_SECONDS
        return events

    def _watch_target(self, hwnd: int) -> None:
        listener = self._listener
        if listener is None:
            return
        try:
            if not listener.watch(hwnd):
                self.logger.warning(
                    "window event hooks are unavailable for hwnd=0x%X; "
                    "falling back to polling",
                    hwnd,
                )
        except Exception:
            self.logger.exception("could not watch the target window")

    def _unwatch_target(self) -> None:
        listener = self._listener
        if listener is None:
            return
        try:
            listener.release()
        except Exception:
            self.logger.exception("could not release the window event hooks")

    def _poll_target(self, now: float) -> WindowTarget | None:
        """Throttle the expensive full window search while nothing is tracked.

        Enumerating every visible window is wasteful, so a missing target is
        searched for at ``idle_tick_ms`` intervals; with a target, the cached
        handle is verified through cheap queries only.
        """
        if self.tracker.cached_hwnd is None and now < self._next_target_search_at:
            return None
        target = self.tracker.tick()
        if self.tracker.cached_hwnd is None:
            self._next_target_search_at = now + self.config.app.idle_tick_ms / 1000.0
        return target

    def _keep_alive(self) -> bool:
        return any(feature.keep_alive() for feature in self._features)

    def _shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._unwatch_target()
        if self._notified_target_hwnd is not None:
            self._notify_target_changed(None)
            self._notified_target_hwnd = None
        self._destroy_overlay()
        for feature in reversed(self._features):
            self._stop_feature_quietly(feature)
        self._features = []
        self.logger.info("ScreenPainter stopped")
