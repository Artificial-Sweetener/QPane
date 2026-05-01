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

"""Typed source descriptors referenced by internal scene layers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass(frozen=True, slots=True)
class CatalogImageSource:
    """Reference a catalog-owned image without moving pixels into scene state."""

    image_id: uuid.UUID
    source_path: Path | None
    revision: int


@dataclass(frozen=True, slots=True)
class PlaceholderImageSource:
    """Reference the internal placeholder image fallback source."""

    source_id: uuid.UUID
    revision: int


@dataclass(frozen=True, slots=True)
class MaskLayerSource:
    """Reference a mask-domain layer by its stable mask identifier."""

    mask_id: uuid.UUID
    revision: int


LayerSource = Union[
    CatalogImageSource,
    MaskLayerSource,
    PlaceholderImageSource,
]
