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

"""Core orchestration, configuration, and diagnostics helpers for QPane."""

from .config import CacheSettings, CacheWeights, Config, PrefetchSettings
from .diagnostics import (
    DiagnosticsProvider,
    DiagnosticsRegistry,
    DiagnosticsSnapshot,
    build_core_diagnostics,
    pyramid_level_record,
)
from .diagnostics_broker import Diagnostics
from .fallbacks import FeatureFailure, FeatureFallbacks
from .feature_coordinator import FeatureCoordinator
from .hooks import (
    CursorProvider,
    OverlayDrawFn,
    QPaneHooks,
    SceneOverlayDrawFn,
    ToolFactory,
    ToolSignalBinder,
)
from ..types import DiagnosticRecord

__all__ = [
    "CacheWeights",
    "CacheSettings",
    "Config",
    "PrefetchSettings",
    "FeatureFailure",
    "FeatureFallbacks",
    "DiagnosticRecord",
    "DiagnosticsProvider",
    "DiagnosticsRegistry",
    "DiagnosticsSnapshot",
    "build_core_diagnostics",
    "pyramid_level_record",
    "Diagnostics",
    "FeatureCoordinator",
    "CursorProvider",
    "OverlayDrawFn",
    "SceneOverlayDrawFn",
    "QPaneHooks",
    "ToolFactory",
    "ToolSignalBinder",
]


def __getattr__(name: str):
    """Defer optional imports that would otherwise create circular references."""
    if name == "QPaneState":
        from .state import QPaneState as _QPaneState

        return _QPaneState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
