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

"""Utility registry that wires cache consumers to the coordinator."""

from __future__ import annotations


import logging

from dataclasses import dataclass, field

from typing import Any, Dict


from .consumers import (
    MaskOverlayCacheConsumer,
    PyramidCacheConsumer,
    SamPredictorCacheConsumer,
    TileCacheConsumer,
)

from .coordinator import CacheCoordinator, CachePriority


logger = logging.getLogger(__name__)


@dataclass
class CacheRegistry:
    """Lightweight container for cache-coordination attachments."""

    coordinator: CacheCoordinator
    consumers: Dict[str, Any] = field(default_factory=dict)

    def _attach(
        self,
        key: str,
        manager: Any,
        *,
        consumer_cls,
        priority: CachePriority,
        missing_warning: str | None = None,
    ):
        """Shared attach helper that guards duplicates and optional missing managers."""
        if manager is None:
            if missing_warning:
                logger.warning(missing_warning)
            return self.consumers.get(key)
        if key in self.consumers:
            return self.consumers[key]
        consumer = consumer_cls(
            manager,
            self.coordinator,
            consumer_id=key,
            priority=priority,
        )
        self.consumers[key] = consumer
        return consumer

    def attach_tile_manager(self, manager: Any, *, consumer_id: str = "tiles"):
        """Attach ``manager`` as the tile cache consumer if not already bound.

        Args:
            manager: Tile manager to register.
            consumer_id: Diagnostics identifier exposed in trim logs.

        Returns:
            Existing consumer when already attached, otherwise the newly created
            :class:`TileCacheConsumer`.
        """
        return self._attach(
            consumer_id,
            manager,
            consumer_cls=TileCacheConsumer,
            priority=CachePriority.TILES,
        )

    def attach_pyramid_manager(self, manager: Any, *, consumer_id: str = "pyramids"):
        """Attach ``manager`` as the pyramid cache consumer if not already bound.

        Args:
            manager: Pyramid manager to register.
            consumer_id: Diagnostics identifier exposed in trim logs.

        Returns:
            Existing consumer when already attached, otherwise the newly created
            :class:`PyramidCacheConsumer`.
        """
        return self._attach(
            consumer_id,
            manager,
            consumer_cls=PyramidCacheConsumer,
            priority=CachePriority.PYRAMIDS,
        )

    def attach_mask_controller(
        self,
        controller: Any,
        *,
        consumer_id: str = "mask_overlays",
    ):
        """Attach ``controller`` as the mask render cache consumer if possible.

        Args:
            controller: Mask controller that exposes overlay cache hooks.
            consumer_id: Diagnostics identifier exposed in trim logs.

        Returns:
            Existing consumer when already attached, otherwise the newly created
            :class:`MaskOverlayCacheConsumer`. Returns ``None`` when the
            controller is missing.
        """
        return self._attach(
            consumer_id,
            controller,
            consumer_cls=MaskOverlayCacheConsumer,
            priority=CachePriority.MASK_OVERLAYS,
            missing_warning="Mask controller missing; skipping cache registry attachment",
        )

    def attachSamManager(
        self,
        manager: Any,
        *,
        consumer_id: str = "predictors",
    ):
        """Attach ``manager`` as the SAM predictor cache consumer if possible.

        Args:
            manager: SAM manager that exposes predictor hooks.
            consumer_id: Diagnostics identifier exposed in trim logs.

        Returns:
            Existing consumer when already attached, otherwise the newly created
            :class:`SamPredictorCacheConsumer`. Returns ``None`` when the
            manager is missing.
        """
        return self._attach(
            consumer_id,
            manager,
            consumer_cls=SamPredictorCacheConsumer,
            priority=CachePriority.PREDICTORS,
            missing_warning="SAM manager missing; skipping predictor cache attachment",
        )
