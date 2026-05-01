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

"""Controllers that keep mask-specific logic out of the QPane QWidget."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QImage

if TYPE_CHECKING:  # pragma: no cover
    from ..qpane import QPane
    from .mask import MaskLayer
    from .mask_service import MaskService
    from .mask_undo import MaskUndoState
logger = logging.getLogger(__name__)


class MaskDelegate:
    """Bridge QPane mask calls to MaskService, undo helpers, and autosave wiring."""

    def __init__(self, qpane: "QPane") -> None:
        """Capture the owning QPane so mask helpers can reach shared state."""
        self._qpane = qpane
        self._catalog_cache = None
        self._mask_undo_slot = None

    def _catalog(self):
        """Return the cached catalog, fetching it from the QPane on first use."""
        catalog = self._catalog_cache
        if catalog is None:
            catalog = self._qpane.catalog()
            self._catalog_cache = catalog
        return catalog

    def _fallbacks(self):
        """Return the feature fallback tracker owned by the QPane."""
        return self._qpane.featureFallbacks()

    # Mask service lifecycle
    def mask_feature_available(self) -> bool:
        """Return True when both a mask manager and service are wired up."""
        mask_manager = self._catalog().maskManager()
        return self._mask_service is not None and mask_manager is not None

    def sync_mask_activation_for_image(self, image_id: uuid.UUID | None) -> bool:
        """Ensure a mask is active for ``image_id`` before brush usage."""
        service = self._mask_service
        if service is None:
            return False
        mask_ready = service.ensureTopMaskActiveForImage(image_id)
        tools = self._qpane._tools_manager
        if (
            not mask_ready
            and tools.get_control_mode() == self._qpane.CONTROL_MODE_DRAW_BRUSH
        ):
            logger.info(
                "Switching to pan/zoom because image %s has no active mask for brush.",
                image_id,
            )
            self._qpane.setControlMode(self._qpane.CONTROL_MODE_PANZOOM)
            return False
        return mask_ready

    def attachMaskService(self, service: "MaskService") -> None:
        """Connect a new mask service, registering hooks and autosave wiring."""
        qpane = self._qpane
        if qpane.mask_service is not None:
            logger.error(
                "attachMaskService called while a mask service is already attached (current=%s, incoming=%s)",
                type(qpane.mask_service).__name__,
                type(service).__name__,
            )
            raise RuntimeError("Mask service already attached")
        qpane.mask_service = service
        qpane.mask_controller = service.controller
        scene_mutations = getattr(qpane, "sceneMutationCoordinator", None)
        if callable(scene_mutations) and hasattr(
            service, "setSceneMutationCoordinator"
        ):
            service.setSceneMutationCoordinator(scene_mutations())
        qpane.swapDelegate.on_mask_service_attached(service)
        workflow = qpane._masks_controller
        undo_slot = workflow.on_mask_undo_stack_changed
        service.connectUndoStackChanged(undo_slot)
        self._mask_undo_slot = undo_slot
        qpane.refreshMaskAutosavePolicy()
        registry = qpane._state.cache_registry
        controller = qpane.mask_controller
        if registry is not None and controller is not None:
            registry.attach_mask_controller(controller)
        qpane.applyCacheSettings()

    def detachMaskService(self) -> None:
        """Tear down the active mask service and undo/hook registrations."""
        qpane = self._qpane
        service = qpane.mask_service
        if service is None:
            return
        if hasattr(service, "setSceneMutationCoordinator"):
            service.setSceneMutationCoordinator(None)
        qpane.swapDelegate.on_mask_service_detached()
        try:
            if self._mask_undo_slot is not None:
                service.disconnectUndoStackChanged(self._mask_undo_slot)
        except AttributeError:
            pass
        self._mask_undo_slot = None
        try:
            service.resetStrokePipeline(clear_counter=True, request_redraw=False)
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("Failed to reset mask stroke state during detach.")
        qpane.mask_service = None
        qpane.mask_controller = None
        qpane.refreshMaskAutosavePolicy()
        qpane.applyCacheSettings()

    # Signal helpers
    def on_mask_undo_stack_changed(self, mask_id: uuid.UUID) -> None:
        """Relay undo stack updates to the workflow controller."""
        self._qpane._masks_controller.on_mask_undo_stack_changed(mask_id)

    def on_mask_saved(self, mask_id: str, path: str) -> None:
        """Notify the workflow when an autosave finished on disk."""
        self._qpane._masks_controller.on_mask_saved(mask_id, path)

    # Autosave
    def refreshMaskAutosavePolicy(self) -> None:
        """Reapply autosave configuration or detach the manager if needed."""
        service = self._mask_service
        if service is not None:
            service.refreshAutosavePolicy()
            return
        current = None
        accessor = getattr(self._qpane, "autosaveManager", None)
        if callable(accessor):
            try:
                current = accessor()
            except Exception:
                logger.debug("autosaveManager() raised during autosave teardown")
        if current is not None:
            self._qpane.hooks.detachAutosaveManager()

    # Public QPane API forwards
    def get_mask_undo_state(self, mask_id: uuid.UUID) -> "MaskUndoState | None":
        """Expose the undo depth for ``mask_id`` when a service is attached."""
        service = self._mask_service
        if service is None:
            return None
        return service.getUndoState(mask_id)

    def load_mask_from_file(self, path: str) -> "uuid.UUID | None":
        """Load a mask from ``path`` via the mask service when available."""
        if not self.mask_feature_available():
            return self._fallbacks().get("mask", "load_mask_from_file", default=None)
        service = self._mask_service
        if service is None:
            return None
        return service.loadMaskFromPath(path)

    def update_mask_from_file(self, mask_id: uuid.UUID, file_path: str) -> bool:
        """Replace mask pixels for ``mask_id`` using the file at ``file_path``."""
        if not self.mask_feature_available():
            return bool(
                self._fallbacks().get("mask", "update_mask_from_file", default=False)
            )
        if not mask_id or not file_path:
            logger.warning(
                "update_mask_from_file aborted: mask_id or file_path missing (mask_id=%s, file_path=%s)",
                mask_id,
                file_path,
            )
            return False
        service = self._mask_service
        if service is None:
            logger.error(
                "update_mask_from_file aborted: mask service is unavailable (mask_id=%s).",
                mask_id,
            )
            return False
        return service.updateMaskFromPath(mask_id, file_path)

    def create_blank_mask(self, size: QSize) -> uuid.UUID | None:
        """Create a blank mask layer sized to ``size`` when tooling exists."""
        if not self.mask_feature_available():
            return self._fallbacks().get("mask", "create_blank_mask", default=None)
        service = self._mask_service
        if service is None:
            return None
        return service.createBlankMask(size)

    def set_active_mask_id(self, mask_id):
        """Select ``mask_id`` for editing and repaint if it moved."""
        if not self.mask_feature_available():
            return bool(self._fallbacks().get("mask", "setActiveMaskID", default=False))
        service = self._mask_service
        if service is None:
            logger.error(
                "setActiveMaskID aborted: mask service is unavailable (mask_id=%s).",
                mask_id,
            )
            return False
        was_moved = service.activateMask(mask_id)
        if mask_id is not None and was_moved:
            self._qpane.markDirty()
            self._qpane.update()
        return True

    def prefetch_mask_overlays(
        self, image_id: uuid.UUID | None, *, reason: str = "navigation"
    ) -> bool:
        """Warm mask renders for ``image_id`` for the given ``reason``."""
        if not self.mask_feature_available():
            return False
        service = self._mask_service
        if service is None:
            return False
        return service.prefetchColorizedMasks(image_id, reason=reason)

    def set_mask_properties(
        self, mask_id, color: QColor | None = None, opacity: float | None = None
    ) -> bool:
        """Update mask presentation attributes when a service is connected."""
        if not self.mask_feature_available():
            return bool(
                self._fallbacks().get("mask", "setMaskProperties", default=False)
            )
        service = self._mask_service
        if service is None:
            logger.error(
                "setMaskProperties aborted: mask service is unavailable (mask_id=%s).",
                mask_id,
            )
            return False
        return service.setMaskProperties(mask_id, color=color, opacity=opacity)

    def remove_mask_from_image(self, image_id: uuid.UUID, mask_id: uuid.UUID) -> bool:
        """Detach ``mask_id`` from ``image_id`` via the mask service."""
        if not self.mask_feature_available():
            return bool(
                self._fallbacks().get("mask", "removeMaskFromImage", default=False)
            )
        service = self._mask_service
        if service is None:
            logger.error(
                "removeMaskFromImage aborted: mask service is unavailable (image_id=%s, mask_id=%s).",
                image_id,
                mask_id,
            )
            return False
        return service.removeMaskFromImage(image_id, mask_id)

    def cycle_masks_forward(self) -> bool:
        """Promote the next mask in the stack, wrapping around when needed."""
        if not self.mask_feature_available():
            return bool(
                self._fallbacks().get("mask", "cycleMasksForward", default=False)
            )
        service = self._mask_service
        current_id = self._catalog().currentImageID()
        if service is None:
            logger.error("cycleMasksForward aborted: mask service is unavailable.")
            return False
        if current_id is None:
            logger.info("cycleMasksForward aborted: no current image selected.")
            return False
        service.cycleMasks(current_id, forward=True)
        return True

    def cycle_masks_backward(self) -> bool:
        """Cycle the mask stack backward for the current image."""
        if not self.mask_feature_available():
            return bool(
                self._fallbacks().get("mask", "cycleMasksBackward", default=False)
            )
        service = self._mask_service
        current_id = self._catalog().currentImageID()
        if service is None:
            logger.error("cycleMasksBackward aborted: mask service is unavailable.")
            return False
        if current_id is None:
            logger.info("cycleMasksBackward aborted: no current image selected.")
            return False
        service.cycleMasks(current_id, forward=False)
        return True

    def get_active_mask_image(self) -> QImage | None:
        """Return the active mask image snapshot when mask tooling is active."""
        if not self.mask_feature_available():
            return self._fallbacks().get("mask", "getActiveMaskImage", default=None)
        service = self._mask_service
        if service is None:
            logger.error("getActiveMaskImage aborted: mask service is unavailable.")
            return None
        return service.getActiveMaskImage()

    def undo_mask_edit(self) -> bool:
        """Undo the most recent change on the active mask."""
        if not self.mask_feature_available():
            return bool(self._fallbacks().get("mask", "undoMaskEdit", default=False))
        service = self._mask_service
        if service is None:
            logger.error("undoMaskEdit aborted: mask service is unavailable.")
            return False
        return service.undoActiveMaskEdit()

    def redo_mask_edit(self) -> bool:
        """Redo the previously undone change on the active mask."""
        if not self.mask_feature_available():
            return bool(self._fallbacks().get("mask", "redoMaskEdit", default=False))
        service = self._mask_service
        if service is None:
            logger.error("redoMaskEdit aborted: mask service is unavailable.")
            return False
        return service.redoActiveMaskEdit()

    def invalidate_active_mask_cache(self) -> bool:
        """Drop cached mask renders for the active mask if the service exists."""
        if not self.mask_feature_available():
            return bool(
                self._fallbacks().get(
                    "mask", "invalidateActiveMaskCache", default=False
                )
            )
        service = self._mask_service
        if service is None:
            logger.error(
                "invalidateActiveMaskCache aborted: mask service is unavailable."
            )
            return False
        service.invalidateActiveMaskCache()
        return True

    def update_mask_region(
        self,
        dirty_image_rect: QRect,
        active_mask_layer: "MaskLayer",
        *,
        sub_mask_image: QImage | None = None,
        force_async_colorize: bool = False,
    ) -> bool:
        """Update the dirty region of the active mask layer on screen."""
        if not self.mask_feature_available():
            return bool(
                self._fallbacks().get("mask", "updateMaskRegion", default=False)
            )
        service = self._mask_service
        if service is None:
            logger.error("updateMaskRegion aborted: mask service is unavailable.")
            return False
        service.updateMaskRegion(
            dirty_image_rect,
            active_mask_layer,
            sub_mask_image=sub_mask_image,
            force_async_colorize=force_async_colorize,
        )
        return True

    def set_brush_size(self, size: int) -> None:
        """Persist ``size`` on the interaction layer and refresh the cursor."""
        qpane = self._qpane
        qpane.interaction.brush_size = max(1, int(size))
        self.update_brush_cursor(erase_indicator=qpane.interaction.alt_key_held)

    def get_brush_size(self) -> int:
        """Expose the brush size recorded on the interaction layer."""
        return self._qpane.interaction.brush_size

    def update_brush_cursor(self, *, erase_indicator: bool = False) -> None:
        """Ask the workflow to refresh the cursor preview when masks exist."""
        qpane = self._qpane
        workflow = getattr(qpane, "_masks", None)
        if workflow is None:
            qpane.interaction.custom_cursor = None
            qpane.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return
        workflow.update_brush_cursor(erase_indicator=erase_indicator)

    def generate_and_apply_mask(
        self, bbox: np.ndarray, erase_mode: bool = False
    ) -> bool:
        """Delegate SAM-driven bbox generation to the workflow."""
        workflow = getattr(self._qpane, "_masks", None)
        if workflow is None:
            logger.error("generate_and_apply_mask aborted: mask workflow unavailable")
            return False
        return workflow.generate_and_apply_mask(bbox, erase_mode=erase_mode)

    # Helpers
    @property
    def _mask_service(self) -> "MaskService | None":
        """Expose the QPane-owned MaskService if present."""
        return getattr(self._qpane, "mask_service", None)
