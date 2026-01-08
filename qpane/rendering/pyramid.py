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

"""Generate and cache image pyramids on executor-backed workers while keeping UI work responsive."""

import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Sequence

from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtGui import QImage

from ..concurrency import (
    BaseWorker,
    RetryController,
    TaskExecutorProtocol,
    TaskHandle,
    TaskRejected,
    makeQtRetryController,
    qt_retry_dispatcher,
)
from ..core import CacheSettings, Config
from ..core.threading import assert_qt_main_thread
from .cache_utils import CacheEvictionCoordinator, ExecutorOwnerMixin
from .cache_metrics import CacheManagerMetrics, CacheMetricsMixin

logger = logging.getLogger(__name__)

_PYRAMID_EVICTION_BATCH = 3
_PYRAMID_RETRY_BASE_MS = 75
_PYRAMID_RETRY_MAX_MS = 1500


class PyramidStatus(str, Enum):
    """Enumerates lifecycle states for pyramid generation."""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PyramidWorkerSignals(QObject):
    """Defines signals available from a running worker thread."""

    finished = Signal(uuid.UUID)  # Emits the image id when generation is done
    error = Signal(uuid.UUID, str)  # Emits image id and error message on failure


class PyramidGeneratorWorker(QRunnable, BaseWorker):
    """Background worker that builds a single image pyramid for a source image."""

    def __init__(self, pyramid: "ImagePyramid", config: Config):
        """Store the target ``pyramid`` and config snapshot for generation."""
        QRunnable.__init__(self)
        BaseWorker.__init__(self)
        self.pyramid = pyramid
        self._config = config
        self.signals = PyramidWorkerSignals()

    def run(self):
        """Generate pyramid levels and report completion or failure."""
        try:
            if self.is_cancelled:
                self._handle_cancellation()
                return
            self.pyramid.status = PyramidStatus.GENERATING
            self.logger.info(
                "Generating pyramid for %s",
                self.pyramid.source_path or self.pyramid.image_id,
            )
            source_qimage = self.pyramid.full_resolution_image
            # Ensure image is in a 4-channel format to preserve transparency.
            if source_qimage.format() != QImage.Format_ARGB32_Premultiplied:
                source_qimage = source_qimage.convertToFormat(
                    QImage.Format_ARGB32_Premultiplied
                )
            width, height = source_qimage.width(), source_qimage.height()
            current_scale = 1.0
            loop_width, loop_height = width, height
            while max(loop_width, loop_height) > self._config.min_view_size_px:
                if self.is_cancelled:
                    self._handle_cancellation()
                    return
                current_scale /= 2.0
                new_width = int(width * current_scale)
                new_height = int(height * current_scale)
                if new_width <= 0 or new_height <= 0:
                    break
                loop_width, loop_height = new_width, new_height
                # Use Qt's high-quality smooth scaler.
                qt_image = source_qimage.scaled(
                    new_width,
                    new_height,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                self.pyramid.levels[current_scale] = qt_image.copy()
            # Calculate the total size of the pyramid
            total_size = self.pyramid.full_resolution_image.sizeInBytes()
            for level_image in self.pyramid.levels.values():
                total_size += level_image.sizeInBytes()
            self.pyramid.size_bytes = total_size
            self.pyramid.status = PyramidStatus.COMPLETE
            self.emit_finished(True, payload=self.pyramid.image_id)
        except Exception as exc:
            self.pyramid.status = PyramidStatus.FAILED
            self.emit_finished(
                False,
                payload=(self.pyramid.image_id, str(exc)),
                error=exc,
            )

    def cancel(self):
        """Request cancellation for the running worker."""
        BaseWorker.cancel(self)

    def _handle_cancellation(self) -> None:
        """Mark the pyramid as cancelled and emit completion payload once."""
        if self.pyramid.status == PyramidStatus.CANCELLED:
            return
        self.pyramid.status = PyramidStatus.CANCELLED
        self.logger.info(
            "Cancelled pyramid generation for %s",
            self.pyramid.source_path or self.pyramid.image_id,
        )
        self.emit_finished(
            False,
            payload=(self.pyramid.image_id, "cancelled"),
        )


@dataclass
class ImagePyramid:
    """Container for the original image plus its downscaled pyramid levels.

    PyramidManager mutates status and levels on the main thread while workers populate levels in the background.
    """

    image_id: uuid.UUID
    source_path: Path | None
    full_resolution_image: QImage
    levels: Dict[float, QImage] = field(default_factory=dict)
    status: PyramidStatus = PyramidStatus.PENDING
    size_bytes: int = 0


class PyramidManager(QObject, CacheMetricsMixin, ExecutorOwnerMixin):
    """Manage pyramid creation, caching, and retrieval for tiled rendering.

    Generates pyramids on the shared executor, enforces byte budgets with LRU eviction, and keeps mutations on the Qt main thread. Retry scheduling relies on the shared controller's main-thread dispatch. Callers treat returned ImagePyramids as read-only snapshots.
    """

    pyramidReady = Signal(uuid.UUID)
    pyramidThrottled = Signal(uuid.UUID, int)
    usageChanged = Signal(object)
    cacheLimitChanged = Signal(object)

    def __init__(
        self,
        config: Config,
        parent=None,
        *,
        executor: TaskExecutorProtocol,
        owns_executor: bool = False,
    ):
        """Initialise caches, workers, and retry controllers for pyramid generation."""
        super().__init__(parent)
        CacheMetricsMixin.__init__(self)
        ExecutorOwnerMixin.__init__(
            self,
            executor_logger=logger,
            owner_name="PyramidManager",
        )
        self._config = config
        self._executor: TaskExecutorProtocol | None = executor
        self._owns_executor = bool(owns_executor)
        self._managed_mode = False
        self._cache_limit_bytes: int = 0
        self._pyramids: Dict[uuid.UUID, ImagePyramid] = {}
        self._cache: OrderedDict[uuid.UUID, ImagePyramid] = OrderedDict()
        self._cache_admission_guard = None
        self._rejected_cache_keys: set[uuid.UUID] = set()
        self._cache_size_bytes: int = 0
        self.cache_limit_bytes = self._resolve_cache_limit_bytes(config)
        self._active_workers: Dict[uuid.UUID, PyramidGeneratorWorker] = {}
        self._active_handles: Dict[uuid.UUID, TaskHandle] = {}
        dispatcher = qt_retry_dispatcher(self._executor, category="pyramid_main")
        self._pyramid_retry: RetryController[uuid.UUID, ImagePyramid] = (
            makeQtRetryController(
                "pyramid",
                _PYRAMID_RETRY_BASE_MS,
                _PYRAMID_RETRY_MAX_MS,
                parent=self,
                dispatcher=dispatcher,
            )
        )
        self._eviction = CacheEvictionCoordinator(logger=logger, name="pyramid cache")

    def apply_config(self, config: Config) -> None:
        """Refresh derived values after a configuration update."""
        self._config = config
        self.cache_limit_bytes = self._resolve_cache_limit_bytes(config)
        if not self._managed_mode:
            self._enforce_cache_size()

    @property
    def cache_usage_bytes(self) -> int:
        """Return the current pyramid cache usage in bytes."""
        return self._cache_size_bytes

    @property
    def cache_limit_bytes(self) -> int:
        """Return the configured pyramid cache budget in bytes."""
        return self._cache_limit_bytes

    @cache_limit_bytes.setter
    def cache_limit_bytes(self, value: int) -> None:
        """Set the pyramid cache budget and emit change notifications."""
        new_value = max(0, int(value))
        previous = getattr(self, "_cache_limit_bytes", 0)
        self._cache_limit_bytes = new_value
        if new_value != previous:
            self.cacheLimitChanged.emit(new_value)
        if not self._managed_mode and self._cache_size_bytes > self._cache_limit_bytes:
            self._enforce_cache_size()

    def set_managed_mode(self, enabled: bool) -> None:
        """Enable or disable managed mode.

        In managed mode, the manager disables automatic self-eviction and relaxes
        admission checks, relying on an external coordinator to drive trims.
        """
        self._managed_mode = bool(enabled)

    def set_admission_guard(self, guard: Callable[[int], bool] | None) -> None:
        """Install an optional hard-cap guard consulted before caching pyramids."""
        self._cache_admission_guard = guard

    def mark_external_trim(self, reason: str) -> None:
        """Tag the next eviction batch with an external ``reason``."""
        self._next_eviction_reason = reason

    def pyramid_for_image_id(self, image_id: uuid.UUID) -> "ImagePyramid | None":
        """Return the ImagePyramid for a given image ID, or None if not present."""
        self._assert_main_thread()
        return self._pyramids.get(image_id)

    def iter_cached_ids(self):
        """Yield cached image IDs in LRU order (oldest first)."""
        self._assert_main_thread()
        return iter(self._cache.keys())

    def pending_ids(self):
        """Return image IDs that still have generation in progress."""
        self._assert_main_thread()
        return set(self._active_workers.keys())

    def prefetch_pyramid(
        self,
        image_id: uuid.UUID,
        image: QImage,
        source_path: Path | None,
        *,
        reason: str = "prefetch",
    ) -> bool:
        """Request background pyramid generation for `image_id` if needed."""
        self._assert_main_thread()
        if not isinstance(image_id, uuid.UUID):
            raise ValueError("image_id is required")
        if image.isNull():
            return False
        if self._prefetch_pending(image_id):
            logger.debug("Pyramid prefetch already pending for %s", image_id)
            return False
        pyramid = self._pyramids.get(image_id)
        if pyramid is not None and pyramid.status == PyramidStatus.COMPLETE:
            self._prefetch_skip_hit()
            return False
        if image_id in self._active_handles:
            logger.debug("Pyramid generation already active for %s", image_id)
            return False
        self._prefetch_begin(image_id, record_start=False)
        try:
            self.generate_pyramid_for_image(image_id, image, source_path)
        except Exception:
            self._prefetch_finish(image_id, success=False)
            logger.exception(
                "Pyramid prefetch submission failed (image_id=%s)", image_id
            )
            raise
        logger.info("Scheduled pyramid prefetch for %s (reason=%s)", image_id, reason)
        return True

    def cancel_prefetch(
        self,
        image_ids: Sequence[uuid.UUID],
        *,
        reason: str = "navigation",
    ) -> list[uuid.UUID]:
        """Cancel outstanding pyramid prefetch requests."""
        if not image_ids:
            return []
        self._assert_main_thread()
        cancelled: list[uuid.UUID] = []
        executor = self._executor
        if executor is None:
            raise RuntimeError("PyramidManager executor is missing")
        for image_id in image_ids:
            if not self._prefetch_pending(image_id):
                continue
            handle = self._active_handles.get(image_id)
            worker = self._active_workers.get(image_id)
            cancelled_flag = False
            if executor is not None and handle is not None:
                try:
                    cancelled_flag = executor.cancel(handle)
                except Exception:
                    cancelled_flag = False
            if not cancelled_flag and worker is not None:
                try:
                    worker.cancel()
                except Exception:  # pragma: no cover - defensive guard
                    logger.exception(
                        "Pyramid worker cancel threw (image_id=%s, reason=%s)",
                        image_id,
                        reason,
                    )
            self._detach_worker(image_id)
            self._cancel_pyramid_retry(image_id)
            self._prefetch_finish(image_id, success=False)
            cancelled.append(image_id)
            logger.info(
                "Cancelled pyramid prefetch %s (reason=%s, executor_cancelled=%s)",
                image_id,
                reason,
                cancelled_flag,
            )
        return cancelled

    def generate_pyramid_for_image(
        self,
        image_id: uuid.UUID,
        image: QImage,
        source_path: Path | None,
    ):
        """Start a worker to generate a pyramid for ``image_id``."""
        self._assert_main_thread()
        if not isinstance(image_id, uuid.UUID):
            raise ValueError("image_id is required")
        existing = self._pyramids.get(image_id)
        if existing is None:
            pyramid = ImagePyramid(
                image_id=image_id,
                source_path=source_path,
                full_resolution_image=image,
            )
            self._pyramids[image_id] = pyramid
        else:
            pyramid = existing
            pyramid.full_resolution_image = image
            pyramid.source_path = source_path

        def _submit(pyr: ImagePyramid, attempt: int):
            """Submit ``pyr`` to the executor unless it already has an active worker."""
            # Avoid duplicate submission when already active
            handle = self._active_handles.get(pyr.image_id)
            if handle is not None:
                return handle
            worker = PyramidGeneratorWorker(pyr, self._config)
            BaseWorker.connect_queued(
                worker.signals.finished,
                self._on_pyramid_generated,
            )
            BaseWorker.connect_queued(
                worker.signals.error,
                self._on_pyramid_error,
            )
            executor = self._executor
            if executor is None:
                raise RuntimeError("PyramidManager executor is missing")
            handle = executor.submit(worker, category="pyramid")
            self._active_workers[pyr.image_id] = worker
            self._active_handles[pyr.image_id] = handle
            self._prefetch_mark_started(pyr.image_id)
            logger.info("Queued pyramid generation for %s", pyr.image_id)
            return handle

        def _coalesce(old: ImagePyramid, new: ImagePyramid) -> ImagePyramid:
            """Update ``old`` pyramid with the latest full-resolution image."""
            old.full_resolution_image = new.full_resolution_image
            return old

        def _throttle(image_key: uuid.UUID, next_attempt: int, rej: TaskRejected):
            """Record throttling metadata and emit the public signal."""
            logger.warning(
                "Pyramid generation for %s throttled: pending %s limit=%s "
                "(total=%s, category=%s)",
                image_key,
                rej.limit_type,
                rej.limit_value,
                rej.pending_total,
                rej.pending_category,
            )
            self.pyramidThrottled.emit(image_key, next_attempt)

        self._queue_pyramid_retry(
            image_id,
            pyramid,
            submit=_submit,
            throttle=_throttle,
            coalesce=_coalesce,
        )

    def _on_pyramid_generated(self, image_id: uuid.UUID):
        """Slot for when a pyramid worker successfully finishes."""
        self._assert_main_thread()
        self._detach_worker(image_id)
        self._pyramid_retry.onSuccess(image_id)
        self._prefetch_finish(image_id, success=True)
        if image_id in self._pyramids:
            pyramid = self._pyramids[image_id]
            if pyramid.status == PyramidStatus.COMPLETE:
                if self._allow_cache_insert(pyramid.size_bytes, image_id):
                    self._cache[image_id] = pyramid
                    self._set_cache_usage_bytes(
                        self._cache_size_bytes + pyramid.size_bytes
                    )
                    if not self._managed_mode:
                        self._enforce_cache_size()
                    logger.info("Pyramid generated for %s", image_id)
                self.pyramidReady.emit(image_id)
            elif pyramid.status == PyramidStatus.CANCELLED:
                logger.info(
                    "Skipped cache promotion for cancelled pyramid %s",
                    image_id,
                )
            else:
                logger.warning(
                    "Unexpected pyramid status %s for %s during completion",
                    pyramid.status,
                    image_id,
                )

    def _on_pyramid_error(self, image_id: uuid.UUID, error_message: str):
        """Slot for when a pyramid worker encounters an error."""
        self._assert_main_thread()
        self._detach_worker(image_id)
        self._pyramid_retry.onFailure(image_id)
        self._prefetch_finish(image_id, success=False)
        pyramid = self._pyramids.get(image_id)
        if pyramid and pyramid.status != PyramidStatus.CANCELLED:
            pyramid.status = PyramidStatus.FAILED
        if error_message == "cancelled":
            logger.info("Pyramid generation cancelled for %s", image_id)
            return
        logger.error(
            "Pyramid generation failed for %s: %s",
            image_id,
            error_message,
        )

    def get_best_fit_image(
        self, image_id: uuid.UUID, target_width: float
    ) -> QImage | None:
        """Return the pyramid level closest to the target width without upscaling.

        Falls back to the full-resolution image when no pyramid exists, generation failed or was cancelled, the target width is invalid, or the pyramid is incomplete or would upscale.
        """
        self._assert_main_thread()
        if image_id is None:
            return None
        pyramid = self.pyramid_for_image_id(image_id)
        if pyramid is None:
            self._cache_misses += 1
            return None
        if pyramid.status in (PyramidStatus.CANCELLED, PyramidStatus.FAILED):
            self._cache_misses += 1
            return pyramid.full_resolution_image
        original_image = pyramid.full_resolution_image
        original_width = original_image.width()
        if original_width <= 0 or target_width is None or target_width <= 0:
            self._cache_misses += 1
            return original_image
        if (
            pyramid.status != PyramidStatus.COMPLETE
            or not pyramid.levels
            or target_width >= original_width
        ):
            self._cache_misses += 1
            return original_image
        target_scale = target_width / original_width
        # Pick the smallest scale that still meets ``target_scale``
        available_scales = [
            scale for scale in pyramid.levels.keys() if scale >= target_scale
        ]
        best_scale = min(available_scales, default=None)
        if best_scale is not None:
            self._cache_hits += 1
            return pyramid.levels[best_scale]
        self._cache_misses += 1
        return original_image

    def remove_pyramid(self, image_id: uuid.UUID) -> None:
        """Purge the pyramid, cache state, and worker bookkeeping for ``image_id``."""
        self._assert_main_thread()
        if not isinstance(image_id, uuid.UUID):
            raise ValueError("image_id is required")
        was_cached = image_id in self._cache
        had_worker = image_id in self._active_workers
        self._drop_cache_entry(image_id)
        self._cancel_pyramid_retry(image_id)
        self._pyramids.pop(image_id, None)
        self._active_handles.pop(image_id, None)
        self._active_workers.pop(image_id, None)
        self._prefetch_drop(image_id)
        logger.info(
            "Removed pyramid state for %s (cached=%s, worker=%s)",
            image_id,
            was_cached,
            had_worker,
        )

    def clear(self) -> None:
        """Cancel workers, reset counters, and empty every cache entry."""
        self.shutdown(wait=False)
        self._assert_main_thread()
        pyramid_count = len(self._pyramids)
        self._pyramids.clear()
        had_entries = bool(self._cache)
        self._cache.clear()
        self._rejected_cache_keys.clear()
        self._prefetch_drop_all()
        self._reset_cache_metrics()
        self._set_cache_usage_bytes(0)
        assert self._cache_size_bytes == 0, "Cache size not zero after clear"
        if had_entries:
            self._record_eviction_metadata("clear")
        logger.info(
            "Cleared pyramid cache (pyramids=%d, cache_entries=%s)",
            pyramid_count,
            had_entries,
        )

    def snapshot_metrics(self) -> CacheManagerMetrics:
        """Return cache metrics for diagnostics and testing."""
        return self._snapshot_cache_metrics(
            cache_bytes=self._cache_size_bytes,
            cache_limit=self.cache_limit_bytes,
            active_jobs=len(self._active_handles),
            pending_retries=len(self.pending_retry_paths()),
        )

    def retry_snapshot(self):
        """Expose the retry controller snapshot for diagnostics consumers."""
        return self._pyramid_retry.snapshot()

    def pending_retry_paths(self) -> list[uuid.UUID]:
        """Return image IDs currently queued for retry."""
        return list(self._pyramid_retry.pendingKeys())

    def _set_cache_usage_bytes(self, value: int) -> None:
        """Clamp and publish cache usage changes."""
        clamped = max(0, int(value))
        if clamped == self._cache_size_bytes:
            return
        self._cache_size_bytes = clamped
        self.usageChanged.emit(clamped)

    def _drop_cache_entry(self, image_id: uuid.UUID) -> None:
        """Remove a pyramid from the LRU cache and update size accounting."""
        self._assert_main_thread()
        if image_id in self._cache:
            self._set_cache_usage_bytes(
                self._cache_size_bytes - self._cache[image_id].size_bytes
            )
            del self._cache[image_id]
            assert self._cache_size_bytes >= 0, "Cache size went negative"

    def _allow_cache_insert(self, size_bytes: int, key: uuid.UUID) -> bool:
        """Return True when ``size_bytes`` is within pyramid guardrails."""
        size = max(0, int(size_bytes))
        budget_limit = max(0, int(self.cache_limit_bytes))

        def _warn(limit_value: int) -> None:
            """Log a cache admission rejection once per key."""
            if key in self._rejected_cache_keys:
                return
            logger.warning(
                "requested item exceeds budget; not cached | consumer=pyramids | "
                "size=%d | budget=%d",
                size,
                limit_value,
            )
            self._rejected_cache_keys.add(key)

        if not self._managed_mode and size > budget_limit:
            _warn(budget_limit)
            return False
        guard = self._cache_admission_guard
        if guard is not None and not guard(size):
            _warn(budget_limit)
            return False
        return True

    def _queue_pyramid_retry(
        self,
        image_id: uuid.UUID,
        pyramid: "ImagePyramid",
        *,
        submit: Callable[["ImagePyramid", int], TaskHandle],
        throttle: Callable[[uuid.UUID, int, TaskRejected], None],
        coalesce: (
            Callable[["ImagePyramid", "ImagePyramid"], "ImagePyramid"] | None
        ) = None,
    ) -> None:
        """Queue pyramid generation work through the retry controller."""
        self._pyramid_retry.queueOrCoalesce(
            image_id,
            pyramid,
            submit=submit,
            throttle=throttle,
            coalesce=coalesce,
        )

    def _cancel_pyramid_retry(self, image_id: uuid.UUID) -> None:
        """Cancel any pending retry for ``image_id``."""
        self._pyramid_retry.cancel(image_id)

    def _cancel_all_pyramid_retries(self) -> None:
        """Cancel every queued pyramid retry."""
        self._pyramid_retry.cancelAll()

    def _enforce_cache_size(self) -> None:
        """Request async eviction when the cache exceeds its budget."""
        if self._cache_size_bytes <= self.cache_limit_bytes or not self._cache:
            return
        if self._eviction.pending:
            return
        self._ensure_next_eviction_reason("limit")
        executor = self._executor
        if executor is None:
            raise RuntimeError("PyramidManager executor is missing")
        self._eviction.schedule(
            executor=executor,
            callback=self._run_eviction_batch,
            category="maintenance",
        )

    def _run_eviction_batch(self) -> None:
        """Evict a bounded batch of pyramids on the main thread."""
        reason = self._consume_next_eviction_reason("limit")
        evicted = 0
        evicted_paths = []
        bytes_freed = 0
        new_usage = self._cache_size_bytes
        while (
            new_usage > self.cache_limit_bytes
            and self._cache
            and evicted < _PYRAMID_EVICTION_BATCH
        ):
            lru_id = next(iter(self._cache))
            removed_bytes = 0
            pyramid = self._cache.get(lru_id)
            if pyramid is not None:
                removed_bytes = pyramid.size_bytes
            self._drop_cache_entry(lru_id)
            if lru_id in self._pyramids:
                del self._pyramids[lru_id]
            if removed_bytes:
                bytes_freed += removed_bytes
                self._evicted_bytes += removed_bytes
                new_usage = max(0, new_usage - removed_bytes)
            evicted_paths.append(str(lru_id))
            self._evictions_total += 1
            self._record_eviction_metadata(reason)
            evicted += 1
        self._set_cache_usage_bytes(new_usage)
        if evicted_paths:
            logger.info(
                "Eviction batch: evicted=%d, paths=%s, bytes_freed=%d, "
                "total=%d, limit=%d",
                evicted,
                evicted_paths,
                bytes_freed,
                self._cache_size_bytes,
                self.cache_limit_bytes,
            )
        if (
            not self._managed_mode
            and self._cache_size_bytes > self.cache_limit_bytes
            and self._cache
        ):
            self._enforce_cache_size()

    def _cancel_eviction_task(self) -> None:
        """Cancel a pending eviction callback when one exists."""
        self._eviction.cancel(self._executor)

    def shutdown(self, *, wait: bool = True) -> None:
        """Cancel workers and pending eviction callbacks."""
        self._assert_main_thread()
        self._cancel_eviction_task()
        self._cancel_all_pyramid_retries()
        if not self._active_handles:
            self._maybe_wait_for_executor(wait)
            return
        for image_id, handle in list(self._active_handles.items()):
            executor = self._executor
            if executor is None:
                raise RuntimeError("PyramidManager executor is missing")
            cancelled = executor.cancel(handle)
            if not cancelled:
                worker = self._active_workers.get(image_id)
                if worker is not None:
                    worker.cancel()
            logger.info(
                "Requested cancellation for pyramid %s (cancelled=%s)",
                image_id,
                cancelled,
            )
        self._active_handles.clear()
        self._active_workers.clear()
        self._prefetch_drop_all()
        self._maybe_wait_for_executor(wait)

    def _detach_worker(self, image_id: uuid.UUID) -> None:
        """Remove bookkeeping for a finished or failed worker."""
        self._active_workers.pop(image_id, None)
        self._active_handles.pop(image_id, None)

    def _assert_main_thread(self):
        """Raise AssertionError if not running on the Qt main thread."""
        assert_qt_main_thread(self)

    @staticmethod
    def _resolve_cache_limit_bytes(config: Config) -> int:
        """Return the pyramid cache budget derived from cache settings."""
        cache_settings = getattr(config, "cache", None)
        if not isinstance(cache_settings, CacheSettings):
            cache_settings = CacheSettings()
        budgets = cache_settings.resolved_consumer_budgets_bytes()
        return int(budgets.get("pyramids", 0))
