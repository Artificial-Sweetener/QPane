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

"""Expose QPane rendering collaborators via lazy imports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "RenderingPresenter",
    "View",
    "CoordinateContext",
    "LogicalPoint",
    "LogicalSize",
    "NormalizedViewState",
    "PhysicalPoint",
    "PhysicalSize",
    "Renderer",
    "Tile",
    "TileGeneratorWorker",
    "TileManager",
    "ImagePyramid",
    "PyramidManager",
    "PyramidStatus",
    "Viewport",
    "ViewportZoomMode",
]

_lazy_imports = {
    "RenderingPresenter": "presenter",
    "View": "view",
    "CoordinateContext": "coordinates",
    "LogicalPoint": "coordinates",
    "LogicalSize": "coordinates",
    "NormalizedViewState": "coordinates",
    "PhysicalPoint": "coordinates",
    "PhysicalSize": "coordinates",
    "Renderer": "render",
    "Tile": "tiles",
    "TileGeneratorWorker": "tiles",
    "TileManager": "tiles",
    "ImagePyramid": "pyramid",
    "PyramidManager": "pyramid",
    "PyramidStatus": "pyramid",
    "Viewport": "viewport",
    "ViewportZoomMode": "viewport",
}


def __getattr__(name: str) -> Any:
    """Lazy-load rendering collaborators to avoid import storms."""
    module_name = _lazy_imports.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__} has no attribute {name}")
    module = import_module(f"{__name__}.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List lazy attributes alongside eagerly loaded globals."""
    return sorted(__all__ + list(globals().keys()))
