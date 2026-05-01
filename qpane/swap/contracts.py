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

"""Typed interfaces for swap-time collaborators.

These runtime-checkable protocols replace reflection-based capability checks in
swap and rendering orchestration. Collaborators must satisfy these contracts at
wire-up time; missing methods or signals are treated as programmer errors.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from PySide6.QtCore import Signal
from PySide6.QtGui import QImage

from ..scene.identity import SceneLayerAssetKey, SceneLayerTileKey


@runtime_checkable
class TilePrefetchManager(Protocol):
    """Capabilities required by swap and rendering when prefetching tiles."""

    tileReady: Signal
    cache_usage_bytes: int
    cache_limit_bytes: int

    def prefetch_tiles(
        self,
        identifiers: Sequence[SceneLayerTileKey],
        source_image: QImage,
        *,
        reason: str = "neighbor",
    ) -> Sequence[SceneLayerTileKey]:
        """Queue tile generation for ``identifiers`` using ``source_image``."""
        ...

    def cancel_prefetch(
        self, identifiers: Sequence[SceneLayerTileKey], *, reason: str
    ) -> None:
        """Request cancellation for prefetches associated with ``identifiers``."""
        ...

    def remove_tiles_for_asset(self, asset_key: SceneLayerAssetKey) -> None:
        """Drop cached tiles and inflight work for ``asset_key``."""
        ...

    def remove_tiles_for_source_asset(
        self, pyramid_asset_key: SceneLayerAssetKey
    ) -> None:
        """Drop cached tiles and inflight work for ``pyramid_asset_key``."""
        ...

    def calculate_grid_dimensions(self, width: int, height: int) -> tuple[int, int]:
        """Return the tile grid dimensions needed to cover ``width`` × ``height``."""
        ...


@runtime_checkable
class PyramidPrefetchManager(Protocol):
    """Capabilities required to prefetch image pyramids."""

    pyramidReady: Signal
    cache_usage_bytes: int
    cache_limit_bytes: int

    def prefetch_pyramid(
        self,
        asset_key: SceneLayerAssetKey,
        image: QImage,
        *,
        reason: str = "prefetch",
    ) -> bool:
        """Begin background pyramid generation for ``asset_key`` when missing."""
        ...

    def cancel_prefetch(
        self, asset_keys: Sequence[SceneLayerAssetKey], *, reason: str = "navigation"
    ) -> Sequence[SceneLayerAssetKey]:
        """Cancel pyramid prefetch for ``asset_keys`` and return cancelled keys."""
        ...


@runtime_checkable
class MaskManagerView(Protocol):
    """Minimal mask manager surface needed for swap prefetch checks."""

    def get_mask_ids_for_image(self, image_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """Return mask identifiers associated with ``image_id``."""
        ...


@runtime_checkable
class MaskPrefetchService(Protocol):
    """Capabilities required for mask prefetch scheduling and cancellation."""

    manager: MaskManagerView

    def prefetchColorizedMasks(
        self,
        image_id: uuid.UUID,
        *,
        reason: str = "navigation",
        scales: Sequence[float] | None = None,
    ) -> bool:
        """Warm colorized mask renders for ``image_id`` on a background executor."""
        ...

    def cancelPrefetch(self, image_id: uuid.UUID | None) -> bool:
        """Cancel queued mask prefetch for ``image_id`` (or all when ``None``)."""
        ...


@runtime_checkable
class SamPredictorManager(Protocol):
    """Capabilities required for SAM predictor warming and cancellation."""

    def requestPredictor(
        self,
        image: QImage,
        image_id: uuid.UUID,
        *,
        source_path: Path | None = None,
    ) -> None:
        """Request or reuse a SAM predictor for ``image_id``."""
        ...

    def cancelPendingPredictor(self, image_id: uuid.UUID) -> bool:
        """Cancel inflight predictor work for ``image_id`` when possible."""
        ...
