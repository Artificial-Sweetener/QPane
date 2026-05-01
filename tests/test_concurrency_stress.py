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

"""Integration stress test covering overlapping manager workloads."""

from __future__ import annotations
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QRunnable
from PySide6.QtGui import QColor, QImage
from qpane import Config, sam
from qpane.concurrency import (
    BaseWorker,
    QThreadPoolExecutor,
    build_thread_policy,
)
from qpane.masks.autosave import AutosaveManager
from qpane.rendering import PyramidManager, TileManager
from qpane.scene.identity import SceneLayerTileKey, default_catalog_asset_key
from qpane.sam.manager import SamManager
from tests.helpers.render_plan import make_tile_key


def _make_image(size: int = 64) -> QImage:
    """Return a solid-color ARGB image for use in stress scenarios."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    return image


def _wait_for_executor_drain(
    executor: QThreadPoolExecutor, qapp, timeout: float = 5.0
) -> None:
    """Spin the Qt event loop until the executor finishes or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        snapshot = executor.snapshot()
        if snapshot.active_total == 0 and snapshot.pending_total == 0:
            return
        time.sleep(0.01)
    raise AssertionError("Executor did not drain within timeout")


@pytest.mark.usefixtures("qapp")
def test_concurrency_managers_operate_cleanly(tmp_path, monkeypatch, qapp) -> None:
    """Launch pyramid, tile, autosave, and SAM tasks and verify coordinated teardown."""
    policy = build_thread_policy(
        max_workers=2,
        category_limits={"pyramid": 1, "tiles": 1, "io": 1, "sam": 1},
    )
    executor = QThreadPoolExecutor(policy=policy, name="stress-test")
    config = Config(cache={"pyramids": {"mb": 2}, "tiles": {"mb": 2}})
    config.mask_autosave_enabled = True
    pyramid_manager = PyramidManager(config=config, executor=executor)
    tile_manager = TileManager(config=config, executor=executor)
    tile_events: list[SceneLayerTileKey] = []
    tile_manager.tileReady.connect(tile_events.append)
    mask_image = QImage(16, 16, QImage.Format_ARGB32)
    mask_image.fill(QColor("black"))
    mask_layer = SimpleNamespace(mask_image=mask_image)
    mask_manager = SimpleNamespace(get_layer=lambda _mask_id: mask_layer)
    autosave_manager = AutosaveManager(
        mask_manager=mask_manager,
        settings=config,
        get_current_image_path=lambda: tmp_path / "image.png",
        executor=executor,
    )

    class _Predictor:
        def __init__(self) -> None:
            self.image = None

        def set_image(self, image) -> None:
            self.image = image

    monkeypatch.setattr(
        sam.service,
        "load_predictor",
        lambda checkpoint_path, device="cpu": _Predictor(),
    )
    checkpoint_path = tmp_path / "sam-checkpoint.pt"
    checkpoint_path.write_bytes(b"checkpoint")
    sam_manager = SamManager(executor=executor, checkpoint_path=checkpoint_path)
    full_image = _make_image()
    image_id = uuid.uuid4()
    source_path = Path(tmp_path / "sample.png")
    tile_identifier = make_tile_key(image_id, source_path, 1.0, 0, 0)
    mask_path = tmp_path / "mask.png"
    predictors: list = []
    sam_manager.predictorReady.connect(
        lambda predictor, predictor_id: predictors.append((predictor, predictor_id))
    )
    asset_key = default_catalog_asset_key(
        image_id,
        revision=0,
        source_path=source_path,
    )
    pyramid_manager.generate_pyramid_for_asset(asset_key, full_image)
    tile_manager.get_tile(tile_identifier, full_image)
    autosave_manager.saveMaskToPath("mask-1", str(mask_path))
    sam_manager.requestPredictor(full_image, image_id, source_path=source_path)
    _wait_for_executor_drain(executor, qapp)
    qapp.processEvents()
    assert pyramid_manager.cache_usage_bytes > 0
    assert tile_events and tile_events[-1] == tile_identifier
    assert not tile_manager._worker_state
    assert mask_path.exists()
    assert predictors and predictors[-1][1] == image_id
    snapshot = executor.snapshot()
    assert snapshot.active_total == 0
    assert snapshot.pending_total == 0
    assert executor.active_counts() == {}
    sam_manager.shutdown()
    autosave_manager.shutdown()
    tile_manager.shutdown()
    pyramid_manager.shutdown()
    executor.shutdown()


class _PendingBlockingWorker(QRunnable, BaseWorker):
    """Worker that blocks until the shared release event is triggered."""

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        QRunnable.__init__(self)
        BaseWorker.__init__(self)
        self._started = started
        self._release = release

    def run(self) -> None:
        self._started.set()
        self._release.wait()
        if self.is_cancelled:
            return
        self.emit_finished(True)


@pytest.mark.usefixtures("qapp")
def test_executor_applies_pending_backpressure(qapp) -> None:
    """Stress the executor pending limits and ensure background threads block."""
    policy = build_thread_policy(
        max_workers=2, max_pending_total=4, pending_limits={"tiles": 3}
    )
    executor = QThreadPoolExecutor(policy=policy, name="stress-pending")
    release = threading.Event()
    start_events = [threading.Event() for _ in range(6)]
    submission_durations: list[float] = []
    exceptions: list[BaseException] = []
    try:
        for idx in range(2):
            worker = _PendingBlockingWorker(start_events[idx], release)
            executor.submit(worker, category="tiles")
            assert start_events[idx].wait(timeout=1.0), "Worker did not become active"
        producer_ready = threading.Event()
        submissions_recorded = threading.Event()

        def producer() -> None:
            try:
                producer_ready.set()
                for idx in range(4):
                    worker = _PendingBlockingWorker(start_events[idx + 2], release)
                    started = time.monotonic()
                    executor.submit(worker, category="tiles")
                    submission_durations.append(time.monotonic() - started)
                    if idx == 2:
                        submissions_recorded.set()
            except BaseException as exc:  # pragma: no cover - defensive guard
                exceptions.append(exc)
                raise

        thread = threading.Thread(target=producer, name="pending-producer")
        thread.start()
        assert producer_ready.wait(timeout=1.0), "Producer thread failed to start"
        assert submissions_recorded.wait(
            timeout=2.0
        ), "Producer failed to queue initial tasks"
        snapshot = executor.snapshot()
        assert snapshot.pending_total <= 4
        assert snapshot.queued_by_category.get("tiles", 0) <= 3
        time.sleep(0.25)
        release.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "Producer thread did not finish after release"
        assert not exceptions, f"Producer raised unexpected exceptions: {exceptions}"
        _wait_for_executor_drain(executor, qapp)
        qapp.processEvents()
        assert len(submission_durations) == 4
        assert submission_durations[0] < 0.2
        assert submission_durations[1] < 0.2
        assert submission_durations[2] < 0.2
        assert submission_durations[3] >= 0.2
        for idx in range(4):
            assert start_events[idx + 2].wait(
                timeout=1.0
            ), "Pending worker did not execute"
    finally:
        release.set()
        if "thread" in locals() and thread.is_alive():
            thread.join(timeout=2.0)
        executor.shutdown()
