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

"""Task executor abstraction backed by Qt thread pools."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from time import monotonic
from typing import Any, Callable, Mapping, NamedTuple, Protocol, runtime_checkable

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QRunnable,
    Qt,
    QThread,
    QThreadPool,
    Signal,
)
from shiboken6 import isValid

from .thread_policy import ThreadPolicy, build_thread_policy, update_thread_policy

logger = logging.getLogger(__name__)


_PENDING_SOFT_LIMIT_RATIO = 0.8
_PENDING_WAIT_INTERVAL = 0.1


class _LimitReason(NamedTuple):
    """Describe why a task cannot transition from pending to active."""

    kind: str
    limit: int
    current: int


@dataclass(frozen=True)
class TaskOutcome:
    """Final result reported by a worker back to the executor."""

    success: bool
    payload: Any | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class TaskHandle:
    """Identifier returned by executors when tasks are submitted."""

    task_id: str
    category: str
    device: str | None = None


@dataclass(frozen=True)
class ExecutorSnapshot:
    """Diagnostic snapshot describing executor state at a point in time."""

    name: str
    max_workers: int
    active_total: int
    active_by_category: dict[str, int]
    queued_by_category: dict[str, int]
    pending_total: int
    max_pending_total: int | None
    pending_limits: dict[str, int]
    pending_utilization_total_pct: float | None
    pending_utilization_by_category_pct: dict[str, float]
    category_limits: dict[str, int]
    device_limits: dict[str, dict[str, int]]
    pool_max_threads: int | None = None
    rejection_count: int | None = None
    average_wait_time_ms: float | None = None
    category_priorities: dict[str, int] = field(default_factory=dict)


class TaskRejected(RuntimeError):
    """Raised when the executor refuses to queue a task because pending limits were exceeded."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        device: str | None,
        limit_type: str,
        limit_value: int,
        pending_total: int,
        pending_category: int,
    ) -> None:
        """Capture executor limit context for diagnostics and callers.

        Args:
            message: User-facing rejection message.
            category: Task category that exceeded its queue or concurrency limit.
            device: Optional device identifier tied to device-level limits.
            limit_type: Label describing which limit fired (total/category/device).
            limit_value: Maximum allowed tasks for the violated limit.
            pending_total: Total pending tasks when the rejection occurred.
            pending_category: Pending tasks for the rejected category.
        """
        super().__init__(message)
        self.category = category
        self.device = device
        self.limit_type = limit_type
        self.limit_value = limit_value
        self.pending_total = pending_total
        self.pending_category = pending_category


@runtime_checkable
class TaskExecutorProtocol(Protocol):
    """Interface required by QPane managers to schedule QRunnables."""

    def _dispatch_main_thread_entries(self, entries: list[_TaskEntry]) -> None:
        """Schedule provided entries for main-thread execution."""
        ...

    def dispatch_to_main_thread(
        self,
        callback: Callable[[], None],
        *,
        category: str = "main",
        device: str | None = None,
    ) -> TaskHandle:
        """Schedule ``callback`` to run on the Qt main thread."""
        ...

    def cancel(self, handle: TaskHandle) -> bool:
        """Attempt to cancel the task represented by ``handle``."""

    def mark_finished(self, handle: TaskHandle, outcome: TaskOutcome) -> None:
        """Report that ``handle`` completed with ``outcome``."""

    def active_counts(self) -> Mapping[str, int]:
        """Return a mapping of category -> active task count."""

    def snapshot(self) -> ExecutorSnapshot:
        """Return a descriptive snapshot suitable for diagnostics."""

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting new work and optionally wait for completion."""


@runtime_checkable
class LiveTunableExecutorProtocol(TaskExecutorProtocol, Protocol):
    """Executor variant that supports live tuning of concurrency limits."""

    def setMaxWorkers(self, max_workers: int) -> None:
        """Apply a new maximum worker count without rebuilding the executor."""

    def setPendingTotal(self, max_pending_total: int | None) -> None:
        """Update the global pending-task budget; ``None`` removes the cap."""

    def setCategoryPriorities(self, priorities: Mapping[str, int]) -> None:
        """Replace per-category scheduling priorities with ``priorities``."""

    def setCategoryLimits(self, limits: Mapping[str, int]) -> None:
        """Set per-category concurrency limits to control active task counts."""

    def setPendingLimits(self, limits: Mapping[str, int]) -> None:
        """Set per-category pending queue limits to cap enqueued tasks."""

    def setDeviceLimits(self, limits: Mapping[str, Mapping[str, int]]) -> None:
        """Set per-device concurrency limits keyed by device identifier."""


@dataclass
class _TaskEntry:
    """Internal bookkeeping for pending/active tasks."""

    handle: TaskHandle
    runnable: QRunnable | None
    priority: int
    state: str  # "pending" or "active"
    callback: Callable[[], None] | None = None


class _MainThreadInvoker(QObject):
    """Bridge that schedules executor callbacks onto the Qt main thread."""

    invoke = Signal(str)

    def __init__(self, owner: "QThreadPoolExecutor") -> None:
        """Bind the helper to its owning executor for queued deliveries."""
        super().__init__()
        self._owner = owner
        self.invoke.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def queue(self, task_id: str) -> None:
        """Request delivery of ``task_id`` on the main thread."""
        self.invoke.emit(task_id)

    def _deliver(self, task_id: str) -> None:
        """Forward ``task_id`` back to the owning executor on the GUI thread."""
        self._owner._execute_main_thread_callback(task_id)


class QThreadPoolExecutor(TaskExecutorProtocol):
    """Executor implementation backed by :class:`QThreadPool`."""

    def __init__(
        self,
        policy: ThreadPolicy | None = None,
        *,
        pool: QThreadPool | None = None,
        name: str | None = None,
    ) -> None:
        """Configure the executor with a concurrency policy and Qt pool.

        Args:
            policy: ThreadPolicy describing priorities and limits. Defaults to
                the host configuration.
            pool: Optional QThreadPool to wrap; defaults to a dedicated pool
                rather than the global instance.
            name: Optional diagnostics label shown in metrics.
        """
        self._policy = policy or build_thread_policy()
        self._pool = pool or QThreadPool()
        self._pool_unavailable = False
        self._pool_unavailable_logged = False
        self._apply_pool_max_threads_locked(self._policy.max_workers)
        self._name = name or self._describe_pool(self._pool)
        self._lock = threading.RLock()
        self._task_id_counter = count(1)
        self._tasks: dict[str, _TaskEntry] = {}
        self._pending_order: deque[str] = deque()
        self._active_by_category: dict[str, int] = {}
        self._queued_by_category: dict[str, int] = {}
        self._active_by_device: dict[tuple[str, str], int] = {}
        self._active_total = 0
        self._main_thread_invoker = _MainThreadInvoker(self)
        self._pending_condition = threading.Condition(self._lock)
        self._pending_total_warned = False
        self._pending_category_warned: set[str] = set()
        self._reschedule_logged: set[str] = set()
        self._rejection_count = 0
        self._active_workers = 0
        self._active_worker_ids: set[str] = set()
        self._pending_wait_total_seconds = 0.0
        self._pending_wait_samples = 0
        self._dirty_callback: Callable[[str], None] | None = None
        self._pool_threads_reset_logged = False
        logger.debug(
            "QThreadPoolExecutor initialised (pool=%s, policy=%s)",
            self._name,
            self._policy,
        )

    def setMaxWorkers(self, max_workers: int) -> None:
        """Update the maximum concurrent workers and propagate to the pool.

        Args:
            max_workers: New worker cap applied immediately to future dispatches.
        """
        new_policy = update_thread_policy(self._policy, max_workers=max_workers)
        self._swap_policy(new_policy, update_pool_threads=True)

    def setPendingTotal(self, max_pending_total: int | None) -> None:
        """Update the global pending queue limit.

        Args:
            max_pending_total: Maximum pending tasks (``None`` means unbounded).
        """
        new_policy = update_thread_policy(
            self._policy, max_pending_total=max_pending_total
        )
        self._swap_policy(new_policy)

    def setCategoryPriorities(self, priorities: Mapping[str, int]) -> None:
        """Replace category submission priorities.

        Args:
            priorities: Mapping of category name to priority.
        """
        new_policy = update_thread_policy(self._policy, category_priorities=priorities)
        self._swap_policy(new_policy)

    def setCategoryLimits(self, limits: Mapping[str, int]) -> None:
        """Replace category concurrency limits.

        Args:
            limits: Mapping of category name to active task limits.
        """
        new_policy = update_thread_policy(self._policy, category_limits=limits)
        self._swap_policy(new_policy)

    def setPendingLimits(self, limits: Mapping[str, int]) -> None:
        """Replace per-category pending queue limits.

        Args:
            limits: Mapping of category name to pending task caps.
        """
        new_policy = update_thread_policy(self._policy, pending_limits=limits)
        self._swap_policy(new_policy)

    def setDeviceLimits(self, limits: Mapping[str, Mapping[str, int]]) -> None:
        """Replace per-device concurrency limits.

        Args:
            limits: Mapping of device identifier to per-category limits.
        """
        new_policy = update_thread_policy(self._policy, device_limits=limits)
        self._swap_policy(new_policy)

    def submit(
        self,
        runnable: QRunnable,
        category: str,
        *,
        device: str | None = None,
    ) -> TaskHandle:
        """Queue ``runnable`` for execution, blocking until capacity is available.

        Args:
            runnable: QRunnable or BaseWorker scheduled on the pool.
            category: Logical category controlling priorities and limits.
            device: Optional device identifier used for device-specific caps.

        Returns:
            TaskHandle assigned to the runnable.

        Raises:
            ValueError: If ``category`` is empty.
            TaskRejected: When called on the GUI thread and pending limits are hit.
        """
        if not isinstance(category, str) or not category:
            raise ValueError("category must be a non-empty string")
        task_id = f"task-{next(self._task_id_counter)}"
        handle = TaskHandle(task_id=task_id, category=category, device=device)
        priority = self._policy.priority_for(category)
        entry = _TaskEntry(
            handle=handle, runnable=runnable, priority=priority, state="pending"
        )
        if hasattr(runnable, "setAutoDelete"):
            try:  # pragma: no branch - PySide can raise if pool already owns runnable
                runnable.setAutoDelete(False)
            except Exception:  # pragma: no cover - Qt specific edge cases
                logger.debug(
                    "setAutoDelete(False) failed for %s", runnable, exc_info=True
                )
        if hasattr(runnable, "bind_executor"):
            try:
                runnable.bind_executor(self, handle)  # type: ignore[call-arg]
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("bind_executor failed for %s", runnable)
        is_main_thread = self._is_gui_thread()
        self._queue_entry(handle, entry, is_main_thread=is_main_thread)
        return handle

    def dispatch_to_main_thread(
        self,
        callback: Callable[[], None],
        *,
        category: str = "main",
        device: str | None = None,
    ) -> TaskHandle:
        """Schedule ``callback`` to run on the Qt main thread.

        Args:
            callback: Parameterless callable executed on the GUI thread.
            category: Category used for priorities and queue tracking.
            device: Optional device identifier for per-device limits.

        Returns:
            TaskHandle that can be cancelled before execution.

        Raises:
            TypeError: If ``callback`` is not callable.
            ValueError: If ``category`` is empty.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        if not isinstance(category, str) or not category:
            raise ValueError("category must be a non-empty string")
        task_id = f"task-{next(self._task_id_counter)}"
        handle = TaskHandle(task_id=task_id, category=category, device=device)
        priority = self._policy.priority_for(category)
        entry = _TaskEntry(
            handle=handle,
            runnable=None,
            priority=priority,
            state="pending",
            callback=callback,
        )
        is_main_thread = self._is_gui_thread()
        self._queue_entry(handle, entry, is_main_thread=is_main_thread)
        return handle

    def set_dirty_callback(self, callback: Callable[[str], None] | None) -> None:
        """Register a diagnostics dirty callback used to refresh overlay rows."""
        self._dirty_callback = callback

    def cancel(self, handle: TaskHandle) -> bool:
        """Attempt to cancel a pending or running task.

        Args:
            handle: TaskHandle returned from ``submit`` or ``dispatch_to_main_thread``.

        Returns:
            True if the task was cancelled or cancellation was forwarded to the
            runnable, False otherwise.
        """
        to_dispatch: list[_TaskEntry] = []
        with self._lock:
            entry = self._tasks.get(handle.task_id)
            if entry is None:
                return False
            if entry.state == "pending":
                self._remove_pending_locked(handle.task_id, handle.category)
                self._tasks.pop(handle.task_id, None)
                self._pending_condition.notify_all()
                to_dispatch = self._try_start_tasks_locked()
                result = True
            else:
                cancelled = False
                runnable = entry.runnable
                if hasattr(runnable, "cancel"):
                    try:
                        runnable.cancel()  # type: ignore[call-arg]
                        cancelled = True
                    except Exception:  # pragma: no cover - defensive guard
                        logger.exception("Error cancelling runnable %s", runnable)
                result = cancelled
            self._reschedule_logged.discard(handle.task_id)
        if to_dispatch:
            self._dispatch_main_thread_entries(to_dispatch)
        self._mark_dirty()
        return result

    def mark_finished(self, handle: TaskHandle, outcome: TaskOutcome) -> None:
        """Record completion for ``handle`` and dispatch queued work.

        Args:
            handle: Handle returned during submission.
            outcome: Result reported by the worker or callback.
        """
        to_dispatch: list[_TaskEntry] = []
        should_log = False
        with self._lock:
            entry = self._tasks.pop(handle.task_id, None)
            if entry is None:
                logger.debug(
                    "Ignoring completion for unknown handle %s", handle.task_id
                )
                return
            self._reschedule_logged.discard(handle.task_id)
            if entry.state == "pending":
                self._remove_pending_locked(handle.task_id, handle.category)
                self._pending_condition.notify_all()
                to_dispatch = self._try_start_tasks_locked()
            else:
                self._decrement_active_locked(handle)
                self._pending_condition.notify_all()
                to_dispatch = self._try_start_tasks_locked()
                should_log = True
        if should_log:
            logger.debug(
                "Task %s finished (success=%s, category=%s)",
                handle.task_id,
                outcome.success,
                handle.category,
            )
        if to_dispatch:
            self._dispatch_main_thread_entries(to_dispatch)
        self._mark_dirty()

    def active_counts(self) -> Mapping[str, int]:
        """Return the number of active tasks per category.

        Returns:
            Dict mapping each category to its active task count.
        """
        with self._lock:
            return dict(self._active_by_category)

    def snapshot(self) -> ExecutorSnapshot:
        """Capture executor metrics for diagnostics surfaces.

        Returns:
            ExecutorSnapshot describing current utilisation and limits.
        """
        with self._lock:
            pending_by_category = dict(self._queued_by_category)
            pending_total = sum(pending_by_category.values())
            normalized_total_limit = self._normalize_pending_limit(
                self._policy.max_pending_total
            )
            if normalized_total_limit:
                pending_total_utilization = (
                    pending_total / normalized_total_limit * 100.0
                )
            else:
                pending_total_utilization = None
            pending_limits = dict(self._policy.pending_limits)
            pending_utilization_by_category_pct: dict[str, float] = {}
            for category, limit in pending_limits.items():
                normalized_limit = self._normalize_pending_limit(limit)
                if not normalized_limit:
                    continue
                count = pending_by_category.get(category, 0)
                pending_utilization_by_category_pct[category] = (
                    count / normalized_limit * 100.0
                )
            wait_samples = self._pending_wait_samples
            if wait_samples:
                average_wait_time_ms = (
                    self._pending_wait_total_seconds / wait_samples * 1000.0
                )
            else:
                average_wait_time_ms = None
            pool_max_threads = self._guard_pool_max_threads_locked()
            snapshot = ExecutorSnapshot(
                name=self._name,
                max_workers=self._policy.max_workers,
                active_total=self._active_total,
                active_by_category=dict(self._active_by_category),
                queued_by_category=pending_by_category,
                pending_total=pending_total,
                max_pending_total=normalized_total_limit,
                pending_limits=pending_limits,
                pending_utilization_total_pct=pending_total_utilization,
                pending_utilization_by_category_pct=pending_utilization_by_category_pct,
                category_limits=dict(self._policy.category_limits),
                device_limits={
                    device: dict(limits)
                    for device, limits in self._policy.device_limits.items()
                },
                pool_max_threads=pool_max_threads,
                rejection_count=self._rejection_count,
                average_wait_time_ms=average_wait_time_ms,
                category_priorities=dict(self._policy.category_priorities),
            )
        return snapshot

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop admitting new tasks and optionally wait for worker completion.

        Args:
            wait: When True, block until the underlying pool reports completion.
        """
        with self._lock:
            pending = list(self._pending_order)
            self._pending_order.clear()
            for task_id in pending:
                entry = self._tasks.pop(task_id, None)
                if entry is None:
                    continue
                self._reschedule_logged.discard(task_id)
                self._queued_by_category[entry.handle.category] = max(
                    0, self._queued_by_category.get(entry.handle.category, 0) - 1
                )
                runnable = entry.runnable
                if hasattr(runnable, "cancel"):
                    try:
                        runnable.cancel()  # type: ignore[call-arg]
                    except Exception:  # pragma: no cover - defensive guard
                        logger.exception(
                            "Error cancelling pending runnable %s", runnable
                        )
            # active tasks continue running; caller controls cancellation
        if wait and hasattr(self._pool, "waitForDone"):
            if self._is_pool_available():
                try:
                    self._pool.waitForDone()
                except Exception:  # pragma: no cover - defensive guard
                    logger.debug("waitForDone failed for %s", self._pool, exc_info=True)
        self._pool_unavailable = True

    def _is_gui_thread(self) -> bool:
        """Return ``True`` when running on the Qt GUI thread."""
        app = QCoreApplication.instance()
        if app is None:
            return False
        try:
            main_thread = app.thread()
        except RuntimeError:
            return False
        if main_thread is None:
            return False
        return QThread.currentThread() == main_thread

    @staticmethod
    def _normalize_pending_limit(limit: int | None) -> int | None:
        """Treat non-positive limits as ``None`` (unbounded)."""
        if limit is None or limit <= 0:
            return None
        return limit

    def _maybe_emit_pending_warnings_locked(
        self,
        *,
        category: str,
        projected_total: int,
        total_limit: int | None,
        projected_category: int,
        category_limit: int | None,
    ) -> None:
        """Log soft warnings when pending queues approach their limits."""
        if total_limit is not None:
            threshold = max(1, int(total_limit * _PENDING_SOFT_LIMIT_RATIO))
            if projected_total >= threshold:
                if not self._pending_total_warned:
                    logger.warning(
                        "Executor %s pending queue nearing capacity: %s/%s tasks queued",
                        self._name,
                        projected_total,
                        total_limit,
                    )
                    self._pending_total_warned = True
            elif self._pending_total_warned and projected_total < threshold:
                self._pending_total_warned = False
        else:
            self._pending_total_warned = False
        if category_limit is not None:
            threshold = max(1, int(category_limit * _PENDING_SOFT_LIMIT_RATIO))
            if projected_category >= threshold:
                if category not in self._pending_category_warned:
                    logger.warning(
                        "Executor %s pending queue for category %s nearing capacity: %s/%s queued",
                        self._name,
                        category,
                        projected_category,
                        category_limit,
                    )
                    self._pending_category_warned.add(category)
            elif (
                category in self._pending_category_warned
                and projected_category < threshold
            ):
                self._pending_category_warned.discard(category)
        else:
            self._pending_category_warned.discard(category)

    def _await_pending_capacity_locked(
        self,
        handle: TaskHandle,
        *,
        is_main_thread: bool,
    ) -> None:
        """Block until pending limits allow queuing ``handle`` (GUI thread rejects)."""
        category = handle.category
        total_limit = self._normalize_pending_limit(self._policy.max_pending_total)
        category_limit = self._normalize_pending_limit(
            self._policy.pending_limits.get(category)
        )
        wait_started: float | None = None
        while True:
            pending_total = len(self._pending_order)
            projected_total = pending_total + 1
            pending_for_category = self._queued_by_category.get(category, 0)
            projected_category = pending_for_category + 1
            self._maybe_emit_pending_warnings_locked(
                category=category,
                projected_total=projected_total,
                total_limit=total_limit,
                projected_category=projected_category,
                category_limit=category_limit,
            )
            limit_type: str | None = None
            limit_value: int | None = None
            if total_limit is not None and projected_total > total_limit:
                limit_type = "total"
                limit_value = total_limit
            elif category_limit is not None and projected_category > category_limit:
                limit_type = "category"
                limit_value = category_limit
            if limit_type is None or limit_value is None:
                break
            if is_main_thread:
                message = (
                    f"Executor {self._name} rejected category {category!r} task because "
                    f"pending {limit_type} limit {limit_value} has been reached"
                )
                self._rejection_count += 1
                raise TaskRejected(
                    message,
                    category=category,
                    device=handle.device,
                    limit_type=limit_type,
                    limit_value=limit_value,
                    pending_total=pending_total,
                    pending_category=pending_for_category,
                )
            if wait_started is None:
                wait_started = monotonic()
            self._pending_condition.wait(timeout=_PENDING_WAIT_INTERVAL)
        if wait_started is not None:
            self._pending_wait_total_seconds += monotonic() - wait_started
            self._pending_wait_samples += 1

    def _guard_pool_max_threads_locked(self) -> int | None:
        """Return the pool thread cap and reapply policy when it drifts."""
        if not self._is_pool_available():
            return None
        if not hasattr(self._pool, "maxThreadCount"):
            return None
        try:
            current = int(self._pool.maxThreadCount())
        except Exception:
            return None
        target = self._policy.max_workers
        if current == target:
            return current
        original = current
        try:
            self._apply_pool_max_threads_locked(target)
            try:
                current = int(self._pool.maxThreadCount())
            except Exception:
                current = original
            if not self._pool_threads_reset_logged:
                logger.info(
                    "Executor %s reapplied pool thread cap (%s -> %s)",
                    self._name,
                    original,
                    target,
                )
                self._pool_threads_reset_logged = True
        except Exception:
            logger.debug("Failed to reapply pool max thread count", exc_info=True)
            current = original
        return current

    def _mark_dirty(self, domain: str = "executor") -> None:
        """Notify diagnostics when executor metrics change."""
        if self._dirty_callback is None:
            return
        try:
            self._dirty_callback(domain)
        except Exception:
            logger.debug("Executor dirty callback failed", exc_info=True)

    def _is_pool_available(self) -> bool:
        """Return True when the underlying pool is still valid."""
        if getattr(self, "_pool_unavailable", False):
            return False
        pool = self._pool
        if pool is None:
            self._pool_unavailable = True
            return False
        try:
            alive = isValid(pool)
        except Exception:
            alive = False
        if not alive:
            self._pool_unavailable = True
        return alive

    def _log_pool_unavailable_once(self) -> None:
        """Warn once when the pool is unavailable and tasks are dropped."""
        if self._pool_unavailable_logged:
            return
        logger.warning(
            "Executor %s thread pool is unavailable; dropping queued work",
            self._name,
        )
        self._pool_unavailable_logged = True

    def _swap_policy(
        self, policy: ThreadPolicy, *, update_pool_threads: bool = False
    ) -> None:
        """Replace the active policy and refresh diagnostics."""
        with self._lock:
            self._policy = policy
            if update_pool_threads:
                self._apply_pool_max_threads_locked(policy.max_workers)
        self._mark_dirty()

    def _dispatch_main_thread_entries(self, entries: list[_TaskEntry]) -> None:
        """Schedule provided entries for main-thread execution."""
        if not entries:
            return
        for entry in entries:
            self._main_thread_invoker.queue(entry.handle.task_id)

    def _queue_entry(
        self,
        handle: TaskHandle,
        entry: _TaskEntry,
        *,
        is_main_thread: bool,
    ) -> None:
        """Register ``entry`` as pending and trigger dispatch attempts."""
        with self._lock:
            self._await_pending_capacity_locked(handle, is_main_thread=is_main_thread)
            self._tasks[handle.task_id] = entry
            self._pending_order.append(handle.task_id)
            self._queued_by_category[handle.category] = (
                self._queued_by_category.get(handle.category, 0) + 1
            )
            to_dispatch = self._try_start_tasks_locked()
        self._dispatch_main_thread_entries(to_dispatch)
        self._mark_dirty()

    def _try_start_tasks_locked(self) -> list[_TaskEntry]:
        """Attempt to activate queued tasks while respecting concurrency limits."""
        if not self._pending_order:
            return []
        rescan: deque[str] = deque()
        to_dispatch: list[_TaskEntry] = []
        while self._pending_order:
            task_id = self._pending_order.popleft()
            entry = self._tasks.get(task_id)
            if entry is None or entry.state != "pending":
                self._reschedule_logged.discard(task_id)
                continue
            can_start, limit_reason = self._can_start_locked(entry)
            if not can_start:
                if limit_reason and task_id not in self._reschedule_logged:
                    handle = entry.handle
                    logger.debug(
                        (
                            "Executor %s rescheduling task %s due to %s limit "
                            "(%s/%s active, category=%s, device=%s)"
                        ),
                        self._name,
                        task_id,
                        limit_reason.kind,
                        limit_reason.current,
                        limit_reason.limit,
                        handle.category,
                        handle.device or "<none>",
                    )
                    self._reschedule_logged.add(task_id)
                rescan.append(task_id)
                continue
            self._reschedule_logged.discard(task_id)
            if entry.callback is not None:
                self._activate_main_thread_locked(entry)
                to_dispatch.append(entry)
            else:
                self._activate_worker_locked(entry)
        self._pending_order = rescan
        return to_dispatch

    def _can_start_locked(self, entry: _TaskEntry) -> tuple[bool, _LimitReason | None]:
        """Return whether ``entry`` can start along with a limit hit reason."""
        handle = entry.handle
        if entry.callback is None and self._active_workers >= self._policy.max_workers:
            return (
                False,
                _LimitReason(
                    kind="max_workers",
                    limit=self._policy.max_workers,
                    current=self._active_workers,
                ),
            )
        category_limit = self._policy.limit_for(handle.category)
        current_category_active = self._active_by_category.get(handle.category, 0)
        if category_limit is not None and current_category_active >= category_limit:
            return (
                False,
                _LimitReason(
                    kind="category",
                    limit=category_limit,
                    current=current_category_active,
                ),
            )
        device_limit = self._policy.device_limit(handle.device, handle.category)
        if device_limit is not None:
            key = (handle.device, handle.category)
            current_device_active = self._active_by_device.get(key, 0)
            if current_device_active >= device_limit:
                return (
                    False,
                    _LimitReason(
                        kind="device",
                        limit=device_limit,
                        current=current_device_active,
                    ),
                )
        return True, None

    def _promote_pending_to_active_locked(self, entry: _TaskEntry) -> None:
        """Move ``entry`` from the pending queue to the active counters."""
        handle = entry.handle
        category = handle.category
        device = handle.device
        self._queued_by_category[category] = max(
            0, self._queued_by_category.get(category, 0) - 1
        )
        if self._queued_by_category.get(category) == 0:
            self._queued_by_category.pop(category, None)
        entry.state = "active"
        self._active_total += 1
        self._active_by_category[category] = (
            self._active_by_category.get(category, 0) + 1
        )
        if device:
            key = (device, category)
            self._active_by_device[key] = self._active_by_device.get(key, 0) + 1

    def _activate_worker_locked(self, entry: _TaskEntry) -> None:
        """Promote ``entry`` and submit its runnable to the Qt thread pool."""
        handle = entry.handle
        category = handle.category
        self._promote_pending_to_active_locked(entry)
        self._active_workers += 1
        self._active_worker_ids.add(handle.task_id)
        runnable = entry.runnable
        if runnable is None:
            logger.error("Runnable missing for worker task %s", handle.task_id)
            self._cleanup_activation_failure_locked(entry)
            return
        # Recompute priority at activation time using the current policy.
        priority = self._policy.priority_for(category)
        if not self._is_pool_available():
            self._log_pool_unavailable_once()
            self._cleanup_activation_failure_locked(entry)
            return
        try:
            self._pool.start(runnable, priority)
        except TypeError:
            try:
                self._pool.start(runnable)
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("Failed to start runnable %s", runnable)
                self._cleanup_activation_failure_locked(entry)
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("Failed to start runnable %s", runnable)
            self._cleanup_activation_failure_locked(entry)

    def _activate_main_thread_locked(self, entry: _TaskEntry) -> None:
        """Promote ``entry`` and keep it queued for main-thread delivery."""
        self._promote_pending_to_active_locked(entry)

    def _execute_main_thread_callback(self, task_id: str) -> None:
        """Invoke the recorded callback for ``task_id`` and mark it finished."""
        callback: Callable[[], None] | None = None
        handle: TaskHandle | None = None
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None or entry.callback is None:
                logger.warning(
                    "Main-thread task %s missing callback; dropping invocation",
                    task_id,
                )
                return
            callback = entry.callback
            handle = entry.handle
        outcome = TaskOutcome(success=True)
        try:
            callback()
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Main-thread task %s failed", task_id)
            outcome = TaskOutcome(success=False, error=exc, payload=str(exc))
        if handle is not None:
            self.mark_finished(handle, outcome)

    def _remove_pending_locked(self, task_id: str, category: str) -> None:
        """Remove ``task_id`` from pending order and update queue counters."""
        try:
            self._pending_order.remove(task_id)
        except ValueError:
            pass
        self._reschedule_logged.discard(task_id)
        self._queued_by_category[category] = max(
            0, self._queued_by_category.get(category, 0) - 1
        )
        if self._queued_by_category.get(category) == 0:
            self._queued_by_category.pop(category, None)

    def _decrement_active_locked(self, handle: TaskHandle) -> None:
        """Decrement active counters for ``handle`` after completion/failure."""
        category = handle.category
        device = handle.device
        self._active_total = max(0, self._active_total - 1)
        if handle.task_id in self._active_worker_ids:
            self._active_worker_ids.discard(handle.task_id)
            self._active_workers = max(0, self._active_workers - 1)
        if category in self._active_by_category:
            next_value = self._active_by_category[category] - 1
            if next_value <= 0:
                self._active_by_category.pop(category, None)
            else:
                self._active_by_category[category] = next_value
        if device:
            key = (device, category)
            if key in self._active_by_device:
                next_value = self._active_by_device[key] - 1
                if next_value <= 0:
                    self._active_by_device.pop(key, None)
                else:
                    self._active_by_device[key] = next_value

    def _cleanup_activation_failure_locked(self, entry: _TaskEntry) -> None:
        """Remove failed activations from bookkeeping and wake pending waiters."""
        handle = entry.handle
        self._tasks.pop(handle.task_id, None)
        self._reschedule_logged.discard(handle.task_id)
        self._decrement_active_locked(handle)
        self._pending_condition.notify_all()

    @staticmethod
    def _describe_pool(pool: QThreadPool) -> str:
        """Return a readable descriptor for ``pool`` useful in logging."""
        base = getattr(pool, "objectName", None)
        if callable(base):  # PySide exposes objectName() callable
            try:
                name = pool.objectName()
            except Exception:  # pragma: no cover - defensive guard
                name = None
        else:
            name = base
        identifier = hex(id(pool))
        return name or f"QThreadPool@{identifier}"

    def _apply_pool_max_threads_locked(self, max_workers: int | None = None) -> None:
        """Apply the configured worker cap to the underlying pool."""
        if not self._is_pool_available():
            return
        target = self._policy.max_workers if max_workers is None else max_workers
        if hasattr(self._pool, "setMaxThreadCount"):
            try:
                self._pool.setMaxThreadCount(target)
            except Exception:  # pragma: no cover - defensive guard
                logger.debug(
                    "setMaxThreadCount failed for %s", self._pool, exc_info=True
                )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        """Return a concise summary of executor state for debugging."""
        snapshot = self.snapshot()
        return (
            f"<QThreadPoolExecutor name={snapshot.name!r} active={snapshot.active_total} "
            f"queued={snapshot.pending_total}>"
        )
