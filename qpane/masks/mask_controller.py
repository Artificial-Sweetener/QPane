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

"""Mask controller coordinating mask layers, caching, and rendering helpers."""

from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, MutableMapping, Sequence

import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from ..catalog.image_utils import (
    numpy_to_qimage_argb32,
    numpy_to_qimage_grayscale8,
    qimage_to_numpy_grayscale8,
)
from ..core import CacheSettings, Config
from ..core.config_features import MaskConfigSlice, require_mask_config
from .mask import MaskLayer, MaskManager
from .mask_diagnostics import MaskStrokeDiagnostics
from .mask_undo import MaskHistoryChange, MaskPatch

logger = logging.getLogger(__name__)

try:
    import cv2  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class MaskOverlayMetrics:
    """Snapshot of mask overlay cache health for diagnostics."""

    cache_bytes: int
    entry_count: int
    hits: int
    misses: int
    evictions: int
    evicted_bytes: int
    last_eviction_reason: str | None
    last_eviction_timestamp: float | None
    cache_limit: int = 0
    pending_retries: int = 0
    prefetch_requested: int = 0
    prefetch_completed: int = 0
    prefetch_failed: int = 0
    last_prefetch_ms: float | None = None
    colorize_last_ms: float | None = None
    colorize_avg_ms: float | None = None
    colorize_max_ms: float | None = None
    colorize_samples: int = 0
    colorize_slow_count: int = 0
    colorize_threshold_ms: float = 25.0
    colorize_last_source: str | None = None


@dataclass(frozen=True, slots=True)
class MaskReadyUpdate:
    """Describe a generated mask update awaiting overlay promotion."""

    mask_id: uuid.UUID
    dirty_rect: QRect | None
    mask_layer: "MaskLayer" | None
    changed: bool


@dataclass(frozen=True, slots=True)
class MaskStrokeSegmentPayload:
    """Immutable description of a brush segment captured on the UI thread."""

    start: tuple[int, int]
    end: tuple[int, int]
    brush_size: int
    erase: bool


@dataclass(frozen=True, slots=True)
class MaskStrokePayload:
    """Bundle stroke segments and stride metadata for worker replays."""

    segments: tuple[MaskStrokeSegmentPayload, ...]
    stride: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MaskStrokeJobSpec:
    """Describe a mask stroke job prepared on the UI thread."""

    mask_id: uuid.UUID
    generation: int
    dirty_rect: QRect
    before: np.ndarray
    payload: MaskStrokePayload | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MaskStrokeJobResult:
    """Capture the outcome of a mask stroke ready for main-thread merging."""

    mask_id: uuid.UUID
    generation: int
    dirty_rect: QRect
    before: np.ndarray
    after: np.ndarray
    preview_image: QImage | None = None
    payload: MaskStrokePayload | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class Masking:
    """Utility helpers for loading and normalizing mask imagery."""

    @staticmethod
    def prepare_from_file(path: str, target_size: QSize) -> QImage | None:
        """Load a mask image and convert it to the grayscale format used by mask layers.

        Scaling honours Qt's KeepAspectRatio mode, so callers expecting an exact
        size should pad or crop the result as needed.
        """
        mask_pixmap = QPixmap(path)
        if mask_pixmap.isNull():
            return None
        # Scale the mask to be the same size as the target image
        if mask_pixmap.size() != target_size:
            mask_pixmap = mask_pixmap.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
            )
        # Convert to a QImage and then to the correct format
        mask_image = mask_pixmap.toImage()
        if mask_image.format() != QImage.Format.Format_Grayscale8:
            mask_image = mask_image.convertToFormat(QImage.Format.Format_Grayscale8)
        return mask_image


@dataclass(slots=True)
class _StrokeAccumulator:
    """Collect and merge mask patches produced during a stroke."""

    _patches: list[MaskPatch]

    def __init__(self) -> None:
        """Start with an empty patch list ready to capture strokes."""
        self._patches = []

    def reset(self) -> None:
        """Clear the recorded patches so a new stroke can begin."""
        self._patches.clear()

    def add_patch(self, patch: MaskPatch) -> None:
        """Append ``patch`` to the accumulator."""
        self._patches.append(patch)

    def consume(self) -> tuple[MaskPatch, ...]:
        """Return and clear the recorded patches."""
        patches = tuple(self._patches)
        self._patches.clear()
        return patches


class MaskController(QObject):
    """Manage mask layer state, caching, and rendering policies.

    Expects a mask configuration slice for runtime toggles such as
    ``mask_border_enabled`` in addition to the base config view for cache metrics.
    """

    mask_updated = Signal(object, QRect)
    active_mask_properties_changed = Signal()
    undo_stack_changed = Signal(object)

    def __init__(
        self,
        mask_manager: MaskManager,
        image_to_panel_point: Callable[[QPoint], QPoint | QPointF | None],
        config: Config,
        mask_config: MaskConfigSlice | None = None,
        stroke_diagnostics: MaskStrokeDiagnostics | None = None,
    ):
        """Initialize mask caches, coordinate transforms, and diagnostics hooks.

        Args:
            mask_manager: Manager providing mask data for each image.
            image_to_panel_point: Callable to convert image coordinates for UI updates.
            config: Feature-aware configuration providing cache budgets.
            mask_config: Optional mask slice override when the caller already
                resolved the feature configuration.
            stroke_diagnostics: Optional diagnostics helper for stroke timing/counters.
        """
        super().__init__()
        self.mask_manager = mask_manager
        self._image_to_panel_point: Callable[[QPoint], QPoint | QPointF | None] = (
            image_to_panel_point
        )
        self._config_source = config
        self._mask_config = mask_config or require_mask_config(config)
        self._stroke_diagnostics = stroke_diagnostics
        self._active_mask_id = None
        self._mask_generations: dict[uuid.UUID, int] = {}
        self._premultiplied_alpha_lut = self._create_premultiplied_alpha_lut()
        self._colorized_mask_cache: OrderedDict[tuple[int, float | None], QPixmap] = (
            OrderedDict()
        )
        self._colorized_mask_bytes: dict[tuple[int, float | None], int] = {}
        self._colorized_cache_total_bytes: int = 0
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._cache_evictions: int = 0
        self._cache_evicted_bytes: int = 0
        self._last_eviction_reason: str | None = None
        self._last_eviction_timestamp: float | None = None
        self._colorize_last_ms: float | None = None
        self._colorize_avg_ms: float | None = None
        self._colorize_total_ms: float = 0.0
        self._colorize_max_ms: float | None = None
        self._colorize_samples: int = 0
        self._colorize_slow_count: int = 0
        self._colorize_threshold_ms: float = 25.0
        self._colorize_last_source: str | None = None
        self._layer_ids: dict[int, uuid.UUID] = {}
        self._layer_cache_index: dict[int, set[tuple[int, float | None]]] = {}
        self._missing_cv2_warned = False
        self._prefetch_requested: int = 0
        self._prefetch_completed: int = 0
        self._prefetch_failed: int = 0
        self._last_prefetch_ms: float | None = None
        self._stroke_accumulators: dict[uuid.UUID, _StrokeAccumulator] = {}
        self._async_colorize_handler: Callable[[uuid.UUID, MaskLayer], bool] | None = (
            None
        )
        self._async_colorize_pending: set[uuid.UUID] = set()
        self._async_colorize_threshold_px: int = 512 * 512
        self._cache_usage_callback: Callable[[], None] | None = None
        self._prefetched_image_limit = 8
        self._prefetched_images: OrderedDict[uuid.UUID, QImage] = OrderedDict()
        self._prefetched_scaled_images: OrderedDict[
            uuid.UUID, OrderedDict[float, QImage]
        ] = OrderedDict()
        self._cache_admission_guard = None
        self._rejected_cache_keys: set[tuple[int, float | None]] = set()

    def _get_layer(self, mask_id) -> "MaskLayer | None":
        """Return the mask layer for ``mask_id`` if it exists."""
        if mask_id is None:
            return None
        layer = self.mask_manager.get_layer(mask_id)
        if layer is not None:
            self._register_layer_identity(mask_id, layer)
        return layer

    def _record_stroke_event(self, event: str) -> None:
        """Record mask stroke diagnostics when trackers are configured."""
        tracker = getattr(self, "_stroke_diagnostics", None)
        if tracker is None or not event:
            return
        tracker.record_generation_event(event)

    def _layer_is_empty(self, mask_layer: MaskLayer | None) -> bool:
        """Return True when `mask_layer` lacks backing pixel data."""
        if mask_layer is None:
            return True
        return mask_layer.surface.is_null()

    def _snapshot_layer_image(self, mask_layer: MaskLayer | None) -> QImage:
        """Return a detached grayscale snapshot for `mask_layer`."""
        if mask_layer is None:
            return QImage()
        return mask_layer.surface.snapshot_qimage()

    def _register_layer_identity(
        self, mask_id: uuid.UUID, mask_layer: MaskLayer
    ) -> None:
        """Cache the association between a mask layer instance and its identifier."""
        self._layer_ids[id(mask_layer)] = mask_id

    def _resolve_mask_id(self, mask_layer: MaskLayer | None) -> uuid.UUID | None:
        """Return the mask identifier for `mask_layer` when available."""
        if mask_layer is None:
            return None
        key = id(mask_layer)
        mask_id = self._layer_ids.get(key)
        if mask_id is not None:
            return mask_id
        mask_id = self.mask_manager.find_mask_id_for_layer(mask_layer)
        if mask_id is not None:
            self._layer_ids[key] = mask_id
        return mask_id

    def _ensure_stroke_accumulator(self, mask_id: uuid.UUID) -> _StrokeAccumulator:
        """Return the accumulator for `mask_id`, creating it when missing."""
        accumulator = self._stroke_accumulators.get(mask_id)
        if accumulator is None:
            accumulator = _StrokeAccumulator()
            self._stroke_accumulators[mask_id] = accumulator
        return accumulator

    def _drain_stroke_patches(self, mask_id: uuid.UUID) -> tuple[MaskPatch, ...]:
        """Return and clear recorded patches for ``mask_id``."""
        accumulator = self._stroke_accumulators.pop(mask_id, None)
        if accumulator is None:
            return tuple()
        return accumulator.consume()

    def getMaskGeneration(self, mask_id: uuid.UUID) -> int:
        """Return the controller-tracked generation counter for `mask_id`."""
        return self._mask_generations.get(mask_id, 0)

    def bumpMaskGeneration(
        self, mask_id: uuid.UUID, *, reason: str | None = None
    ) -> int:
        """Advance and return the generation counter for `mask_id`."""
        next_generation = self.getMaskGeneration(mask_id) + 1
        self._mask_generations[mask_id] = next_generation
        if reason:
            logger.debug(
                "Mask %s generation advanced to %s (%s).",
                mask_id,
                next_generation,
                reason,
            )
        return next_generation

    def discardMaskGeneration(self, mask_id: uuid.UUID) -> None:
        """Forget controller generation tracking for `mask_id`."""
        self._mask_generations.pop(mask_id, None)

    def prepareStrokeJob(
        self,
        mask_id: uuid.UUID,
        dirty_rect: QRect,
        *,
        payload: MaskStrokePayload | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MaskStrokeJobSpec | None:
        """Snapshot the data required to process a stroke off the UI thread."""
        layer = self._get_layer(mask_id)
        if layer is None or self._layer_is_empty(layer):
            logger.warning("Cannot prepare stroke job for missing mask %s.", mask_id)
            return None
        bounded = dirty_rect.normalized().intersected(layer.mask_image.rect())
        if bounded.isNull() or bounded.isEmpty():
            return None
        top = bounded.top()
        left = bounded.left()
        height = bounded.height()
        width = bounded.width()
        bottom = top + height
        right = left + width
        with layer.surface.lock:
            view = layer.surface.borrow_buffer()
            before_slice = np.array(view[top:bottom, left:right], copy=True)
        meta: MutableMapping[str, Any]
        if metadata is None:
            meta = {}
        else:
            meta = dict(metadata)
        return MaskStrokeJobSpec(
            mask_id=mask_id,
            generation=self.getMaskGeneration(mask_id),
            dirty_rect=bounded,
            before=before_slice,
            payload=payload,
            metadata=meta,
        )

    def applyStrokeJob(
        self,
        job: MaskStrokeJobResult,
        *,
        emit_mask_updated: bool = True,
        on_stale: Callable[[MaskStrokeJobResult], None] | None = None,
    ) -> bool:
        """Merge a stroke job result into the canonical mask state."""
        mask_id = job.mask_id
        diagnostics_log = (
            logger.info
            if getattr(self._stroke_diagnostics, "logging_enabled", False)
            else logger.debug
        )
        layer = self._get_layer(mask_id)
        if layer is None or self._layer_is_empty(layer):
            logger.warning("Cannot apply stroke job for missing mask %s.", mask_id)
            return False
        expected_generation = self.getMaskGeneration(mask_id)
        if job.generation != expected_generation:
            allow_rebase = bool(job.metadata.get("allow_generation_rebase"))
            if allow_rebase and job.generation < expected_generation:
                diagnostics_log(
                    "rebasing stroke job generation (mask=%s job=%s expected=%s)",
                    mask_id,
                    job.generation,
                    expected_generation,
                )
                self._record_stroke_event("rebased")
                job = replace(job, generation=expected_generation)
            elif job.generation > expected_generation:
                diagnostics_log(
                    "clamping future stroke job generation (mask=%s job=%s expected=%s)",
                    mask_id,
                    job.generation,
                    expected_generation,
                )
                self._record_stroke_event("clamped")
                job = replace(job, generation=expected_generation)
            else:
                logger.info(
                    "Discarded stroke job for mask %s due to stale generation (job=%s current=%s).",
                    mask_id,
                    job.generation,
                    expected_generation,
                )
                self._record_stroke_event("stale_drop")
                if on_stale is not None:
                    on_stale(job)
                return False
        rect = job.dirty_rect.normalized()
        if rect.isNull() or rect.isEmpty():
            return False
        height = rect.height()
        width = rect.width()
        if job.after.shape != (height, width) or job.before.shape != (height, width):
            logger.error(
                "Stroke job payload dimensions do not match dirty rect %s for mask %s.",
                rect,
                mask_id,
            )
            return False
        top = rect.top()
        left = rect.left()
        bottom = top + height
        right = left + width

        def _apply(dest_view: np.ndarray, _: QImage) -> None:
            """Copy the stroke result into the destination mask slice."""
            region = dest_view[top:bottom, left:right]
            np.copyto(region, job.after)

        layer.surface.mutate_with_view(_apply)
        self.recordStrokePatchFromArrays(mask_id, rect, job.before, job.after)
        self.bumpMaskGeneration(mask_id, reason="stroke_job_applied")
        if emit_mask_updated:
            self.mask_updated.emit(mask_id, rect)
        return True

    def _array_to_patch_image(self, array: np.ndarray) -> QImage:
        """Return a detached QImage created from a grayscale NumPy array."""
        if array.ndim != 2:
            raise ValueError("Patch arrays must be 2-D grayscale slices.")
        if array.dtype != np.uint8:
            array = array.astype(np.uint8, copy=False)
        return numpy_to_qimage_grayscale8(array)

    def recordStrokePatch(
        self,
        mask_id: uuid.UUID,
        rect: QRect,
        before: QImage,
        after: QImage,
    ) -> None:
        """Record a patch delta for `mask_id` covering `rect`."""
        before_np = qimage_to_numpy_grayscale8(before)
        after_np = qimage_to_numpy_grayscale8(after)
        self.recordStrokePatchFromArrays(mask_id, rect, before_np, after_np)

    def recordStrokePatchFromArrays(
        self,
        mask_id: uuid.UUID,
        rect: QRect,
        before: np.ndarray,
        after: np.ndarray,
    ) -> None:
        """Record a patch delta using precomputed grayscale arrays."""
        if rect.isNull() or rect.isEmpty():
            return
        if before.shape != after.shape:
            raise ValueError("Patch arrays must share identical shape.")
        diff_mask = before != after
        if not np.any(diff_mask):
            return
        ys, xs = np.nonzero(diff_mask)
        min_y = int(ys.min())
        max_y = int(ys.max())
        min_x = int(xs.min())
        max_x = int(xs.max())
        local_width = max_x - min_x + 1
        local_height = max_y - min_y + 1
        normalized_rect = rect.normalized()
        global_top_left = QPoint(
            normalized_rect.left() + min_x,
            normalized_rect.top() + min_y,
        )
        global_rect = QRect(global_top_left, QSize(local_width, local_height))
        before_slice = before[min_y : max_y + 1, min_x : max_x + 1]
        after_slice = after[min_y : max_y + 1, min_x : max_x + 1]
        before_image = self._array_to_patch_image(before_slice)
        after_image = self._array_to_patch_image(after_slice)
        mask_slice = np.ascontiguousarray(
            diff_mask[min_y : max_y + 1, min_x : max_x + 1]
        )
        accumulator = self._ensure_stroke_accumulator(mask_id)
        accumulator.add_patch(
            MaskPatch(
                rect=global_rect,
                before=before_image,
                after=after_image,
                mask=mask_slice,
            )
        )

    def _commit_mask_update(
        self,
        mask_id: uuid.UUID,
        *,
        image: QImage | None = None,
        before: QImage | None = None,
        patches: Sequence[MaskPatch] | tuple[MaskPatch, ...] = (),
        preserve_cache: bool = False,
    ) -> bool:
        """Submit the recorded update and refresh caches."""
        if patches:
            success = self.mask_manager.commit_mask_patches(mask_id, patches)
        else:
            if image is None:
                logger.error(
                    "commit_mask_update aborted for mask %s: image payload missing.",
                    mask_id,
                )
                return False
            success = self.mask_manager.commit_mask_image(
                mask_id,
                image,
                before_image=before,
            )
        if not success:
            return False
        self.bumpMaskGeneration(mask_id, reason="commit_mask_update")
        layer = self._get_layer(mask_id)
        if layer is not None and not preserve_cache:
            self._invalidate_colorized_mask_cache(layer)
        self.undo_stack_changed.emit(mask_id)
        return True

    def _normalize_scale_key(self, scale: float | None) -> float | None:
        """Return a normalized cache key for the requested scale."""
        if scale is None:
            return None
        try:
            value = float(scale)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        if abs(value - 1.0) < 1e-3:
            return None
        return round(value, 4)

    def _cache_key(
        self, mask_layer: MaskLayer, scale_key: float | None
    ) -> tuple[int, float | None]:
        """Build a composite cache key from the layer identity and scale marker."""
        return (id(mask_layer), scale_key)

    def _target_scaled_size(self, size: QSize, scale_key: float) -> QSize:
        """Return the integer QSize that corresponds to applying `scale_key`."""
        width = max(1, int(round(size.width() * scale_key)))
        height = max(1, int(round(size.height() * scale_key)))
        return QSize(width, height)

    def apply_config(
        self, config: Config, mask_config: MaskConfigSlice | None = None
    ) -> None:
        """Swap the configuration used for rendering policies."""
        previous_config = self._mask_config
        self._config_source = config
        self._mask_config = mask_config or require_mask_config(config)
        if previous_config.mask_border_enabled != self._mask_config.mask_border_enabled:
            self._missing_cv2_warned = False
            self.clear_cache()
            if self._active_mask_id is not None:
                self._warm_mask_cache(self._active_mask_id)
                self.mask_updated.emit(self._active_mask_id, QRect())

    def set_cache_usage_callback(self, callback: Callable[[], None] | None) -> None:
        """Register ``callback`` to run whenever mask cache usage changes."""
        self._cache_usage_callback = callback
        if callback is not None:
            self._notify_cache_usage()

    def set_admission_guard(self, guard: Callable[[int], bool] | None) -> None:
        """Install an optional guard consulted before caching overlays."""
        self._cache_admission_guard = guard

    def mask_cache_limit_bytes(self) -> int:
        """Return the configured mask cache budget in bytes."""
        cache_settings = getattr(self._config_source, "cache", None)
        if not isinstance(cache_settings, CacheSettings):
            cache_settings = CacheSettings()
        budgets = cache_settings.resolved_consumer_budgets_bytes()
        return max(0, int(budgets.get("masks", 0)))

    def _store_prefetched_image(
        self, mask_id: uuid.UUID | None, image: QImage | None
    ) -> None:
        """Remember a prefetched colorized image for reuse on the UI thread."""
        if mask_id is None or image is None or image.isNull():
            return
        self._prefetched_images[mask_id] = image
        self._prefetched_images.move_to_end(mask_id)
        while len(self._prefetched_images) > self._prefetched_image_limit:
            stale_mask_id, _ = self._prefetched_images.popitem(last=False)
            self._prefetched_scaled_images.pop(stale_mask_id, None)

    def _store_prefetched_scaled_images(
        self,
        mask_id: uuid.UUID | None,
        scaled: Sequence[tuple[float, QImage]] | None,
    ) -> None:
        """Remember prefetched scaled overlays so they can be promoted into the cache."""
        if mask_id is None or not scaled:
            return
        bucket: OrderedDict[float, QImage] = OrderedDict()
        limit = self._prefetched_image_limit
        for scale_value, scaled_image in scaled:
            normalized_scale = self._normalize_scale_key(scale_value)
            if normalized_scale is None or scaled_image.isNull():
                continue
            bucket[normalized_scale] = scaled_image
        if not bucket:
            return
        self._prefetched_scaled_images[mask_id] = bucket
        self._prefetched_scaled_images.move_to_end(mask_id)
        while len(bucket) > limit:
            bucket.popitem(last=False)
        while len(self._prefetched_scaled_images) > limit:
            stale_mask_id, _ = self._prefetched_scaled_images.popitem(last=False)
            self._prefetched_images.pop(stale_mask_id, None)

    def _forget_prefetched_image(self, mask_id: uuid.UUID | None) -> None:
        """Drop any stored prefetched image associated with `mask_id`."""
        if mask_id is None:
            return
        self._prefetched_images.pop(mask_id, None)
        self._prefetched_scaled_images.pop(mask_id, None)

    def _notify_cache_usage(self) -> None:
        """Invoke the registered cache usage callback if present."""
        if self._cache_usage_callback is None:
            return
        try:
            self._cache_usage_callback()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Mask cache usage callback failed")

    def record_prefetch_request(self, count: int) -> None:
        """Track the number of mask overlays scheduled for prefetch work."""
        if count <= 0:
            return
        self._prefetch_requested += count

    def record_prefetch_completion(
        self,
        *,
        completed: int,
        failed: int = 0,
        duration_ms: float | None = None,
    ) -> None:
        """Update prefetch metrics once background warming completes."""
        if completed > 0:
            self._prefetch_completed += completed
        if failed > 0:
            self._prefetch_failed += failed
        if duration_ms is not None:
            self._last_prefetch_ms = duration_ms
        self._notify_cache_usage()

    def set_async_colorize_handler(
        self,
        handler: Callable[[uuid.UUID, MaskLayer], bool] | None,
        *,
        threshold_px: int | None = None,
    ) -> None:
        """Register a handler invoked when cache misses should colorize off-thread."""
        self._async_colorize_handler = handler
        if threshold_px is not None and threshold_px > 0:
            self._async_colorize_threshold_px = threshold_px

    def notify_async_colorize_complete(self, mask_id: uuid.UUID) -> None:
        """Mark any pending async colorize request for `mask_id` as finished."""
        self._async_colorize_pending.discard(mask_id)

    def invalidate_mask_cache(
        self, mask_id: uuid.UUID | None, *, reason: str = "invalidate"
    ) -> None:
        """Invalidate cached overlays for ``mask_id`` when available."""
        if mask_id is None:
            return
        mask_layer = self._get_layer(mask_id)
        self._invalidate_colorized_mask_cache(mask_layer, reason=reason)

    def invalidate_image_cache(
        self, image_id: uuid.UUID, *, reason: str = "image_invalidate"
    ) -> None:
        """Invalidate cached overlays for all masks linked to ``image_id``."""
        mask_ids = self.mask_manager.get_mask_ids_for_image(image_id)
        for mask_id in mask_ids:
            self.invalidate_mask_cache(mask_id, reason=reason)

    def setActiveMaskID(
        self, mask_id, *, warm_cache: bool = True, emit_signals: bool = True
    ) -> bool:
        """Set the currently active mask layer, emitting only when it changes."""
        if mask_id == self._active_mask_id:
            return False
        self._active_mask_id = mask_id
        if warm_cache:
            self._warm_mask_cache(mask_id)
        if emit_signals:
            self.active_mask_properties_changed.emit()
            self.mask_updated.emit(mask_id, QRect())
        return True

    def emit_activation_signals(self, mask_id: uuid.UUID | None) -> None:
        """Emit activation-related signals for `mask_id`."""
        self.active_mask_properties_changed.emit()
        self.mask_updated.emit(mask_id, QRect())

    def warmMaskCache(self, mask_id: uuid.UUID | None) -> None:
        """Prime the colorized cache for `mask_id` when present."""
        if mask_id is None:
            return
        self._warm_mask_cache(mask_id)

    def prepare_colorized_mask(
        self,
        mask_layer: MaskLayer,
        *,
        mask_id: uuid.UUID | None = None,
        source: str = "prefetch",
    ) -> QImage | None:
        """Build a colorized mask image while recording timing metrics."""
        if self._layer_is_empty(mask_layer):
            return None
        if mask_id is not None:
            self._register_layer_identity(mask_id, mask_layer)
            resolved_id = mask_id
        else:
            resolved_id = self._resolve_mask_id(mask_layer)
        snapshot = self._snapshot_layer_image(mask_layer)
        if snapshot.isNull():
            return None
        return self._colorize_with_metrics(
            snapshot,
            mask_layer.color,
            mask_id=resolved_id,
            source=source,
        )

    def commit_prefetched_mask(
        self,
        mask_id: uuid.UUID,
        mask_layer: MaskLayer,
        image: QImage,
        *,
        scaled: Sequence[tuple[float, QImage]] | None = None,
    ) -> None:
        """Insert a prefetched mask image (and any scaled variants) into the cache."""
        if mask_layer is None or image.isNull():
            return
        self._store_prefetched_image(mask_id, image)
        self._store_prefetched_scaled_images(mask_id, scaled)
        pixmap = QPixmap.fromImage(image)
        cache_key = self._cache_key(mask_layer, None)
        self._register_layer_identity(mask_id, mask_layer)
        self._cache_misses += 1
        self._record_cache_insert(cache_key, pixmap, mask_id=mask_id)
        if scaled:
            for scale_value, scaled_image in scaled:
                normalized_scale = self._normalize_scale_key(scale_value)
                if normalized_scale is None or scaled_image.isNull():
                    continue
                scaled_cache_key = self._cache_key(mask_layer, normalized_scale)
                scaled_pixmap = QPixmap.fromImage(scaled_image)
                self._record_cache_insert(
                    scaled_cache_key, scaled_pixmap, mask_id=mask_id
                )
        self.mask_updated.emit(mask_id, QRect())

    def setMaskProperties(self, mask_id, color: QColor = None, opacity: float = None):
        """Update mask presentation details and emit when values change."""
        mask_layer = self._get_layer(mask_id)
        if mask_layer is None:
            return False
        changed = False
        if color is not None and color != mask_layer.color:
            changed = True
        if opacity is not None and opacity != mask_layer.opacity:
            changed = True
        if not changed:
            return False
        self.mask_manager.set_mask_properties(mask_id, color, opacity)
        self._invalidate_colorized_mask_cache(mask_layer)
        self._warm_mask_cache(mask_id)
        self.active_mask_properties_changed.emit()
        self.mask_updated.emit(mask_id, QRect())
        return True

    def getActiveMaskImage(self) -> QImage | None:
        """Retrieves the QImage for the currently active mask layer."""
        layer = self._get_layer(self._active_mask_id)
        if layer is None:
            return None
        snapshot = self._snapshot_layer_image(layer)
        return None if snapshot.isNull() else snapshot

    def get_active_mask_color(self) -> QColor | None:
        """Gets the QColor for the currently active mask layer."""
        layer = self._get_layer(self._active_mask_id)
        if layer is not None:
            return layer.color
        return None

    def get_active_mask_id(self):
        """Gets the ID of the currently active mask layer."""
        return self._active_mask_id

    @property
    def cache_usage_bytes(self) -> int:
        """Return the total bytes consumed by cached colorized masks."""
        return self._colorized_cache_total_bytes

    def snapshot_metrics(self) -> MaskOverlayMetrics:
        """Return cache metrics for diagnostics overlays and tests."""
        return MaskOverlayMetrics(
            cache_bytes=self._colorized_cache_total_bytes,
            entry_count=len(self._colorized_mask_cache),
            hits=self._cache_hits,
            misses=self._cache_misses,
            evictions=self._cache_evictions,
            evicted_bytes=self._cache_evicted_bytes,
            last_eviction_reason=self._last_eviction_reason,
            last_eviction_timestamp=self._last_eviction_timestamp,
            prefetch_requested=self._prefetch_requested,
            prefetch_completed=self._prefetch_completed,
            prefetch_failed=self._prefetch_failed,
            last_prefetch_ms=self._last_prefetch_ms,
            colorize_last_ms=self._colorize_last_ms,
            colorize_avg_ms=self._colorize_avg_ms,
            colorize_max_ms=self._colorize_max_ms,
            colorize_samples=self._colorize_samples,
            colorize_slow_count=self._colorize_slow_count,
            colorize_threshold_ms=self._colorize_threshold_ms,
            colorize_last_source=self._colorize_last_source,
        )

    def clear_cache(self):
        """Clears the internal cache of colorized mask pixmaps."""
        if not self._colorized_mask_cache:
            return
        for key in list(self._colorized_mask_cache.keys()):
            self._drop_cached_mask(key, reason="clear")
        self._colorized_mask_cache.clear()
        self._colorized_mask_bytes.clear()
        self._layer_cache_index.clear()
        self._colorized_cache_total_bytes = 0
        self._rejected_cache_keys.clear()
        self._notify_cache_usage()

    def _apply_history_operation(
        self, operator: Callable[[uuid.UUID], MaskHistoryChange | None]
    ) -> bool:
        """Execute an undo/redo operation supplied by the manager."""
        mask_id = self._active_mask_id
        if mask_id is None:
            return False
        change = operator(mask_id)
        if change is None:
            return False
        mask_layer = self._get_layer(mask_id)
        applied_delta = False
        if mask_layer is not None and change.has_snippets:
            applied_delta = self._apply_history_delta(mask_layer, change)
        if not applied_delta:
            if mask_layer is not None:
                self._invalidate_colorized_mask_cache(mask_layer)
            self.mask_updated.emit(mask_id, QRect())
        self.undo_stack_changed.emit(mask_id)
        return True

    def undoMaskEdit(self) -> bool:
        """Undo the most recent mask change tracked for the active layer."""
        return self._apply_history_operation(self.mask_manager.undo_mask)

    def redoMaskEdit(self) -> bool:
        """Redo the previously undone mask change for the active layer."""
        return self._apply_history_operation(self.mask_manager.redo_mask)

    def cycle_active_mask(self, image_id, forward: bool = True):
        """Calls the MaskManager to cycle the layer order and then updates the

        active mask to be the new top layer.
        """
        new_top_id = self.mask_manager.cycle_mask_order(image_id, forward)
        if new_top_id:
            self.bumpMaskGeneration(new_top_id, reason="cycle_active_mask")
            self.setActiveMaskID(new_top_id)
            self.mask_updated.emit(new_top_id, QRect())

    def pushUndoState(self):
        """Prepare the patch accumulator for the next undoable stroke."""
        mask_id = self._active_mask_id
        if mask_id is None:
            return False
        accumulator = self._ensure_stroke_accumulator(mask_id)
        accumulator.reset()
        return True

    def updateStrokeImage(self, mask_id: uuid.UUID, image: QImage) -> MaskLayer | None:
        """Update the mask image without recording an undo command."""
        layer = self._get_layer(mask_id)
        if layer is None:
            logger.warning("Cannot update stroke for missing mask %s.", mask_id)
            return None
        existing_image = layer.mask_image
        if not existing_image.isNull() and not image.isNull():
            if existing_image.size() == image.size():
                before_np = qimage_to_numpy_grayscale8(existing_image)
                after_np = qimage_to_numpy_grayscale8(image)
                diff_mask = before_np != after_np
                if np.any(diff_mask):
                    ys, xs = np.nonzero(diff_mask)
                    min_x = int(xs.min())
                    max_x = int(xs.max())
                    min_y = int(ys.min())
                    max_y = int(ys.max())
                    diff_rect = QRect(
                        min_x,
                        min_y,
                        max_x - min_x + 1,
                        max_y - min_y + 1,
                    )
                    before_patch = existing_image.copy(diff_rect)
                    after_patch = image.copy(diff_rect)
                    self.recordStrokePatch(
                        mask_id, diff_rect, before_patch, after_patch
                    )
            else:
                logger.debug(
                    "Skipping patch capture for mask %s: stroke image size changed %sx%s -> %sx%s.",
                    mask_id,
                    existing_image.width(),
                    existing_image.height(),
                    image.width(),
                    image.height(),
                )
        self.mask_manager.set_mask_image(mask_id, image)
        self.bumpMaskGeneration(mask_id, reason="update_stroke_image")
        return layer

    def commitStroke(self, mask_id: uuid.UUID) -> bool:
        """Finalize a stroke using accumulated patches or full-image fallback."""
        layer = self._get_layer(mask_id)
        if layer is None:
            self._stroke_accumulators.pop(mask_id, None)
            logger.warning("Cannot commit stroke %s: missing mask.", mask_id)
            return False
        patches = self._drain_stroke_patches(mask_id)
        if patches:
            if not self._commit_mask_update(
                mask_id, patches=patches, preserve_cache=True
            ):
                return False
            return True
        final_image = layer.mask_image.copy()
        if not self._commit_mask_update(mask_id, image=final_image):
            return False
        return True

    def apply_mask_image(
        self,
        mask_id: uuid.UUID,
        image: QImage,
        *,
        before: QImage | None = None,
        preserve_cache: bool = False,
    ) -> bool:
        """Submit an undoable command using patch data when available."""
        patches = self._drain_stroke_patches(mask_id)
        if patches:
            return self._commit_mask_update(
                mask_id, patches=patches, preserve_cache=True
            )
        return self._commit_mask_update(
            mask_id,
            image=image,
            before=before,
            preserve_cache=preserve_cache,
        )

    def invalidateActiveMaskCache(self):
        """Invalidates the colorized pixmap cache for the currently active mask.

        This should be called by external tools (like the brush tool) that
        modify a mask's QImage directly.
        """
        if self._active_mask_id:
            active_mask_layer = self._get_layer(self._active_mask_id)
            self._invalidate_colorized_mask_cache(active_mask_layer)

    def updateMaskRegion(
        self,
        dirty_image_rect: QRect,
        active_mask_layer: "MaskLayer",
        *,
        sub_mask_image: QImage | None = None,
        colorized_image: QImage | None = None,
    ):
        """Refresh cached overlays for `dirty_image_rect` on the active layer.

        When `sub_mask_image` is supplied the caller has already copied the
        updated mask snippet, so we reuse it instead of issuing another copy.
        """
        if (
            not active_mask_layer
            or dirty_image_rect.isNull()
            or dirty_image_rect.isEmpty()
        ):
            return
        colorized_pixmap_cache = self._get_colorized_mask(active_mask_layer)
        if not colorized_pixmap_cache:
            self.mask_updated.emit(self._active_mask_id, QRect())
            return
        mask_uuid = self._resolve_mask_id(active_mask_layer)
        preview_stride: int | None = None
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
        if mask_uuid is not None:
            self._prefetched_images.pop(mask_uuid, None)
            self._prefetched_scaled_images.pop(mask_uuid, None)
        if colorized_image is None:
            region_image = sub_mask_image or active_mask_layer.mask_image.copy(
                dirty_image_rect
            )
            colorized_qimage = self._colorize_with_metrics(
                region_image,
                active_mask_layer.color,
                mask_id=mask_uuid,
                source="snippet_provisional" if preview_provisional else "snippet",
            )
        else:
            colorized_qimage = colorized_image
        snippet_pixmap = QPixmap.fromImage(colorized_qimage)
        cache_painter = QPainter(colorized_pixmap_cache)
        cache_painter.setCompositionMode(QPainter.CompositionMode_Source)
        if (
            preview_stride
            and preview_stride > 1
            and snippet_pixmap.size() != dirty_image_rect.size()
        ):
            destination_rect = QRect(
                dirty_image_rect.topLeft(),
                dirty_image_rect.size(),
            )
            cache_painter.drawPixmap(
                destination_rect,
                snippet_pixmap,
                QRect(QPoint(0, 0), snippet_pixmap.size()),
            )
        else:
            cache_painter.drawPixmap(dirty_image_rect.topLeft(), snippet_pixmap)
        cache_painter.end()
        layer_key = id(active_mask_layer)
        for cache_key in self._layer_cache_index.get(layer_key, ()):
            _, scale_key = cache_key
            if scale_key is None:
                continue
            scaled_pixmap = self._colorized_mask_cache.get(cache_key)
            if scaled_pixmap is None or scaled_pixmap.isNull():
                continue
            scaled_size = self._target_scaled_size(dirty_image_rect.size(), scale_key)
            if scaled_size.isEmpty():
                continue
            scaled_top_left = QPoint(
                int(round(dirty_image_rect.left() * scale_key)),
                int(round(dirty_image_rect.top() * scale_key)),
            )
            scaled_painter = QPainter(scaled_pixmap)
            scaled_painter.setRenderHint(
                QPainter.RenderHint.SmoothPixmapTransform, True
            )
            scaled_painter.setCompositionMode(QPainter.CompositionMode_Source)
            scaled_painter.drawPixmap(
                QRect(scaled_top_left, scaled_size),
                snippet_pixmap,
                QRect(QPoint(0, 0), snippet_pixmap.size()),
            )
            scaled_painter.end()
        tl = self._image_to_panel_point(dirty_image_rect.topLeft())
        br = self._image_to_panel_point(dirty_image_rect.bottomRight())
        if not tl or not br:
            self.mask_updated.emit(self._active_mask_id, QRect())
            return
        dirty_panel_rect_f = QRectF(tl, br).normalized().adjusted(-2, -2, 2, 2)
        self.mask_updated.emit(self._active_mask_id, dirty_panel_rect_f.toRect())

    def handle_mask_ready(
        self,
        mask_array_uint8: np.ndarray | None,
        bbox: np.ndarray,
        erase_mode: bool,
        image_id: uuid.UUID | None = None,
    ) -> MaskReadyUpdate | None:
        """Process a generated mask from SAM and describe the resulting change."""
        mask_id = self._active_mask_id
        if mask_id is None:
            return None
        mask_layer = self._get_layer(mask_id)
        if image_id is not None:
            mask_ids_for_image = self.mask_manager.get_mask_ids_for_image(image_id)
            if mask_id not in mask_ids_for_image:
                logger.warning(
                    "Ignoring generated mask update for %s; not associated with image %s.",
                    mask_id,
                    image_id,
                )
                return MaskReadyUpdate(
                    mask_id=mask_id,
                    dirty_rect=None,
                    mask_layer=mask_layer,
                    changed=False,
                )
        if mask_array_uint8 is None:
            self.invalidateActiveMaskCache()
            image_rect = QRect(
                QPoint(int(bbox[0]), int(bbox[1])),
                QPoint(int(bbox[2]), int(bbox[3])),
            )
            return MaskReadyUpdate(
                mask_id=mask_id,
                dirty_rect=image_rect,
                mask_layer=mask_layer,
                changed=False,
            )
        combine_result = self.mask_manager.combine_with_numpy_mask(
            mask_id, mask_array_uint8, erase_mode=erase_mode
        )
        if not combine_result.changed:
            return MaskReadyUpdate(
                mask_id=mask_id,
                dirty_rect=None,
                mask_layer=mask_layer,
                changed=False,
            )
        new_image = combine_result.image
        if new_image is None:
            logger.warning(
                "Mask combination for %s reported a change without an image; skipping.",
                mask_id,
            )
            return MaskReadyUpdate(
                mask_id=mask_id,
                dirty_rect=None,
                mask_layer=mask_layer,
                changed=False,
            )
        if not self.apply_mask_image(
            mask_id,
            new_image,
            preserve_cache=True,
        ):
            return MaskReadyUpdate(
                mask_id=mask_id,
                dirty_rect=None,
                mask_layer=mask_layer,
                changed=False,
            )
        mask_layer = self._get_layer(mask_id)
        image_rect = QRect(
            QPoint(int(bbox[0]), int(bbox[1])),
            QPoint(int(bbox[2]), int(bbox[3])),
        )
        return MaskReadyUpdate(
            mask_id=mask_id,
            dirty_rect=image_rect,
            mask_layer=mask_layer,
            changed=True,
        )

    def invalidate_layer_cache(self, mask_layer: "MaskLayer | None") -> None:
        """Invalidate cached colorized pixmaps for ``mask_layer`` if present."""
        self._invalidate_colorized_mask_cache(mask_layer)

    def drop_oldest_cached_mask(
        self, *, reason: str, exclude: set[uuid.UUID] | None = None
    ) -> int:
        """Evict the least-recently-used mask cache entry."""
        if not self._colorized_mask_cache:
            return 0
        excluded = exclude or set()
        for key in list(self._colorized_mask_cache.keys()):
            layer_key, _ = key
            mask_uuid = self._layer_ids.get(layer_key)
            if mask_uuid is not None and mask_uuid in excluded:
                continue
            return self._drop_cached_mask(key, reason=reason)
        return 0

    def _record_eviction(self, reason: str) -> None:
        """Track the most recent eviction reason and timestamp for metrics."""
        self._last_eviction_reason = reason
        self._last_eviction_timestamp = time.monotonic()

    def _estimate_pixmap_bytes(self, pixmap: QPixmap) -> int:
        """Approximate the memory footprint of `pixmap` in bytes."""
        if pixmap is None or pixmap.isNull():
            return 0
        size = pixmap.size()
        depth = pixmap.depth() or 32
        return size.width() * size.height() * (depth // 8)

    def _allow_cache_insert(
        self, size_bytes: int, key: tuple[int, float | None]
    ) -> bool:
        """Return True when ``size_bytes`` is within configured guardrails."""
        size = max(0, int(size_bytes))
        budget_limit = self.mask_cache_limit_bytes()

        def _warn(limit_value: int) -> None:
            """Log and remember cache admission rejections for this key."""
            if key in self._rejected_cache_keys:
                return
            logger.warning(
                "requested item exceeds budget; not cached | consumer=mask_overlays | "
                "size=%d | budget=%d",
                size,
                limit_value,
            )
            self._rejected_cache_keys.add(key)

        if budget_limit >= 0 and size > budget_limit:
            _warn(budget_limit)
            return False
        guard = self._cache_admission_guard
        if guard is not None and not guard(size):
            _warn(budget_limit)
            return False
        return True

    def _drop_cached_mask(self, key: tuple[int, float | None], *, reason: str) -> int:
        """Remove a cached pixmap entry and return the freed byte count."""
        self._colorized_mask_cache.pop(key, None)
        size = self._colorized_mask_bytes.pop(key, 0)
        layer_key, _ = key
        mask_uuid = self._layer_ids.get(layer_key)
        bucket = self._layer_cache_index.get(layer_key)
        if bucket is not None:
            bucket.discard(key)
            if not bucket:
                self._layer_cache_index.pop(layer_key, None)
                if mask_uuid is not None:
                    self._prefetched_scaled_images.pop(mask_uuid, None)
                self._layer_ids.pop(layer_key, None)
        if size:
            self._colorized_cache_total_bytes = max(
                0, self._colorized_cache_total_bytes - size
            )
            self._cache_evictions += 1
            self._cache_evicted_bytes += size
            self._record_eviction(reason)
        self._notify_cache_usage()
        return size

    def _record_cache_insert(
        self,
        key: tuple[int, float | None],
        pixmap: QPixmap,
        *,
        mask_id: uuid.UUID | None = None,
    ) -> None:
        """Track a colorized mask insert and update cache accounting."""
        size_bytes = self._estimate_pixmap_bytes(pixmap)
        if not self._allow_cache_insert(size_bytes, key):
            return
        previous = self._colorized_mask_bytes.get(key)
        if previous:
            self._colorized_cache_total_bytes = max(
                0, self._colorized_cache_total_bytes - previous
            )
        self._colorized_mask_cache[key] = pixmap
        self._colorized_mask_cache.move_to_end(key)
        self._colorized_mask_bytes[key] = size_bytes
        layer_key, _ = key
        bucket = self._layer_cache_index.setdefault(layer_key, set())
        bucket.add(key)
        if mask_id is not None:
            self._layer_ids[layer_key] = mask_id
        self._colorized_cache_total_bytes += size_bytes
        exclude_ids: set[uuid.UUID] = set()
        if mask_id is not None:
            exclude_ids.add(mask_id)
        if self._active_mask_id is not None:
            exclude_ids.add(self._active_mask_id)
        self._evict_until_within_budget(reason="capacity", exclude=exclude_ids)
        self._notify_cache_usage()

    def _evict_until_within_budget(
        self, *, reason: str, exclude: set[uuid.UUID] | None = None
    ) -> None:
        """Trim cached overlays until they fit within the configured budget."""
        limit = self.mask_cache_limit_bytes()
        if limit <= 0:
            return
        excluded = set(exclude or set())
        if self._active_mask_id is not None:
            excluded.add(self._active_mask_id)
        attempts = 0
        max_attempts = len(self._colorized_mask_cache)
        while (
            self._colorized_cache_total_bytes > limit
            and self._colorized_mask_cache
            and attempts < max_attempts
        ):
            freed = self.drop_oldest_cached_mask(reason=reason, exclude=excluded)
            if freed <= 0:
                break
            attempts += 1

    def _record_colorize_metrics(
        self,
        duration_ms: float,
        *,
        mask_id: uuid.UUID | None,
        source: str,
        mask_width: int,
        mask_height: int,
    ) -> None:
        """Update colorization timing aggregates and emit slow-path warnings."""
        self._colorize_last_ms = duration_ms
        self._colorize_last_source = source
        self._colorize_total_ms += duration_ms
        self._colorize_samples += 1
        self._colorize_avg_ms = self._colorize_total_ms / self._colorize_samples
        if self._colorize_max_ms is None:
            self._colorize_max_ms = duration_ms
        else:
            self._colorize_max_ms = max(self._colorize_max_ms, duration_ms)
        threshold = self._colorize_threshold_ms
        if duration_ms >= threshold:
            self._colorize_slow_count += 1
            self._notify_cache_usage()

    def _invalidate_colorized_mask_cache(
        self, mask_layer: MaskLayer | None, *, reason: str = "invalidate"
    ):
        """Invalidate cached pixmaps for ``mask_layer`` at all scales."""
        if mask_layer is None:
            return
        mask_id = self._resolve_mask_id(mask_layer)
        if mask_id is not None:
            self._forget_prefetched_image(mask_id)
        layer_key = id(mask_layer)
        for cache_key in list(self._layer_cache_index.get(layer_key, ())):
            self._drop_cached_mask(cache_key, reason=reason)

    def _warm_mask_cache(self, mask_id):
        """Pre-generates the colorized pixmap for a given mask ID to avoid

        lag on first use.
        """
        mask_layer = self._get_layer(mask_id)
        if mask_layer is not None:
            self._get_colorized_mask(mask_layer)

    def _get_colorized_mask(
        self, mask_layer: MaskLayer, *, scale: float | None = None
    ) -> QPixmap | None:
        """Retrieve a colorized pixmap, optionally scaled for caching."""
        scale_key = self._normalize_scale_key(scale)
        cache_key = self._cache_key(mask_layer, scale_key)
        pixmap = self._colorized_mask_cache.get(cache_key)
        if pixmap is not None:
            self._colorized_mask_cache.move_to_end(cache_key)
            self._cache_hits += 1
            return pixmap
        mask_id = self._resolve_mask_id(mask_layer)
        if scale_key is not None and mask_id is not None:
            stored_bucket = self._prefetched_scaled_images.get(mask_id)
            if stored_bucket is not None:
                stored_image = stored_bucket.get(scale_key)
                if stored_image is not None and not stored_image.isNull():
                    stored_bucket.move_to_end(scale_key)
                    pixmap = QPixmap.fromImage(stored_image)
                    self._cache_hits += 1
                    self._record_cache_insert(cache_key, pixmap, mask_id=mask_id)
                    return pixmap
        if scale_key is None:
            if mask_layer.mask_image.isNull():
                return None
            fallback_pixmap: QPixmap | None = None
            if mask_id is not None:
                stored_image = self._prefetched_images.get(mask_id)
                if stored_image is not None and not stored_image.isNull():
                    fallback_pixmap = QPixmap.fromImage(stored_image)
                    self._prefetched_images.move_to_end(mask_id)
            if fallback_pixmap is not None:
                self._cache_hits += 1
                self._record_cache_insert(cache_key, fallback_pixmap, mask_id=mask_id)
                return fallback_pixmap
            if (
                mask_id is not None
                and self._async_colorize_handler is not None
                and mask_layer.mask_image.width() * mask_layer.mask_image.height()
                > self._async_colorize_threshold_px
                and mask_id not in self._async_colorize_pending
            ):
                self._async_colorize_pending.add(mask_id)
                scheduled = False
                try:
                    scheduled = bool(self._async_colorize_handler(mask_id, mask_layer))
                except Exception:
                    logger.exception(
                        "Async colorize handler failed for mask %s", mask_id
                    )
                if scheduled:
                    return None
                self._async_colorize_pending.discard(mask_id)
            pixmap = self.colorize_mask(
                mask_layer.mask_image,
                mask_layer.color,
                mask_id=mask_id,
                source="cache_miss",
            )
        else:
            base_key = self._cache_key(mask_layer, None)
            base_pixmap = self._colorized_mask_cache.get(base_key)
            if base_pixmap is not None:
                self._colorized_mask_cache.move_to_end(base_key)
                target_size = self._target_scaled_size(base_pixmap.size(), scale_key)
                if target_size == base_pixmap.size():
                    pixmap = base_pixmap
                else:
                    pixmap = base_pixmap.scaled(
                        target_size,
                    )
            else:
                mask_image = mask_layer.mask_image
                if mask_image.isNull():
                    return None
                target_size = self._target_scaled_size(mask_image.size(), scale_key)
                if target_size == mask_image.size():
                    scaled_image = mask_image
                else:
                    scaled_image = mask_image.scaled(
                        target_size,
                    )
                if scaled_image.format() != QImage.Format.Format_Grayscale8:
                    scaled_image = scaled_image.convertToFormat(
                        QImage.Format.Format_Grayscale8
                    )
                pixmap = self.colorize_mask(
                    scaled_image,
                    mask_layer.color,
                    mask_id=mask_id,
                    source="scaled_cache_miss",
                )
        self._cache_misses += 1
        self._record_cache_insert(cache_key, pixmap, mask_id=mask_id)
        return pixmap

    def get_colorized_mask(
        self, mask_layer: "MaskLayer", *, scale: float | None = None
    ) -> QPixmap | None:
        """Return the cached pixmap for ``mask_layer`` at the requested scale."""
        if mask_layer is None:
            return None
        return self._get_colorized_mask(mask_layer, scale=scale)

    def _apply_history_delta(
        self, mask_layer: MaskLayer, change: MaskHistoryChange
    ) -> bool:
        """Apply cached undo/redo snippets directly to cached overlays."""
        if not change.snippets:
            return False
        base_key = self._cache_key(mask_layer, None)
        base_pixmap = self._colorized_mask_cache.get(base_key)
        if base_pixmap is None or base_pixmap.isNull():
            return False
        applied = False
        for snippet in change.snippets:
            rect = snippet.rect.normalized()
            if rect.isNull() or rect.isEmpty():
                continue
            self.updateMaskRegion(rect, mask_layer, sub_mask_image=snippet.image)
            applied = True
        return applied

    def _create_premultiplied_alpha_lut(self) -> np.ndarray:
        """Create a 256x256 LUT for premultiplied alpha blending."""
        alpha_values = np.arange(256, dtype=np.uint16)[:, None]
        color_values = np.arange(256, dtype=np.uint16)[None, :]
        lut = (alpha_values * color_values) // 255
        return lut.astype(np.uint8)

    def _compose_colorized_image(self, mask_image: QImage, color: QColor) -> QImage:
        """Return a colorized ARGB32 image built from ``mask_image`` and ``color``."""
        mask_np = qimage_to_numpy_grayscale8(mask_image)
        h, w = mask_np.shape
        colorized_np = np.zeros((h, w, 4), dtype=np.uint8)
        alpha_channel = mask_np
        colorized_np[..., 0] = self._premultiplied_alpha_lut[
            alpha_channel, color.blue()
        ]
        colorized_np[..., 1] = self._premultiplied_alpha_lut[
            alpha_channel, color.green()
        ]
        colorized_np[..., 2] = self._premultiplied_alpha_lut[alpha_channel, color.red()]
        colorized_np[..., 3] = alpha_channel
        if self._mask_config.mask_border_enabled:
            if cv2 is None:
                if not self._missing_cv2_warned:
                    logger.warning(
                        "Mask border rendering requested but OpenCV is unavailable; skipping border drawing."
                    )
                    self._missing_cv2_warned = True
            else:
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
                dilated_mask = cv2.dilate(mask_np, kernel, iterations=1)
                border_mask = cv2.subtract(dilated_mask, mask_np)
                border_color = color.darker(120)
                border_pixels = np.zeros_like(colorized_np)
                border_alpha = border_mask
                border_pixels[..., 0] = self._premultiplied_alpha_lut[
                    border_alpha, border_color.blue()
                ]
                border_pixels[..., 1] = self._premultiplied_alpha_lut[
                    border_alpha, border_color.green()
                ]
                border_pixels[..., 2] = self._premultiplied_alpha_lut[
                    border_alpha, border_color.red()
                ]
                border_pixels[..., 3] = border_alpha
                cv2.add(colorized_np, border_pixels, dst=colorized_np)
        return numpy_to_qimage_argb32(colorized_np)

    def _colorize_with_metrics(
        self,
        mask_image: QImage,
        color: QColor,
        *,
        mask_id: uuid.UUID | None,
        source: str | None,
    ) -> QImage:
        """Return a colorized image and record timing diagnostics."""
        normalized_source = source or "cache_miss"
        start = time.perf_counter()
        colorized_image = self._compose_colorized_image(mask_image, color)
        duration_ms = (time.perf_counter() - start) * 1000.0
        self._record_colorize_metrics(
            duration_ms,
            mask_id=mask_id,
            source=normalized_source,
            mask_width=mask_image.width(),
            mask_height=mask_image.height(),
        )
        if mask_id is not None and (
            normalized_source == "cache_miss"
            or normalized_source.startswith("prefetch")
        ):
            self._store_prefetched_image(mask_id, colorized_image)
        return colorized_image

    def colorize_mask(
        self,
        mask_image: QImage,
        color: QColor,
        *,
        mask_id: uuid.UUID | None = None,
        source: str = "cache_miss",
    ) -> QPixmap:
        """Create a colored pixmap from an 8-bit grayscale mask and record timing."""
        colorized_image = self._colorize_with_metrics(
            mask_image,
            color,
            mask_id=mask_id,
            source=source,
        )
        return QPixmap.fromImage(colorized_image)
