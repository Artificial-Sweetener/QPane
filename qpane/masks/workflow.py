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

"""Masks controller that centralizes mask and SAM orchestration for QPane instances.

The controller wires overlay ordering, activation deferment, diagnostics, and

signal relays through ``QPane.interaction`` and catalog navigation so the

QWidget facade stays focused on presentation.
"""

from __future__ import annotations


import logging

import uuid

from dataclasses import dataclass

from typing import TYPE_CHECKING


import numpy as np

from PySide6.QtCore import QRect, QSize

from PySide6.QtGui import QColor, QCursor, QImage, Qt


from ..core import Config
from ..types import DiagnosticRecord

from ..masks import MaskDelegate
from .mask_diagnostics import (
    mask_brush_detail_provider,
    mask_job_detail_provider,
    mask_summary_provider,
)
from ..rendering import CoordinateContext

from ..sam import SamDelegate


logger = logging.getLogger(__name__)


if TYPE_CHECKING:  # pragma: no cover

    from ..catalog import Catalog, NavigationEvent
    from ..masks.mask_service import MaskService
    from ..qpane import QPane
    from ..swap import SwapDelegate
    from ..core.diagnostics_broker import Diagnostics


@dataclass(frozen=True)
class MaskActivationSyncResult:
    """Describe whether overlays should remain suspended during activation."""

    activation_pending: bool = False
    prefetch_requested: bool = False


@dataclass(frozen=True)
class MaskInfo:
    """Metadata describing a mask layer for host-facing presentation."""

    mask_id: uuid.UUID
    color: QColor | None
    label: str | None
    opacity: float | None
    image_ids: tuple[uuid.UUID, ...]
    is_active: bool


class _MaskUndoAPI:
    """Own undo/redo helpers so Masks can stay a thin facade."""

    def __init__(self, owner: "Masks") -> None:
        """Store the owning Masks controller for delegate lookups."""
        self._owner = owner

    def push_active_mask_state(self) -> bool:
        """Request an undo-state snapshot from the active mask service."""
        service = self._owner.mask_service()
        if service is None:
            return False
        return getattr(service, "pushActiveMaskState", lambda: False)()

    def undo_mask_edit(self) -> bool:
        """Dispatch an undo request to the mask delegate when available."""
        delegate = self._owner._ensure_mask_delegate()
        return False if delegate is None else delegate.undo_mask_edit()

    def redo_mask_edit(self) -> bool:
        """Dispatch a redo request to the mask delegate when available."""
        delegate = self._owner._ensure_mask_delegate()
        return False if delegate is None else delegate.redo_mask_edit()

    def get_mask_undo_state(self, mask_id: uuid.UUID):
        """Return the undo stack state for ``mask_id`` or ``None`` when missing."""
        delegate = self._owner._ensure_mask_delegate()
        return None if delegate is None else delegate.get_mask_undo_state(mask_id)

    def update_mask_region(
        self,
        dirty_image_rect: QRect,
        active_mask_layer,
        *,
        sub_mask_image: QImage | None = None,
        force_async_colorize: bool = False,
    ) -> bool:
        """Apply edits to the active mask region using the delegate."""
        delegate = self._owner._ensure_mask_delegate()
        if delegate is None:
            return False
        return delegate.update_mask_region(
            dirty_image_rect,
            active_mask_layer,
            sub_mask_image=sub_mask_image,
            force_async_colorize=force_async_colorize,
        )

    def invalidate_active_mask_cache(self) -> bool:
        """Invalidate the active mask cache via the delegate."""
        delegate = self._owner._ensure_mask_delegate()
        return False if delegate is None else delegate.invalidate_active_mask_cache()


class _BrushCursorAdapter:
    """Keep brush cursor rendering isolated from the Masks facade."""

    def __init__(self, owner: "Masks") -> None:
        """Capture the owning Masks controller."""
        self._owner = owner

    def update_cursor(self, erase_indicator: bool = False) -> None:
        """Apply the appropriate brush cursor to the QPane."""
        qpane = self._owner._qpane
        if not self._owner.mask_feature_available():
            qpane.interaction.custom_cursor = None
            qpane.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return
        cursor = self.compute_cursor(erase_indicator=erase_indicator)
        if cursor is None:
            qpane.interaction.custom_cursor = None
            qpane.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return
        qpane.interaction.custom_cursor = cursor
        qpane.setCursor(cursor)

    def cursor_provider(self, _qpane_instance: "QPane") -> QCursor | None:
        """Return the cursor used for the active brush state."""
        qpane = self._owner._qpane
        if not self._owner.mask_feature_available():
            qpane.interaction.custom_cursor = None
            return QCursor(Qt.CursorShape.ArrowCursor)
        cursor = self.compute_cursor(
            erase_indicator=getattr(qpane.interaction, "alt_key_held", False)
        )
        if cursor is None:
            qpane.interaction.custom_cursor = None
            return QCursor(Qt.CursorShape.ArrowCursor)
        qpane.interaction.custom_cursor = cursor
        return cursor

    def compute_cursor(self, *, erase_indicator: bool) -> QCursor | None:
        """Construct a cursor image sized to the logical brush diameter."""
        qpane = self._owner._qpane
        service = self._owner._ensure_mask_service()
        if service is None:
            return None
        color = service.getActiveMaskColor() or QColor(128, 128, 128)
        try:
            view = qpane.view()
        except AttributeError:
            logger.debug("Brush cursor requested before view initialization")
            return None
        viewport = getattr(view, "viewport", None)
        zoom = getattr(viewport, "zoom", 1.0) or 1.0
        brush_size = max(1, int(getattr(qpane.interaction, "brush_size", 1)))
        physical_size = brush_size * zoom
        context = CoordinateContext(qpane)
        logical_size = context.physical_to_logical(physical_size)
        viewport_size = qpane.size()
        if logical_size > min(viewport_size.width(), viewport_size.height()):
            return None
        cursor_size = max(2, int(logical_size))
        paint_cursor, erase_cursor = qpane.cursor_builder.get_brush_cursor_pair(
            cursor_size,
            color,
        )
        return erase_cursor if erase_indicator else paint_cursor


class _SamWorkflow:
    """Encapsulate SAM orchestration so Masks can delegate high-level calls."""

    def __init__(self, owner: "Masks") -> None:
        """Store the owning Masks controller."""
        self._owner = owner

    def attach_manager(self, sam_manager) -> None:
        """Attach the SAM manager to the QPane and delegate."""
        qpane = self._owner._qpane
        if qpane.samManager() is not None:
            raise RuntimeError("SAM manager already attached")
        delegate = self._owner._ensure_sam_delegate()
        if delegate is None:
            raise RuntimeError("SAM delegate unavailable")
        qpane._set_sam_manager(sam_manager)
        delegate.attachManager(sam_manager)
        self._owner._sam_delegate = delegate

    def detach_manager(self) -> None:
        """Detach the SAM manager from the QPane and delegate."""
        qpane = self._owner._qpane
        manager = qpane.samManager()
        if manager is None:
            return
        delegate = self._owner._ensure_sam_delegate()
        if delegate is not None:
            delegate.detachManager()
        qpane._set_sam_manager(None)
        self._owner._sam_delegate = None

    def reset_active_predictor(self) -> None:
        """Clear the active SAM predictor if one is attached."""
        delegate = self._owner._ensure_sam_delegate()
        if delegate is not None:
            delegate.resetActivePredictor()

    def generate_and_apply_mask(self, bbox, erase_mode: bool = False) -> bool:
        """Generate a mask from ``bbox`` using SAM and apply it to the active image."""
        owner = self._owner
        qpane = owner._qpane
        fallbacks = qpane.featureFallbacks()
        if not owner.mask_feature_available():
            return bool(fallbacks.get("mask", "generate_and_apply_mask", default=False))
        if not owner.sam_feature_available():
            return bool(fallbacks.get("sam", "generate_and_apply_mask", default=False))
        service = owner._ensure_mask_service()
        if service is None:
            logger.error(
                "generate_and_apply_mask aborted: mask service is unavailable (image_path=%s)",
                qpane.currentImagePath,
            )
            return False
        path_display = (
            str(qpane.currentImagePath)
            if qpane.currentImagePath is not None
            else "<none>"
        )
        bbox_payload = bbox.tolist() if hasattr(bbox, "tolist") else bbox
        if qpane.original_image.isNull():
            logger.warning(
                "generate_and_apply_mask aborted: no image loaded (image_path=%s)",
                path_display,
            )
            return False
        if service.getActiveMaskId() is None:
            logger.info(
                "generate_and_apply_mask aborted: no active mask selected (image_path=%s)",
                path_display,
            )
            return False
        delegate = owner._ensure_sam_delegate()
        predictor = None if delegate is None else delegate.activePredictor
        if predictor is None:
            logger.info(
                "generate_and_apply_mask aborted: SAM predictor not ready (image_path=%s)",
                path_display,
            )
            return False
        try:
            mask_array_bool = service.predict_mask_from_box(predictor, bbox)
        except ValueError as exc:
            logger.warning(
                "generate_and_apply_mask aborted: bounding box invalid (image_path=%s, bbox=%s, erase_mode=%s, reason=%s)",
                path_display,
                bbox_payload,
                erase_mode,
                exc,
            )
            return False
        except RuntimeError as exc:
            logger.error(
                "generate_and_apply_mask aborted: SAM services unavailable (image_path=%s, reason=%s)",
                path_display,
                exc,
            )
            return False
        except Exception:
            logger.exception(
                "generate_and_apply_mask failed (image_path=%s, bbox=%s, erase_mode=%s)",
                path_display,
                bbox_payload,
                erase_mode,
            )
            return False
        if mask_array_bool is None:
            logger.warning(
                "generate_and_apply_mask aborted: SAM returned no mask (image_path=%s, bbox=%s)",
                path_display,
                bbox_payload,
            )
            return False
        mask_array_uint8 = np.asarray(mask_array_bool, dtype=np.uint8) * 255
        service.handleGeneratedMask(mask_array_uint8, bbox, erase_mode)
        return True


class Masks:
    """Controller that owns mask and SAM orchestration on behalf of QPane."""

    def __init__(
        self,
        *,
        qpane: QPane,
        catalog: Catalog,
        swap_delegate: SwapDelegate,
        cache_registry,  # cache registry kept for future diagnostics usage
        mask_delegate: MaskDelegate | None = None,
        sam_delegate: SamDelegate | None = None,
    ) -> None:
        """Wire QPane collaborators plus mask and SAM delegates.

        Args:
            qpane: Owning QPane widget coordinating interaction state.
            catalog: Catalog facade used for navigation hooks and metadata.
            swap_delegate: Swap delegate for image loading and activation callbacks.
            cache_registry: Cache registry used when mask tools inspect cache usage.
            mask_delegate: Optional delegate that handles mask-specific workflows.
            sam_delegate: Optional delegate for SAM predictors; created when absent.
        """
        self._qpane = qpane
        self._catalog = catalog
        self._swap_delegate = swap_delegate
        self._cache_registry = cache_registry
        delegate = mask_delegate if mask_delegate is not None else MaskDelegate(qpane)
        self._mask_delegate: MaskDelegate | None = delegate
        sam = sam_delegate
        if sam is None:
            sam = SamDelegate(
                qpane=qpane,
                swap_delegate=swap_delegate,
                cache_registry=cache_registry,
            )
        else:
            sam.updateCacheRegistry(cache_registry)
        self._sam_delegate: SamDelegate | None = sam
        self._mask_service: MaskService | None = getattr(qpane, "mask_service", None)
        self._last_navigation_event: "NavigationEvent | None" = None
        self._pending_activation_images: set[uuid.UUID] = set()
        self._cached_mask_service_records: tuple[DiagnosticRecord, ...] = ()
        self._diagnostics_registered = False
        self._undo_api = _MaskUndoAPI(self)
        self._brush_cursor_adapter = _BrushCursorAdapter(self)
        self._sam_workflow = _SamWorkflow(self)
        # Subscribe to navigation so we can own overlay suspension ordering and activation sequencing.
        self._catalog.onNavigationStarted(self.on_navigation_started)
        if self._mask_service is not None:
            self._register_activation_hooks(self._mask_service)
        self._register_interaction_hooks()

    # Internal hooks
    # Interaction hooks

    def _register_interaction_hooks(self) -> None:
        """Wire overlays and cursor providers through the interaction layer."""
        qpane = self._qpane
        interaction = qpane.interaction
        interaction.unregisterOverlay("mask")
        interaction.unregisterCursorProvider(qpane.CONTROL_MODE_DRAW_BRUSH)
        interaction.registerCursorProvider(
            qpane.CONTROL_MODE_DRAW_BRUSH,
            self._brush_cursor_adapter.cursor_provider,
        )

    def on_navigation_started(self, event: "NavigationEvent") -> None:
        """Record navigation metadata and suspend overlays when needed."""
        self._last_navigation_event = event
        interaction = self._qpane.interaction
        if not getattr(interaction, "overlays_suspended", False):
            interaction.suspend_overlays_for_navigation()

    def on_swap_applied(
        self, image_id: uuid.UUID | None, activation_pending: bool
    ) -> None:
        """React to swap completion so overlays resume in workflow order."""
        resolved_id = self._resolve_image_id(image_id)
        interaction = self._qpane.interaction
        if activation_pending and resolved_id is not None:
            self._pending_activation_images.add(resolved_id)
            interaction.overlays_resume_pending = True
            if not interaction.overlays_suspended:
                interaction.suspend_overlays_for_navigation()
            return
        if resolved_id is not None:
            self._pending_activation_images.discard(resolved_id)
        interaction.overlays_resume_pending = False
        if interaction.overlays_suspended:
            interaction.resume_overlays()
        self._qpane.update()

    def handle_activation_ready(
        self,
        image_id: uuid.UUID | None,
        *,
        resumed_with_update: bool,
    ) -> None:
        """Receive activation completion notifications from MaskService."""
        resolved_id = self._resolve_image_id(image_id)
        if resolved_id is not None:
            self._pending_activation_images.discard(resolved_id)
        interaction = self._qpane.interaction
        interaction.overlays_resume_pending = False
        if interaction.overlays_suspended:
            interaction.resume_overlays()
        if resumed_with_update:
            self._qpane.update()

    def _handle_activation_pending(self, image_id: uuid.UUID | None) -> None:
        """Record pending activation state and keep overlays suspended."""
        resolved_id = self._resolve_image_id(image_id)
        if resolved_id is None:
            return
        self._pending_activation_images.add(resolved_id)
        interaction = self._qpane.interaction
        interaction.overlays_resume_pending = True
        if not getattr(interaction, "overlays_suspended", False):
            interaction.suspend_overlays_for_navigation()

    # Diagnostics

    def register_diagnostics(self, broker: "Diagnostics") -> None:
        """Register mask diagnostics providers once via the diagnostics broker."""
        if self._diagnostics_registered:
            return
        broker.register_mask_providers(self._mask_summary_diagnostics, tier="core")
        broker.register_mask_providers(self._mask_job_diagnostics)
        broker.register_mask_providers(self._mask_service_diagnostics)
        broker.register_mask_providers(self._mask_brush_diagnostics)
        self._diagnostics_registered = True

    def _mask_summary_diagnostics(self, qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
        """Return summary diagnostics rows for overlay consumption."""
        return tuple(mask_summary_provider(qpane))

    def _mask_job_diagnostics(self, qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
        """Return detailed mask job diagnostics rows."""
        return tuple(mask_job_detail_provider(qpane))

    def _mask_service_diagnostics(self, qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
        """Return cached or fresh diagnostics emitted by the mask service."""
        service = self._ensure_mask_service()
        if service is None:
            return self._cached_mask_service_records
        try:
            records = tuple(service._diagnostics_provider(qpane))
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("Mask service diagnostics provider failed")
            return self._cached_mask_service_records
        self._cached_mask_service_records = records
        return records

    def _mask_brush_diagnostics(self, qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
        """Return brush diagnostics rows for the detail overlay tier."""
        return tuple(mask_brush_detail_provider(qpane))

    # Signal relays

    def on_mask_saved(self, mask_id: str, path: str) -> None:
        """Relay autosave completions through the QPane signal surface."""
        self._qpane.maskSaved.emit(mask_id, path)

    def on_mask_undo_stack_changed(self, mask_id: uuid.UUID) -> None:
        """Relay undo stack notifications through the QPane signal surface."""
        self._qpane.maskUndoStackChanged.emit(mask_id)

    def _resolve_image_id(
        self,
        candidate: uuid.UUID | None,
        *,
        use_fallback: bool = True,
    ) -> uuid.UUID | None:
        """Resolve an image id from callbacks, optionally falling back to current state."""
        if isinstance(candidate, uuid.UUID):
            return candidate
        if not use_fallback:
            return None
        event = self._last_navigation_event
        target_id = getattr(event, "target_id", None)
        if isinstance(target_id, uuid.UUID):
            return target_id
        current = self.current_image_id()
        return current if isinstance(current, uuid.UUID) else None

    # Accessors and helpers

    def mask_delegate(self) -> MaskDelegate | None:
        """Return the mask delegate coordinating QPane interactions."""
        return self._mask_delegate

    def sam_delegate(self) -> SamDelegate | None:
        """Return the SAM delegate when the feature is available."""
        return self._sam_delegate

    def mask_service(self) -> MaskService | None:
        """Expose the active mask service, updating hooks if needed."""
        return self._ensure_mask_service()

    def apply_config(self, config: Config) -> None:
        """Forward configuration updates to the mask service when present."""
        service = self.mask_service()
        if service is None:
            return
        service.applyConfig(config)
        configure = getattr(service, "configureStrokeDiagnostics", None)
        if callable(configure):
            configure(config)

    def catalog(self) -> Catalog:
        """Return the catalog collaborator the controller wraps."""
        return self._catalog

    def swap_delegate(self) -> SwapDelegate:
        """Expose the swap delegate used for image loading and apply hooks."""
        return self._swap_delegate

    def mask_feature_available(self) -> bool:
        """Return True when mask tooling is installed for the host QPane."""
        return self._ensure_mask_service() is not None

    def sam_feature_available(self) -> bool:
        """Return True when SAM manager support is active for the host QPane."""
        delegate = self._ensure_sam_delegate()
        if delegate is None:
            return False
        return getattr(delegate, "manager", None) is not None

    # Internal lazy fetchers to tolerate attach/detach during runtime.

    def _ensure_mask_delegate(self) -> MaskDelegate | None:
        """Return the mask delegate if attached."""
        return self._mask_delegate

    def _ensure_mask_service(self) -> MaskService | None:
        """Return the mask service while keeping activation hooks aligned."""
        svc = getattr(self._qpane, "mask_service", None)
        if svc is not None and svc is not self._mask_service:
            self._mask_service = svc
            self._register_activation_hooks(svc)
        elif svc is None and self._mask_service is not None:
            self._mask_service = None
        return svc

    def _ensure_sam_delegate(self) -> SamDelegate | None:
        """Return the SAM delegate if attached."""
        return self._sam_delegate

    def _register_activation_hooks(self, service: MaskService) -> None:
        """Connect activation lifecycle hooks from the mask service."""
        setter = getattr(service, "set_activation_resume_hooks", None)
        if not callable(setter):  # pragma: no cover - defensive for legacy builds
            logger.debug("MaskService lacks activation resume hooks; skipping wiring")
            return
        setter(
            self._make_activation_resume_callback(resumed_with_update=False),
            self._make_activation_resume_callback(resumed_with_update=True),
            self._handle_activation_pending,
        )

    def _reset_activation_hooks(self, service: MaskService) -> None:
        """Disconnect activation hooks when the service detaches."""
        setter = getattr(service, "set_activation_resume_hooks", None)
        if not callable(setter):  # pragma: no cover - defensive for legacy builds
            return
        setter(None, None, None)

    def _make_activation_resume_callback(self, *, resumed_with_update: bool):
        """Return a callback that resumes overlays after activation."""

        def _callback(image_id: uuid.UUID | None = None) -> None:
            """Handle activation completion and resume overlays."""
            self.handle_activation_ready(
                image_id,
                resumed_with_update=resumed_with_update,
            )

        return _callback

    # Public convenience properties

    def active_mask_id(self) -> uuid.UUID | None:
        """Return the currently active mask id when one is selected."""
        svc = self._ensure_mask_service()
        return None if svc is None else svc.getActiveMaskId()

    def getActiveMaskID(self) -> uuid.UUID | None:
        """Expose the currently active mask identifier via the public facade."""
        return self.active_mask_id()

    def maskIDsForImage(self, image_id: uuid.UUID | None = None) -> list[uuid.UUID]:
        """Return mask identifiers for ``image_id`` (or the current image when omitted)."""
        resolved_id = self._resolve_image_id(image_id)
        if resolved_id is None:
            return []
        service = self._ensure_mask_service()
        manager = getattr(service, "manager", None)
        if manager is None:
            return []
        try:
            return list(manager.get_mask_ids_for_image(resolved_id))
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("Failed to fetch mask ids for image %s", resolved_id)
            return []

    def maskInfo(self, mask_id: uuid.UUID) -> MaskInfo | None:
        """Return metadata for ``mask_id`` suitable for UI display."""
        service = self._ensure_mask_service()
        manager = getattr(service, "manager", None)
        if manager is None:
            return None
        try:
            layer = manager.get_layer(mask_id)
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("Failed to fetch mask layer for %s", mask_id)
            return None
        if layer is None:
            return None
        try:
            image_ids = tuple(manager.get_images_for_mask(mask_id))
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("Failed to fetch image ids for mask %s", mask_id)
            image_ids = ()
        label = getattr(layer, "label", None)
        if isinstance(label, str):
            label = label.strip() or None
        opacity = getattr(layer, "opacity", None)
        try:
            opacity = float(opacity) if opacity is not None else None
        except (TypeError, ValueError):
            opacity = None
        return MaskInfo(
            mask_id=mask_id,
            color=getattr(layer, "color", None),
            label=label,
            opacity=opacity,
            image_ids=image_ids,
            is_active=mask_id == self.active_mask_id(),
        )

    def listMasksForImage(
        self, image_id: uuid.UUID | None = None
    ) -> tuple[MaskInfo, ...]:
        """Return mask metadata for ``image_id`` (or the current image) as a tuple."""
        resolved_id = self._resolve_image_id(image_id)
        if resolved_id is None:
            return ()
        mask_ids = self.maskIDsForImage(resolved_id)
        info: list[MaskInfo] = []
        for mask_id in mask_ids:
            record = self.maskInfo(mask_id)
            if record is not None and resolved_id in record.image_ids:
                info.append(record)
        return tuple(info)

    # Lifecycle / autosave

    def attachMaskService(self, service: MaskService) -> None:
        """Attach the mask service via MaskDelegate to avoid QPane indirection."""
        delegate = self._ensure_mask_delegate()
        if delegate is None:
            logger.error("attachMaskService failed: mask delegate unavailable")
            raise RuntimeError("Mask delegate unavailable")
        delegate.attachMaskService(service)
        self._mask_service = service
        self._mask_delegate = delegate
        self._register_activation_hooks(service)

    def detachMaskService(self) -> None:
        """Detach the mask service via MaskDelegate to avoid QPane indirection."""
        delegate = self._ensure_mask_delegate()
        service = self._ensure_mask_service()
        if service is not None:
            self._reset_activation_hooks(service)
        if delegate is not None:
            delegate.detachMaskService()
        self._mask_service = None
        self._pending_activation_images.clear()

    def refreshMaskAutosavePolicy(self) -> None:
        """Refresh autosave wiring via MaskDelegate (no behaviour change)."""
        delegate = self._ensure_mask_delegate()
        if delegate is None:
            return
        delegate.refreshMaskAutosavePolicy()

    # Mask operations

    def load_mask_from_file(self, path: str) -> uuid.UUID | None:
        """Load a mask from ``path`` and return the created mask id."""
        delegate = self._ensure_mask_delegate()
        return None if delegate is None else delegate.load_mask_from_file(path)

    def update_mask_from_file(self, mask_id: uuid.UUID, path: str) -> bool:
        """Replace ``mask_id`` with mask data loaded from ``path``."""
        delegate = self._ensure_mask_delegate()
        return (
            False if delegate is None else delegate.update_mask_from_file(mask_id, path)
        )

    def create_blank_mask(self, size: QSize) -> uuid.UUID | None:
        """Create and activate a blank mask of ``size``."""
        delegate = self._ensure_mask_delegate()
        return None if delegate is None else delegate.create_blank_mask(size)

    def set_mask_properties(
        self,
        mask_id: uuid.UUID,
        *,
        color: QColor | None = None,
        opacity: float | None = None,
    ) -> bool:
        """Update mask color/opacity properties when supported."""
        delegate = self._ensure_mask_delegate()
        return (
            False
            if delegate is None
            else delegate.set_mask_properties(mask_id, color=color, opacity=opacity)
        )

    def remove_mask_from_image(self, image_id: uuid.UUID, mask_id: uuid.UUID) -> bool:
        """Remove ``mask_id`` from the specified image."""
        delegate = self._ensure_mask_delegate()
        return (
            False
            if delegate is None
            else delegate.remove_mask_from_image(image_id, mask_id)
        )

    def cycle_masks_forward(self) -> bool:
        """Advance the active mask selection forward."""
        delegate = self._ensure_mask_delegate()
        return False if delegate is None else delegate.cycle_masks_forward()

    def cycle_masks_backward(self) -> bool:
        """Move the active mask selection backward."""
        delegate = self._ensure_mask_delegate()
        return False if delegate is None else delegate.cycle_masks_backward()

    def get_active_mask_image(self) -> QImage | None:
        """Return the active mask image when available."""
        delegate = self._ensure_mask_delegate()
        return None if delegate is None else delegate.get_active_mask_image()

    # Activation / prefetch -------------------------------------------------

    def set_active_mask_id(self, mask_id: uuid.UUID | None) -> bool:
        """Activate ``mask_id`` if present in the delegate."""
        delegate = self._ensure_mask_delegate()
        return False if delegate is None else delegate.set_active_mask_id(mask_id)

    def sync_mask_activation_for_image(
        self, image_id: uuid.UUID | None
    ) -> MaskActivationSyncResult:
        """Sync activation and overlay prefetching for ``image_id``."""
        resolved_id = self._resolve_image_id(image_id)
        delegate = self._ensure_mask_delegate()
        service = self._ensure_mask_service()
        if delegate is not None:
            try:
                delegate.sync_mask_activation_for_image(resolved_id)
            except Exception:  # pragma: no cover - defensive guard
                logger.exception(
                    "Mask delegate activation sync failed for image %s", resolved_id
                )
        activation_pending = False
        prefetch_requested = False
        if service is not None and resolved_id is not None:
            try:
                activation_pending = bool(service.isActivationPending(resolved_id))
            except Exception:  # pragma: no cover - defensive guard
                logger.exception(
                    "Mask service pending check failed for image %s", resolved_id
                )
                activation_pending = False
        elif resolved_id is not None:
            activation_pending = resolved_id in self._pending_activation_images
        if activation_pending and resolved_id is not None:
            self._pending_activation_images.add(resolved_id)
        elif resolved_id is not None:
            self._pending_activation_images.discard(resolved_id)
        if (
            not activation_pending
            and delegate is not None
            and resolved_id is not None
            and service is None
        ):
            try:
                prefetch_requested = bool(
                    delegate.prefetch_mask_overlays(resolved_id, reason="navigation")
                )
            except Exception:  # pragma: no cover - defensive guard
                logger.exception(
                    "Mask overlay prefetch failed for image %s", resolved_id
                )
                prefetch_requested = False
        return MaskActivationSyncResult(
            activation_pending=activation_pending,
            prefetch_requested=prefetch_requested,
        )

    def prefetch_mask_overlays(
        self, image_id: uuid.UUID | None, *, reason: str = "navigation"
    ) -> bool:
        """Prefetch overlay pixmaps for the requested image."""
        delegate = self._ensure_mask_delegate()
        return (
            False
            if delegate is None
            else delegate.prefetch_mask_overlays(image_id, reason=reason)
        )

    def is_activation_pending(self, image_id: uuid.UUID | None) -> bool:
        """Return True when activation work is still pending for ``image_id``."""
        resolved_id = self._resolve_image_id(image_id)
        if resolved_id is None:
            return False
        service = self._ensure_mask_service()
        if service is not None:
            try:
                return bool(service.isActivationPending(resolved_id))
            except Exception:  # pragma: no cover - defensive guard
                logger.exception(
                    "Mask service pending check failed for image %s", resolved_id
                )
        return resolved_id in self._pending_activation_images

    # Edit / undo -----------------------------------------------------------

    def undo_mask_edit(self) -> bool:
        """Undo the most recent mask edit."""
        return self._undo_api.undo_mask_edit()

    def redo_mask_edit(self) -> bool:
        """Redo the most recently undone mask edit."""
        return self._undo_api.redo_mask_edit()

    def get_mask_undo_state(self, mask_id: uuid.UUID):
        """Return the undo stack state for ``mask_id``."""
        return self._undo_api.get_mask_undo_state(mask_id)

    def update_mask_region(
        self,
        dirty_image_rect: QRect,
        active_mask_layer,
        *,
        sub_mask_image: QImage | None = None,
        force_async_colorize: bool = False,
    ) -> bool:
        """Apply the mask update for ``dirty_image_rect`` via the undo API."""
        return self._undo_api.update_mask_region(
            dirty_image_rect,
            active_mask_layer,
            sub_mask_image=sub_mask_image,
            force_async_colorize=force_async_colorize,
        )

    def invalidate_active_mask_cache(self) -> bool:
        """Invalidate cached mask renders for the active mask."""
        return self._undo_api.invalidate_active_mask_cache()

    # Brush / cursor --------------------------------------------------------

    def set_brush_size(self, size: int) -> None:
        """Update the brush size in the delegate."""
        delegate = self._ensure_mask_delegate()
        if delegate is not None:
            delegate.set_brush_size(size)

    def get_brush_size(self) -> int:
        """Return the current brush size."""
        delegate = self._ensure_mask_delegate()
        return 0 if delegate is None else delegate.get_brush_size()

    def update_brush_cursor(self, erase_indicator: bool = False) -> None:
        """Refresh the brush cursor appearance based on erase mode."""
        self._brush_cursor_adapter.update_cursor(erase_indicator=erase_indicator)

    # SAM ------------------------------------------------------------------

    def attachSamManager(self, sam_manager) -> None:
        """Attach a SAM manager to enable predictor operations."""
        self._sam_workflow.attach_manager(sam_manager)

    def detachSamManager(self) -> None:
        """Detach the active SAM manager."""
        self._sam_workflow.detach_manager()

    def resetActiveSamPredictor(self) -> None:
        """Clear the active SAM predictor if one is present."""
        self._sam_workflow.reset_active_predictor()

    def generate_and_apply_mask(self, bbox, erase_mode: bool = False) -> bool:
        """Generate a mask from ``bbox`` via SAM and apply it."""
        return self._sam_workflow.generate_and_apply_mask(bbox, erase_mode=erase_mode)

    # Introspection ---------------------------------------------------------

    def current_image_id(self) -> uuid.UUID | None:
        """Return the catalog's current image id."""
        return self._catalog.currentImageID()
