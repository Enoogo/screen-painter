"""Built-in features are discovered from sub-packages of this package.

To add an effect: create ``screenpainter/features/<your_feature>/`` with an
``__init__.py``, register its class with ``@register_feature("...")``, and point
a ``[features.<name>]`` table in the config at it.  No central import list needs
to be edited.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

from screenpainter.core.feature import registered_feature_types


def load_builtin_features(logger: logging.Logger | None = None) -> tuple[str, ...]:
    """Import every immediate sub-package and return registered type names.

    A broken experimental feature is logged and skipped so it cannot prevent
    the rest of the application from starting.  If the config references a
    skipped type, startup fails with the available type list.
    """
    package_name = __name__
    package_path = __path__  # type: ignore[name-defined]
    for module_info in sorted(pkgutil.iter_modules(package_path), key=lambda item: item.name):
        if module_info.name.startswith("_") or not module_info.ispkg:
            continue
        module_name = f"{package_name}.{module_info.name}"
        try:
            importlib.import_module(module_name)
        except Exception:
            if logger is not None:
                logger.exception("failed to load feature package %s; skipping", module_name)
            else:
                continue
    return registered_feature_types()


__all__ = ["load_builtin_features", "registered_feature_types"]
