#    QPane - High-performance PySide6 image viewer
#    Copyright (C) 2025  Artificial Sweetener and contributors
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Expose the host-facing QPane package surface via lazy-loaded collaborators."""

from __future__ import annotations
from importlib import import_module
from typing import Any

__all__ = [
    "CacheMode",
    "PlaceholderScaleMode",
    "ZoomMode",
    "DiagnosticsDomain",
    "ControlMode",
    "ComparisonOrientation",
    "CatalogEntry",
    "LinkedGroup",
    "ComparisonState",
    "ComparisonDividerState",
    "CompositionEntry",
    "CompositionSnapshot",
    "DiagnosticRecord",
    "OverlayState",
    "MaskInfo",
    "MaskSavedPayload",
    "CatalogMutationEvent",
    "CatalogSnapshot",
    "QPaneScene",
    "QPaneSceneLayer",
    "QPaneSceneRequest",
    "QPaneCatalogImageLayerRequest",
    "QPaneSceneTemplate",
    "QPaneTemplateLayer",
    "QPaneSceneTemplateBindings",
    "QPaneSceneClip",
    "QPaneSceneHit",
    "QPaneSceneOverlayState",
    "QPaneSceneOverlayLayer",
    "ExtensionToolSignals",
    "ExtensionTool",
    "Config",
    "QPane",
    "__version__",
]
_LAZY_SYMBOLS: dict[str, tuple[str, str]] = {
    "QPane": ("qpane.qpane", "QPane"),
    "Config": ("qpane.core.config", "Config"),
    "CatalogMutationEvent": ("qpane.catalog.catalog", "CatalogMutationEvent"),
    "CacheMode": ("qpane.types", "CacheMode"),
    "PlaceholderScaleMode": ("qpane.types", "PlaceholderScaleMode"),
    "ZoomMode": ("qpane.types", "ZoomMode"),
    "DiagnosticsDomain": ("qpane.types", "DiagnosticsDomain"),
    "ControlMode": ("qpane.types", "ControlMode"),
    "ComparisonOrientation": ("qpane.types", "ComparisonOrientation"),
    "CatalogEntry": ("qpane.types", "CatalogEntry"),
    "LinkedGroup": ("qpane.types", "LinkedGroup"),
    "ComparisonState": ("qpane.types", "ComparisonState"),
    "ComparisonDividerState": ("qpane.types", "ComparisonDividerState"),
    "CompositionEntry": ("qpane.types", "CompositionEntry"),
    "CompositionSnapshot": ("qpane.types", "CompositionSnapshot"),
    "DiagnosticRecord": ("qpane.types", "DiagnosticRecord"),
    "OverlayState": ("qpane.types", "OverlayState"),
    "MaskInfo": ("qpane.types", "MaskInfo"),
    "MaskSavedPayload": ("qpane.types", "MaskSavedPayload"),
    "CatalogSnapshot": ("qpane.types", "CatalogSnapshot"),
    "QPaneScene": ("qpane.types", "QPaneScene"),
    "QPaneSceneLayer": ("qpane.types", "QPaneSceneLayer"),
    "QPaneSceneRequest": ("qpane.types", "QPaneSceneRequest"),
    "QPaneCatalogImageLayerRequest": ("qpane.types", "QPaneCatalogImageLayerRequest"),
    "QPaneSceneTemplate": ("qpane.types", "QPaneSceneTemplate"),
    "QPaneTemplateLayer": ("qpane.types", "QPaneTemplateLayer"),
    "QPaneSceneTemplateBindings": ("qpane.types", "QPaneSceneTemplateBindings"),
    "QPaneSceneClip": ("qpane.types", "QPaneSceneClip"),
    "QPaneSceneHit": ("qpane.types", "QPaneSceneHit"),
    "QPaneSceneOverlayState": ("qpane.types", "QPaneSceneOverlayState"),
    "QPaneSceneOverlayLayer": ("qpane.types", "QPaneSceneOverlayLayer"),
    "ExtensionToolSignals": ("qpane.tools.base", "ExtensionToolSignals"),
    "ExtensionTool": ("qpane.tools.base", "ExtensionTool"),
    "__version__": ("qpane._version", "version"),
}


def __getattr__(name: str) -> Any:
    """Lazily import public symbols to keep ``import qpane`` lightweight."""
    target = _LAZY_SYMBOLS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__} has no attribute {name}")
    module = import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return the sorted public attributes including lazy-loaded entries."""
    return sorted(
        __all__ + [key for key in globals().keys() if not key.startswith("_")]
    )
