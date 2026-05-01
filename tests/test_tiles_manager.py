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

"""Tests covering TileManager eviction via low-priority executor tasks."""

from __future__ import annotations

import time
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from qpane.concurrency import (
    ExecutorSnapshot,
    TaskExecutorProtocol,
    TaskHandle,
    TaskOutcome,
)
from qpane import Config, QPane
from qpane.rendering import Tile, TileManager
from qpane.rendering.tiles import _SourceTilePayloadKey
from qpane.scene.identity import (
    SceneLayerAssetKey,
    SceneLayerTileKey,
    default_catalog_asset_key,
)
from tests.helpers.render_plan import make_tile_key
from tests.helpers.executor_stubs import RejectingStubExecutor, StubExecutor

MB = 1024 * 1024


class ImmediateStubExecutor(TaskExecutorProtocol):
    """Test executor that records submissions and executes runnables on demand."""

    def __init__(self) -> None:
        self._submissions: List[Dict[str, Any]] = []
        self.cancelled: list[TaskHandle] = []
        self.finished: list[tuple[TaskHandle, TaskOutcome]] = []
        self.shutdown_called = False
        self._counter = 0

    def submit(
        self, runnable, category: str, *, device: str | None = None
    ) -> TaskHandle:
        self._counter += 1
        handle = TaskHandle(
            task_id=f"stub-{self._counter}", category=category, device=device
        )
        if hasattr(runnable, "bind_executor"):
            runnable.bind_executor(self, handle)
        self._submissions.append(
            {
                "handle": handle,
                "runnable": runnable,
                "category": category,
            }
        )
        return handle

    def cancel(self, handle: TaskHandle) -> bool:
        self.cancelled.append(handle)
        for entry in list(self._submissions):
            if entry["handle"].task_id == handle.task_id:
                self._submissions.remove(entry)
        return True

    def mark_finished(self, handle: TaskHandle, outcome: TaskOutcome) -> None:
        self.finished.append((handle, outcome))

    def active_counts(self) -> Dict[str, int]:
        return {}

    def snapshot(self) -> ExecutorSnapshot:
        pending_total = len(self._submissions)
        return ExecutorSnapshot(
            name="stub",
            max_workers=1,
            active_total=0,
            active_by_category={},
            queued_by_category={},
            pending_total=pending_total,
            max_pending_total=None,
            pending_limits={},
            pending_utilization_total_pct=None,
            pending_utilization_by_category_pct={},
            category_limits={},
            device_limits={},
        )

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_called = True

    # Helpers for tests -------------------------------------------------

    def pending_categories(self) -> List[str]:
        """Return the categories of currently queued runnables."""
        return [entry["category"] for entry in self._submissions]

    def drain_category(self, category: str) -> None:
        """Execute all pending tasks for ``category`` immediately."""
        for entry in list(self._submissions):
            if entry["category"] != category:
                continue
            self._submissions.remove(entry)
            entry["runnable"].run()


def _make_tile(key: SceneLayerTileKey, *, side: int = 1024) -> Tile:
    """Create a tile with predictable memory usage for testing."""
    image = QImage(side, side, QImage.Format_ARGB32)
    image.fill(0)
    return Tile(key=key, image=image)


def test_tile_guard_rejects_oversized_item(caplog: pytest.LogCaptureFixture) -> None:
    """Tiles exceeding the hard cache budget should be refused and logged once."""
    config = Config(
        cache={
            "mode": "hard",
            "budget_mb": 1,
            "weights": {"tiles": 1, "pyramids": 0, "masks": 0, "predictors": 0},
        },
    )
    executor = StubExecutor()
    manager = TileManager(config=config, executor=executor)
    image_id = uuid.uuid4()
    tile = _make_tile(make_tile_key(image_id, Path("oversize.png"), 1.0, 0, 0))
    with caplog.at_level(logging.WARNING):
        manager.add_tile(tile)
        manager.add_tile(tile)
    assert manager.cache_usage_bytes == 0
    assert list(executor.pending_tasks()) == []
    warnings = [record for record in caplog.records if "not cached" in record.message]
    assert len(warnings) == 1


@pytest.mark.usefixtures("qapp")
def test_tile_guard_blocks_when_hard_cap_already_over_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Admission guard should reject tiles that would push the hard cap over budget."""
    qpane_widget = QPane(features=())
    try:
        qpane_widget.applySettings(
            cache={
                "mode": "hard",
                "budget_mb": 1,
                "weights": {"tiles": 1, "pyramids": 0, "masks": 0, "predictors": 0},
            }
        )
        coordinator = qpane_widget.cacheCoordinator
        assert coordinator is not None
        tile_manager = qpane_widget.view().tile_manager
        coordinator.update_usage("tiles", int(0.9 * MB))
        image_id = uuid.uuid4()
        small_tile = _make_tile(
            make_tile_key(image_id, Path("over-cap.png"), 1.0, 0, 0), side=256
        )
        guard_result = coordinator.should_admit(small_tile.size_bytes)
        assert (
            guard_result is False
        ), "Expected hard-cap guard to reject over-budget admission"
        tile_manager.set_admission_guard(lambda size: guard_result)
        with caplog.at_level(logging.WARNING):
            tile_manager.add_tile(small_tile)
            tile_manager.add_tile(small_tile)
        assert tile_manager.cache_usage_bytes == 0
        warnings = [
            record for record in caplog.records if "not cached" in record.message
        ]
        assert len(warnings) == 1
    finally:
        qpane_widget.deleteLater()


def test_tile_manager_eviction_uses_maintenance_executor_lane(
    qapp: QApplication,
) -> None:
    """Eviction should run via the shared executor's maintenance category."""
    config = Config(cache={"tiles": {"mb": 1}})
    executor = ImmediateStubExecutor()
    manager = TileManager(config=config, executor=executor)
    image_id = uuid.uuid4()
    tile = _make_tile(make_tile_key(image_id, Path("a.png"), 1.0, 0, 0), side=512)
    overflow = _make_tile(
        make_tile_key(image_id, Path("a-over.png"), 1.0, 0, 1), side=512
    )
    manager.add_tile(tile)
    manager.add_tile(overflow)
    assert executor.pending_categories() == ["maintenance"]
    executor.drain_category("maintenance")
    qapp.processEvents()
    assert manager.cache_usage_bytes <= manager.cache_limit_bytes
    assert len(executor.finished) == 1


def test_tile_manager_coalesces_pending_eviction_requests(
    qapp: QApplication,
) -> None:
    """Multiple overflow events should reuse the existing maintenance task."""
    config = Config(cache={"tiles": {"mb": 1}})
    executor = ImmediateStubExecutor()
    manager = TileManager(config=config, executor=executor)
    image_id = uuid.uuid4()
    first_tile = _make_tile(make_tile_key(image_id, Path("b.png"), 1.0, 0, 0), side=512)
    second_tile = _make_tile(
        make_tile_key(image_id, Path("c.png"), 1.0, 0, 1), side=512
    )
    manager.add_tile(first_tile)
    manager.add_tile(second_tile)
    assert executor.pending_categories() == ["maintenance"]
    executor.drain_category("maintenance")
    qapp.processEvents()
    assert manager.cache_usage_bytes <= manager.cache_limit_bytes
    assert len(executor.finished) == 1


def test_tile_manager_recovers_after_budget_increase(
    qapp: QApplication,
) -> None:
    """Dropping and then restoring the tile cache budget should recover caching."""
    low_config = Config(cache={"tiles": {"mb": 1}})
    executor = ImmediateStubExecutor()
    manager = TileManager(config=low_config, executor=executor)
    image_id = uuid.uuid4()
    tile = _make_tile(make_tile_key(image_id, Path("d.png"), 1.0, 0, 0), side=512)
    overflow = _make_tile(make_tile_key(image_id, Path("e.png"), 1.0, 0, 1), side=512)
    manager.add_tile(tile)
    manager.add_tile(overflow)
    assert executor.pending_categories() == ["maintenance"]
    executor.drain_category("maintenance")
    qapp.processEvents()
    assert manager.cache_usage_bytes <= manager.cache_limit_bytes
    assert not executor.pending_categories()
    manager.clear_caches()
    restored = Config(cache={"tiles": {"mb": 8}})
    manager.apply_config(restored)
    assert manager.cache_limit_bytes == 8 * 1024 * 1024
    manager.add_tile(tile)
    manager.add_tile(overflow)
    qapp.processEvents()
    assert manager.cache_usage_bytes == tile.size_bytes + overflow.size_bytes
    assert executor.pending_categories() == []


@pytest.mark.usefixtures("qapp")
def test_get_tile_queues_executor_and_populates_cache(qapp) -> None:
    """TileManager should submit tile tasks through the shared executor."""
    config = Config(cache={"tiles": {"mb": 8}})
    executor = StubExecutor()
    manager = TileManager(config=config, executor=executor)
    source_image = QImage(128, 128, QImage.Format_ARGB32)
    source_image.fill(0)
    image_id = uuid.uuid4()
    identifier = make_tile_key(image_id, Path("z.png"), 1.0, 0, 0)
    result = manager.get_tile(identifier, source_image)
    assert result is None
    pending = list(executor.pending_tasks())
    assert pending and pending[0].handle.category == "tiles"
    executor.run_task(pending[0].handle.task_id)
    qapp.processEvents()
    cached = manager.get_tile(identifier, source_image)
    assert cached is not None


@pytest.mark.usefixtures("qapp")
def test_get_tile_reuses_source_payload_across_layer_keys(qapp) -> None:
    """Layer-specific tile keys sharing a source tile should generate once."""
    config = Config(cache={"tiles": {"mb": 8}})
    executor = StubExecutor()
    manager = TileManager(config=config, executor=executor)
    source_image = QImage(128, 128, QImage.Format_ARGB32)
    source_image.fill(0)
    source_id = uuid.uuid4()
    source_key = default_catalog_asset_key(
        source_id,
        revision=0,
        source_path=Path("shared.png"),
    )
    first_key = make_tile_key(source_id, Path("shared.png"), 1.0, 0, 0)
    second_asset_key = SceneLayerAssetKey(
        scene_id=uuid.uuid4(),
        layer_id=uuid.uuid4(),
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=0,
        source_path=Path("shared.png"),
    )
    second_key = SceneLayerTileKey(
        asset_key=second_asset_key,
        pyramid_asset_key=source_key,
        pyramid_scale=1.0,
        row=0,
        col=0,
    )
    ready: list[SceneLayerTileKey] = []
    manager.tileReady.connect(ready.append)

    assert manager.get_tile(first_key, source_image) is None
    assert manager.get_tile(second_key, source_image) is None
    assert len(list(executor.pending_tasks())) == 1

    pending = list(executor.pending_tasks())
    executor.run_task(pending[0].handle.task_id)
    qapp.processEvents()

    payload_key = _SourceTilePayloadKey.from_layer_key(first_key)
    assert payload_key in manager._tile_cache
    assert set(ready) == {first_key, second_key}
    assert manager.get_tile(second_key, source_image) is not None


@pytest.mark.usefixtures("qapp")
def test_remove_tiles_for_asset_keeps_shared_source_worker(qapp) -> None:
    """Removing one layer asset should keep a shared source worker for other layers."""
    config = Config(cache={"tiles": {"mb": 8}})
    executor = StubExecutor()
    manager = TileManager(config=config, executor=executor)
    source_image = QImage(128, 128, QImage.Format_ARGB32)
    source_image.fill(0)
    source_id = uuid.uuid4()
    source_key = default_catalog_asset_key(
        source_id,
        revision=0,
        source_path=Path("shared-worker.png"),
    )
    first_key = make_tile_key(source_id, Path("shared-worker.png"), 1.0, 0, 0)
    second_asset_key = SceneLayerAssetKey(
        scene_id=uuid.uuid4(),
        layer_id=uuid.uuid4(),
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=0,
        source_path=Path("shared-worker.png"),
    )
    second_key = SceneLayerTileKey(
        asset_key=second_asset_key,
        pyramid_asset_key=source_key,
        pyramid_scale=1.0,
        row=0,
        col=0,
    )
    ready: list[SceneLayerTileKey] = []
    manager.tileReady.connect(ready.append)

    manager.get_tile(first_key, source_image)
    manager.get_tile(second_key, source_image)
    manager.remove_tiles_for_asset(first_key.asset_key)

    assert len(list(executor.pending_tasks())) == 1
    executor.run_task(list(executor.pending_tasks())[0].handle.task_id)
    qapp.processEvents()

    assert ready == [second_key]
    payload_key = _SourceTilePayloadKey.from_layer_key(second_key)
    assert manager._payload_layer_keys[payload_key] == {second_key}


@pytest.mark.usefixtures("qapp")
def test_tile_manager_shutdown_waits_when_owning_executor() -> None:
    """wait=True should trigger executor.shutdown when the manager owns it."""
    executor = StubExecutor()
    manager = TileManager(
        config=Config(cache={"tiles": {"mb": 8}}),
        executor=executor,
        owns_executor=True,
    )
    manager.shutdown(wait=True)
    assert executor.shutdown_called is True


@pytest.mark.usefixtures("qapp")
def test_tile_manager_shutdown_does_not_stop_shared_executor() -> None:
    """wait=True should not tear down shared executors."""
    executor = StubExecutor()
    manager = TileManager(config=Config(), executor=executor)
    manager.shutdown(wait=True)
    assert executor.shutdown_called is False


@pytest.mark.usefixtures("qapp")
def test_tile_manager_retries_after_throttle(qapp) -> None:
    """TileManager should retry submissions that were initially throttled."""
    config = Config(cache={"tiles": {"mb": 8}})
    executor = RejectingStubExecutor(reject_counts={"tiles": 1})
    manager = TileManager(config=config, executor=executor)
    source_image = QImage(128, 128, QImage.Format_ARGB32)
    source_image.fill(0)
    image_id = uuid.uuid4()
    identifier = make_tile_key(image_id, Path("retry.png"), 1.0, 0, 0)
    throttled: list[tuple[SceneLayerTileKey, int]] = []
    manager.tilesThrottled.connect(
        lambda ident, attempt: throttled.append((ident, attempt))
    )
    assert manager.get_tile(identifier, source_image) is None
    assert throttled == [(identifier, 1)]
    assert executor.rejections

    def wait_for(predicate, *, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qapp.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition was not met before timeout")

    wait_for(
        lambda: any(
            record.handle.category == "tiles" for record in executor.pending_tasks()
        )
    )
    pending = list(executor.pending_tasks())
    assert pending
    executor.run_task(pending[0].handle.task_id)
    payload_key = _SourceTilePayloadKey.from_layer_key(identifier)
    wait_for(lambda: payload_key in manager._tile_cache)
    cached = manager.get_tile(identifier, source_image)
    assert cached is not None
    assert manager.pending_retry_tiles() == []


def test_prefetch_tiles_counts_cache_hits(qapp):
    manager = TileManager(config=Config(), executor=StubExecutor())
    source_image = QImage(64, 64, QImage.Format_ARGB32)
    image_id = uuid.uuid4()
    identifier = make_tile_key(image_id, Path("cache-hit.png"), 1.0, 0, 0)
    tile = Tile(key=identifier, image=source_image.copy())
    manager.add_tile(tile)
    scheduled = manager.prefetch_tiles([identifier], source_image)
    assert scheduled == []
    metrics = manager.snapshot_metrics()
    assert metrics.prefetch_completed >= 1
    assert metrics.prefetch_requested == 0


@pytest.mark.usefixtures("qapp")
def test_prefetch_tiles_handles_throttled_requests(qapp):
    config = Config(cache={"tiles": {"mb": 8}})
    executor = RejectingStubExecutor(reject_counts={"tiles": 1})
    manager = TileManager(config=config, executor=executor)
    source_image = QImage(64, 64, QImage.Format_ARGB32)
    source_image.fill(0)
    image_id = uuid.uuid4()
    identifier = make_tile_key(image_id, Path("throttled.png"), 1.0, 0, 0)
    scheduled = manager.prefetch_tiles([identifier], source_image)
    assert scheduled == [identifier]
    assert manager._prefetch_pending(identifier)
    assert identifier not in manager._worker_state
    assert identifier in manager.pending_retry_tiles()
    cancelled = manager.cancel_prefetch([identifier])
    assert cancelled == [identifier]
    assert not manager._prefetch_pending(identifier)
    assert identifier not in manager.pending_retry_tiles()


@pytest.mark.usefixtures("qapp")
def test_remove_tiles_for_asset_cancels_matching_pending_retries(qapp):
    config = Config(cache={"tiles": {"mb": 8}})
    executor = RejectingStubExecutor(reject_counts={"tiles": 1})
    manager = TileManager(config=config, executor=executor)
    source_image = QImage(64, 64, QImage.Format_ARGB32)
    source_image.fill(0)
    image_id = uuid.uuid4()
    identifier = make_tile_key(image_id, Path("retry-evict.png"), 1.0, 0, 0)
    scheduled = manager.prefetch_tiles([identifier], source_image)
    assert scheduled == [identifier]
    assert identifier in manager.pending_retry_tiles()
    assert manager._prefetch_pending(identifier)
    manager.remove_tiles_for_asset(identifier.asset_key)
    assert identifier not in manager.pending_retry_tiles()
    assert not manager._prefetch_pending(identifier)
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert list(executor.pending_tasks()) == []


class _DummyExecutor:
    def __init__(self) -> None:
        self.cancelled = []

    def submit(
        self, runnable, category: str, *, device: str | None = None
    ):  # pragma: no cover - unused
        raise NotImplementedError

    def cancel(self, handle):
        self.cancelled.append(handle)
        return True

    def shutdown(self, wait: bool = True):  # pragma: no cover - unused
        return None


class _DummyWorker:
    def cancel(self) -> None:
        return None


def test_cancel_prefetch_updates_metrics(qapp):
    manager = TileManager(config=Config(), executor=StubExecutor())
    manager._executor = _DummyExecutor()
    image_id = uuid.uuid4()
    identifier = make_tile_key(image_id, Path("prefetch-cancel.png"), 1.0, 0, 0)
    manager._worker_state[identifier] = {
        "worker": _DummyWorker(),
        "handle": object(),
    }
    manager._prefetch_begin(identifier)
    cancelled = manager.cancel_prefetch([identifier])
    assert cancelled == [identifier]
    metrics = manager.snapshot_metrics()
    assert metrics.prefetch_failed >= 1
    assert not manager._prefetch_pending(identifier)
    assert identifier not in manager._worker_state


def test_remove_tiles_for_asset_purges_matching_cache_and_workers(qapp):
    manager = TileManager(
        config=Config(cache={"tiles": {"mb": 8}}), executor=StubExecutor()
    )
    manager._executor = _DummyExecutor()
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first_cached = make_tile_key(first_id, Path("first.png"), 1.0, 0, 0)
    second_cached = make_tile_key(second_id, Path("second.png"), 1.0, 0, 0)
    first_worker = make_tile_key(first_id, Path("first.png"), 1.0, 0, 1)
    first_tile = _make_tile(first_cached, side=16)
    second_tile = _make_tile(second_cached, side=16)
    manager.add_tile(first_tile)
    manager.add_tile(second_tile)
    manager._worker_state[first_worker] = {
        "worker": _DummyWorker(),
        "handle": object(),
    }
    manager._prefetch_begin(first_worker)
    manager.remove_tiles_for_asset(first_cached.asset_key)
    assert _SourceTilePayloadKey.from_layer_key(first_cached) not in manager._tile_cache
    assert _SourceTilePayloadKey.from_layer_key(second_cached) in manager._tile_cache
    assert manager._cache_size_bytes == second_tile.size_bytes
    assert first_worker not in manager._worker_state
    assert not manager._prefetch_pending(first_worker)


def test_remove_tiles_for_source_asset_purges_derived_layer_tiles(qapp):
    """Source eviction should remove every layer tile generated from that source."""
    manager = TileManager(
        config=Config(cache={"tiles": {"mb": 8}}), executor=StubExecutor()
    )
    source_id = uuid.uuid4()
    first_layer_id = uuid.uuid4()
    second_layer_id = uuid.uuid4()
    source_key = default_catalog_asset_key(
        source_id,
        revision=1,
        source_path=Path("source.png"),
    )
    first_render_key = SceneLayerAssetKey(
        scene_id=uuid.uuid4(),
        layer_id=first_layer_id,
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=1,
        source_path=Path("source.png"),
    )
    second_render_key = SceneLayerAssetKey(
        scene_id=uuid.uuid4(),
        layer_id=second_layer_id,
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=1,
        source_path=Path("source.png"),
    )
    other_key = make_tile_key(uuid.uuid4(), Path("other.png"), 1.0, 0, 0)
    first_tile_key = SceneLayerTileKey(
        asset_key=first_render_key,
        pyramid_asset_key=source_key,
        pyramid_scale=1.0,
        row=0,
        col=0,
    )
    second_tile_key = SceneLayerTileKey(
        asset_key=second_render_key,
        pyramid_asset_key=source_key,
        pyramid_scale=1.0,
        row=0,
        col=1,
    )
    first_tile = _make_tile(first_tile_key, side=16)
    second_tile = _make_tile(second_tile_key, side=16)
    other_tile = _make_tile(other_key, side=16)

    manager.add_tile(first_tile)
    manager.add_tile(second_tile)
    manager.add_tile(other_tile)
    manager.remove_tiles_for_source_asset(source_key)

    assert (
        _SourceTilePayloadKey.from_layer_key(first_tile_key) not in manager._tile_cache
    )
    assert (
        _SourceTilePayloadKey.from_layer_key(second_tile_key) not in manager._tile_cache
    )
    assert _SourceTilePayloadKey.from_layer_key(other_key) in manager._tile_cache
    assert manager._cache_size_bytes == other_tile.size_bytes
