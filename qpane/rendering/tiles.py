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

"""Tile generation and caching primitives powered by the shared task executor."""

import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, NamedTuple, Sequence, TypedDict
from typing import OrderedDict as OrderedDictType

from PySide6.QtCore import QObject, QRunnable, Signal
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

_TILE_EVICTION_BATCH = 16
_TILE_RETRY_BASE_MS = 50
_TILE_RETRY_MAX_MS = 1000


class TileIdentifier(NamedTuple):
    """Uniquely identifies a tile by image UUID, pyramid scale, and grid position."""

    image_id: uuid.UUID
    source_path: Path | None
    pyramid_scale: float  # The scale of the pyramid level (e.g., 1.0, 0.5, 0.25)
    row: int
    col: int


@dataclass(slots=True, frozen=True)
class Tile:
    """A container for tile data and its memory footprint."""

    identifier: TileIdentifier
    image: QImage
    size_bytes: int = field(init=False)

    def __post_init__(self):
        """Calculate the byte footprint for this tile image."""
        # QImage.sizeInBytes() is more accurate than width * height * depth/8
        object.__setattr__(self, "size_bytes", self.image.sizeInBytes())


class TileWorkerSignals(QObject):
    """Defines signals available from a running tile worker thread."""

    finished = Signal(Tile)
    error = Signal(TileIdentifier, str)


class TileGeneratorWorker(QRunnable, BaseWorker):
    """Background worker that crops and packages a single tile image."""

    def __init__(
        self,
        identifier: TileIdentifier,
        source_image: QImage,
        tile_size: int,
        tile_overlap: int,
    ):
        """Bind tile metadata and geometry used when cropping from ``source_image``."""
        QRunnable.__init__(self)
        BaseWorker.__init__(self)
        self.signals = TileWorkerSignals()
        self.identifier = identifier
        self.source_image = source_image
        self.tile_size = tile_size
        self.tile_overlap = tile_overlap

    def run(self):
        """Crop ``tile_size`` pixels from ``source_image`` and emit completion."""
        try:
            if self.is_cancelled:
                self._emit_cancelled()
                return
            stride = self.tile_size - self.tile_overlap
            x = self.identifier.col * stride
            y = self.identifier.row * stride
            cropped_qimage = self.source_image.copy(
                x, y, self.tile_size, self.tile_size
            )
            if self.is_cancelled:
                self._emit_cancelled()
                return
            tile = Tile(identifier=self.identifier, image=cropped_qimage)
            self.emit_finished(True, payload=tile)
        except Exception as exc:
            self.emit_finished(False, payload=(self.identifier, str(exc)), error=exc)

    def cancel(self):
        """Signal cancellation through the BaseWorker helper."""
        BaseWorker.cancel(self)

    def _emit_cancelled(self) -> None:
        """Emit the cancellation payload once the worker has been stopped."""
        if self.is_cancelled:
            self.emit_finished(False, payload=(self.identifier, "cancelled"))


# Typed aliases for clarity
TileCache = OrderedDictType[TileIdentifier, Tile]


class WorkerEntry(TypedDict, total=False):
    """Structure describing an active worker and its executor handle."""

    worker: "TileGeneratorWorker"
    handle: TaskHandle


WorkerState = Dict[TileIdentifier, WorkerEntry]


class TileManager(QObject, CacheMetricsMixin, ExecutorOwnerMixin):
    """Generate, cache, and serve image tiles with executor-backed workers.

    Provides LRU eviction by byte budget, tracks prefetch stats, and emits throttle events when executor limits reject work.
    All public entrypoints expect to run on the Qt main thread; retry scheduling relies on the shared controller's main-thread dispatch.
    """

    tileReady = Signal(TileIdentifier)
    tilesThrottled = Signal(TileIdentifier, int)
    usageChanged = Signal(object)
    cacheLimitChanged = Signal(object)

    def __init__(
        self,
        config: Config,
        parent: QObject | None = None,
        *,
        executor: TaskExecutorProtocol,
        owns_executor: bool = False,
    ):
        """Initialise cache limits, worker pools, and retry bookkeeping."""
        super().__init__(parent)
        CacheMetricsMixin.__init__(self)
        ExecutorOwnerMixin.__init__(
            self,
            executor_logger=logger,
            owner_name="TileManager",
        )
        self._config = config
        self.tile_size = config.tile_size
        self.tile_overlap = config.tile_overlap
        self._cache_admission_guard = None
        self._managed_mode = False
        self._rejected_cache_keys: set[TileIdentifier] = set()
        self._tile_cache: TileCache = OrderedDict()
        self._cache_size_bytes: int = 0
        self._cache_limit_bytes: int = 0
        self.cache_limit_bytes = self._resolve_cache_limit_bytes(config)
        self._executor: TaskExecutorProtocol | None = executor
        self._owns_executor = bool(owns_executor)
        # Unified worker state: identifier -> executor handle plus worker reference
        self._worker_state: WorkerState = {}
        dispatcher = qt_retry_dispatcher(self._executor, category="tiles_main")
        self._tile_retry: RetryController[TileIdentifier, QImage] = (
            makeQtRetryController(
                "tiles",
                _TILE_RETRY_BASE_MS,
                _TILE_RETRY_MAX_MS,
                parent=self,
                dispatcher=dispatcher,
            )
        )
        self._eviction = CacheEvictionCoordinator(logger=logger, name="tile cache")

    @property
    def cache_usage_bytes(self) -> int:
        """Return the current tile cache usage in bytes."""
        return self._cache_size_bytes

    @property
    def cache_limit_bytes(self) -> int:
        """Return the configured tile cache budget in bytes."""
        return self._cache_limit_bytes

    @cache_limit_bytes.setter
    def cache_limit_bytes(self, value: int) -> None:
        """Set the tile cache budget and emit change notifications."""
        new_value = max(0, int(value))
        previous = getattr(self, "_cache_limit_bytes", 0)
        self._cache_limit_bytes = new_value
        if new_value != previous:
            self.cacheLimitChanged.emit(new_value)
        if not self._managed_mode and self._cache_size_bytes > self._cache_limit_bytes:
            self._schedule_cache_eviction()

    def set_managed_mode(self, enabled: bool) -> None:
        """Enable or disable managed mode.

        In managed mode, the manager disables automatic self-eviction and relaxes
        admission checks, relying on an external coordinator to drive trims.
        """
        self._managed_mode = bool(enabled)

    def set_admission_guard(self, guard: Callable[[int], bool] | None) -> None:
        """Install an optional hard-cap guard consulted before caching tiles."""
        self._cache_admission_guard = guard

    def apply_config(self, config: Config) -> None:
        """Refresh derived values after a configuration update."""
        previous_tile_size = self.tile_size
        previous_tile_overlap = self.tile_overlap
        self._config = config
        self.tile_size = config.tile_size
        self.tile_overlap = config.tile_overlap
        if (self.tile_size != previous_tile_size) or (
            self.tile_overlap != previous_tile_overlap
        ):
            self.clear_caches()
        if self._eviction.pending:
            self._cancel_eviction_task()
        self.cache_limit_bytes = self._resolve_cache_limit_bytes(config)
        if not self._managed_mode and self._cache_size_bytes > self.cache_limit_bytes:
            self._schedule_cache_eviction()

    def snapshot_metrics(self) -> CacheManagerMetrics:
        """Return cache and prefetch counters for diagnostics and tests."""
        return self._snapshot_cache_metrics(
            cache_bytes=self._cache_size_bytes,
            cache_limit=self.cache_limit_bytes,
            active_jobs=len(self._worker_state),
            pending_retries=len(self.pending_retry_tiles()),
        )

    def retry_snapshot(self):
        """Expose the retry controller snapshot for diagnostics consumers."""
        return self._tile_retry.snapshot()

    def pending_retry_tiles(self) -> list[TileIdentifier]:
        """Return tile identifiers currently queued for retry."""
        return list(self._tile_retry.pendingKeys())

    def _set_cache_usage_bytes(self, value: int) -> None:
        """Clamp and publish cache usage changes."""
        clamped = max(0, int(value))
        if clamped == self._cache_size_bytes:
            return
        self._cache_size_bytes = clamped
        self.usageChanged.emit(clamped)

    def add_tile(self, tile: Tile) -> None:
        """Insert `tile` into the cache while updating bookkeeping."""
        identifier = tile.identifier
        if not self._allow_cache_insert(tile.size_bytes, identifier):
            return
        new_size = self._cache_size_bytes
        previous = self._tile_cache.pop(identifier, None)
        if previous is not None:
            new_size = max(0, new_size - previous.size_bytes)
        self._tile_cache[identifier] = tile
        self._tile_cache.move_to_end(identifier)
        new_size += tile.size_bytes
        self._set_cache_usage_bytes(new_size)
        if (
            not self._managed_mode
            and self.cache_limit_bytes > 0
            and self._cache_size_bytes > self.cache_limit_bytes
        ):
            self._schedule_cache_eviction()

    def get_tile(
        self, identifier: TileIdentifier, source_image: QImage
    ) -> QImage | None:
        """Retrieves a tile image from the cache or starts a worker to generate it.

        Args:
            identifier: The unique identifier for the tile.
            source_image: The QImage to crop from if generation is needed.

        Returns:
            The cached QImage if present, or None if generation is pending.

        Side effects:
            May enqueue a worker, update cache, or emit signals.
        """
        self._assert_main_thread()
        cached_tile = self._tile_cache.get(identifier)
        if cached_tile is not None:
            self._tile_cache.move_to_end(identifier)
            self._cancel_tile_retry(identifier)
            self._cache_hits += 1
            return cached_tile.image
        if identifier in self._worker_state:
            return None
        self._cache_misses += 1
        # Route through shared retry controller; attempt immediate submit

        def _submit(img: QImage, attempt: int):
            """Enqueue a TileGeneratorWorker for ``identifier`` if capacity allows."""
            worker = TileGeneratorWorker(
                identifier=identifier,
                source_image=img,
                tile_size=self.tile_size,
                tile_overlap=self.tile_overlap,
            )
            BaseWorker.connect_queued(worker.signals.finished, self._on_tile_generated)
            BaseWorker.connect_queued(worker.signals.error, self._on_tile_error)
            executor = self._executor
            if executor is None:
                raise RuntimeError("TileManager executor is missing")
            handle = executor.submit(worker, category="tiles")
            self._mark_generating(identifier, worker, handle)
            logger.debug(
                "Queued tile generation for %s (via RetryController)", identifier
            )
            return handle

        def _throttle(key: TileIdentifier, next_attempt: int, rej: TaskRejected):
            """Emit throttle diagnostics when executor limits reject the request."""
            logger.warning(
                "Tile generation for %s throttled: pending %s limit=%s (total=%s, category=%s)",
                key,
                rej.limit_type,
                rej.limit_value,
                rej.pending_total,
                rej.pending_category,
            )
            self.tilesThrottled.emit(key, next_attempt)

        self._queue_tile_retry(
            identifier,
            source_image,
            submit=_submit,
            throttle=_throttle,
        )
        return None

    def calculate_grid_dimensions(self, width: int, height: int) -> tuple[int, int]:
        """Return the tile grid size needed to cover `width` by `height`."""
        if width <= 0 or height <= 0:
            return 0, 0
        tile_size = max(1, int(self.tile_size))
        overlap = max(0, int(self.tile_overlap))
        step = max(1, tile_size - overlap)
        cols = max(1, (max(0, width - overlap) + step - 1) // step)
        rows = max(1, (max(0, height - overlap) + step - 1) // step)
        return cols, rows

    def _remove_tile_locked(self, identifier: TileIdentifier) -> None:
        """Remove ``identifier`` from the cache while updating size tracking."""
        tile = self._tile_cache.pop(identifier, None)
        if tile is None:
            return
        self._set_cache_usage_bytes(max(0, self._cache_size_bytes - tile.size_bytes))

    def clear_caches(self):
        """Removes all tiles from the cache and resets memory counters.

        Side effects:
            Cancels eviction, clears retry state, resets cache and worker state.
        """
        self._assert_main_thread()
        self._cancel_eviction_task()
        self._cancel_all_tile_retries()
        had_entries = bool(self._tile_cache)
        cached_tiles = len(self._tile_cache)
        active_workers = len(self._worker_state)
        self._tile_cache.clear()
        self._worker_state.clear()
        self._rejected_cache_keys.clear()
        self._prefetch_drop_all()
        self._reset_cache_metrics()
        self._set_cache_usage_bytes(0)
        if had_entries:
            self._record_eviction_metadata("clear")
        logger.info(
            "Cleared tile cache (tiles=%d, workers=%d)",
            cached_tiles,
            active_workers,
        )

    def remove_tiles_for_image_id(self, image_id: uuid.UUID) -> None:
        """Removes all tiles associated with a specific image ID.

        Args:
            image_id: Identifier to remove tiles for.

        Side effects:
            Cancels workers, updates cache/state, emits logs.
        """
        self._assert_main_thread()
        ids_to_remove = [
            identifier
            for identifier in self._tile_cache
            if identifier.image_id == image_id
        ]
        for identifier in ids_to_remove:
            self._remove_tile_locked(identifier)
            self._cancel_tile_retry(identifier)
        worker_ids = [
            identifier
            for identifier in self._worker_state
            if identifier.image_id == image_id
        ]
        for identifier in worker_ids:
            entry = self._worker_state.pop(identifier, None)
            if not entry:
                continue
            cancelled = self._stop_worker(
                identifier,
                entry=entry,
                already_removed=True,
            )
            logger.info(
                "Cancelled inflight tile %s due to source eviction (cancelled=%s)",
                identifier,
                cancelled,
            )

    def cancel_invisible_workers(self, visible_identifiers: set):
        """Cancels any running workers for tiles that are no longer visible.

        Args:
            visible_identifiers: Set of TileIdentifier currently visible.

        Side effects:
            Cancels workers, updates state, emits logs.
        """
        self._assert_main_thread()
        hidden_identifiers = set(self._worker_state.keys()) - set(visible_identifiers)
        for identifier in hidden_identifiers:
            entry = self._worker_state.pop(identifier, None)
            if not entry:
                continue
            cancelled = self._stop_worker(
                identifier,
                entry=entry,
                already_removed=True,
            )
            logger.info(
                "Cancelled invisible tile worker %s (cancelled=%s)",
                identifier,
                cancelled,
            )
        for identifier in self._pending_tile_retry_keys():
            if identifier not in visible_identifiers:
                self._cancel_tile_retry(identifier)

    def prefetch_tiles(
        self,
        identifiers: Sequence[TileIdentifier],
        source_image: QImage,
        *,
        reason: str = "prefetch",
    ) -> list[TileIdentifier]:
        """Schedule background generation for `identifiers` using `source_image`."""
        if not identifiers or source_image.isNull():
            return []
        self._assert_main_thread()
        scheduled: list[TileIdentifier] = []
        for ident in identifiers:
            if self._prefetch_pending(ident):
                continue
            if ident in self._worker_state:
                logger.debug(
                    "Skipping tile prefetch for %s; worker already active", ident
                )
                continue
            cached_tile = self._tile_cache.get(ident)
            if cached_tile is not None:
                self._prefetch_skip_hit()
                continue
            self._prefetch_begin(ident, record_start=False)
            try:
                self.get_tile(ident, source_image)
            except Exception:
                self._prefetch_finish(ident, success=False)
                raise
            entry = self._worker_state.get(ident)
            pending_retry = ident in self._pending_tile_retry_keys()
            if entry is None and not pending_retry:
                cached_tile = self._tile_cache.get(ident)
                if cached_tile is not None:
                    self._prefetch_finish(ident, success=True)
                else:
                    self._prefetch_finish(ident, success=False)
                continue
            scheduled.append(ident)
        if scheduled:
            for ident in scheduled:
                logger.info("Scheduled tile prefetch %s (reason=%s)", ident, reason)
        return scheduled

    def cancel_prefetch(
        self,
        identifiers: Sequence[TileIdentifier],
        *,
        reason: str = "navigation",
    ) -> list[TileIdentifier]:
        """Cancel outstanding prefetch workers for the provided identifiers."""
        if not identifiers:
            return []
        self._assert_main_thread()
        cancelled: list[TileIdentifier] = []
        executor = self._executor
        if executor is None:
            raise RuntimeError("TileManager executor is missing")
        for ident in identifiers:
            if not self._prefetch_pending(ident):
                continue
            entry = self._worker_state.get(ident)
            if entry:
                cancelled_flag = self._stop_worker(ident, entry=entry)
                cancelled.append(ident)
                logger.info(
                    "Cancelled tile prefetch %s (reason=%s, executor_cancelled=%s)",
                    ident,
                    reason,
                    cancelled_flag,
                )
                continue
            if ident in self._pending_tile_retry_keys():
                self._cancel_tile_retry(ident)
                self._prefetch_finish(ident, success=False)
                cancelled.append(ident)
                logger.info(
                    "Cancelled tile prefetch %s before worker submission (reason=%s)",
                    ident,
                    reason,
                )
        return cancelled

    def _allow_cache_insert(self, size_bytes: int, key: TileIdentifier) -> bool:
        """Return True when ``size_bytes`` is within guardrail limits."""
        size = max(0, int(size_bytes))
        budget_limit = max(0, int(self.cache_limit_bytes))

        def _warn(limit_value: int) -> None:
            """Log cache admission rejection for oversize tile entries."""
            if key in self._rejected_cache_keys:
                return
            logger.warning(
                "requested item exceeds budget; not cached | consumer=tiles | "
                "size=%d | budget=%d",
                size,
                not self._managed_mode and limit_value,
            )
            self._rejected_cache_keys.add(key)

        if size > budget_limit:
            _warn(budget_limit)
            return False
        guard = self._cache_admission_guard
        if guard is not None and not guard(size):
            _warn(budget_limit)
            return False
        return True

    def _schedule_cache_eviction(self) -> None:
        """Queue a maintenance callback when cache usage exceeds the configured limit."""
        if self._eviction.pending:
            return
        if self._cache_size_bytes <= self.cache_limit_bytes or not self._tile_cache:
            return
        self._ensure_next_eviction_reason("limit")
        executor = self._executor
        if executor is None:
            raise RuntimeError("TileManager executor is missing")
        self._eviction.schedule(
            executor=executor,
            callback=self._evict_cache_batch,
            category="maintenance",
        )

    def _evict_cache_batch(self) -> None:
        """Evict a bounded batch of tiles on the main thread."""
        reason = self._consume_next_eviction_reason("limit")
        evicted = 0
        new_usage = self._cache_size_bytes
        while (
            new_usage > self.cache_limit_bytes
            and self._tile_cache
            and evicted < _TILE_EVICTION_BATCH
        ):
            identifier, removed_tile = self._tile_cache.popitem(last=False)
            new_usage -= removed_tile.size_bytes
            logger.info("Evicted tile from cache: %s", identifier)
            self._evictions_total += 1
            self._evicted_bytes += removed_tile.size_bytes
            self._record_eviction_metadata(reason)
            evicted += 1
        self._set_cache_usage_bytes(new_usage)
        if (
            not self._managed_mode
            and self._cache_size_bytes > self.cache_limit_bytes
            and self._tile_cache
        ):
            self._schedule_cache_eviction()

    def _cancel_eviction_task(self) -> None:
        """Cancel any pending eviction maintenance task."""
        self._eviction.cancel(self._executor)

    def _on_tile_generated(self, tile: Tile):
        """Slot for when a tile worker successfully finishes."""
        self._worker_state.pop(tile.identifier, None)
        self._tile_retry.onSuccess(tile.identifier)
        self.add_tile(tile)
        self._prefetch_finish(tile.identifier, success=True)
        logger.info("Tile generated for %s", tile.identifier)
        self.tileReady.emit(tile.identifier)

    def _on_tile_error(self, identifier: TileIdentifier, error_message: str):
        """Slot for when a tile worker encounters an error."""
        self._worker_state.pop(identifier, None)
        self._tile_retry.onFailure(identifier)
        self._prefetch_finish(identifier, success=False)
        if error_message == "cancelled":
            logger.info("Tile generation cancelled for %s", identifier)
            return
        logger.error("Tile generation failed for %s: %s", identifier, error_message)

    def shutdown(self, *, wait: bool = True) -> None:
        """Cancel outstanding workers and optionally wait for executor cleanup."""
        self._assert_main_thread()
        self._cancel_eviction_task()
        self._cancel_all_tile_retries()
        if not self._worker_state:
            self._maybe_wait_for_executor(wait)
            return
        executor = self._executor
        if executor is None:
            raise RuntimeError("TileManager executor is missing")
        for identifier, entry in list(self._worker_state.items()):
            cancelled = self._stop_worker(
                identifier,
                entry=entry,
                finalize_prefetch=False,
                cancel_retry=False,
            )
            logger.info(
                "Requested cancellation for tile %s (cancelled=%s)",
                identifier,
                cancelled,
            )
        self._worker_state.clear()
        self._prefetch_drop_all()
        self._maybe_wait_for_executor(wait)

    def _stop_worker(
        self,
        identifier: TileIdentifier,
        *,
        entry: WorkerEntry | None = None,
        already_removed: bool = False,
        finalize_prefetch: bool = True,
        cancel_retry: bool = True,
    ) -> bool:
        """Cancel the worker represented by ``entry`` and update retry/prefetch state."""
        if entry is None:
            entry = self._worker_state.get(identifier)
        if not already_removed:
            self._worker_state.pop(identifier, None)
        if entry is None:
            if finalize_prefetch:
                self._prefetch_finish(identifier, success=False)
            if cancel_retry:
                self._cancel_tile_retry(identifier)
            return False
        executor = self._executor
        if executor is None:
            raise RuntimeError("TileManager executor is missing")
        handle = entry.get("handle")
        worker = entry.get("worker")
        cancelled = False
        if handle is not None:
            try:
                cancelled = executor.cancel(handle)
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("Executor cancel raised for tile %s", identifier)
                cancelled = False
        if not cancelled and worker is not None:
            try:
                worker.cancel()
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("Tile worker cancel threw (tile=%s)", identifier)
        if finalize_prefetch:
            self._prefetch_finish(identifier, success=False)
        if cancel_retry:
            self._cancel_tile_retry(identifier)
        return cancelled

    def _mark_generating(self, identifier, worker, handle):
        """Record a tile as being generated in the unified worker state."""
        entry: WorkerEntry = {
            "worker": worker,
            "handle": handle,
        }
        self._worker_state[identifier] = entry
        self._prefetch_mark_started(identifier)

    def _queue_tile_retry(
        self,
        identifier: TileIdentifier,
        source_image: QImage,
        *,
        submit: Callable[[QImage, int], TaskHandle],
        throttle: Callable[[TileIdentifier, int, TaskRejected], None],
    ) -> None:
        """Queue tile generation through the retry controller."""
        self._tile_retry.queueOrCoalesce(
            identifier,
            source_image,
            submit=submit,
            throttle=throttle,
        )

    def _cancel_tile_retry(self, identifier: TileIdentifier) -> None:
        """Cancel a pending retry for ``identifier`` when present."""
        self._tile_retry.cancel(identifier)

    def _cancel_all_tile_retries(self) -> None:
        """Cancel every queued tile retry."""
        self._tile_retry.cancelAll()

    def _pending_tile_retry_keys(self) -> list[TileIdentifier]:
        """Return identifiers pending retry without exposing controller internals."""
        return list(self._tile_retry.pendingKeys())

    def _assert_main_thread(self) -> None:
        """Raise AssertionError if called off the Qt main thread."""
        assert_qt_main_thread(self)

    @staticmethod
    def _resolve_cache_limit_bytes(config: Config) -> int:
        """Return the tile cache budget derived from cache settings."""
        cache_settings = getattr(config, "cache", None)
        if not isinstance(cache_settings, CacheSettings):
            cache_settings = CacheSettings()
        budgets = cache_settings.resolved_consumer_budgets_bytes()
        return int(budgets.get("tiles", 0))
