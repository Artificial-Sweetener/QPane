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

"""Tests for the Qt-based executor and worker scaffolding."""

from __future__ import annotations
import threading
import pytest
from PySide6.QtCore import QRunnable, QThreadPool
from qpane.concurrency import QThreadPoolExecutor, TaskHandle, TaskOutcome, ThreadPolicy
from qpane.concurrency.base_worker import BaseWorker
from types import SimpleNamespace

from qpane.concurrency.metrics import (
    executor_diagnostics_provider,
    gather_executor_snapshot,
    retry_summary_provider,
)
from qpane.concurrency.executor import ExecutorSnapshot, _TaskEntry
from tests.helpers.executor_stubs import CallableRunnable, StubExecutor


class _CompletionWorker(QRunnable, BaseWorker):
    """Worker that immediately reports success when scheduled."""

    def __init__(self) -> None:
        QRunnable.__init__(self)
        BaseWorker.__init__(self)
        self.ran = False

    def run(self) -> None:
        self.ran = True
        self.emit_finished(True, payload="ok")


class _BlockingWorker(QRunnable, BaseWorker):
    """Worker that waits on an event before reporting completion."""

    def __init__(self, release: threading.Event, started: threading.Event) -> None:
        QRunnable.__init__(self)
        BaseWorker.__init__(self)
        self._release = release
        self._started = started

    def run(self) -> None:
        self._started.set()
        self._release.wait()
        if self.is_cancelled:
            return
        self.emit_finished(True)


class _RecordingWorker(QRunnable, BaseWorker):
    """Worker that captures completion metadata for assertions."""

    def __init__(self) -> None:
        QRunnable.__init__(self)
        BaseWorker.__init__(self)
        self.outcomes: list[TaskOutcome] = []

    def run(self) -> None:
        self.emit_finished(True, payload="completed")


@pytest.mark.usefixtures("qapp")
class TestQThreadPoolExecutor:
    """Tests covering behaviour of the QThreadPoolExecutor implementation."""

    def test_runs_workers_and_tracks_completion(self) -> None:
        """Submitting a worker should execute it and clear executor bookkeeping."""
        pool = QThreadPool()
        executor = QThreadPoolExecutor(pool=pool, name="test-pool")
        worker = _CompletionWorker()
        handle = executor.submit(worker, category="tiles")
        pool.waitForDone()
        snapshot = executor.snapshot()
        assert worker.ran is True
        assert snapshot.active_total == 0
        assert snapshot.pending_total == 0
        assert executor.cancel(handle) is False
        executor.shutdown()

    def test_cancel_pending_task(self) -> None:
        """Pending tasks should be cancellable while another worker is active."""
        pool = QThreadPool()
        pool.setMaxThreadCount(1)
        executor = QThreadPoolExecutor(pool=pool, name="blocking")
        release = threading.Event()
        started = threading.Event()
        blocking_worker = _BlockingWorker(release=release, started=started)
        executor.submit(blocking_worker, category="tiles")
        assert started.wait(timeout=5), "Blocking worker failed to start"
        follower = _CompletionWorker()
        pending_handle = executor.submit(follower, category="tiles")
        cancelled = executor.cancel(pending_handle)
        assert cancelled is True
        release.set()
        pool.waitForDone()
        snapshot = executor.snapshot()
        assert snapshot.active_total == 0
        assert snapshot.pending_total == 0
        assert executor.cancel(pending_handle) is False
        executor.shutdown()

    def test_drops_tasks_when_pool_deleted(self, qapp, caplog) -> None:
        """Workers should be dropped quietly when the pool has been deleted."""
        pool = QThreadPool(qapp)
        executor = QThreadPoolExecutor(pool=pool, name="dead-pool")
        pool.deleteLater()
        qapp.processEvents()
        executor._pool_unavailable = True
        executor._pool_unavailable_logged = False
        worker = _CompletionWorker()
        with caplog.at_level("WARNING"):
            handle = executor.submit(worker, category="tiles")
        snapshot = executor.snapshot()
        assert snapshot.active_total == 0
        assert snapshot.pending_total == 0
        assert executor.cancel(handle) is False
        assert any(
            "thread pool is unavailable" in record.message for record in caplog.records
        )
        executor.shutdown()

    def test_dispatch_to_main_thread_marks_completion(self, qapp) -> None:
        """Main-thread callbacks should run and report completion."""
        pool = QThreadPool()
        executor = QThreadPoolExecutor(pool=pool, name="main-dispatch")
        calls: list[str] = []
        handle = executor.dispatch_to_main_thread(
            lambda: calls.append("ran"), category="maintenance"
        )
        for _ in range(10):
            qapp.processEvents()
        snapshot = executor.snapshot()
        assert calls == ["ran"]
        assert snapshot.pending_total == 0
        assert snapshot.active_total == 0
        assert executor.cancel(handle) is False
        executor.shutdown()

    def test_callbacks_do_not_block_worker_capacity(self, qapp) -> None:
        """GUI callbacks should not count against worker pool limits."""
        pool = QThreadPool()
        pool.setMaxThreadCount(1)
        executor = QThreadPoolExecutor(pool=pool, name="main-callbacks")
        for _ in range(3):
            executor.dispatch_to_main_thread(lambda: None, category="main")
        release = threading.Event()
        started = threading.Event()
        worker = _BlockingWorker(release=release, started=started)
        executor.submit(worker, category="tiles")
        assert started.wait(
            timeout=2
        ), "Worker should start even when callbacks are active"
        release.set()
        pool.waitForDone()
        for _ in range(10):
            qapp.processEvents()
        snapshot = executor.snapshot()
        assert snapshot.active_total == 0

    def test_max_worker_limit_reports_worker_count(self, qapp) -> None:
        """Max-worker limit diagnostics should reflect worker-only counts."""
        pool = QThreadPool()
        policy = ThreadPolicy(max_workers=2)
        executor = QThreadPoolExecutor(policy=policy, pool=pool, name="limit-report")
        entry = _TaskEntry(
            handle=TaskHandle("task-1", category="tiles"),
            runnable=CallableRunnable(lambda: None),
            priority=0,
            state="pending",
            callback=None,
        )
        with executor._lock:
            executor._active_workers = 2
            executor._active_total = 5  # includes callbacks
        can_start, reason = executor._can_start_locked(entry)
        assert can_start is False
        assert reason is not None
        assert reason.kind == "max_workers"
        assert reason.current == 2
        assert reason.limit == policy.max_workers

    def test_cancel_marks_dirty_once(self) -> None:
        """Cancelling a pending task should notify diagnostics once."""
        pool = QThreadPool()
        pool.setMaxThreadCount(1)
        executor = QThreadPoolExecutor(pool=pool, name="dirty-cancel")
        dirty_calls: list[str] = []
        executor.set_dirty_callback(lambda domain: dirty_calls.append(domain))
        release = threading.Event()
        started = threading.Event()
        blocker = _BlockingWorker(release=release, started=started)
        executor.submit(blocker, category="tiles")
        assert started.wait(timeout=5), "Blocking worker failed to start"
        follower = _CompletionWorker()
        handle = executor.submit(follower, category="tiles")
        before = len(dirty_calls)
        cancelled = executor.cancel(handle)
        assert cancelled is True
        assert len(dirty_calls) == before + 1
        release.set()
        pool.waitForDone()
        executor.shutdown()


class TestBaseWorkerIntegration:
    """Tests for BaseWorker integration with the stub executor."""

    def test_emit_finished_records_outcome(self) -> None:
        """BaseWorker.emit_finished should notify the bound executor."""
        executor = StubExecutor()
        worker = _RecordingWorker()
        handle = executor.submit(worker, category="sam")
        executor.run_task(handle.task_id)
        assert executor.finished, "Worker completion should be recorded"
        recorded_handle, outcome = executor.finished[-1]
        assert recorded_handle == handle
        assert outcome.success is True
        assert outcome.payload == "completed"


class TestExecutorDiagnostics:
    """Tests for executor diagnostics helpers."""

    def test_executor_diagnostics_provider_reports_counts(self) -> None:
        """executor_diagnostics_provider should expose queued categories."""
        executor = StubExecutor()
        executor.submit(CallableRunnable(lambda: None), category="tiles")
        executor.submit(CallableRunnable(lambda: None), category="sam")
        snapshot = gather_executor_snapshot(executor)
        assert snapshot.pending_total == 2
        records = list(executor_diagnostics_provider(executor))
        labels = {record.label for record in records}
        assert {"Executor|Threads", "Executor|Queued"}.issubset(labels)
        queued_record = next(
            record for record in records if record.label == "Executor|Queued"
        )
        assert "tiles" in queued_record.value and "sam" in queued_record.value

    def test_executor_diagnostics_provider_includes_limits(self) -> None:
        """Queued summaries should include category and device limits when set."""

        class _SnapshotExecutor:
            def snapshot(self) -> ExecutorSnapshot:
                return ExecutorSnapshot(
                    name="limits",
                    max_workers=4,
                    active_total=1,
                    active_by_category={"tiles": 1},
                    queued_by_category={"tiles": 2, "sam": 1},
                    pending_total=3,
                    max_pending_total=6,
                    pending_limits={"tiles": 4},
                    pending_utilization_total_pct=50.0,
                    pending_utilization_by_category_pct={"tiles": 50.0},
                    category_limits={"sam": 2},
                    device_limits={"cpu": {"sam": 1}},
                    pool_max_threads=8,
                    average_wait_time_ms=12.0,
                    rejection_count=0,
                )

        records = list(executor_diagnostics_provider(_SnapshotExecutor()))
        labels = {record.label for record in records}
        assert "Executor|Category Limits" in labels
        assert "Executor|Device Limits" in labels
        queued = next(record for record in records if record.label == "Executor|Queued")
        assert "tiles:2/4" in queued.value

    def test_retry_summary_provider_skips_missing_managers(self) -> None:
        """Retry summaries should be empty when no managers expose snapshots."""
        qpane = SimpleNamespace(
            view=lambda: None,
            catalog=lambda: None,
            autosaveManager=lambda: None,
            samManager=lambda: None,
        )
        assert tuple(retry_summary_provider(qpane)) == ()

    def test_retry_summary_provider_formats_first_two_categories(self) -> None:
        """Retry summaries should include tiles and pyramid categories first."""

        def _snapshot(category: str, active: int, total: int, peak: int | None):
            return SimpleNamespace(
                categories={
                    category: SimpleNamespace(
                        active=active,
                        total_scheduled=total,
                        peak_active=peak,
                    )
                }
            )

        qpane = SimpleNamespace(
            view=lambda: SimpleNamespace(
                tile_manager=SimpleNamespace(
                    retrySnapshot=lambda: _snapshot("tiles", 2, 5, 3)
                )
            ),
            catalog=lambda: SimpleNamespace(
                pyramidManager=lambda: SimpleNamespace(
                    retrySnapshot=lambda: _snapshot("pyramid", 1, 4, None)
                )
            ),
            autosaveManager=lambda: SimpleNamespace(
                retrySnapshot=lambda: _snapshot("autosave", 1, 2, None)
            ),
            samManager=lambda: SimpleNamespace(
                retrySnapshot=lambda: _snapshot("sam", 3, 6, 4)
            ),
        )
        records = tuple(retry_summary_provider(qpane))
        assert records
        assert records[0].label == "Retry|Summary"
        assert "tiles:2/5" in records[0].value
        assert "pyramid:1/4" in records[0].value
