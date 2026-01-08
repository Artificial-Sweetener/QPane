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

"""Manage MobileSAM predictor loading, caching, retries, and mask generation."""

import logging
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QThread, QTimer, Signal
from PySide6.QtGui import QImage

from ..concurrency import (
    BaseWorker,
    RetryContext,
    TaskExecutorProtocol,
    TaskHandle,
    TaskRejected,
    makeQtRetryController,
    qt_retry_dispatcher,
)
from ..concurrency import RetryEntriesView
from . import service

if TYPE_CHECKING:
    from mobile_sam import SamPredictor
logger = logging.getLogger(__name__)

_SAM_RETRY_BASE_DELAY_MS = 150
_SAM_RETRY_MAX_DELAY_MS = 2500
_DEFAULT_PREDICTOR_ESTIMATE_BYTES = 128 * 1024 * 1024


@dataclass
class _PredictorRetryEntry:
    """Bookkeep retry attempts for throttled predictor submissions."""

    attempts: int
    timer: QTimer
    image: QImage
    image_id: uuid.UUID
    source_path: Path | None


@dataclass(frozen=True, slots=True)
class SamPredictorMetrics:
    """Metrics snapshot describing predictor cache and job activity."""

    cache_bytes: int
    cache_count: int
    active_jobs: int
    pending_retries: int
    hits: int
    misses: int
    cache_limit: int = 0
    evictions: int = 0
    evicted_bytes: int = 0
    last_eviction_reason: str | None = None
    last_eviction_timestamp: float | None = None
    prefetch_requested: int = 0
    prefetch_completed: int = 0
    prefetch_failed: int = 0
    last_prefetch_ms: float | None = None


class SamWorkerSignals(QObject):
    """Signals emitted by SamWorker for completion and failures."""

    finished = Signal(object, uuid.UUID)
    error = Signal(uuid.UUID, str)


class SamWorker(QRunnable, BaseWorker):
    """Load SAM predictors in background threads to keep the UI responsive."""

    def __init__(
        self,
        image: QImage,
        image_id: uuid.UUID,
        path: Path | None,
        checkpoint_path: Path,
        device: str = "cpu",
    ):
        """Capture predictor inputs, checkpoint path, and device target."""
        super().__init__()
        BaseWorker.__init__(self)
        self.image = image
        self.image_id = image_id
        self.path = path
        self._device = device
        self._checkpoint_path = checkpoint_path
        self.signals = SamWorkerSignals()

    def run(self):
        """Load MobileSAM, prepare the predictor image payload, and emit results."""
        try:
            if self.is_cancelled:
                self._handle_cancelled()
                return
            predictor = service.load_predictor(
                self._checkpoint_path, device=self._device
            )
            if self.is_cancelled:
                self._handle_cancelled()
                return
            if not self.image.isNull():
                image_rgb = self._prepare_image_rgb(self.image)
                if self.is_cancelled:
                    self._handle_cancelled()
                    return
                predictor.set_image(image_rgb)
            if self.is_cancelled:
                self._handle_cancelled()
                return
            self.emit_finished(True, payload=(predictor, self.image_id))
        except service.SamDependencyError as exc:
            self.emit_finished(False, payload=(self.image_id, str(exc)), error=exc)
        except Exception as exc:  # pragma: no cover - defensive guard
            self.emit_finished(False, payload=(self.image_id, str(exc)), error=exc)

    def _handle_cancelled(self) -> None:
        """Emit a cancellation result when work stops early."""
        self.logger.info(
            "Cancelled SAM predictor load for %s", self.path or self.image_id
        )
        self.emit_finished(False, payload=(self.image_id, "cancelled"))

    @staticmethod
    def _prepare_image_rgb(image: QImage) -> np.ndarray:
        """Convert a QImage to a contiguous RGB array expected by MobileSAM."""
        if image.format() != QImage.Format_RGBA8888:
            working = image.convertToFormat(QImage.Format_RGBA8888)
        else:
            working = image
        height = working.height()
        width = working.width()
        bytes_per_line = working.bytesPerLine()
        buffer = working.bits()
        raw = np.frombuffer(
            buffer, dtype=np.uint8, count=height * bytes_per_line
        ).reshape((height, bytes_per_line))
        pixel_data = raw[:, : width * 4]
        return pixel_data.reshape((height, width, 4))[:, :, :3].copy()


class SamManager(QObject):
    """Manage SAM predictor caching, retries, and mask generation for a device.

    Caches predictors per image ID, schedules background preparation, and
    emits signals when predictors are ready, throttled, trimmed, or fail to load.
    """

    predictorReady = Signal(object, uuid.UUID)
    predictorLoadFailed = Signal(uuid.UUID, str)
    predictorThrottled = Signal(uuid.UUID, int)
    predictorCacheCleared = Signal()
    predictorRemoved = Signal(uuid.UUID)
    maskReady = Signal(object, np.ndarray, bool)

    def __init__(
        self,
        parent=None,
        *,
        device: str = "cpu",
        executor: TaskExecutorProtocol,
        cache_limit: int | None = None,
        checkpoint_path: Path,
    ):
        """Initialise SAM caches, retry state, and executor handles.

        Args:
            parent: Optional QObject parent for Qt ownership.
            device: Compute target passed through to predictor and model loading.
            executor: Shared executor to reuse for predictor workers.
            checkpoint_path: Resolved filesystem path to the SAM checkpoint file.
        """
        super().__init__(parent)
        self._device = device
        self._checkpoint_path = checkpoint_path
        self._executor: TaskExecutorProtocol | None = executor
        self._sam_predictors: dict[uuid.UUID, "SamPredictor"] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._predictor_sizes: Dict[uuid.UUID, int] = {}
        self._predictor_paths: Dict[uuid.UUID, Path | None] = {}
        self._pending_estimates: Dict[uuid.UUID, int] = {}
        self._inflight: dict[uuid.UUID, tuple[SamWorker, TaskHandle]] = {}
        self._predictor_retry_entries: dict[
            tuple[uuid.UUID, str], _PredictorRetryEntry
        ] = {}
        self._retry_controller = RetryEntriesView(
            "sam", lambda: self._predictor_retry_entries
        )
        self._cache_limit = self._sanitize_cache_limit(cache_limit)
        if self._cache_limit is None:
            self._cache_limit = 1
        self._retry = makeQtRetryController(
            "sam",
            150,
            2500,
            parent=self,
            contextProvider=lambda key, img: RetryContext(
                "sam",
                self._device,
                key,
                (
                    getattr(img, "sizeInBytes", lambda: None)()
                    if hasattr(img, "sizeInBytes")
                    else None
                ),
            ),
            dispatcher=qt_retry_dispatcher(self._executor, category="sam_main"),
        )

    def retrySnapshot(self):
        """Expose retry metrics for diagnostics without leaking controllers."""
        return self._retry_controller.snapshot()

    def checkpointPath(self) -> Path:
        """Return the resolved SAM checkpoint path used by this manager."""
        return self._checkpoint_path

    def checkpointReady(self) -> bool:
        """Return True when the SAM checkpoint is available on disk."""
        return self._checkpoint_path.exists()

    def getCachedPredictorCount(self) -> int:
        """Return how many predictors are cached for reuse."""
        return len(self._sam_predictors)

    def predictorImageIds(self) -> list[uuid.UUID]:
        """Return the cached predictor keys for eviction and diagnostics."""
        return list(self._sam_predictors.keys())

    def cache_usage_bytes(self) -> int:
        """Return the total estimated predictor cache footprint in bytes."""
        return sum(self._predictor_sizes.values())

    def pendingUsageBytes(self) -> int:
        """Return pending predictor memory estimates that are not yet cached.

        Pending predictor loads are not charged against the cache budget to avoid
        inflating usage during retries or queueing.
        """
        return sum(self._pending_estimates.values())

    def snapshot_metrics(self) -> SamPredictorMetrics:
        """Return cache and execution metrics for diagnostics overlays and tests."""
        return SamPredictorMetrics(
            cache_bytes=self.cache_usage_bytes(),
            cache_count=len(self._sam_predictors),
            active_jobs=len(self._inflight),
            pending_retries=len(self._predictor_retry_entries),
            hits=self._cache_hits,
            misses=self._cache_misses,
            cache_limit=0,
            evictions=0,
            evicted_bytes=0,
            last_eviction_reason=None,
            last_eviction_timestamp=None,
            prefetch_requested=0,
            prefetch_completed=0,
            prefetch_failed=0,
            last_prefetch_ms=None,
        )

    def activePredictorLoads(self) -> int:
        """Return the number of active predictor jobs, preferring executor stats when available."""
        if self._executor is None:
            return len(self._inflight)
        try:
            return self._executor.active_counts().get("sam", 0)
        except Exception:  # pragma: no cover - defensive fallback
            return len(self._inflight)

    def getPredictor(self, image_id: uuid.UUID) -> "SamPredictor | None":
        """Return the cached predictor for image_id and record the cache hit when present."""
        predictor = self._sam_predictors.get(image_id)
        if predictor is not None:
            self._cache_hits += 1
        return predictor

    def requestPredictor(
        self,
        image: QImage,
        image_id: uuid.UUID,
        *,
        source_path: Path | None = None,
    ):
        """Request a predictor for image_id, emitting predictorReady on cache hit or queueing background work.

        Side effects:
            Increments cache metrics, coalesces duplicate requests, and emits predictorThrottled when executor capacity is exceeded.
        """
        predictor = self.getPredictor(image_id)
        if predictor is not None:
            self.predictorReady.emit(predictor, image_id)
            return
        if not self.checkpointReady():
            logger.warning(
                "Predictor request skipped because SAM checkpoint is missing at %s",
                self._checkpoint_path,
            )
            return
        if image_id in self._inflight:
            logger.debug("Predictor request already queued for %s", image_id)
            return
        self._cache_misses += 1
        self._pending_estimates[image_id] = self._estimate_predictor_bytes(image)
        self._predictor_paths[image_id] = source_path

        def _submit(img: QImage, attempt: int):
            """Submit predictor construction to the executor with retry metadata."""
            return self._submit_predictor_job(
                img,
                image_id,
                source_path,
                attempt=attempt,
                trap_rejection=False,
            )

        def _throttle(key: tuple[uuid.UUID, str], next_attempt: int, rej: TaskRejected):
            """Log and surface throttling while keeping retry sequencing."""
            image_key, _device = key
            logger.warning(
                "SAM predictor load for %s throttled: pending %s limit=%s "
                "(total=%s, category=%s)",
                image_key,
                rej.limit_type,
                rej.limit_value,
                rej.pending_total,
                rej.pending_category,
            )
            self.predictorThrottled.emit(image_key, next_attempt)

        self._retry.queueOrCoalesce(
            self._retry_key(image_id), image, submit=_submit, throttle=_throttle
        )

    def cancelPendingPredictor(self, image_id: uuid.UUID) -> bool:
        """Cancel any in-flight predictor load for image_id.

        Returns:
            True when the executor cancelled the task, False when nothing was pending or cancellation fell back to worker signalling.
        """
        self._retry.cancel(self._retry_key(image_id))
        entry = self._inflight.pop(image_id, None)
        if entry is None:
            self._pending_estimates.pop(image_id, None)
            return False
        worker, handle = entry
        executor = self._ensure_executor()
        cancelled = executor.cancel(handle)
        if not cancelled:
            worker.cancel()
        self._pending_estimates.pop(image_id, None)
        logger.info(
            "Cancelled SAM predictor load for %s (cancelled=%s)",
            image_id,
            cancelled,
        )
        return cancelled

    def clearCache(self):
        """Cancel pending loads and retries, drop cached predictors, and emit predictorCacheCleared."""
        for image_id in list(self._inflight.keys()):
            self.cancelPendingPredictor(image_id)
        for key in list(self._predictor_retry_entries.keys()):
            self._cancel_predictor_retry(key[0])
        self._sam_predictors.clear()
        self._predictor_sizes.clear()
        self._predictor_paths.clear()
        self._pending_estimates.clear()
        self.predictorCacheCleared.emit()

    def removeFromCache(self, image_id: uuid.UUID):
        """Remove the cached predictor for image_id and cancel any queued retry."""
        self._retry.cancel(self._retry_key(image_id))
        self._drop_predictor(image_id)

    def shutdown(self) -> None:
        """Cancel pending predictor work and clear retries."""
        for image_id in list(self._inflight.keys()):
            self.cancelPendingPredictor(image_id)
        self._retry.cancelAll()
        self._pending_estimates.clear()

    def generateMaskFromBox(
        self, image_id: uuid.UUID, bbox: np.ndarray, erase_mode: bool = False
    ):
        """Generate a mask from bbox and emit it via maskReady.

        Args:
            image_id: Image identifier used to locate the predictor.
            bbox: Bounding box in (x0, y0, x1, y1) order.
            erase_mode: Emit the mask for erasing when True instead of adding.

        Side effects:
            Emits maskReady with mask bytes or None and logs warnings for missing predictors or invalid boxes.
        """
        predictor = self.getPredictor(image_id)
        if predictor is None:
            logger.warning(
                "Mask request skipped because predictor for %s is not ready",
                image_id,
            )
            self.maskReady.emit(None, bbox, erase_mode)
            return
        try:
            mask_array_bool = service.predict_mask_from_box(predictor, bbox)
            if mask_array_bool is None:
                logger.info("Mask prediction returned no result for %s", image_id)
                self.maskReady.emit(None, bbox, erase_mode)
                return
            mask_array_uint8 = mask_array_bool.astype(np.uint8) * 255
            self.maskReady.emit(mask_array_uint8, bbox, erase_mode)
        except ValueError as exc:
            logger.warning(
                "Invalid bounding box for SAM prediction on %s: %s",
                image_id,
                exc,
            )
            self.maskReady.emit(None, bbox, erase_mode)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception(
                "Error during mask generation for %s (erase=%s): %s",
                image_id,
                erase_mode,
                exc,
            )
            self.maskReady.emit(None, bbox, erase_mode)

    def _ensure_executor(self) -> TaskExecutorProtocol:
        """Return the executor when present or raise for missing wiring."""
        if self._executor is None:
            raise RuntimeError("SamManager executor is missing")
        return self._executor

    def _retry_key(self, image_id: uuid.UUID) -> tuple[uuid.UUID, str]:
        """Return the retry bookkeeping key for image_id on this device."""
        return (image_id, self._device)

    def _on_worker_finished(self, predictor: "SamPredictor", image_id: uuid.UUID):
        """Cache the finished predictor, resolve retries, and emit predictorReady."""
        self._inflight.pop(image_id, None)
        self._retry.onSuccess(self._retry_key(image_id))
        self._pending_estimates.pop(image_id, None)
        self._sam_predictors[image_id] = predictor
        measured_bytes = self._measure_predictor_bytes(predictor)
        self._predictor_sizes[image_id] = measured_bytes
        logger.info("SAM predictor ready for %s", image_id)
        self.predictorReady.emit(predictor, image_id)
        self._enforce_cache_limit()

    def _on_worker_error(self, image_id: uuid.UUID, message: str):
        """Remove failed jobs from bookkeeping, update retries, and emit predictorLoadFailed unless cancelled."""
        self._inflight.pop(image_id, None)
        self._retry.onFailure(self._retry_key(image_id))
        self._pending_estimates.pop(image_id, None)
        if message == "cancelled":
            logger.info("SAM predictor load cancelled for %s", image_id)
            return
        logger.error("SAM predictor load failed for %s: %s", image_id, message)
        self.predictorLoadFailed.emit(image_id, message)

    def _submit_predictor_job(
        self,
        image: QImage,
        image_id: uuid.UUID,
        source_path: Path | None,
        *,
        attempt: int,
        trap_rejection: bool,
    ) -> TaskHandle | None:
        """Submit a predictor load and optionally trap executor throttling."""
        executor = self._ensure_executor()
        worker = SamWorker(
            image,
            image_id,
            source_path,
            self._checkpoint_path,
            device=self._device,
        )
        BaseWorker.connect_queued(worker.signals.finished, self._on_worker_finished)
        BaseWorker.connect_queued(worker.signals.error, self._on_worker_error)
        try:
            handle = executor.submit(worker, category="sam", device=self._device)
        except TaskRejected as exc:
            if trap_rejection:
                self._handle_predictor_rejection(
                    image, image_id, source_path, attempt, exc
                )
                return None
            raise
        self._inflight[image_id] = (worker, handle)
        logger.info(
            "Queued SAM predictor load for %s (task=%s)",
            image_id,
            handle.task_id,
        )
        return handle

    def _handle_predictor_rejection(
        self,
        image: QImage,
        image_id: uuid.UUID,
        source_path: Path | None,
        attempt: int,
        rejection: TaskRejected,
    ) -> None:
        """Log executor throttling and schedule a backoff retry for predictor loads."""
        next_attempt = max(1, attempt + 1)
        logger.warning(
            "SAM predictor load for %s throttled: pending %s limit=%s "
            "(total=%s, category=%s)",
            image_id,
            rejection.limit_type,
            rejection.limit_value,
            rejection.pending_total,
            rejection.pending_category,
        )
        self.predictorThrottled.emit(image_id, next_attempt)
        self._schedule_predictor_retry(
            image, image_id, source_path, attempts=next_attempt
        )

    def _schedule_predictor_retry(
        self,
        image: QImage,
        image_id: uuid.UUID,
        source_path: Path | None,
        *,
        attempts: int,
    ) -> None:
        """Schedule a delayed predictor submission retry with exponential backoff."""
        self._run_on_main_thread(
            lambda: self._schedule_predictor_retry_on_main(
                image, image_id, source_path, attempts
            )
        )

    def _schedule_predictor_retry_on_main(
        self,
        image: QImage,
        image_id: uuid.UUID,
        source_path: Path | None,
        attempts: int,
    ) -> None:
        """Main-thread implementation for scheduling predictor retry timers."""
        key = self._retry_key(image_id)
        entry = self._predictor_retry_entries.get(key)
        if entry is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(
                lambda retry_id=image_id: self._retry_predictor(retry_id)
            )
            entry = _PredictorRetryEntry(
                attempts=attempts,
                timer=timer,
                image=image,
                image_id=image_id,
                source_path=source_path,
            )
            self._predictor_retry_entries[key] = entry
        else:
            entry.attempts = attempts
            entry.image = image
            entry.image_id = image_id
            entry.source_path = source_path
            timer = entry.timer
        delay_ms = self._compute_predictor_retry_delay(attempts)
        self._pending_estimates[image_id] = self._estimate_predictor_bytes(image)
        self._predictor_paths[image_id] = source_path
        if timer.isActive():
            timer.stop()
        self._retry_controller.total_scheduled += 1
        timer.start(delay_ms)

    def _retry_predictor(self, image_id: uuid.UUID) -> None:
        """Retry a throttled predictor submission after its delay elapses."""
        entry = self._predictor_retry_entries.pop(self._retry_key(image_id), None)
        if entry is None:
            return
        timer = entry.timer
        if timer.isActive():
            timer.stop()
        timer.deleteLater()
        self._pending_estimates[image_id] = self._estimate_predictor_bytes(entry.image)
        self._predictor_paths[image_id] = entry.source_path
        self._submit_predictor_job(
            entry.image,
            entry.image_id,
            entry.source_path,
            attempt=entry.attempts,
            trap_rejection=True,
        )

    def _cancel_predictor_retry(self, image_id: uuid.UUID) -> None:
        """Cancel and dispose of any scheduled retry for image_id."""
        entry = self._predictor_retry_entries.pop(self._retry_key(image_id), None)
        if entry is None:
            return
        self._pending_estimates.pop(image_id, None)
        self._run_on_main_thread(lambda: self._cancel_predictor_timer(entry.timer))

    def _cancel_predictor_timer(self, timer: QTimer) -> None:
        """Stop and dispose of a predictor retry timer on the main thread."""
        if timer.isActive():
            timer.stop()
        timer.deleteLater()

    def _compute_predictor_retry_delay(self, attempts: int) -> int:
        """Compute an exponential backoff delay with jitter within configured bounds."""
        capped_attempts = max(1, attempts)
        base = min(
            _SAM_RETRY_MAX_DELAY_MS,
            _SAM_RETRY_BASE_DELAY_MS * (2 ** (capped_attempts - 1)),
        )
        jitter = min(_SAM_RETRY_BASE_DELAY_MS, int(base * 0.25))
        delay = base + random.randint(0, jitter if jitter > 0 else 0)
        return min(
            _SAM_RETRY_MAX_DELAY_MS,
            max(_SAM_RETRY_BASE_DELAY_MS, delay),
        )

    def _run_on_main_thread(self, callback) -> None:
        """Execute ``callback`` on the Qt main thread when invoked off-thread."""
        app = QCoreApplication.instance()
        main_thread = app.thread() if app else None
        if main_thread is None:
            callback()
            return
        if QThread.currentThread() == main_thread:
            callback()
            return
        dispatcher = qt_retry_dispatcher(self._executor, category="sam_main")
        if callable(dispatcher):
            try:
                dispatcher(callback)
                return
            except Exception:  # pragma: no cover - defensive guard
                logger.debug(
                    "dispatch_to_main_thread failed; falling back to QTimer",
                    exc_info=True,
                )
        if app is not None:
            try:
                QTimer.singleShot(0, app, callback)
                return
            except Exception:  # pragma: no cover - defensive guard
                logger.exception(
                    "Failed to schedule main-thread callback; running inline."
                )
        callback()

    def cacheLimit(self) -> int | None:
        """Return the maximum number of cached predictors when enforced."""
        return self._cache_limit

    def setCacheLimit(self, limit: int | None) -> None:
        """Update the predictor cache limit and trim existing predictors if needed."""
        self._cache_limit = self._sanitize_cache_limit(limit)
        self._enforce_cache_limit()

    def _enforce_cache_limit(self) -> None:
        """Drop the oldest cached predictors until the configured limit is satisfied."""
        limit = self._cache_limit
        if limit is None or limit < 0:
            return
        while len(self._sam_predictors) > limit:
            try:
                oldest_id = next(iter(self._sam_predictors))
            except StopIteration:  # pragma: no cover - defensive guard
                break
            logger.info(
                "Trimming SAM predictor cache entry %s to honor cache_limit=%s",
                oldest_id,
                limit,
            )
            self._drop_predictor(oldest_id)

    def _drop_predictor(self, image_id: uuid.UUID) -> bool:
        """Remove ``image_id`` from the predictor cache and notify listeners."""
        predictor = self._sam_predictors.pop(image_id, None)
        if predictor is None:
            return False
        self._predictor_sizes.pop(image_id, None)
        self._predictor_paths.pop(image_id, None)
        self._pending_estimates.pop(image_id, None)
        self.predictorRemoved.emit(image_id)
        return True

    @staticmethod
    def _sanitize_cache_limit(limit: int | None) -> int | None:
        """Normalize cache limit inputs, treating invalid values as unbounded."""
        if limit is None:
            return None
        try:
            numeric = int(limit)
        except (TypeError, ValueError):
            return None
        if numeric < 0:
            return None
        return numeric

    def _estimate_predictor_bytes(self, image: QImage | None) -> int:
        """Estimate predictor footprint using the image payload as a floor."""
        if image is not None and hasattr(image, "sizeInBytes"):
            try:
                return max(int(image.sizeInBytes()), _DEFAULT_PREDICTOR_ESTIMATE_BYTES)
            except Exception:  # pragma: no cover - defensive
                return _DEFAULT_PREDICTOR_ESTIMATE_BYTES
        return _DEFAULT_PREDICTOR_ESTIMATE_BYTES

    def _measure_predictor_bytes(self, predictor: "SamPredictor") -> int:
        """Return the predictor footprint in bytes using its model tensors."""
        try:
            model = self._resolve_predictor_model(predictor)
        except Exception:  # pragma: no cover - defensive guard
            logger.warning(
                "SAM predictor model unavailable; recording 0 bytes for cache usage",
                exc_info=True,
            )
            return 0
        return self._model_tensor_bytes(model)

    @staticmethod
    def _resolve_predictor_model(predictor: "SamPredictor"):
        """Return the underlying SAM model attached to the predictor."""
        model = getattr(predictor, "model", None)
        if model is None:
            raise RuntimeError("SAM predictor does not expose a model attribute")
        return model

    @staticmethod
    def _model_tensor_bytes(model) -> int:
        """Return the byte footprint of parameters and buffers on a model."""

        def _sum_bytes(tensors: Iterable) -> int:
            """Sum element storage across tensors that expose numel/element_size."""
            total = 0
            for tensor in tensors:
                try:
                    total += int(tensor.numel()) * int(tensor.element_size())
                except Exception:  # pragma: no cover - defensive guard
                    continue
            return total

        params = _sum_bytes(getattr(model, "parameters", lambda: ())())
        buffers = _sum_bytes(getattr(model, "buffers", lambda: ())())
        return params + buffers
