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

"""Tests for SAM feature installation and interactions."""

import types
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPoint

from qpane.types import DiagnosticRecord
from qpane.masks import sam_feature
from qpane.masks.sam_feature import (
    _sam_detail_diagnostics_provider,
    _sam_summary_diagnostics_provider,
)
from qpane import Config, QPane
from qpane.features import FeatureInstallError
from qpane.sam import service

from tests.helpers.executor_stubs import StubExecutor


def _stub_sam_service(monkeypatch):
    monkeypatch.setattr(service, "ensure_dependencies", lambda: None)
    monkeypatch.setattr(
        service,
        "ensure_checkpoint",
        lambda *args, **kwargs: Path("checkpoint.pt"),
    )
    monkeypatch.setattr(
        service,
        "resolve_checkpoint_path",
        lambda checkpoint_path=None: Path("checkpoint.pt"),
    )
    monkeypatch.setattr(
        service,
        "load_predictor",
        lambda checkpoint_path, device="cpu": object(),
    )
    monkeypatch.setattr(
        service,
        "predict_mask_from_box",
        lambda predictor, bbox: np.ones((1, 1), dtype=bool),
    )
    monkeypatch.setattr(service, "SamDependencyError", RuntimeError)


def _detachSamManager_keep_delegate(qpane: QPane) -> None:
    """Detach the active SAM manager while preserving the delegate reference."""
    masks = qpane._masks_controller
    delegate = masks.sam_delegate()
    qpane.detachSamManager()
    if delegate is not None:
        masks._sam_delegate = delegate  # type: ignore[attr-defined]


def _seed_mask_service(qpane: QPane) -> None:
    """Seed the mask service and catalog for SAM feature tests."""
    catalog = qpane.catalog()
    mask_manager = types.SimpleNamespace(
        adjust_component_at_point=lambda mask_id, point, grow: True
    )
    catalog.setMaskManager(mask_manager)
    qpane.mask_service = types.SimpleNamespace(
        manager=mask_manager,
        getActiveMaskId=lambda: "mask-1",
        refreshAutosavePolicy=lambda: None,
        get_latest_status_message=lambda *args: None,
        controller=types.SimpleNamespace(
            apply_mask_image=lambda *_args, **_kwargs: True
        ),
    )


@pytest.fixture
def qpane_with_sam(monkeypatch, qapp):
    _stub_sam_service(monkeypatch)
    qpane = QPane(features=("mask", "sam"))
    qpane.resize(64, 64)
    catalog = qpane.catalog()
    mask_manager = types.SimpleNamespace(
        adjust_component_at_point=lambda mask_id, point, grow: True
    )
    catalog.setMaskManager(mask_manager)
    qpane.mask_service = types.SimpleNamespace(
        manager=mask_manager,
        getActiveMaskId=lambda: "mask-1",
        refreshAutosavePolicy=lambda: None,
        get_latest_status_message=lambda *args: None,
        controller=types.SimpleNamespace(
            apply_mask_image=lambda *_args, **_kwargs: True
        ),
    )
    raw_catalog = catalog.imageCatalog()
    raw_catalog.getCurrentPath = lambda: Path("image.png")
    try:
        assert qpane.currentImagePath == Path("image.png")
        yield qpane
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_sam_feature_ignores_empty_bbox(monkeypatch, qpane_with_sam, caplog):
    qpane = qpane_with_sam
    calls = []

    def record_mask(path, bbox, erase_mode=False):
        calls.append((path, bbox, erase_mode))

    manager = qpane.samManager()
    monkeypatch.setattr(manager, "generateMaskFromBox", record_mask)
    caplog.clear()
    tools = qpane._tools_manager
    tools.signals.region_selected_for_masking.emit(
        np.array([]),
        False,
    )
    assert not calls
    valid_bbox = np.array([0, 0, 4, 4])
    tools.signals.region_selected_for_masking.emit(
        valid_bbox,
        True,
    )
    assert calls[-1] == (qpane.currentImagePath, valid_bbox, True)


def test_sam_feature_component_adjusts_mask(qpane_with_sam):
    qpane = qpane_with_sam
    adjustments = []

    def adjust(mask_id, point, grow):
        adjustments.append((mask_id, point, grow))
        return True

    qpane.mask_service.manager.adjust_component_at_point = adjust
    qpane._tools_manager.signals.mask_component_adjustment_requested.emit(
        QPoint(1, 2), True
    )
    assert adjustments == [("mask-1", QPoint(1, 2), True)]


def test_sam_providers_report_additional_metrics():
    thread_pool = types.SimpleNamespace(
        activeThreadCount=lambda: 1, maxThreadCount=lambda: 4
    )
    manager = types.SimpleNamespace(
        getCachedPredictorCount=lambda: 2,
        thread_pool=thread_pool,
    )
    qpane = types.SimpleNamespace(
        samManager=lambda: manager,
        masks=lambda: types.SimpleNamespace(
            sam_delegate=lambda: types.SimpleNamespace(activePredictor=object())
        ),
    )
    summary = _sam_summary_diagnostics_provider(qpane)
    detail = _sam_detail_diagnostics_provider(qpane)
    assert DiagnosticRecord("SAM|Cache", "2") in summary
    assert DiagnosticRecord("SAM|State", "Ready") in summary
    assert DiagnosticRecord("SAM|Active Jobs", "1") in detail
    assert DiagnosticRecord("SAM|Max Threads", "4") in detail


def test_install_sam_feature_respects_config(monkeypatch, qapp):
    _stub_sam_service(monkeypatch)
    qpane = QPane(features=("mask", "sam"))
    qpane.resize(64, 64)
    _detachSamManager_keep_delegate(qpane)
    qpane.applySettings(sam_device="cuda", sam_cache_limit=1)
    _seed_mask_service(qpane)
    sam_feature.install_sam_feature(qpane)
    manager = qpane.samManager()
    assert manager is not None
    try:
        import torch

        cuda_available = bool(
            getattr(torch, "cuda", None)
            and callable(getattr(torch.cuda, "is_available", None))
            and torch.cuda.is_available()
        )
    except Exception:
        cuda_available = False
    expected_device = "cuda" if cuda_available else "cpu"
    assert getattr(manager, "_device") == expected_device
    assert manager.cacheLimit() == 1


def test_install_sam_feature_disabled_missing_checkpoint(monkeypatch, qapp):
    _stub_sam_service(monkeypatch)

    def _raise_missing(*_args, **_kwargs):
        raise service.SamDependencyError("missing checkpoint")

    monkeypatch.setattr(service, "ensure_checkpoint", _raise_missing)
    qpane = QPane(features=("mask", "sam"))
    qpane.resize(64, 64)
    _detachSamManager_keep_delegate(qpane)
    qpane.applySettings(sam_download_mode="disabled")
    _seed_mask_service(qpane)
    statuses: list[str] = []
    qpane.samCheckpointStatusChanged.connect(
        lambda status, _path: statuses.append(status)
    )
    with pytest.raises(FeatureInstallError):
        sam_feature.install_sam_feature(qpane)
    assert "missing" in statuses


def test_install_sam_feature_background_download_signals(monkeypatch, qapp, tmp_path):
    _stub_sam_service(monkeypatch)
    checkpoint = tmp_path / "sam-checkpoint.pt"
    if checkpoint.exists():
        checkpoint.unlink()
    initial_checkpoint = tmp_path / "initial-checkpoint.pt"
    initial_checkpoint.write_bytes(b"ready")

    def fake_ensure_checkpoint(
        checkpoint_path,
        *,
        download_mode,
        model_url,
        expected_hash=None,
        progress_callback=None,
    ):
        assert download_mode == "background"
        assert expected_hash is None
        if progress_callback is not None:
            progress_callback(5, 10)
        checkpoint_path.write_bytes(b"checkpoint")
        return checkpoint_path

    monkeypatch.setattr(
        service,
        "resolve_checkpoint_path",
        lambda checkpoint_path=None: Path(checkpoint_path).resolve(),
    )
    monkeypatch.setattr(service, "ensure_checkpoint", fake_ensure_checkpoint)
    executor = StubExecutor()
    config = Config(
        sam_download_mode="background",
        sam_model_path=str(initial_checkpoint),
    )
    qpane = QPane(features=("mask", "sam"), config=config, task_executor=executor)
    qpane.resize(64, 64)
    _detachSamManager_keep_delegate(qpane)
    qpane.applySettings(
        sam_download_mode="background",
        sam_model_path=str(checkpoint),
    )
    _seed_mask_service(qpane)
    statuses: list[str] = []
    progress: list[tuple[int, int | None]] = []
    qpane.samCheckpointStatusChanged.connect(
        lambda status, _path: statuses.append(status)
    )
    qpane.samCheckpointProgress.connect(
        lambda downloaded, total: progress.append((downloaded, total))
    )
    sam_feature.install_sam_feature(qpane)
    assert statuses == ["downloading"]
    pending = list(executor.pending_tasks())
    assert pending and pending[0].handle.category == "sam"
    executor.run_task(pending[0].handle.task_id)
    qapp.processEvents()
    assert statuses[-1] == "ready"
    assert progress == [(5, 10)]


def test_install_sam_feature_background_noop_when_ready(monkeypatch, qapp, tmp_path):
    _stub_sam_service(monkeypatch)
    checkpoint = tmp_path / "sam-checkpoint.pt"
    checkpoint.write_bytes(b"checkpoint")

    def fail_ensure_checkpoint(*_args, **_kwargs):
        raise AssertionError("ensure_checkpoint should not be invoked when ready")

    monkeypatch.setattr(
        service,
        "resolve_checkpoint_path",
        lambda checkpoint_path=None: Path(checkpoint_path).resolve(),
    )
    monkeypatch.setattr(service, "ensure_checkpoint", fail_ensure_checkpoint)
    executor = StubExecutor()
    config = Config(
        sam_download_mode="background",
        sam_model_path=str(checkpoint),
    )
    qpane = QPane(features=("mask", "sam"), config=config, task_executor=executor)
    qpane.resize(64, 64)
    _detachSamManager_keep_delegate(qpane)
    qpane.applySettings(
        sam_download_mode="background",
        sam_model_path=str(checkpoint),
    )
    _seed_mask_service(qpane)
    statuses: list[str] = []
    qpane.samCheckpointStatusChanged.connect(
        lambda status, _path: statuses.append(status)
    )
    sam_feature.install_sam_feature(qpane)
    assert statuses == ["ready"]
    assert not list(executor.pending_tasks())


def test_install_sam_feature_disabled_mode_skips_executor(monkeypatch, qapp, tmp_path):
    _stub_sam_service(monkeypatch)
    checkpoint = tmp_path / "sam-checkpoint.pt"
    initial_checkpoint = tmp_path / "initial-checkpoint.pt"
    initial_checkpoint.write_bytes(b"ready")

    def raise_missing(*_args, **_kwargs):
        raise service.SamDependencyError("missing checkpoint")

    monkeypatch.setattr(
        service,
        "resolve_checkpoint_path",
        lambda checkpoint_path=None: Path(checkpoint_path).resolve(),
    )
    monkeypatch.setattr(service, "ensure_checkpoint", raise_missing)
    executor = StubExecutor()
    config = Config(
        sam_download_mode="background",
        sam_model_path=str(initial_checkpoint),
    )
    qpane = QPane(features=("mask", "sam"), config=config, task_executor=executor)
    qpane.resize(64, 64)
    _detachSamManager_keep_delegate(qpane)
    qpane.applySettings(
        sam_download_mode="disabled",
        sam_model_path=str(checkpoint),
    )
    _seed_mask_service(qpane)
    statuses: list[str] = []
    qpane.samCheckpointStatusChanged.connect(
        lambda status, _path: statuses.append(status)
    )
    with pytest.raises(FeatureInstallError):
        sam_feature.install_sam_feature(qpane)
    assert "missing" in statuses
    assert not list(executor.pending_tasks())
