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

"""Mask domain service and autosave coordination utilities."""

from __future__ import annotations

import logging
import uuid
import weakref
from collections import deque
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Callable, Deque, Mapping, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QMetaObject, QRect, QRunnable, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap

from ..catalog.image_utils import (
    numpy_to_qimage_grayscale8,
    qimage_to_numpy_view_grayscale8,
)
from ..concurrency import BaseWorker, TaskExecutorProtocol, TaskHandle, TaskRejected
from ..core import Config
from ..types import DiagnosticRecord, DiagnosticsDomain
from ..core.config_features import MaskConfigSlice, require_mask_config
from ..features import FeatureInstallError
from .autosave import AutosaveManager
from .mask import MaskLayer, MaskManager
from .mask_controller import MaskController, Masking
from .mask_diagnostics import MaskStrokeDiagnostics
from .mask_undo import MaskUndoProvider, MaskUndoState
from .strokes import MaskStrokeDebugSnapshot, MaskStrokePipeline

if TYPE_CHECKING:  # pragma: no cover - import cycle guard

    from ..qpane import QPane
    from ..scene.model import LayerDescriptor, SceneDescriptor
    from ..scene.mutations import SceneMutationCoordinator
    from .scene_mutations import MaskSceneMutationOwner
logger = logging.getLogger(__name__)


_DEFER_ACTIVATION_RATIO = 0.6


SNIPPET_ASYNC_THRESHOLD_PX = 512 * 512


@dataclass(slots=True)
class _MaskPrefetchStats:
    """Bookkeeping for mask prefetch activity surfaced in diagnostics."""

    scheduled: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    last_message: str | None = None
    last_duration_ms: float | None = None


@dataclass(frozen=True, slots=True)
class _PrefetchHandle:
    """Track the queued handle and mask count for a prefetch request."""

    handle: TaskHandle
    mask_count: int


@dataclass(frozen=True, slots=True)
class PrefetchedOverlay:
    """Colourized overlay produced by the worker, with optional scaled variants."""

    mask_id: uuid.UUID
    image: QImage
    scaled: Tuple[Tuple[float, QImage], ...] = tuple()


class MaskService:
    """Facade around mask domain operations, keeping QPane lightweight."""

    def __init__(
        self,
        *,
        qpane: "QPane",
        mask_manager: MaskManager,
        mask_controller: MaskController,
        config: Config,
        mask_config: MaskConfigSlice | None = None,
        executor: TaskExecutorProtocol,
        stroke_diagnostics: MaskStrokeDiagnostics | None = None,
    ) -> None:
        """Bind qpane collaborators plus mask, autosave, and executor plumbing."""
        self._qpane = qpane
        self._catalog = qpane.catalog()
        self._mask_manager = mask_manager
        mask_config = mask_config or require_mask_config(config)
        self._mask_manager.set_undo_limit(mask_config.mask_undo_limit)
        self._mask_controller = mask_controller
        self._mask_controller.set_async_colorize_handler(
            self._request_async_colorize, threshold_px=SNIPPET_ASYNC_THRESHOLD_PX
        )
        self._config_source = config
        self._config: MaskConfigSlice = mask_config
        self._executor = executor
        self._autosave = MaskAutosaveCoordinator(
            qpane=qpane,
            mask_manager=mask_manager,
            mask_controller=mask_controller,
            executor=executor,
        )
        self._connected_autosave_manager: AutosaveManager | None = None
        self._status_messages: Deque[tuple[str, str]] = deque(maxlen=8)
        self._prefetch_enabled = True
        self._cancelled_prefetch_tasks: set[str] = set()
        self._prefetch_handles: dict[uuid.UUID, _PrefetchHandle] = {}
        self._snippet_handles: dict[uuid.UUID, TaskHandle] = {}
        self._pending_prefetched_overlays: dict[uuid.UUID, PrefetchedOverlay] = {}
        self._pending_activation_images: set[uuid.UUID] = set()
        self._prefetch_stats = _MaskPrefetchStats()
        self._prefetch_scales: Tuple[float, ...] = (0.5, 0.25)
        self._scene_mutations: SceneMutationCoordinator | None = None
        self._scene_mutation_owner: MaskSceneMutationOwner | None = None
        self._catalog.onNavigationStarted(self._handle_catalog_navigation_started)
        self._default_resume_cb = (
            lambda image_id=None, qpane_ref=qpane: qpane_ref.resumeOverlays()
        )
        self._default_resume_update_cb = (
            lambda image_id=None, qpane_ref=qpane: qpane_ref.resumeOverlaysAndUpdate()
        )
        self._default_activation_pending_cb = lambda image_id=None: None
        self._resume_overlays_cb = self._default_resume_cb
        self._resume_overlays_and_update_cb = self._default_resume_update_cb
        self._activation_pending_cb = self._default_activation_pending_cb
        self._stroke_pipeline = MaskStrokePipeline(
            qpane=qpane,
            service=self,
            diagnostics=stroke_diagnostics,
        )
        self._stroke_pipeline.set_idle_callback(self._on_mask_idle)
        qpane.diagnosticsDomainToggled.connect(self._handle_diagnostics_domain_toggled)
        if stroke_diagnostics is not None:
            try:
                is_enabled = qpane.diagnosticsDomainEnabled(DiagnosticsDomain.MASK)
                stroke_diagnostics.enabled = is_enabled
            except ValueError:
                pass

    @property
    def controller(self) -> MaskController:
        """Expose the active MaskController for callers that need it."""
        return self._mask_controller

    @property
    def executor(self) -> TaskExecutorProtocol | None:
        """Expose the executor powering stroke/snippet workers."""
        return self._executor

    def applyStrokeSegment(
        self,
        start_point,
        end_point,
        erase: bool,
    ) -> None:
        """Handle a brush segment emitted by the tool manager."""
        self._stroke_pipeline.apply_stroke_segment(start_point, end_point, erase)

    def commitStroke(self) -> None:
        """Flush the currently recorded stroke to the controller."""
        self._stroke_pipeline.commit_active_stroke()

    def resetStrokePipeline(
        self,
        mask_id: uuid.UUID | None = None,
        *,
        clear_counter: bool = False,
        request_redraw: bool = True,
    ) -> None:
        """Expose a direct reset hook for delegates/tests."""
        self._stroke_pipeline.reset_state(
            mask_id,
            clear_counter=clear_counter,
            request_redraw=request_redraw,
        )

    def strokeDebugSnapshot(self) -> MaskStrokeDebugSnapshot:
        """Return a snapshot of pending preview/job state for tests."""
        return self._stroke_pipeline.debug_snapshot()

    def configureStrokeDiagnostics(
        self, config: Config | MaskConfigSlice | None = None
    ) -> None:
        """Refresh stroke diagnostics toggles after settings changes."""
        if config is not None:
            settings = require_mask_config(config)
            self._config = settings
        else:
            settings = self._config
        self._stroke_pipeline.configure_diagnostics(
            enabled=None,
        )

    def _handle_diagnostics_domain_toggled(self, domain: str, enabled: bool) -> None:
        """Update stroke diagnostics state when the mask domain toggles."""
        if domain == DiagnosticsDomain.MASK.value:
            self._stroke_pipeline.configure_diagnostics(
                enabled=enabled,
            )

    def strokeDiagnosticsSnapshot(self):
        """Return the latest stroke diagnostics snapshot when available."""
        return self._stroke_pipeline.diagnostics_snapshot()

    def _handle_catalog_navigation_started(self, event) -> None:
        """Track activation pending state when navigation begins."""
        target_id = getattr(event, "target_id", None)
        if target_id is None:
            logger.warning(
                "Catalog navigation started event missing target_id; skipping activation pending tracking"
            )
            return
        was_pending = target_id in self._pending_activation_images
        self._pending_activation_images.add(target_id)
        if not was_pending:
            logger.info(
                "Marked image %s as activation pending due to navigation start",
                target_id,
            )
        try:
            self._activation_pending_cb(target_id)
        except Exception:
            logger.exception(
                "Activation pending callback failed for navigation start (image=%s)",
                target_id,
            )

    def set_activation_resume_hooks(
        self,
        resume: Callable[[uuid.UUID | None], None] | None,
        resume_and_update: Callable[[uuid.UUID | None], None] | None,
        on_pending: Callable[[uuid.UUID | None], None] | None,
    ) -> None:
        """Override activation resume callbacks used during deferred activation."""
        self._resume_overlays_cb = (
            resume if resume is not None else self._default_resume_cb
        )
        self._resume_overlays_and_update_cb = (
            resume_and_update
            if resume_and_update is not None
            else self._default_resume_update_cb
        )
        self._activation_pending_cb = (
            on_pending
            if on_pending is not None
            else self._default_activation_pending_cb
        )

    @property
    def manager(self) -> MaskManager:
        """Expose the underlying MaskManager."""
        return self._mask_manager

    def setSceneMutationCoordinator(
        self, coordinator: "SceneMutationCoordinator | None"
    ) -> None:
        """Register mask layer mutations with the internal scene coordinator."""
        current_owner = self._scene_mutation_owner
        current_coordinator = self._scene_mutations
        if current_owner is not None and current_coordinator is not None:
            current_coordinator.unregister_owner(current_owner)
        self._scene_mutations = coordinator
        self._scene_mutation_owner = None
        if coordinator is None:
            return
        from .scene_mutations import MaskSceneMutationOwner

        owner = MaskSceneMutationOwner(self)
        coordinator.register_owner(owner)
        self._scene_mutation_owner = owner

    def applySceneMaskOpacity(self, mask_id: uuid.UUID, opacity: float) -> bool:
        """Apply a validated scene opacity mutation to a mask layer."""
        return self._mask_controller.setMaskOpacity(mask_id, opacity)

    def applySceneMaskReorder(self, mask_id: uuid.UUID, target_index: int) -> bool:
        """Apply a validated scene reorder mutation to the mask stack."""
        image_id = self._catalog.currentImageID()
        if image_id is None:
            return False
        mask_ids = self._mask_manager.get_mask_ids_for_image(image_id)
        if mask_id not in mask_ids:
            return False
        target_mask_index = self._mask_index_for_scene_index(target_index)
        if target_mask_index is None:
            return False
        return self._mask_manager.reorder_mask(image_id, mask_id, target_mask_index)

    def applySceneMaskRemoval(self, mask_id: uuid.UUID) -> bool:
        """Apply a validated scene removal mutation to a mask layer."""
        image_id = self._catalog.currentImageID()
        if image_id is None:
            return False
        return self.removeMaskFromImage(image_id, mask_id)

    def applySceneMaskRevisionRequest(self, mask_id: uuid.UUID, *, reason: str) -> bool:
        """Apply a validated source revision request for a mask layer."""
        if self._mask_manager.get_layer(mask_id) is None:
            return False
        self._mask_controller.bumpMaskStyleRevision(mask_id, reason=reason)
        self._mask_controller.invalidate_mask_cache(mask_id, reason=reason)
        self._mask_controller.warmMaskCache(mask_id)
        self._mask_controller.mask_updated.emit(mask_id, QRect())
        return True

    def _mask_index_for_scene_index(self, target_index: int) -> int | None:
        """Translate a scene layer index into mask-domain order."""
        coordinator = self._scene_mutations
        scene = coordinator.active_scene() if coordinator is not None else None
        if scene is None or target_index < 0 or target_index >= len(scene.layers):
            return None
        from ..scene.model import LayerKind

        mask_indexes = [
            index
            for index, layer in enumerate(scene.layers)
            if layer.kind == LayerKind.MASK
        ]
        if not mask_indexes or target_index < mask_indexes[0]:
            return None
        target_mask_index = (
            sum(1 for index in mask_indexes if index <= target_index) - 1
        )
        return max(0, min(target_mask_index, len(mask_indexes) - 1))

    def _scene_layer_for_mask(
        self, mask_id: uuid.UUID
    ) -> tuple["SceneDescriptor", "LayerDescriptor"] | None:
        """Return the active scene/layer pair for ``mask_id`` when visible."""
        coordinator = self._scene_mutations
        if coordinator is None:
            return None
        from ..scene.sources import MaskLayerSource

        return coordinator.find_layer(
            lambda layer: isinstance(layer.source, MaskLayerSource)
            and layer.source.mask_id == mask_id
        )

    def _route_mask_reorder_through_scene(
        self, mask_id: uuid.UUID, target_scene_index: int
    ) -> bool | None:
        """Route mask reordering through the scene coordinator when possible."""
        coordinator = self._scene_mutations
        scene_layer = self._scene_layer_for_mask(mask_id)
        if coordinator is None or scene_layer is None:
            return None
        scene, layer = scene_layer
        result = coordinator.reorder_layer(
            scene.scene_id,
            layer.layer_id,
            target_scene_index,
        )
        return result.changed if result.accepted else False

    def _route_mask_opacity_through_scene(
        self, mask_id: uuid.UUID, opacity: float
    ) -> bool | None:
        """Route mask opacity updates through the scene coordinator when possible."""
        coordinator = self._scene_mutations
        scene_layer = self._scene_layer_for_mask(mask_id)
        if coordinator is None or scene_layer is None:
            return None
        scene, layer = scene_layer
        result = coordinator.set_opacity(scene.scene_id, layer.layer_id, opacity)
        return result.changed if result.accepted else False

    def _scene_mask_stack_end_index(self, *, forward: bool) -> int | None:
        """Return the scene index used to rotate mask order."""
        coordinator = self._scene_mutations
        scene = coordinator.active_scene() if coordinator is not None else None
        if scene is None:
            return None
        from ..scene.model import LayerKind

        mask_indexes = [
            index
            for index, layer in enumerate(scene.layers)
            if layer.kind == LayerKind.MASK
        ]
        if not mask_indexes:
            return None
        return mask_indexes[-1] if forward else mask_indexes[0]

    def getUndoProvider(self) -> MaskUndoProvider:
        """Expose the undo provider used for mask history integration."""
        return self._mask_manager.undo_provider

    def setUndoProvider(self, provider: MaskUndoProvider | None) -> None:
        """Install or replace the mask undo provider."""
        self._mask_manager.set_undo_provider(provider)

    def connectUndoStackChanged(self, slot: Callable[[uuid.UUID], None]) -> None:
        """Register slot for undo stack change notifications."""
        self._mask_controller.undo_stack_changed.connect(slot)

    def disconnectUndoStackChanged(self, slot: Callable[[uuid.UUID], None]) -> None:
        """Detach slot from undo stack change notifications."""
        try:
            self._mask_controller.undo_stack_changed.disconnect(slot)
        except (TypeError, RuntimeError) as exc:
            logger.warning("Failed to disconnect undo stack listener: %s", exc)

    def getUndoState(self, mask_id: uuid.UUID) -> MaskUndoState | None:
        """Return the undo/redo stack depth for mask_id when available."""
        return self._mask_manager.get_undo_state(mask_id)

    def getActiveMaskId(self) -> uuid.UUID | None:
        """Get the identifier of the mask currently selected for editing."""
        return self._mask_controller.get_active_mask_id()

    def getActiveMaskColor(self) -> QColor | None:
        """Get the color assigned to the active mask layer."""
        return self._mask_controller.get_active_mask_color()

    def getActiveMaskImage(self) -> QImage | None:
        """Get the rendered image backing the active mask layer."""
        return self._mask_controller.getActiveMaskImage()

    def clearRenderCache(self) -> None:
        """Clear the cached colorized mask previews maintained by the controller."""
        self._mask_controller.clear_cache()

    def setPrefetchEnabled(self, enabled: bool) -> None:
        """Enable or disable asynchronous mask render prefetch."""
        enabled = bool(enabled)
        if enabled == self._prefetch_enabled:
            return
        self._prefetch_enabled = enabled
        if not enabled:
            self._cancel_all_prefetches()
            message = "Mask prefetch disabled; pending jobs cancelled."
            self._prefetch_stats.last_message = message
            self._prefetch_stats.last_duration_ms = None
            self._record_status(message, label="Mask Prefetch")
            logger.info("Mask prefetch disabled; pending jobs cancelled")
        else:
            message = "Mask prefetch enabled."
            self._prefetch_stats.last_message = message
            self._prefetch_stats.last_duration_ms = None
            self._record_status(message, label="Mask Prefetch")
            logger.info("Mask prefetch enabled")

    def _resolve_prefetch_scales(
        self, scales: Sequence[float] | None
    ) -> Tuple[float, ...]:
        """Normalize and de-duplicate requested overlay scales for worker prefetch."""
        candidate = scales if scales is not None else self._prefetch_scales
        normalized: list[float] = []
        seen: set[float] = set()
        for value in candidate:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            scale_key = self._mask_controller._normalize_scale_key(numeric)
            if scale_key is None or scale_key in seen:
                continue
            seen.add(scale_key)
            normalized.append(scale_key)
        return tuple(normalized)

    def prefetchColorizedMasks(
        self,
        image_id: uuid.UUID | None,
        *,
        reason: str = "navigation",
        scales: Sequence[float] | None = None,
    ) -> bool:
        """Warm mask renders for image_id using the background executor."""
        if not self._prefetch_enabled:
            logger.debug("Mask prefetch skipped for %s: disabled", image_id)
            return False
        executor = self._executor
        if executor is None:
            logger.debug("Mask prefetch skipped for %s: executor unavailable", image_id)
            return False
        if image_id is None:
            return False
        mask_ids = self._mask_manager.get_mask_ids_for_image(image_id)
        prefetch_scales = self._resolve_prefetch_scales(scales)
        if not mask_ids:
            self._prefetch_stats.skipped += 1
            message = f"No masks to prefetch for image {self._format_uuid(image_id)}."
            self._prefetch_stats.last_message = message
            self._prefetch_stats.last_duration_ms = None
            self._record_status(message, label="Mask Prefetch")
            return False
        active_mask_id = self._mask_controller.get_active_mask_id()
        next_top_mask = mask_ids[-1]
        if self._should_defer_activation_signals(active_mask_id, next_top_mask):
            self._prefetch_stats.skipped += 1
            message = (
                "Prefetch deferred for image "
                f"{self._format_uuid(image_id)}; activation will run synchronously."
            )
            self._prefetch_stats.last_message = message
            self._prefetch_stats.last_duration_ms = None
            self._record_status(message, label="Mask Prefetch")
            return False
        self.cancelPrefetch(image_id)
        worker = MaskPrefetchWorker(
            image_id=image_id,
            mask_ids=tuple(mask_ids),
            mask_manager=self._mask_manager,
            controller=self._mask_controller,
            service=self,
            scales=prefetch_scales,
        )
        try:
            handle = executor.submit(worker, category="mask_prefetch")
        except Exception as exc:
            self._prefetch_stats.failed += len(mask_ids)
            message = (
                f"Prefetch rejected for image {self._format_uuid(image_id)}: {exc}"
            )
            self._prefetch_stats.last_message = message
            self._prefetch_stats.last_duration_ms = None
            self._record_status(message, label="Mask Prefetch Error")
            logger.exception("Failed to queue mask prefetch for image %s", image_id)
            return False
        worker.set_task_id(handle.task_id)
        count = len(mask_ids)
        self._prefetch_handles[image_id] = _PrefetchHandle(
            handle=handle, mask_count=count
        )
        self._prefetch_stats.scheduled += count
        self._prefetch_stats.last_message = (
            f"Prefetch queued for {count} mask(s) on {self._format_uuid(image_id)}."
        )
        self._prefetch_stats.last_duration_ms = None
        self._mask_controller.record_prefetch_request(count)
        self._record_status(self._prefetch_stats.last_message, label="Mask Prefetch")
        logger.info(
            "Queued mask prefetch for image %s (%d mask(s), reason=%s)",
            image_id,
            count,
            reason,
        )
        return True

    def cancelPrefetch(self, image_id: uuid.UUID | None) -> bool:
        """Cancel any queued mask prefetch task associated with image_id."""
        if image_id is None:
            cancelled_any = False
            for candidate in list(self._prefetch_handles):
                cancelled_any = self.cancelPrefetch(candidate) or cancelled_any
            return cancelled_any
        handle_entry = self._prefetch_handles.pop(image_id, None)
        if handle_entry is None:
            return False
        handle = handle_entry.handle
        mask_count = handle_entry.mask_count
        executor = self._executor
        cancelled = False
        if executor is not None:
            try:
                cancelled = executor.cancel(handle)
            except Exception:
                cancelled = False
        if cancelled:
            message = f"Prefetch cancelled for {self._format_uuid(image_id)}."
        else:
            message = (
                "Prefetch cancellation requested for "
                f"{self._format_uuid(image_id)}; task already running."
            )
        self._prefetch_stats.last_message = message
        self._prefetch_stats.last_duration_ms = None
        self._record_status(message, label="Mask Prefetch")
        log_fn = logger.info if cancelled else logger.debug
        log_fn(
            "Mask prefetch cancellation for image %s (cancelled=%s)",
            image_id,
            cancelled,
        )
        if cancelled:
            self._cancelled_prefetch_tasks.add(handle.task_id)
            metrics = self._mask_controller.snapshot_metrics()
            outstanding = metrics.prefetch_requested - (
                metrics.prefetch_completed + metrics.prefetch_failed
            )
            failed = min(mask_count, max(0, outstanding))
            if failed > 0:
                self._mask_controller.record_prefetch_completion(
                    completed=0, failed=failed
                )
                self._prefetch_stats.failed += failed
        return cancelled

    def activateMask(self, mask_id: uuid.UUID | None) -> bool:
        """Select the mask to edit and keep caches in sync.

        Returns:
            bool: True when the mask changed position in the stack during activation.
        """
        previous_active = self._mask_controller.get_active_mask_id()
        if mask_id is None:
            self._invalidate_pending_mask_jobs(
                previous_active, reason="mask_deselected", request_redraw=False
            )
            self._mask_controller.setActiveMaskID(None)
            return True
        if previous_active is not None and previous_active != mask_id:
            self._invalidate_pending_mask_jobs(previous_active, reason="mask_switch")
        was_moved = self.promoteMaskToTop(mask_id)
        self._mask_controller.setActiveMaskID(mask_id)
        return was_moved

    def ensureTopMaskActiveForImage(self, image_id: uuid.UUID | None) -> bool:
        """Ensure the active mask aligns with image_id's stack before brush use."""

        def record_once(message: str, *, label: str) -> None:
            """Emit a status message unless it matches the last entry."""
            if not self._status_messages or self._status_messages[-1] != (
                label,
                message,
            ):
                self._record_status(message, label=label)

        active_mask_id = self._mask_controller.get_active_mask_id()
        if image_id is None:
            self._invalidate_pending_mask_jobs(
                active_mask_id, reason="mask_switch", request_redraw=False
            )
            self._mask_controller.setActiveMaskID(None)
            record_once(
                "Brush tool unavailable: no image selected.",
                label="Mask Error",
            )
            return False
        mask_ids = self._mask_manager.get_mask_ids_for_image(image_id)
        if not mask_ids:
            should_defer = self._should_defer_activation_signals(active_mask_id, None)
            self._invalidate_pending_mask_jobs(
                active_mask_id, reason="mask_switch", request_redraw=False
            )
            self._mask_controller.setActiveMaskID(
                None, warm_cache=False, emit_signals=not should_defer
            )
            if image_id is not None:
                pending = image_id in self._pending_activation_images
                if pending:
                    self._schedule_activation_signals(
                        None,
                        image_id=image_id,
                    )
                else:
                    self._pending_activation_images.discard(image_id)
                    try:
                        self._resume_overlays_cb(image_id)
                    except Exception:  # pragma: no cover - defensive guard
                        logger.exception(
                            "Failed to resume overlays after maskless activation"
                        )
            record_once(
                f"Brush tool unavailable: image {image_id} has no masks.",
                label="Mask Error",
            )
            return False
        prefetch_pending = image_id in self._prefetch_handles
        if image_id is not None:
            self._pending_activation_images.discard(image_id)
        if active_mask_id in mask_ids:
            if active_mask_id != mask_ids[-1]:
                top_scene_index = self._scene_mask_stack_end_index(forward=True)
                moved = (
                    self._route_mask_reorder_through_scene(
                        active_mask_id,
                        top_scene_index,
                    )
                    if top_scene_index is not None
                    else None
                )
                if moved is None:
                    moved = self._mask_manager.reorder_mask(
                        image_id,
                        active_mask_id,
                        len(mask_ids) - 1,
                    )
                if moved:
                    self._mask_controller.bumpMaskGeneration(
                        active_mask_id, reason="mask_reordered"
                    )
            return True
        self._invalidate_pending_mask_jobs(active_mask_id, reason="mask_switch")
        top_mask_id = mask_ids[-1]
        top_scene_index = self._scene_mask_stack_end_index(forward=True)
        moved = (
            self._route_mask_reorder_through_scene(top_mask_id, top_scene_index)
            if top_scene_index is not None
            else None
        )
        if moved is None:
            moved = self._mask_manager.reorder_mask(
                image_id,
                top_mask_id,
                len(mask_ids) - 1,
            )
        if moved:
            self._mask_controller.bumpMaskGeneration(
                top_mask_id, reason="mask_reordered"
            )
        size_defer = self._should_defer_activation_signals(active_mask_id, top_mask_id)
        scheduled_prefetch = False
        if size_defer:
            try:
                scheduled_prefetch = self.prefetchColorizedMasks(
                    image_id, reason="activation"
                )
            except Exception:
                logger.exception(
                    "Failed to prefetch mask renders for %s during activation",
                    image_id,
                )
            else:
                if scheduled_prefetch:
                    prefetch_pending = True
        should_defer = size_defer or prefetch_pending
        self._mask_controller.setActiveMaskID(
            top_mask_id, warm_cache=not should_defer, emit_signals=not should_defer
        )
        if should_defer:
            if image_id is not None:
                self._pending_activation_images.add(image_id)
            self._schedule_activation_signals(
                top_mask_id,
                warm_cache=not prefetch_pending,
                image_id=image_id,
            )
        record_once(
            f"Activated mask {top_mask_id} for image {image_id} before brush use.",
            label="Mask",
        )
        return True

    def isActivationPending(self, image_id: uuid.UUID | None) -> bool:
        """Return True while we are waiting on deferred mask activation."""
        if image_id is None:
            return False
        return image_id in self._pending_activation_images

    def _should_defer_activation_signals(
        self,
        previous_mask_id: uuid.UUID | None,
        next_mask_id: uuid.UUID | None,
    ) -> bool:
        """Return True when activation signals should be deferred."""
        if previous_mask_id is None or next_mask_id is None:
            return False
        if previous_mask_id == next_mask_id:
            return False
        next_layer = self._mask_manager.get_layer(next_mask_id)
        if next_layer is None:
            return False
        next_image = next_layer.mask_image
        if next_image.isNull():
            return False
        next_pixels = next_image.width() * next_image.height()
        if next_pixels <= 0:
            return False
        previous_layer = self._mask_manager.get_layer(previous_mask_id)
        if previous_layer is None:
            return False
        previous_image = previous_layer.mask_image
        if previous_image.isNull():
            return False
        previous_pixels = previous_image.width() * previous_image.height()
        if previous_pixels <= 0:
            return False
        if next_pixels >= previous_pixels:
            return False
        ratio = next_pixels / previous_pixels
        should_defer = ratio < _DEFER_ACTIVATION_RATIO
        if should_defer:
            logger.info(
                "Deferring mask activation signals: prev=%s (%dx%d) next=%s (%dx%d) ratio=%.3f threshold=%.2f",
                previous_mask_id,
                previous_image.width(),
                previous_image.height(),
                next_mask_id,
                next_image.width(),
                next_image.height(),
                ratio,
                _DEFER_ACTIVATION_RATIO,
            )
        return should_defer

    def _schedule_activation_signals(
        self,
        mask_id: uuid.UUID | None,
        *,
        warm_cache: bool = False,
        image_id: uuid.UUID | None = None,
    ) -> None:
        """Emit activation signals once the mask data is ready."""
        controller = self._mask_controller

        def emit_later(
            mid: uuid.UUID | None = mask_id, *, target_image_id=image_id
        ) -> None:
            """Emit activation signals after optional cache warmup."""
            resolved_image_id = target_image_id
            if resolved_image_id is None:
                catalog = self._catalog
                if catalog is not None:
                    try:
                        resolved_image_id = catalog.currentImageID()
                    except Exception:
                        resolved_image_id = None
            try:
                if warm_cache and mid is not None:
                    controller.warmMaskCache(mid)
                controller.emit_activation_signals(mid)
            finally:
                was_pending = (
                    resolved_image_id is not None
                    and resolved_image_id in self._pending_activation_images
                )
                if resolved_image_id is not None:
                    self._pending_activation_images.discard(resolved_image_id)
                try:
                    callback = (
                        self._resume_overlays_and_update_cb
                        if was_pending
                        else self._resume_overlays_cb
                    )
                    callback(resolved_image_id)
                except Exception:
                    logger.exception("Activation resume callback failed")

        try:
            self._activation_pending_cb(image_id)
        except Exception:
            logger.exception("Activation pending callback failed during scheduling")
        QTimer.singleShot(0, lambda: emit_later(target_image_id=image_id))

    def undoActiveMaskEdit(self) -> bool:
        """Undo the most recent edit on the active mask layer."""
        result = self._mask_controller.undoMaskEdit()
        if result:
            mask_id = self._mask_controller.get_active_mask_id()
            image_id = self._image_id_for_mask(mask_id)
            if image_id is not None:
                self.prefetchColorizedMasks(image_id, reason="undo")
        return result

    def redoActiveMaskEdit(self) -> bool:
        """Redo the previously undone edit on the active mask layer."""
        result = self._mask_controller.redoMaskEdit()
        if result:
            mask_id = self._mask_controller.get_active_mask_id()
            image_id = self._image_id_for_mask(mask_id)
            if image_id is not None:
                self.prefetchColorizedMasks(image_id, reason="redo")
        return result

    def pushActiveMaskState(self) -> bool:
        """Push the current active mask image onto its undo stack."""
        return self._mask_controller.pushUndoState()

    def invalidateActiveMaskCache(self) -> None:
        """Invalidate the colorized pixmap cache for the active mask."""
        self._mask_controller.invalidateActiveMaskCache()

    def invalidateMaskCache(self, mask_id: uuid.UUID | None) -> None:
        """Invalidate cached mask renders for mask_id when present."""
        self._mask_controller.invalidate_mask_cache(mask_id)

    def invalidateMaskCachesForImage(self, image_id: uuid.UUID | None) -> None:
        """Invalidate cached mask renders for all masks associated with image_id."""
        if image_id is None:
            return
        self._mask_controller.invalidate_image_cache(image_id)

    def updateMaskRegion(
        self,
        dirty_image_rect: QRect,
        mask_layer: "MaskLayer",
        *,
        sub_mask_image: QImage | None = None,
        force_async_colorize: bool = False,
    ) -> None:
        """Propagate a region update for the provided mask layer.

        When `force_async_colorize` is True the method keeps the provisional
        preview for immediate feedback but still schedules a high-resolution
        colorization pass even if the preview was decimated.
        """
        if mask_layer is None or dirty_image_rect.isNull():
            return
        mask_id = self._mask_manager.find_mask_id_for_layer(mask_layer)
        mask_image = getattr(mask_layer, "mask_image", None)
        preview_stride = None
        preview_provisional = False
        if sub_mask_image is not None:
            try:
                preview_stride = int(sub_mask_image.text("qpane_preview_stride"))
            except (TypeError, ValueError):
                preview_stride = None
            try:
                preview_provisional = (
                    sub_mask_image.text("qpane_preview_provisional") == "1"
                )
            except (TypeError, ValueError):
                preview_provisional = False
        qpane = getattr(self, "_qpane", None)
        qpane_viewport = None
        if qpane is not None:
            try:
                qpane_viewport = qpane.view().viewport
            except AttributeError:
                qpane_viewport = getattr(qpane, "viewport", None)
        zoom = getattr(qpane_viewport, "zoom", 1.0) or 1.0
        if zoom <= 0.0:
            zoom = 1.0
        stride = 1
        if zoom < 1.0:
            stride = max(1, int(round(1.0 / max(zoom, 1e-6))))
        if (
            sub_mask_image is None
            and stride > 1
            and mask_image is not None
            and not mask_image.isNull()
        ):
            y0 = dirty_image_rect.top()
            x0 = dirty_image_rect.left()
            y1 = dirty_image_rect.bottom() + 1
            x1 = dirty_image_rect.right() + 1
            mask_view, _ = qimage_to_numpy_view_grayscale8(mask_image)
            preview_slice = mask_view[y0:y1:stride, x0:x1:stride].copy()
            preview_image = numpy_to_qimage_grayscale8(preview_slice)
            preview_image.setText("qpane_preview_stride", str(stride))
            preview_image.setText("qpane_preview_provisional", "1")
            sub_mask_image = preview_image
            preview_stride = stride
            preview_provisional = True
        snippet_source = sub_mask_image
        if (
            snippet_source is None
            and mask_image is not None
            and not mask_image.isNull()
        ):
            snippet_source = mask_image.copy(dirty_image_rect)
        async_snippet = snippet_source
        if force_async_colorize and mask_image is not None and not mask_image.isNull():
            async_snippet = mask_image.copy(dirty_image_rect)
        async_snippet_available = (
            async_snippet is not None and not async_snippet.isNull()
        )
        area = dirty_image_rect.width() * dirty_image_rect.height()
        should_request_async = (
            (not preview_provisional or force_async_colorize)
            and mask_id is not None
            and async_snippet_available
            and self._executor is not None
            and (area > SNIPPET_ASYNC_THRESHOLD_PX or preview_stride is not None)
        )
        scheduled = False
        if should_request_async:
            scheduled = self._schedule_snippet_colorize(
                mask_id,
                dirty_image_rect,
                mask_layer,
                async_snippet,
            )
        if sub_mask_image is not None or not scheduled:
            self._mask_controller.updateMaskRegion(
                dirty_image_rect,
                mask_layer,
                sub_mask_image=sub_mask_image,
            )

    def handleGeneratedMask(
        self,
        mask_array_uint8: np.ndarray | None,
        bbox: np.ndarray,
        erase_mode: bool,
    ) -> None:
        """Merge a generated mask array into the active layer or clear stale overlays."""
        catalog = self._catalog
        current_image_id = catalog.currentImageID() if catalog else None
        if not self.ensureTopMaskActiveForImage(current_image_id):
            logger.info(
                "Mask generation skipped: no active mask available for image %s.",
                current_image_id,
            )
            return
        update = self._mask_controller.handle_mask_ready(
            mask_array_uint8,
            bbox,
            erase_mode,
            image_id=current_image_id,
        )
        if update is None:
            return
        if update.dirty_rect is not None and update.mask_layer is not None:
            self.updateMaskRegion(
                update.dirty_rect,
                update.mask_layer,
                force_async_colorize=True,
            )
        elif update.changed:
            self._mask_controller.mask_updated.emit(update.mask_id, QRect())

    def getColorizedMask(
        self, mask_layer: "MaskLayer", *, scale: float | None = None
    ) -> QPixmap | None:
        """Get the colorized pixmap for ``mask_layer`` when available."""
        return self._mask_controller.get_colorized_mask(mask_layer, scale=scale)

    def getColorizedMaskById(
        self, mask_id: uuid.UUID, *, scale: float | None = None
    ) -> QPixmap | None:
        """Get the colorized pixmap for ``mask_id`` when available."""
        return self._mask_controller.get_colorized_mask_by_id(mask_id, scale=scale)

    def predict_mask_from_box(
        self,
        predictor,
        bbox: np.ndarray,
    ) -> np.ndarray | None:
        """Run SAM prediction for ``bbox`` using ``predictor``.

        This helper defers imports of the optional SAM stack until required and
        re-raises shape/validation errors so callers can surface consistent
        logging.
        """
        try:
            from qpane.sam import service as sam_service
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("SAM services are unavailable") from exc
        return sam_service.predict_mask_from_box(predictor, bbox)

    def get_latest_status_message(self, *labels: str) -> tuple[str, str] | None:
        """Return the most recent status message filtered by labels when provided."""
        if not self._status_messages:
            return None
        if labels:
            label_set = set(labels)
            for label, message in reversed(self._status_messages):
                if label in label_set:
                    return label, message
        return self._status_messages[-1]

    def _handle_autosave_completed(self, mask_id: str, path: str) -> None:
        """Record a status entry when an autosave succeeds."""
        self._record_status(
            f"Autosaved mask {mask_id} to {path}",
            label="Mask Autosave",
        )

    def _handle_autosave_failed(
        self, mask_id: str, path: str, error: Exception
    ) -> None:
        """Record a status entry when an autosave fails."""
        self._record_status(
            f"Autosave failed for mask {mask_id}: {error}",
            label="Mask Autosave Error",
        )

    def _commit_mask_image(
        self, mask_id: uuid.UUID, image: QImage, *, before: QImage | None = None
    ) -> bool:
        """Apply ``image`` to the mask controller and emit an update signal."""
        if not self._mask_controller.apply_mask_image(mask_id, image, before=before):
            return False
        self._mask_controller.mask_updated.emit(mask_id, QRect())
        return True

    def applyConfig(
        self, config: Config, mask_config: MaskConfigSlice | None = None
    ) -> None:
        """Refresh service dependencies after a configuration update."""
        mask_config = mask_config or require_mask_config(config)
        self._config_source = config
        self._config = mask_config
        self._mask_manager.set_undo_limit(mask_config.mask_undo_limit)
        self._mask_controller.apply_config(config, mask_config)
        self._autosave.applyConfig(mask_config)
        self.setPrefetchEnabled(mask_config.mask_prefetch_enabled)
        self.configureStrokeDiagnostics(mask_config)

    def _request_async_colorize(
        self,
        mask_id: uuid.UUID,
        mask_layer: "MaskLayer",
    ) -> bool:
        """Queue asynchronous colorization for full-mask cache misses."""
        if mask_layer.mask_image.isNull():
            self._mask_controller.notify_async_colorize_complete(mask_id)
            return False
        image_id = self._image_id_for_mask(mask_id)
        if image_id is not None:
            scheduled = self.prefetchColorizedMasks(
                image_id,
                reason="cache-miss",
                scales=self._prefetch_scales,
            )
            if scheduled:
                return True
        dirty_rect = mask_layer.mask_image.rect()
        snippet = mask_layer.mask_image.copy()
        scheduled = self._schedule_snippet_colorize(
            mask_id,
            dirty_rect,
            mask_layer,
            snippet,
        )
        if not scheduled:
            self._mask_controller.notify_async_colorize_complete(mask_id)
        return scheduled

    def _image_id_for_mask(self, mask_id: uuid.UUID | None) -> uuid.UUID | None:
        """Return a likely image identifier for ``mask_id`` when available."""
        if mask_id is None:
            return None
        image_ids = self._mask_manager.get_images_for_mask(mask_id)
        if image_ids:
            return image_ids[-1]
        catalog = getattr(self, "_catalog", None)
        if catalog is None:
            return None
        try:
            return catalog.currentImageID()
        except Exception:  # pragma: no cover - defensive guard
            return None

    def _schedule_snippet_colorize(
        self,
        mask_id: uuid.UUID,
        dirty_image_rect: QRect,
        mask_layer: "MaskLayer",
        snippet: QImage,
    ) -> bool:
        """Dispatch a snippet colorization worker for the dirty mask region."""
        executor = self._executor
        if executor is None:
            return False
        worker = MaskSnippetWorker(
            mask_id=mask_id,
            dirty_rect=QRect(dirty_image_rect),
            snippet=snippet,
            color=mask_layer.color,
            controller=self._mask_controller,
            service=self,
        )
        previous = self._snippet_handles.pop(mask_id, None)
        if previous is not None:
            try:
                executor.cancel(previous)
            except Exception:  # pragma: no cover - defensive
                pass
        try:
            handle = executor.submit(worker, category="mask_snippet")
        except TaskRejected as exc:
            message = f"Mask snippet colorization rejected for mask {self._format_uuid(mask_id)}: {exc}"
            logger.debug(message)
            self._record_status(message, label="Mask Snippet Error")
            return False
        self._snippet_handles[mask_id] = handle
        return True

    def _consume_snippet_result(
        self,
        *,
        mask_id: uuid.UUID,
        handle: TaskHandle | None,
        dirty_rect: QRect,
        colorized_image: QImage | None,
    ) -> None:
        """Apply snippet colorization results and finalize async notifications."""
        if handle is not None:
            current = self._snippet_handles.get(mask_id)
            if current is None or current.task_id != handle.task_id:
                return
            self._snippet_handles.pop(mask_id, None)
        mask_layer = self._mask_manager.get_layer(mask_id)
        if mask_layer is None:
            return
        if colorized_image is None or colorized_image.isNull():
            self._mask_controller.updateMaskRegion(dirty_rect, mask_layer)
            self._mask_controller.notify_async_colorize_complete(mask_id)
            return
        self._mask_controller.updateMaskRegion(
            dirty_rect,
            mask_layer,
            colorized_image=colorized_image,
        )
        self._mask_controller.notify_async_colorize_complete(mask_id)

    def _consume_prefetch_results(
        self,
        *,
        image_id: uuid.UUID,
        warmed: Sequence[PrefetchedOverlay],
        failures: Mapping[uuid.UUID, str],
        duration_ms: float,
        error: BaseException | None,
        task_id: str | None = None,
    ) -> None:
        """Commit prefetched overlays and update diagnostics on the main thread."""
        self._prefetch_handles.pop(image_id, None)
        if task_id is not None and task_id in self._cancelled_prefetch_tasks:
            self._cancelled_prefetch_tasks.discard(task_id)
            return
        failure_messages = dict(failures)
        completed = 0
        pipeline = self._stroke_pipeline
        for overlay in warmed:
            mask_id = overlay.mask_id
            layer = self._mask_manager.get_layer(mask_id)
            if layer is None or overlay.image.isNull():
                failure_messages[mask_id] = "layer unavailable"
                continue
            if pipeline.is_mask_busy(mask_id):
                self._pending_prefetched_overlays[mask_id] = overlay
                completed += 1
                continue
            self._mask_controller.commit_prefetched_mask(
                mask_id,
                layer,
                overlay.image,
                scaled=overlay.scaled,
            )
            completed += 1
        if error is not None:
            failure_messages["worker"] = str(error)
        failure_count = len(failure_messages)
        duration_value = duration_ms if (completed or failure_count) else None
        self._mask_controller.record_prefetch_completion(
            completed=completed, failed=failure_count, duration_ms=duration_value
        )
        if completed:
            self._prefetch_stats.completed += completed
        if failure_count:
            self._prefetch_stats.failed += failure_count
        summary_prefix = f"Prefetch warmed {completed} mask(s)"
        if failure_count:
            summary = f"{summary_prefix} with {failure_count} failure(s) for {self._format_uuid(image_id)}"
        elif completed:
            summary = f"{summary_prefix} for {self._format_uuid(image_id)}"
        else:
            summary = f"Prefetch found cached renders for {self._format_uuid(image_id)}"
        if duration_ms is not None:
            summary = f"{summary} ({duration_ms:.1f} ms)"
        self._prefetch_stats.last_message = summary
        self._prefetch_stats.last_duration_ms = duration_ms
        if failure_count:
            label = "Mask Prefetch Error"
            logger.warning(
                "Mask prefetch completed with %d failure(s) for image %s",
                failure_count,
                image_id,
            )
            for failed_mask, reason in failure_messages.items():
                logger.debug("Prefetch failure detail for %s: %s", failed_mask, reason)
        else:
            label = "Mask Prefetch"
            logger.info(
                "Mask prefetch completed for image %s (masks=%d)", image_id, completed
            )
        self._record_status(summary, label=label)
        for overlay in warmed:
            self._maybe_apply_pending_prefetch(overlay.mask_id)

    def _on_mask_idle(self, mask_id: uuid.UUID) -> None:
        """Apply any deferred prefetch once stroke work for mask_id completes."""
        try:
            self._maybe_apply_pending_prefetch(mask_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Deferred prefetch application failed for mask %s", mask_id
            )

    def _maybe_apply_pending_prefetch(self, mask_id: uuid.UUID | None) -> bool:
        """Apply a stashed prefetched overlay when the mask is idle."""
        if mask_id is None:
            return False
        pipeline = self._stroke_pipeline
        if pipeline.is_mask_busy(mask_id):
            return False
        overlay = self._pending_prefetched_overlays.pop(mask_id, None)
        if overlay is None:
            return False
        layer = self._mask_manager.get_layer(mask_id)
        if layer is None or overlay.image.isNull():
            return False
        self._mask_controller.commit_prefetched_mask(
            mask_id,
            layer,
            overlay.image,
            scaled=overlay.scaled,
        )
        return True

    def _cancel_all_prefetches(self) -> None:
        """Cancel any queued mask prefetch work."""
        self.cancelPrefetch(None)

    def _prefetch_summary(self) -> str:
        """Return a human-friendly description of current prefetch state."""
        state = "Enabled" if self._prefetch_enabled else "Disabled"
        message = self._prefetch_stats.last_message or "No activity"
        duration_ms = self._prefetch_stats.last_duration_ms
        if duration_ms is not None:
            message = f"{message} ({duration_ms:.1f} ms)"
        return f"{state} - {message}"

    @staticmethod
    def _format_uuid(value: uuid.UUID | None) -> str:
        """Return a short, diagnostics-friendly representation of value."""
        if isinstance(value, uuid.UUID):
            return value.hex[:8].upper()
        return "None"

    def _reset_pending_strokes(
        self,
        mask_id: uuid.UUID | None,
        *,
        clear_counter: bool = False,
        request_redraw: bool = True,
    ) -> None:
        """Cancel pending stroke jobs using the pipeline-owned state."""
        self._stroke_pipeline.reset_state(
            mask_id,
            clear_counter=clear_counter,
            request_redraw=request_redraw,
        )

    def _invalidate_pending_mask_jobs(
        self,
        mask_id: uuid.UUID | None,
        *,
        reason: str,
        request_redraw: bool = True,
    ) -> None:
        """Advance the mask generation and cancel any queued stroke jobs."""
        if mask_id is None:
            logger.info(
                "Skipped mask job invalidation because mask id was None (reason=%s)",
                reason,
            )
            return
        logger.info(
            "Invalidating pending mask jobs for %s (reason=%s, redraw=%s)",
            mask_id,
            reason,
            request_redraw,
        )
        self._pending_prefetched_overlays.pop(mask_id, None)
        self._mask_controller.bumpMaskGeneration(mask_id, reason=reason)
        self._reset_pending_strokes(mask_id, request_redraw=request_redraw)

    def _diagnostics_provider(self, _: "QPane") -> Sequence[DiagnosticRecord]:
        """Surface recent mask service status messages for diagnostics overlays."""
        records: list[DiagnosticRecord] = []
        suppressed_labels = {"Mask", "Mask Autosave"}
        filtered: list[tuple[str, str]] = []
        if self._status_messages:
            filtered = [
                (label, message)
                for label, message in self._status_messages
                if label not in suppressed_labels
            ]
        label_counts: dict[str, int] = {}
        latest_messages: dict[str, str] = {}
        ordered_labels: list[str] = []
        for label, message in filtered:
            if label in ordered_labels:
                ordered_labels.remove(label)
            ordered_labels.append(label)
            label_counts[label] = label_counts.get(label, 0) + 1
            latest_messages[label] = message
        prefetch_messages = [
            message for label, message in filtered if label == "Mask Prefetch"
        ]
        display_labels = [label for label in ordered_labels if label != "Mask Prefetch"]
        for label in display_labels[-3:]:
            message = latest_messages[label]
            count = label_counts.get(label, 0)
            if count > 1:
                message = f"{message} (+{count - 1} earlier)"
            records.append(DiagnosticRecord(label, message))
        stats = self._prefetch_stats
        summary_line = self._prefetch_summary()
        detail_parts = []
        if stats.scheduled or stats.completed or stats.skipped or stats.failed:
            detail_parts.append(
                f"scheduled={stats.scheduled} completed={stats.completed} "
                f"skipped={stats.skipped} failed={stats.failed}"
            )
        hidden_events = max(len(prefetch_messages) - 1, 0)
        if hidden_events:
            plural = "s" if hidden_events > 1 else ""
            detail_parts.append(f"{hidden_events} earlier event{plural} hidden")
        value = summary_line
        if detail_parts:
            value = f"{summary_line} | {' | '.join(detail_parts)}"
        records.append(DiagnosticRecord("Mask|Prefetch", value))
        return tuple(records)

    def _record_status(self, message: str, *, label: str) -> None:
        """Cache a status update so diagnostics surfaces the latest mask activity."""
        self._status_messages.append((label, message))

    def _prepareMaskFromPath(
        self,
        path: str,
        *,
        target_size: QSize,
        failure_message: str,
        failure_label: str = "Mask Error",
    ) -> QImage | None:
        """Load and normalize mask image data from path."""
        prepared = Masking.prepare_from_file(path, target_size)
        if prepared is None:
            self._record_status(failure_message, label=failure_label)
        return prepared

    def _invalidateLayerCache(self, mask_id: uuid.UUID) -> MaskLayer | None:
        """Invalidate controller caches associated with mask_id when present."""
        layer = self._mask_manager.get_layer(mask_id)
        if layer is not None:
            self._mask_controller.invalidate_layer_cache(layer)
        return layer

    # High-level operations -------------------------------------------------

    def loadMaskFromPath(self, path: str) -> uuid.UUID | None:
        """Import a mask from path and attach it to the current image."""
        image = self._qpane.original_image
        if image is None or image.isNull():
            self._record_status(
                "Cannot load a mask before an image is set.", label="Mask Error"
            )
            return None
        prepared = self._prepareMaskFromPath(
            path,
            target_size=image.size(),
            failure_message=f"Failed to load or prepare mask from {path}",
        )
        if prepared is None:
            return None
        mask_id = self._mask_manager.create_mask(image)
        if not self._commit_mask_image(mask_id, prepared):
            return None
        current_image_id = self._catalog.currentImageID()
        if current_image_id is None:
            self._record_status(
                "Cannot attach a mask without an active image.", label="Mask Error"
            )
            return None
        self._mask_manager.associate_mask_with_image(mask_id, current_image_id)
        self._invalidateLayerCache(mask_id)
        self.activateMask(mask_id)
        self._qpane.markDirty()
        self._qpane.update()
        self._record_status(
            f"Successfully loaded mask data from {path} as new layer ({mask_id}).",
            label="Mask",
        )
        return mask_id

    def updateMaskFromPath(self, mask_id: uuid.UUID, path: str) -> bool:
        """Replace mask pixels for mask_id with data from path."""
        layer = self._mask_manager.get_layer(mask_id)
        if layer is None:
            self._record_status(
                f"Update failed: no mask layer found for {mask_id}", label="Mask Error"
            )
            return False
        prepared = self._prepareMaskFromPath(
            path,
            target_size=layer.mask_image.size(),
            failure_message=f"Update failed: could not prepare mask from {path}",
        )
        if prepared is None:
            return False
        if not self._commit_mask_image(mask_id, prepared):
            return False
        self._invalidateLayerCache(mask_id)
        self._qpane.markDirty()
        self._qpane.repaint()
        self._record_status(f"Mask {mask_id} updated from {path}", label="Mask")
        return True

    def createBlankMask(self, size: QSize) -> uuid.UUID | None:
        """Create a blank mask layer and associate it with the current image."""
        if size.isNull() or not size.isValid():
            self._record_status(
                "Cannot create blank mask with invalid size.", label="Mask Error"
            )
            return None
        current_image_id = self._catalog.currentImageID()
        if current_image_id is None:
            self._record_status(
                "Cannot create blank mask without an active image.", label="Mask Error"
            )
            return None
        template = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        template.fill(Qt.transparent)
        layer_index = len(self._mask_manager.get_mask_ids_for_image(current_image_id))
        mask_id = self._mask_manager.create_mask(template)
        self._mask_manager.associate_mask_with_image(mask_id, current_image_id)
        self.setMaskProperties(mask_id, color=_random_mask_color(layer_index))
        self._qpane.markDirty()
        self._qpane.update()
        self._record_status(f"Created blank mask layer ({mask_id}).", label="Mask")
        return mask_id

    def removeMaskFromImage(self, image_id: uuid.UUID, mask_id: uuid.UUID) -> bool:
        """Remove mask_id from image_id and refresh controller state."""
        manager = self._mask_manager
        controller = self._mask_controller
        if manager is None or controller is None:
            self._record_status(
                "Cannot remove mask because mask subsystems are not configured.",
                label="Mask Error",
            )
            return False
        if image_id is None:
            self._record_status(
                "Cannot remove mask because the image identifier is missing.",
                label="Mask Error",
            )
            return False
        was_active = controller.get_active_mask_id() == mask_id
        layer = manager.get_layer(mask_id)
        removed = manager.remove_mask_from_image(image_id, mask_id)
        if not removed:
            self._record_status(
                f"Mask {mask_id} is not associated with image {image_id}.",
                label="Mask Error",
            )
            return False
        controller.invalidate_layer_cache(layer)
        controller.bumpMaskGeneration(mask_id, reason="mask_removed")
        self._reset_pending_strokes(mask_id, request_redraw=False)
        controller.discardMaskGeneration(mask_id)
        remaining_ids = manager.get_mask_ids_for_image(image_id)
        next_active = remaining_ids[-1] if remaining_ids else None
        if was_active:
            controller.setActiveMaskID(next_active)
        else:
            controller.active_mask_properties_changed.emit()
            controller.mask_updated.emit(None, QRect())
        self._qpane.markDirty()
        self._qpane.update()
        self._record_status(
            f"Removed mask {mask_id} from image {image_id}.",
            label="Mask",
        )
        return True

    def setMaskProperties(
        self,
        mask_id: uuid.UUID,
        *,
        color: QColor | None = None,
        opacity: float | None = None,
    ) -> bool:
        """Update presentation properties for a mask layer."""
        layer = self._mask_manager.get_layer(mask_id)
        if layer is None:
            return False
        changed = False
        if color is not None:
            changed = self._mask_controller.setMaskColor(mask_id, color) or changed
        if opacity is not None:
            routed = self._route_mask_opacity_through_scene(mask_id, opacity)
            if routed is None:
                routed = self._mask_controller.setMaskOpacity(mask_id, opacity)
            changed = routed or changed
        return changed

    def cycleMasks(self, image_id: uuid.UUID, *, forward: bool) -> None:
        """Cycle mask ordering for image_id and refresh controller cache."""
        previous_active = self._mask_controller.get_active_mask_id()
        mask_ids = self._mask_manager.get_mask_ids_for_image(image_id)
        if len(mask_ids) >= 2:
            moving_mask = mask_ids[0] if forward else mask_ids[-1]
            target_index = self._scene_mask_stack_end_index(forward=forward)
            moved = (
                self._route_mask_reorder_through_scene(moving_mask, target_index)
                if target_index is not None
                else None
            )
            if moved is None:
                target_mask_index = len(mask_ids) - 1 if forward else 0
                moved = self._mask_manager.reorder_mask(
                    image_id,
                    moving_mask,
                    target_mask_index,
                )
        else:
            moved = False
        new_order = self._mask_manager.get_mask_ids_for_image(image_id)
        new_top = new_order[-1] if moved and new_order else None
        direction = "forward" if forward else "backward"
        if new_top:
            if previous_active is not None and previous_active != new_top:
                self._invalidate_pending_mask_jobs(
                    previous_active, reason="mask_reordered"
                )
            self._mask_controller.bumpMaskGeneration(new_top, reason="mask_reordered")
            self._reset_pending_strokes(new_top, request_redraw=False)
            self._mask_controller.setActiveMaskID(new_top)
            self._record_status(
                f"Cycled {direction} mask order; {new_top} is now active.",
                label="Mask",
            )
        else:
            self._record_status(
                f"Cycling {direction} mask order for {image_id} had no effect.",
                label="Mask",
            )

    def promoteMaskToTop(self, mask_id: uuid.UUID) -> bool:
        """Bring mask_id to the top of the active image mask stack."""
        current_image_id = self._catalog.currentImageID()
        if current_image_id is None:
            self._record_status(
                "Cannot promote a mask without an active image.", label="Mask Error"
            )
            return False
        manager = self._mask_manager
        if manager is None:
            self._record_status(
                "Cannot promote mask because no mask manager is configured.",
                label="Mask Error",
            )
            return False
        mask_ids = manager.get_mask_ids_for_image(current_image_id)
        if mask_id not in mask_ids:
            was_moved = False
        else:
            top_scene_index = self._scene_mask_stack_end_index(forward=True)
            routed = (
                self._route_mask_reorder_through_scene(mask_id, top_scene_index)
                if top_scene_index is not None
                else None
            )
            was_moved = (
                manager.reorder_mask(current_image_id, mask_id, len(mask_ids) - 1)
                if routed is None
                else routed
            )
        if was_moved:
            controller = self._mask_controller
            if controller is not None:
                controller.bumpMaskGeneration(mask_id, reason="mask_promoted")
            self._record_status(
                f"Promoted mask {mask_id} to the top of the stack.", label="Mask"
            )
        else:
            self._record_status(
                f"Mask {mask_id} is already at the top or not associated with the current image.",
                label="Mask Error",
            )
        return was_moved

    def refreshAutosavePolicy(self) -> None:
        """Re-evaluate autosave wiring based on the latest configuration and record its state.

        Hooks MaskController.mask_updated and MaskController.active_mask_properties_changed
        to the active autosave manager while listening for AutosaveManager.saveCompleted
        announcements.
        """
        self._autosave.refresh()
        manager = self._qpane.autosaveManager()
        autosave_active = should_enable_mask_autosave(self._qpane) and isinstance(
            manager, AutosaveManager
        )
        state = "enabled" if autosave_active else "disabled"
        manager_label = type(manager).__name__ if manager is not None else "None"
        previous_manager = self._connected_autosave_manager
        if isinstance(manager, AutosaveManager):
            if previous_manager is not manager:
                if previous_manager is not None:
                    try:
                        previous_manager.saveCompleted.disconnect(
                            self._handle_autosave_completed
                        )
                        previous_manager.saveFailed.disconnect(
                            self._handle_autosave_failed
                        )
                    except (TypeError, RuntimeError):
                        pass
                manager.saveCompleted.connect(self._handle_autosave_completed)
                manager.saveFailed.connect(self._handle_autosave_failed)
                self._connected_autosave_manager = manager
        elif previous_manager is not None:
            try:
                previous_manager.saveCompleted.disconnect(
                    self._handle_autosave_completed
                )
                previous_manager.saveFailed.disconnect(self._handle_autosave_failed)
            except (TypeError, RuntimeError):
                pass
            self._connected_autosave_manager = None
        self._record_status(
            f"Mask autosave {state} (manager={manager_label}).",
            label="Mask Autosave",
        )

    def handleMaskRegionUpdate(
        self, dirty_image_rect: QRect, mask_layer_supplier: Callable[[], object]
    ) -> None:
        """Notify controller of paint updates after external edits."""
        layer = mask_layer_supplier()
        if layer is None:
            return
        self.updateMaskRegion(dirty_image_rect, layer)


class MaskPrefetchWorker(QRunnable, BaseWorker):
    """Background worker that prepares colorized mask renders off the UI thread."""

    def __init__(
        self,
        *,
        image_id: uuid.UUID,
        mask_ids: Sequence[uuid.UUID],
        mask_manager: MaskManager,
        controller: MaskController,
        service: "MaskService",
        scales: Sequence[float] | None = None,
    ) -> None:
        """Record collaborators required to pre-colorize mask renders."""
        QRunnable.__init__(self)
        BaseWorker.__init__(self, logger=logger.getChild("MaskPrefetchWorker"))
        self._image_id = image_id
        manager = mask_manager
        masks = list(mask_ids)
        # Always process the current image first when available so scaled overlays are ready.
        current_id = None
        try:
            catalog = getattr(service, "_catalog", None)
            if catalog is not None:
                current_id = catalog.currentImageID()
        except Exception:  # pragma: no cover - defensive
            current_id = None
        if current_id in masks:
            masks.remove(current_id)
            masks.insert(0, current_id)
        self._mask_ids = tuple(masks)
        self._mask_manager = manager
        self._controller = controller
        self._service_ref = weakref.ref(service)
        self._scales: Tuple[float, ...] = tuple(scales or ())
        self._task_id: str | None = None
        self.setAutoDelete(False)

    def set_task_id(self, task_id: str) -> None:
        """Capture the executor task identifier for cancellation tracking."""
        self._task_id = task_id

    def run(self) -> None:
        """Perform the overlay prefetch work off the UI thread."""
        service = self._service_ref()
        if service is None:
            self.emit_finished(True)
            return
        warmed: list[PrefetchedOverlay] = []
        failures: dict[uuid.UUID, str] = {}
        error: BaseException | None = None
        start = monotonic()
        try:
            for mask_id in self._mask_ids:
                if self.is_cancelled:
                    error = RuntimeError("prefetch cancelled")
                    break
                layer = self._mask_manager.get_layer(mask_id)
                if layer is None or layer.mask_image.isNull():
                    continue
                try:
                    image = self._controller.prepare_colorized_mask(
                        layer, mask_id=mask_id
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    failures[mask_id] = str(exc)
                    continue
                if image is not None:
                    scaled_outputs: list[Tuple[float, QImage]] = []
                    for scale_key in self._scales:
                        target_size = self._controller._target_scaled_size(
                            image.size(), scale_key
                        )
                        if target_size == image.size() or target_size.isEmpty():
                            continue
                        scaled_image = image.scaled(
                            target_size,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        if scaled_image.isNull():
                            continue
                        scaled_outputs.append((scale_key, scaled_image))
                    warmed.append(
                        PrefetchedOverlay(
                            mask_id=mask_id,
                            image=image,
                            scaled=tuple(scaled_outputs),
                        )
                    )
        except Exception as exc:  # pragma: no cover - defensive
            error = exc
            logger.exception("Mask prefetch worker failed for image %s", self._image_id)
        duration_ms = (monotonic() - start) * 1000.0
        self._dispatch_finalize(tuple(warmed), failures, duration_ms, error)
        success = error is None and not self.is_cancelled
        self.emit_finished(success, payload=None, error=error)

    def _dispatch_finalize(
        self,
        warmed: tuple[PrefetchedOverlay, ...],
        failures: Mapping[uuid.UUID, str],
        duration_ms: float,
        error: BaseException | None,
    ) -> None:
        """Deliver prefetch completion on the main thread."""
        service = self._service_ref()
        if service is None:
            return

        def finalize() -> None:
            """Apply warmed overlays and propagate prefetch results to the service."""
            svc = self._service_ref()
            if svc is None:
                return
            svc._consume_prefetch_results(
                image_id=self._image_id,
                warmed=warmed,
                failures=dict(failures),
                duration_ms=duration_ms,
                error=error,
                task_id=self._task_id,
            )

        executor = self._executor
        if executor is not None and hasattr(executor, "dispatch_to_main_thread"):
            try:
                executor.dispatch_to_main_thread(
                    finalize, category="mask_prefetch_main"
                )
                return
            except AttributeError:
                pass
        QMetaObject.invokeMethod(
            self._controller,
            finalize,
            Qt.ConnectionType.QueuedConnection,
        )


class MaskSnippetWorker(QRunnable, BaseWorker):
    """Background worker that colorizes dirty mask snippets off the UI thread."""

    def __init__(
        self,
        *,
        mask_id: uuid.UUID,
        dirty_rect: QRect,
        snippet: QImage,
        color: QColor,
        controller: MaskController,
        service: "MaskService",
    ) -> None:
        """Capture snippet metadata and controller hooks for async colorization."""
        QRunnable.__init__(self)
        BaseWorker.__init__(self, logger=logger.getChild("MaskSnippetWorker"))
        self._mask_id = mask_id
        self._dirty_rect = QRect(dirty_rect)
        self._snippet = snippet
        self._color = QColor(color)
        self._controller = controller
        self._service_ref = weakref.ref(service)
        self.setAutoDelete(False)

    def run(self) -> None:
        """Colorize the provided snippet and dispatch results back to Qt."""
        service = self._service_ref()
        if service is None:
            self.emit_finished(True)
            return
        if self.is_cancelled:
            self.emit_finished(True)
            return
        colorized_image: QImage | None = None
        error: BaseException | None = None
        try:
            colorized_image = self._controller._colorize_with_metrics(
                self._snippet,
                self._color,
                mask_id=self._mask_id,
                source="snippet_async",
            )
        except Exception as exc:  # pragma: no cover - defensive
            error = exc
            self.logger.exception(
                "Mask snippet worker failed for mask %s", self._mask_id
            )
        if self.is_cancelled:
            self.emit_finished(True)
            return
        self._dispatch_finalize(colorized_image)
        self.emit_finished(error is None, error=error)

    def _dispatch_finalize(self, colorized_image: QImage | None) -> None:
        """Deliver snippet colorization results back to the service on the GUI thread."""
        service = self._service_ref()
        if service is None:
            return
        handle = getattr(self, "_handle", None)
        mask_id = self._mask_id
        rect = QRect(self._dirty_rect)

        def finalize() -> None:
            """Apply snippet outputs and clear bookkeeping safely."""
            svc = self._service_ref()
            if svc is None:
                return
            svc._consume_snippet_result(
                mask_id=mask_id,
                handle=handle,
                dirty_rect=rect,
                colorized_image=colorized_image,
            )

        executor = getattr(self, "_executor", None)
        if executor is not None and hasattr(executor, "dispatch_to_main_thread"):
            try:
                executor.dispatch_to_main_thread(finalize, category="mask_snippet_main")
                return
            except AttributeError:
                pass
        controller = service._mask_controller
        QMetaObject.invokeMethod(
            controller,
            finalize,
            Qt.ConnectionType.QueuedConnection,
        )


class MaskAutosaveCoordinator:
    """Manage autosave wiring between MaskController signals and the autosave manager."""

    def __init__(
        self,
        *,
        qpane: "QPane",
        mask_manager: MaskManager,
        mask_controller: MaskController,
        executor: TaskExecutorProtocol,
    ) -> None:
        """Store QPane/Mask collaborators and the shared executor for autosave wiring."""
        self._qpane = qpane
        self._mask_manager = mask_manager
        self._mask_controller = mask_controller
        self._executor = executor
        self._connected_manager: AutosaveManager | None = None
        self._active_save_slot = None
        self._connecting = False
        self._disconnecting = False

    def applyConfig(self, config: MaskConfigSlice) -> None:
        """Propagate configuration updates and refresh wiring."""
        manager = self._qpane.autosaveManager()
        if manager is not None:
            manager.applyConfig(config)
        self.refresh()

    def refresh(self) -> None:
        """Install or remove autosave wiring depending on configuration.

        Connects MaskController.mask_updated/active_mask_properties_changed to the autosave
        manager and listens for AutosaveManager.saveCompleted so QPane is notified when saves wrap.
        """
        if not should_enable_mask_autosave(self._qpane):
            self._disconnect(force=True)
            return
        manager = self._qpane.autosaveManager()
        if not isinstance(manager, AutosaveManager):
            manager = AutosaveManager(
                mask_manager=self._mask_manager,
                settings=require_mask_config(self._qpane.settings),
                get_current_image_path=lambda: self._qpane.currentImagePath,
                executor=self._executor,
                diagnostics_dirty=lambda domain="mask": self._qpane.diagnostics().set_dirty(
                    domain
                ),
                parent=self._qpane,
            )
            self._qpane.attachAutosaveManager(manager)
        self._connect(manager)

    def _connect(self, manager: AutosaveManager) -> None:
        """Wire autosave callbacks between the mask controller and manager."""
        if self._connected_manager is manager:
            return
        if self._connecting:
            logger.debug(
                "Mask autosave connect requested while another connect is in progress; skipping."
            )
            return
        self._connecting = True
        try:
            self._disconnect(force=False)
            controller = self._mask_controller
            controller.mask_updated.connect(manager.scheduleSave)
            controller.active_mask_properties_changed.connect(self._save_blank_mask)
            workflow = self._qpane._masks_controller
            save_slot = workflow.on_mask_saved
            manager.saveCompleted.connect(save_slot)
            self._active_save_slot = save_slot
            self._connected_manager = manager
        finally:
            self._connecting = False

    def _disconnect(self, *, force: bool) -> None:
        """Unhook autosave callbacks and optionally detach the manager from QPane."""
        if self._disconnecting:
            return
        self._disconnecting = True
        manager = self._connected_manager
        controller = self._mask_controller
        try:
            if manager is not None:
                mappings = (
                    (controller.mask_updated, manager.scheduleSave, "mask_updated"),
                    (
                        controller.active_mask_properties_changed,
                        self._save_blank_mask,
                        "active_mask_properties_changed",
                    ),
                )
                for signal, slot, signal_name in mappings:
                    try:
                        signal.disconnect(slot)
                    except (TypeError, RuntimeError) as exc:
                        logger.warning(
                            "Failed to disconnect mask autosave signal %s: %s",
                            signal_name,
                            exc,
                        )
                save_slot = getattr(self, "_active_save_slot", None)
                if save_slot is not None:
                    try:
                        manager.saveCompleted.disconnect(save_slot)
                    except (TypeError, RuntimeError) as exc:
                        logger.warning(
                            "Failed to disconnect autosave completion handler: %s",
                            exc,
                        )
            self._connected_manager = None
            self._active_save_slot = None
            if force:
                current = self._qpane.autosaveManager()
                if current is manager:
                    self._qpane.detachAutosaveManager()
        finally:
            self._disconnecting = False

    def _save_blank_mask(self) -> None:
        """Request a blank mask autosave when properties change on the active mask."""
        manager = self._qpane.autosaveManager()
        if manager is None:
            return
        active_id = self._mask_controller.get_active_mask_id()
        image = getattr(self._qpane, "original_image", None)
        if not active_id or image is None or image.isNull():
            return
        manager.saveBlankMask(str(active_id), image.size())


def should_enable_mask_autosave(qpane: "QPane") -> bool:
    """Return True when mask autosave should be active for qpane using public QPane accessors."""
    settings = getattr(qpane, "settings", None)
    if settings is None:
        return False
    try:
        mask_config = require_mask_config(settings)
    except FeatureInstallError:
        return False
    if not mask_config.mask_autosave_enabled:
        return False
    service = getattr(qpane, "mask_service", None)
    if service is None:
        return False
    image_accessor = getattr(qpane, "currentImage", None)
    if callable(image_accessor):
        try:
            image_accessor()
        except Exception:
            pass
    return True


def _random_mask_color(layer_index: int = 0) -> QColor:
    """Generate a deterministic color for new mask layers."""
    golden_ratio = 0.6180339887498949
    hue_fraction = (layer_index * golden_ratio) % 1.0
    hue = int(hue_fraction * 359)
    return QColor.fromHsv(hue, 200, 255)
