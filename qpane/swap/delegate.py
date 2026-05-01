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

"""Delegate routing QPane swap callbacks to the coordinator."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from PySide6.QtCore import QRect, QRectF

from ..core import Config, PrefetchSettings
from ..scene.identity import SceneLayerAssetKey, SceneLayerTileKey
from .coordinator import SwapCoordinator, SwapCoordinatorMetrics

if TYPE_CHECKING:  # pragma: no cover
    from ..catalog.controller import CatalogController
    from ..catalog.image_catalog import ImageCatalog
    from ..qpane import QPane
    from ..rendering import RenderingPresenter, TileManager, Viewport
logger = logging.getLogger(__name__)


class SwapDelegate:
    """Facade that bridges QPane widget callbacks to a SwapCoordinator."""

    def __init__(
        self,
        *,
        qpane: "QPane",
        catalog: "ImageCatalog",
        viewport: "Viewport",
        tile_manager: "TileManager",
        rendering: "RenderingPresenter",
        prefetch_settings: PrefetchSettings | None,
        mark_dirty: Callable[[QRect | QRectF | None], None],
    ) -> None:
        """Capture collaborators needed to forward swap lifecycle callbacks.

        Args:
            qpane: Owning QPane widget.
            catalog: Catalog providing images for swaps.
            viewport: Viewport used to size and fit images.
            tile_manager: Tile manager handling prefetch hints and tile sizes.
            rendering: Rendering presenter used to calculate draw regions.
            prefetch_settings: Initial prefetch configuration, when provided.
            mark_dirty: Callback that invalidates a region when tiles arrive.
        """
        self._qpane = qpane
        self._tile_manager = tile_manager
        self._rendering = rendering
        self._mark_dirty = mark_dirty
        self._catalog_controller: "CatalogController | None" = None
        self._coordinator = SwapCoordinator(
            qpane=qpane,
            catalog=catalog,
            viewport=viewport,
            tile_manager=tile_manager,
            prefetch_settings=prefetch_settings,
        )

    def attach_catalog_controller(self, controller: "CatalogController") -> None:
        """Track the catalog controller used to persist view state."""
        self._catalog_controller = controller

    def snapshot_metrics(self) -> SwapCoordinatorMetrics:
        """Expose swap orchestration metrics for diagnostics consumers."""
        return self._coordinator.snapshot_metrics()

    def save_zoom_pan_for_current_image(self) -> None:
        """Persist the viewport transform for the currently active image."""
        controller = self._catalog_controller
        if controller is not None:
            controller.saveZoomPanForCurrentImage()

    def restore_zoom_pan_for_new_image(self, image_id: uuid.UUID) -> None:
        """Reapply saved viewport state after navigation completes."""
        controller = self._catalog_controller
        if controller is not None:
            controller.restoreZoomPanForNewImage(image_id)

    def set_current_image(
        self,
        image_id: uuid.UUID,
        *,
        fit_view: bool | None = None,
        save_view: bool = True,
    ) -> None:
        """Delegate image activation to the coordinator with optional zoom-fit.

        Args:
            image_id: Catalog identifier to activate.
            fit_view: Force zoom-to-fit when True.
            save_view: Persist the outgoing viewport transform before navigation.
        """
        self._coordinator.set_current_image(
            image_id, fit_view=fit_view, save_view=save_view
        )

    def reset(self) -> None:
        """Cancel pending work and clear the active image selection."""
        self._coordinator.reset()

    def display_current_image(self, *, fit_view: bool) -> None:
        """Render the catalog's current image via the coordinator."""
        self._coordinator.display_current_image(fit_view=fit_view)

    def prefetch_neighbors(
        self, image_id: uuid.UUID, *, candidates: Sequence[uuid.UUID] | None = None
    ) -> None:
        """Ask the coordinator to warm up neighbors for ``image_id``."""
        self._coordinator.prefetch_neighbors(image_id, candidates=candidates)

    def apply_image(
        self,
        image,
        source_path: Path | None,
        *,
        image_id: uuid.UUID | None = None,
        fit_view: bool,
    ) -> None:
        """Display ``image`` sourced from ``source_path`` via the coordinator."""
        self._coordinator.apply_image(
            image, source_path, image_id=image_id, fit_view=fit_view
        )

    def apply_config(self, config: Config) -> None:
        """Forward configuration changes to the coordinator."""
        self._coordinator.apply_config(config)

    def on_mask_service_attached(self, service) -> None:
        """Forward mask service attachment to the coordinator."""
        self._coordinator.on_mask_service_attached(service)

    def on_mask_service_detached(self) -> None:
        """Forward mask service removal to the coordinator."""
        self._coordinator.on_mask_service_detached()

    def on_sam_manager_attached(self, manager) -> None:
        """Provide the SAM manager to the coordinator."""
        self._coordinator.on_sam_manager_attached(manager)

    def on_sam_manager_detached(self) -> None:
        """Inform the coordinator that SAM support is unavailable."""
        self._coordinator.on_sam_manager_detached()

    def handle_tile_ready(self, key: SceneLayerTileKey) -> None:
        """Mark the active region dirty when a matching prefetched tile arrives."""
        if not self._rendering.has_renderable_content():
            return
        dirty_rect = self._rendering.dirty_rect_for_tile_key(key)
        if dirty_rect is None:
            return
        self._mark_dirty(dirty_rect)

    def handle_pyramid_ready(self, asset_key: SceneLayerAssetKey | None) -> None:
        """Trigger a repaint when the active image's pyramid finishes."""
        qpane = self._qpane
        if asset_key is None:
            return
        render_plan = self._rendering.calculateRenderPlan(
            use_pan=None,
            is_blank=qpane._is_blank,
        )
        if render_plan is None:
            return
        if not any(
            getattr(item, "pyramid_asset_key", None) == asset_key
            for item in render_plan.render_items
        ):
            return
        logger.info(
            "Pyramid ready for current scene layer; scheduling repaint (asset_key=%s)",
            asset_key,
        )
        qpane.markDirty()
        qpane.update()
