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

"""Tests for mask workflow orchestration."""

import logging
import time
import uuid
from types import MethodType

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTransform, Qt

from qpane.catalog import NavigationEvent
from qpane.catalog.image_utils import (
    numpy_to_qimage_grayscale8,
    qimage_to_numpy_view_grayscale8,
)
from qpane.masks.stroke_render import render_stroke_segments
from qpane.masks.stroke_worker import MaskStrokeWorker
from qpane.masks.autosave import AutosaveManager
from qpane.masks.mask import MaskManager
from qpane.masks.mask_controller import (
    MaskController,
    MaskReadyUpdate,
    MaskStrokeJobResult,
    MaskStrokePayload,
    MaskStrokeSegmentPayload,
)
from qpane.masks.mask_service import MaskService, PrefetchedOverlay
from qpane.masks.mask_undo import MaskLayerUndoProvider, MaskUndoProvider
from qpane.core.config_features import MaskConfigSlice
from qpane import Config, QPane
from tests.helpers.executor_stubs import StubExecutor
from tests.helpers.mask_test_utils import drain_mask_jobs, snapshot_mask_layer


def _panel_point(point):
    """Convert QPointF-like values to floating precision QPointF."""
    return QPointF(float(point.x()), float(point.y()))


def _cleanup_qpane(qpane, qapp):
    qpane.deleteLater()
    qapp.processEvents()


def _current_image_size(qpane):
    """Return the active image size for tests that prepare a selected image."""
    image = qpane.currentImage
    assert image is not None
    return image.size()


def _make_test_qpane(qapp):
    executor = StubExecutor(auto_finish=True)
    qpane = QPane(task_executor=executor, features=())
    qpane.resize(128, 128)
    base_config = Config()
    mask_config = MaskConfigSlice()
    mask_manager = MaskManager(undo_limit=mask_config.mask_undo_limit)
    qpane.catalog().setMaskManager(mask_manager)
    controller = MaskController(
        mask_manager,
        image_to_panel_point=_panel_point,
        config=base_config,
        mask_config=mask_config,
    )
    service = MaskService(
        qpane=qpane,
        mask_manager=mask_manager,
        mask_controller=controller,
        config=base_config,
        mask_config=mask_config,
        executor=qpane.executor,
    )
    qpane.attachMaskService(service)
    return qpane, mask_manager, service


class _RecordingMaskPainter:
    """Painter stub that records mask overlay draw operations."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def save(self) -> None:
        self.calls.append(("save", None))

    def restore(self) -> None:
        self.calls.append(("restore", None))

    def setTransform(self, transform: QTransform) -> None:
        self.calls.append(("transform", transform))

    def setRenderHint(self, hint, enabled: bool) -> None:
        self.calls.append(("hint", (hint, enabled)))

    def setOpacity(self, opacity: float) -> None:
        self.calls.append(("opacity", opacity))

    def drawPixmap(self, x: int, y: int, pixmap) -> None:
        self.calls.append(("draw", (x, y, pixmap)))


def _masks(qpane: QPane):
    masks = qpane._masks_controller
    assert masks is not None
    return masks


def _mask_service(qpane: QPane):
    service = _masks(qpane).mask_service()
    assert service is not None
    return service


def test_mask_workflow_navigation_suspend_resume(qpane_with_mask):
    qpane, _, image_id = qpane_with_mask
    masks = _masks(qpane)
    interaction = qpane.interaction
    interaction.resume_overlays()
    event = NavigationEvent(reason="unit-test", target_id=image_id, fit_view=True)
    masks.on_navigation_started(event)
    assert interaction.overlays_suspended is True
    masks.on_swap_applied(image_id, activation_pending=False)
    assert interaction.overlays_suspended is False
    assert interaction.overlays_resume_pending is False


def test_mask_workflow_activation_ready_resumes_overlays(qpane_with_mask):
    qpane, _, image_id = qpane_with_mask
    masks = _masks(qpane)
    interaction = qpane.interaction
    interaction.resume_overlays()
    event = NavigationEvent(reason="pending", target_id=image_id, fit_view=False)
    masks.on_navigation_started(event)
    masks.on_swap_applied(image_id, activation_pending=True)
    assert interaction.overlays_suspended is True
    assert interaction.overlays_resume_pending is True
    masks.handle_activation_ready(image_id, resumed_with_update=False)
    assert interaction.overlays_suspended is False
    assert interaction.overlays_resume_pending is False


def test_mask_workflow_signal_relays(qpane_with_mask):
    qpane, _, _ = qpane_with_mask
    masks = _masks(qpane)
    saved_payload: list[tuple[str, str]] = []
    undo_payload: list[uuid.UUID] = []

    def saved_slot(mid, path):
        saved_payload.append((mid, path))

    def undo_slot(mid):
        undo_payload.append(mid)

    qpane.maskSaved.connect(saved_slot)
    qpane.maskUndoStackChanged.connect(undo_slot)
    try:
        masks.on_mask_saved("mask-id", "path.png")
        masks.on_mask_undo_stack_changed(uuid.uuid4())
    finally:
        qpane.maskSaved.disconnect(saved_slot)
        qpane.maskUndoStackChanged.disconnect(undo_slot)
    assert saved_payload and saved_payload[-1] == ("mask-id", "path.png")
    assert undo_payload


def test_mask_manager_order_matches_current_overlay_order(qapp):
    manager = MaskManager()
    image_id = uuid.uuid4()
    mask_image = QImage(8, 8, QImage.Format_Grayscale8)
    mask_image.fill(0)
    bottom = manager.create_mask(mask_image)
    middle = manager.create_mask(mask_image)
    top = manager.create_mask(mask_image)
    for mask_id in (bottom, middle, top):
        manager.associate_mask_with_image(mask_id, image_id)
    assert manager.get_mask_ids_for_image(image_id) == [bottom, middle, top]
    assert manager.get_masks_for_image(image_id) == [
        manager.get_layer(bottom),
        manager.get_layer(middle),
        manager.get_layer(top),
    ]
    assert manager.reorder_mask(image_id, bottom, 2) is True
    assert manager.get_mask_ids_for_image(image_id) == [middle, top, bottom]
    assert manager.reorder_mask(image_id, bottom, 0) is True
    assert manager.get_mask_ids_for_image(image_id) == [bottom, middle, top]


def test_mask_feature_does_not_register_internal_mask_overlay(qapp):
    qpane, _image = _prepare_qpane_with_mask_feature()
    try:
        assert "mask" not in qpane.interaction.content_overlays
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_opacity_change_does_not_invalidate_colorized_cache(
    monkeypatch, qpane_with_mask
):
    qpane, mask_manager, _image_id = qpane_with_mask
    service = _mask_service(qpane)
    controller = service.controller
    mask_id = qpane.createBlankMask(_current_image_size(qpane))
    assert mask_id is not None
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    invalidated: list[object] = []
    original_invalidate = controller._invalidate_colorized_mask_cache

    def tracking_invalidate(mask_layer, *args, **kwargs):
        invalidated.append(mask_layer)
        return original_invalidate(mask_layer, *args, **kwargs)

    monkeypatch.setattr(
        controller,
        "_invalidate_colorized_mask_cache",
        tracking_invalidate,
    )
    assert qpane.setMaskProperties(mask_id, opacity=0.8) is True
    assert invalidated == []


def test_mask_property_empty_update_marks_full_scene_dirty(monkeypatch, qapp):
    qpane, _image = _prepare_qpane_with_mask_feature()
    try:
        mask_id = qpane.createBlankMask(_current_image_size(qpane))
        assert mask_id is not None
        dirty_calls: list[object] = []

        def tracking_mark_dirty(dirty_rect=None):
            """Record renderer dirty-region requests."""
            dirty_calls.append(dirty_rect)

        monkeypatch.setattr(qpane, "markDirty", tracking_mark_dirty)

        assert qpane.setMaskProperties(mask_id, opacity=0.8) is True
        assert dirty_calls == [None]

        dirty_calls.clear()
        assert qpane.setMaskProperties(mask_id, color=QColor("cyan")) is True
        assert dirty_calls == [None]
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_opacity_updates_route_through_scene_mutations(
    monkeypatch, qpane_with_mask
):
    qpane, _mask_manager, _image_id = qpane_with_mask
    service = _mask_service(qpane)
    mask_id = qpane.createBlankMask(_current_image_size(qpane))
    assert mask_id is not None
    calls: list[tuple[uuid.UUID, float]] = []
    original = service.applySceneMaskOpacity

    def tracking_opacity(target_mask_id: uuid.UUID, opacity: float) -> bool:
        """Record scene-routed opacity updates."""
        calls.append((target_mask_id, opacity))
        return original(target_mask_id, opacity)

    monkeypatch.setattr(service, "applySceneMaskOpacity", tracking_opacity)

    assert qpane.setMaskProperties(mask_id, opacity=0.8) is True

    assert calls == [(mask_id, 0.8)]


def test_mask_cycle_routes_reorder_through_scene_mutations(
    monkeypatch, qpane_with_mask
):
    qpane, _mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    image_size = _current_image_size(qpane)
    first_id = qpane.createBlankMask(image_size)
    second_id = qpane.createBlankMask(image_size)
    assert first_id is not None and second_id is not None
    calls: list[tuple[uuid.UUID, int]] = []
    original = service.applySceneMaskReorder

    def tracking_reorder(target_mask_id: uuid.UUID, target_index: int) -> bool:
        """Record scene-routed reorder updates."""
        calls.append((target_mask_id, target_index))
        return original(target_mask_id, target_index)

    monkeypatch.setattr(service, "applySceneMaskReorder", tracking_reorder)

    assert qpane.maskIDsForImage(image_id) == [first_id, second_id]
    assert qpane.cycleMasksForward() is True

    assert calls == [(first_id, 2)]
    assert qpane.maskIDsForImage(image_id) == [second_id, first_id]


def test_qpane_brush_wrapper_delegates(monkeypatch, qapp):
    qpane = QPane(features=("mask",))
    qpane.resize(16, 16)
    calls: list[int] = []
    masks = _masks(qpane)
    monkeypatch.setattr(masks, "set_brush_size", lambda size: calls.append(size))
    try:
        qpane.setBrushSize(13)
    finally:
        _cleanup_qpane(qpane, qapp)
    assert calls == [13]


@pytest.fixture
def qpane_with_mask(qapp, monkeypatch):
    from qpane.masks import install as mask

    manager_box: dict[str, MaskManager] = {}

    def install_mask_feature(qpane):
        mask_manager = MaskManager(undo_limit=qpane.settings.mask_undo_limit)
        manager_box["manager"] = mask_manager
        catalog = qpane.catalog()
        catalog.setMaskManager(mask_manager)
        controller = MaskController(
            mask_manager,
            image_to_panel_point=_panel_point,
            config=qpane.settings,
        )
        service = MaskService(
            qpane=qpane,
            mask_manager=mask_manager,
            mask_controller=controller,
            config=qpane.settings,
            executor=qpane.executor,
        )
        qpane.attachMaskService(service)
        qpane.refreshMaskAutosavePolicy()

    monkeypatch.setattr(mask, "install_mask_feature", install_mask_feature)
    qpane = QPane(features=("mask",))
    qpane.resize(32, 32)
    qpane.applySettings(mask_autosave_enabled=True)
    image = QImage(8, 8, QImage.Format_ARGB32)
    image.fill(Qt.white)
    image_id = uuid.uuid4()
    image_map = QPane.imageMapFromLists(
        [image],
        paths=[None],
        ids=[image_id],
    )
    qpane.catalog().setImagesByID(image_map, image_id)
    try:
        yield qpane, manager_box["manager"], image_id
    finally:
        _cleanup_qpane(qpane, qapp)


def test_load_and_update_mask_workflow(monkeypatch, qpane_with_mask, tmp_path):
    qpane, mask_manager, image_id = qpane_with_mask
    commit_patch_calls: list[tuple] = []
    commit_image_calls: list[None] = []
    original_commit_patches = MaskManager.commit_mask_patches
    original_commit_image = MaskManager.commit_mask_image

    def tracking_commit_patches(self, mask_id, patches, **kwargs):
        commit_patch_calls.append(tuple(patches))
        return original_commit_patches(self, mask_id, patches, **kwargs)

    def tracking_commit_image(self, mask_id, new_image, **kwargs):
        commit_image_calls.append(None)
        return original_commit_image(self, mask_id, new_image, **kwargs)

    monkeypatch.setattr(
        MaskManager,
        "commit_mask_patches",
        tracking_commit_patches,
    )
    monkeypatch.setattr(
        MaskManager,
        "commit_mask_image",
        tracking_commit_image,
    )
    grayscale = QImage(8, 8, QImage.Format_Grayscale8)
    grayscale.fill(255)
    mask_path = tmp_path / "mask.png"
    assert grayscale.save(str(mask_path))
    invalidations: list = []
    service = _mask_service(qpane)
    controller = service.controller
    original_invalidate = controller.invalidate_layer_cache

    def tracking_invalidate(layer):
        invalidations.append(layer)
        return original_invalidate(layer)

    monkeypatch.setattr(
        controller,
        "invalidate_layer_cache",
        tracking_invalidate,
    )
    mask_id = qpane.loadMaskFromFile(str(mask_path))
    assert mask_id is not None
    assert mask_id in mask_manager.get_mask_ids_for_image(image_id)
    assert len(invalidations) == 1
    assert invalidations[-1] is mask_manager.get_layer(mask_id)
    updated = QImage(8, 8, QImage.Format_Grayscale8)
    updated.fill(64)
    update_path = tmp_path / "mask_updated.png"
    assert updated.save(str(update_path))
    result = qpane.updateMaskFromFile(mask_id, str(update_path))
    assert result is True
    assert len(invalidations) == 2
    stored_layer = mask_manager.get_layer(mask_id)
    assert stored_layer is not None
    assert stored_layer.mask_image.pixelColor(0, 0).value() == 64


def test_mask_autosave_coordinator_disconnects_when_disabled(
    qapp, tmp_path, monkeypatch
):
    from qpane.masks import install as mask

    class TrackingAutosaveManager(AutosaveManager):
        def __init__(
            self,
            mask_manager,
            settings,
            get_current_image_path,
            *,
            executor,
            parent=None,
        ):
            super().__init__(
                mask_manager,
                settings,
                get_current_image_path,
                executor=executor,
                parent=parent,
            )
            self.schedule_calls: list[tuple[object, object]] = []
            self.blank_calls: list[str] = []

        def scheduleSave(self, mask_id, dirty_rect=None):
            self.schedule_calls.append((mask_id, dirty_rect))

        def saveBlankMask(self, mask_id: str, image_size):
            self.blank_calls.append(mask_id)

    def install_mask_feature(qpane):
        catalog = qpane.catalog()
        mask_manager = MaskManager(undo_limit=qpane.settings.mask_undo_limit)
        catalog.setMaskManager(mask_manager)
        controller = MaskController(
            mask_manager,
            image_to_panel_point=_panel_point,
            config=qpane.settings,
        )
        service = MaskService(
            qpane=qpane,
            mask_manager=mask_manager,
            mask_controller=controller,
            config=qpane.settings,
            executor=qpane.executor,
        )
        qpane.attachMaskService(service)
        manager = TrackingAutosaveManager(
            mask_manager,
            qpane.settings,
            lambda: catalog.currentImagePath(),
            executor=qpane.executor,
            parent=qpane,
        )
        qpane.hooks.attachAutosaveManager(manager)
        qpane.refreshMaskAutosavePolicy()

    monkeypatch.setattr(mask, "install_mask_feature", install_mask_feature)
    qpane = QPane(features=("mask",))
    qpane.resize(32, 32)
    qpane.applySettings(mask_autosave_enabled=True)
    catalog = qpane.catalog()
    try:
        mask_manager = catalog.maskManager()
        assert isinstance(mask_manager, MaskManager)
        image = QImage(8, 8, QImage.Format_ARGB32)
        image.fill(Qt.white)
        image_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [image],
            paths=[None],
            ids=[image_id],
        )
        catalog.setImagesByID(image_map, image_id)
        mask_id = mask_manager.create_mask(image)
        mask_manager.associate_mask_with_image(mask_id, image_id)
        service = _mask_service(qpane)
        controller = service.controller
        controller.setActiveMaskID(mask_id)
        manager = qpane.autosaveManager()
        assert isinstance(manager, TrackingAutosaveManager)
        controller.mask_updated.emit(mask_id, QRect())
        assert len(manager.schedule_calls) == 2
        assert manager.schedule_calls[0][0] == mask_id
        qpane.applySettings(mask_autosave_enabled=False)
        assert qpane.autosaveManager() is None
        controller.mask_updated.emit(mask_id, QRect())
        assert len(manager.schedule_calls) == 2
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_region_update_triggers_autosave(qpane_with_mask, monkeypatch):
    qpane, mask_manager, image_id = qpane_with_mask
    commit_patch_calls: list[tuple] = []
    commit_image_calls: list[None] = []
    original_commit_patches = MaskManager.commit_mask_patches
    original_commit_image = MaskManager.commit_mask_image

    def tracking_commit_patches(self, mask_id, patches, **kwargs):
        commit_patch_calls.append(tuple(patches))
        return original_commit_patches(self, mask_id, patches, **kwargs)

    def tracking_commit_image(self, mask_id, new_image, **kwargs):
        commit_image_calls.append(None)
        return original_commit_image(self, mask_id, new_image, **kwargs)

    monkeypatch.setattr(MaskManager, "commit_mask_patches", tracking_commit_patches)
    monkeypatch.setattr(MaskManager, "commit_mask_image", tracking_commit_image)
    service = _mask_service(qpane)
    commit_patch_calls: list[tuple] = []
    commit_image_calls: list[None] = []
    original_commit_patches = MaskManager.commit_mask_patches
    original_commit_image = MaskManager.commit_mask_image

    def tracking_commit_patches(self, mask_id, patches, **kwargs):
        commit_patch_calls.append(tuple(patches))
        return original_commit_patches(self, mask_id, patches, **kwargs)

    def tracking_commit_image(self, mask_id, new_image, **kwargs):
        commit_image_calls.append(None)
        return original_commit_image(self, mask_id, new_image, **kwargs)

    monkeypatch.setattr(MaskManager, "commit_mask_patches", tracking_commit_patches)
    monkeypatch.setattr(MaskManager, "commit_mask_image", tracking_commit_image)
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id) is True
    mask_layer = mask_manager.get_layer(mask_id)
    assert mask_layer is not None
    qpane.hooks.detachAutosaveManager()

    class TrackingAutosaveManager(AutosaveManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls: list[tuple[str, object | None]] = []

        def scheduleSave(self, mask_id, dirty_rect=None):
            self.calls.append((mask_id, dirty_rect))
            super().scheduleSave(mask_id, dirty_rect)

    manager = TrackingAutosaveManager(
        mask_manager,
        qpane.settings,
        lambda: qpane.catalog().currentImagePath(),
        executor=qpane.executor,
        parent=qpane,
    )
    qpane.hooks.attachAutosaveManager(manager)
    qpane.refreshMaskAutosavePolicy()
    rect = QRect(0, 0, 2, 2)
    mask_layer.mask_image.fill(Qt.black)
    qpane.updateMaskRegion(rect, mask_layer)
    assert manager.calls
    assert manager.calls[-1][0] == mask_id


def test_mask_autosave_uses_template_when_no_listener(qpane_with_mask, tmp_path, qapp):
    qpane, mask_manager, _ = qpane_with_mask
    service = _mask_service(qpane)
    qpane.applySettings(
        mask_autosave_debounce_ms=0,
        mask_autosave_on_creation=False,
        mask_autosave_path_template=str(tmp_path / "{mask_id}.png"),
    )
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    mask_layer = mask_manager.get_layer(mask_id)
    assert mask_layer is not None
    mask_layer.mask_image.fill(Qt.white)
    qpane.updateMaskRegion(QRect(0, 0, 2, 2), mask_layer)
    expected_path = tmp_path / f"{mask_id}.png"
    deadline = time.time() + 2
    while time.time() < deadline and not expected_path.exists():
        qapp.processEvents()
        time.sleep(0.01)
    assert expected_path.exists()


def _mask_records(qpane):
    """Return diagnostics records produced by the mask service."""
    snapshot = qpane.gatherDiagnostics()
    return [record for record in snapshot.records if record.label.startswith("Mask")]


def test_mask_workflow_generate_and_apply_mask_success(qpane_with_mask, monkeypatch):
    qpane, mask_manager, image_id = qpane_with_mask
    masks = _masks(qpane)
    service = _mask_service(qpane)
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    predictor = object()
    delegate = masks.sam_delegate()
    assert delegate is not None
    monkeypatch.setattr(delegate, "_active_predictor", predictor)
    monkeypatch.setattr(
        masks,
        "sam_feature_available",
        lambda: True,
    )
    captured_predict = {}

    def fake_predict(predictor_arg, bbox_arg):
        captured_predict["predictor"] = predictor_arg
        captured_predict["bbox"] = bbox_arg
        return np.ones((1, 1), dtype=bool)

    monkeypatch.setattr(service, "predict_mask_from_box", fake_predict)
    handled: list[tuple[np.ndarray, np.ndarray, bool]] = []

    def fake_handle(mask_array, bbox_arg, erase_mode):
        handled.append((mask_array, bbox_arg, erase_mode))

    monkeypatch.setattr(service, "handleGeneratedMask", fake_handle)
    bbox = np.array([0, 0, 4, 4])
    result = masks.generate_and_apply_mask(bbox, erase_mode=True)
    assert result is True
    assert captured_predict["predictor"] is predictor
    assert np.array_equal(captured_predict["bbox"], bbox)
    assert handled
    mask_array, handled_bbox, erase_flag = handled[-1]
    assert np.array_equal(handled_bbox, bbox)
    assert erase_flag is True
    assert mask_array.dtype == np.uint8
    assert mask_array.shape == (1, 1)
    assert int(mask_array[0, 0]) == 255


def test_mask_workflow_generate_and_apply_mask_invalid_bbox(
    qpane_with_mask, monkeypatch, caplog
):
    qpane, mask_manager, image_id = qpane_with_mask
    masks = _masks(qpane)
    service = _mask_service(qpane)
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    predictor = object()
    delegate = masks.sam_delegate()
    assert delegate is not None
    monkeypatch.setattr(delegate, "_active_predictor", predictor)
    monkeypatch.setattr(
        masks,
        "sam_feature_available",
        lambda: True,
    )

    def fake_predict(_predictor, _bbox):
        raise ValueError("bad bbox")

    monkeypatch.setattr(service, "predict_mask_from_box", fake_predict)
    handled = []

    def fake_handle(mask_array, bbox_arg, erase_mode):
        handled.append((mask_array, bbox_arg, erase_mode))

    monkeypatch.setattr(service, "handleGeneratedMask", fake_handle)
    bbox = np.array([1, 2, 3])
    caplog.set_level(logging.WARNING, logger="qpane.masks.workflow")
    caplog.clear()
    result = masks.generate_and_apply_mask(bbox, erase_mode=False)
    assert result is False
    assert not handled
    assert any("bounding box invalid" in record.message for record in caplog.records)


def test_mask_service_reports_diagnostics(qpane_with_mask, tmp_path):
    qpane, _, _ = qpane_with_mask
    missing_path = tmp_path / "missing.png"
    assert qpane.loadMaskFromFile(str(missing_path)) is None
    records = _mask_records(qpane)
    assert any(
        "Failed to load or prepare mask from" in record.value for record in records
    )
    grayscale = QImage(8, 8, QImage.Format_Grayscale8)
    grayscale.fill(64)
    mask_path = tmp_path / "mask.png"
    assert grayscale.save(str(mask_path))
    mask_id = qpane.loadMaskFromFile(str(mask_path))
    assert mask_id is not None
    records = _mask_records(qpane)
    assert any(
        "Successfully loaded mask data from" in record.value for record in records
    )


def test_mask_workflow_diagnostics_survive_detach(qpane_with_mask):
    qpane, _, _ = qpane_with_mask
    qpane.diagnostics().set_domain_detail_enabled("mask", True)
    snapshot = qpane.gatherDiagnostics()
    assert any(record.label == "Mask|Prefetch" for record in snapshot.records)
    qpane.detachMaskService()
    snapshot_after = qpane.gatherDiagnostics()
    assert any(record.label == "Mask|Prefetch" for record in snapshot_after.records)


def test_mask_workflow_brush_cursor_respects_viewport(qpane_with_mask, qapp):
    qpane, _, _ = qpane_with_mask
    interaction = qpane.interaction
    interaction.brush_size = 8
    _masks(qpane).update_brush_cursor(erase_indicator=False)
    qapp.processEvents()
    assert interaction.custom_cursor is not None
    interaction.brush_size = max(qpane.size().width(), qpane.size().height()) * 4
    _masks(qpane).update_brush_cursor(erase_indicator=False)
    qapp.processEvents()
    assert interaction.custom_cursor is None
    assert qpane.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_set_active_mask_promotes_top(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    mask_service = _mask_service(qpane)
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    first_id = mask_service.createBlankMask(base_image.size())
    second_id = mask_service.createBlankMask(base_image.size())
    assert first_id is not None and second_id is not None
    first_layer = mask_manager.get_layer(first_id)
    second_layer = mask_manager.get_layer(second_id)
    order = mask_manager.get_masks_for_image(image_id)
    assert len(order) == 2
    assert order[-1] is second_layer
    assert qpane.setActiveMaskID(first_id) is True
    reordered = mask_manager.get_masks_for_image(image_id)
    assert reordered[-1] is first_layer


def test_remove_active_mask_promotes_next(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    assert service is not None
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    first_mask = service.createBlankMask(base_image.size())
    second_mask = service.createBlankMask(base_image.size())
    assert first_mask is not None and second_mask is not None
    assert qpane.setActiveMaskID(first_mask)
    removed = qpane.removeMaskFromImage(image_id, first_mask)
    assert removed is True
    mask_ids = mask_manager.get_mask_ids_for_image(image_id)
    assert mask_ids == [second_mask]
    assert mask_manager.get_layer(first_mask) is None
    assert service.getActiveMaskId() == second_mask


def test_prepare_apply_stroke_job_merges_pixels(qpane_with_mask, qapp):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    assert service is not None
    controller = service.controller
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    mask_layer = mask_manager.get_layer(mask_id)
    assert mask_layer is not None
    dirty_rect = QRect(2, 2, 6, 6)
    baseline_generation = controller.getMaskGeneration(mask_id)
    spec = controller.prepareStrokeJob(mask_id, dirty_rect)
    assert spec is not None
    assert spec.before.shape == (dirty_rect.height(), dirty_rect.width())
    assert spec.generation == baseline_generation
    after = np.array(spec.before, copy=True)
    after.fill(180)
    preview = numpy_to_qimage_grayscale8(after)
    job = MaskStrokeJobResult(
        mask_id=spec.mask_id,
        generation=spec.generation,
        dirty_rect=spec.dirty_rect,
        before=spec.before,
        after=after,
        preview_image=preview,
        metadata=dict(spec.metadata),
    )
    applied = controller.applyStrokeJob(job)
    assert applied
    assert controller.getMaskGeneration(mask_id) == baseline_generation + 1
    mask_view, _ = qimage_to_numpy_view_grayscale8(mask_layer.mask_image)
    top = dirty_rect.top()
    left = dirty_rect.left()
    height, width = spec.before.shape
    np.testing.assert_array_equal(
        mask_view[top : top + height, left : left + width],
        after,
    )


def test_apply_stroke_job_rejects_stale_generation(qpane_with_mask, qapp):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    assert service is not None
    controller = service.controller
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    dirty_rect = QRect(1, 1, 4, 4)
    baseline_generation = controller.getMaskGeneration(mask_id)
    spec = controller.prepareStrokeJob(mask_id, dirty_rect)
    assert spec is not None
    assert spec.generation == baseline_generation
    controller.bumpMaskGeneration(mask_id, reason="test_stale_generation")
    after = np.array(spec.before, copy=True)
    preview = numpy_to_qimage_grayscale8(after)
    job = MaskStrokeJobResult(
        mask_id=spec.mask_id,
        generation=spec.generation,
        dirty_rect=spec.dirty_rect,
        before=spec.before,
        after=after,
        preview_image=preview,
        metadata=dict(spec.metadata),
    )
    applied = controller.applyStrokeJob(job)
    assert not applied
    assert controller.getMaskGeneration(mask_id) == baseline_generation + 1
    mask_layer = mask_manager.get_layer(mask_id)
    assert mask_layer is not None
    mask_view, _ = qimage_to_numpy_view_grayscale8(mask_layer.mask_image)
    top = dirty_rect.top()
    left = dirty_rect.left()
    height, width = spec.before.shape
    np.testing.assert_array_equal(
        mask_view[top : top + height, left : left + width],
        spec.before,
    )


def test_remove_last_mask_clears_active(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    removed = qpane.removeMaskFromImage(image_id, mask_id)
    assert removed is True
    assert mask_manager.get_mask_ids_for_image(image_id) == []
    assert mask_manager.get_layer(mask_id) is None
    assert service.getActiveMaskId() is None


def test_mask_install_invokes_autosave_refresh_once(monkeypatch, qapp):
    import types
    from qpane.masks import install as mask

    qpane = QPane(features=("mask",))
    qpane.resize(32, 32)
    qpane.detachMaskService()
    original_refresh = qpane.refreshMaskAutosavePolicy
    calls: list[None] = []

    def tracking(self):
        calls.append(None)
        return original_refresh()

    monkeypatch.setattr(
        qpane,
        "refreshMaskAutosavePolicy",
        types.MethodType(tracking, qpane),
    )
    mask.install_mask_feature(qpane)
    assert len(calls) == 1
    _cleanup_qpane(qpane, qapp)


def _prepare_qpane_with_mask_feature(
    *,
    executor: StubExecutor | None = None,
    features: tuple[str, ...] | None = None,
    image_size_px: int = 64,
):
    """Build a QPane seeded with an image and ready-to-use mask tooling."""
    if executor is None:
        executor = StubExecutor(auto_finish=True)
    active_features = features if features is not None else ("mask",)
    qpane = QPane(task_executor=executor, features=active_features)
    qpane.resize(max(32, image_size_px * 2), max(32, image_size_px * 2))
    qpane.applySettings(mask_autosave_enabled=True)
    catalog = qpane.catalog()
    image = QImage(image_size_px, image_size_px, QImage.Format_ARGB32)
    image.fill(Qt.white)
    image_id = uuid.uuid4()
    image_map = QPane.imageMapFromLists([image], paths=[None], ids=[image_id])
    catalog.setImagesByID(image_map, image_id)
    return qpane, image


def _emit_brush_stroke(qpane, start, end=None, erase=False):
    tools = qpane._tools_manager
    tools.signals.undo_state_push_requested.emit()
    end_point = start if end is None else end
    tools.signals.stroke_applied.emit(start, end_point, erase)
    tools.signals.stroke_completed.emit()
    executor = getattr(qpane, "executor", None)
    drain = getattr(executor, "drain_all", None)
    if callable(drain):
        try:
            drain()
        except Exception:  # pragma: no cover - defensive guard
            pass


def test_brush_mode_persists_when_mask_available(qapp):
    qpane, image = _prepare_qpane_with_mask_feature()
    try:
        assert _masks(qpane).mask_feature_available()
        service = qpane.mask_service
        assert service is not None
        mask_id = service.createBlankMask(image.size())
        assert mask_id is not None
        qpane.setControlMode(QPane.CONTROL_MODE_DRAW_BRUSH)
        assert qpane.getControlMode() == QPane.CONTROL_MODE_DRAW_BRUSH
    finally:
        _cleanup_qpane(qpane, qapp)
    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents()
        app.processEvents()


def _queue_pending_stroke(qpane, start, end=None, erase=False):
    """Emit a brush stroke but leave executor work pending for assertions."""
    tools = qpane._tools_manager
    tools.signals.undo_state_push_requested.emit()
    end_point = start if end is None else end
    tools.signals.stroke_applied.emit(start, end_point, erase)
    tools.signals.stroke_completed.emit()
    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents()


def test_empty_stroke_completion_does_not_commit(qapp):
    qpane, image = _prepare_qpane_with_mask_feature()
    try:
        service = _mask_service(qpane)
        assert service is not None
        mask_id = service.createBlankMask(image.size())
        assert mask_id is not None
        assert qpane.setActiveMaskID(mask_id)
        controller = service.controller
        generation_before = controller.getMaskGeneration(mask_id)
        tools = qpane._tools_manager
        tools.signals.stroke_completed.emit()
        qapp.processEvents()
        generation_after = controller.getMaskGeneration(mask_id)
        assert generation_after == generation_before
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_stroke_finalize_clears_preview_and_pending_jobs(qapp):
    qpane, image = _prepare_qpane_with_mask_feature()
    service = _mask_service(qpane)
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(0)
    qpane.interaction.brush_size = 6
    try:
        _emit_brush_stroke(qpane, QPoint(3, 3))
        qapp.processEvents()
        qapp.processEvents()
        preview_states = service.strokeDebugSnapshot().preview_state_ids
        assert mask_id not in preview_states
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert mask_id not in preview_tokens
        pending_jobs = service.strokeDebugSnapshot().pending_jobs
        assert not pending_jobs.get(mask_id)
        controller = service.controller
        assert controller.getMaskGeneration(mask_id) > 0
        view, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
        assert view[3, 3] == 255
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_stroke_finalize_drops_stale_generation(qapp):
    executor = StubExecutor(auto_finish=False)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    service = _mask_service(qpane)
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(0)
    qpane.interaction.brush_size = 5
    try:
        _emit_brush_stroke(qpane, QPoint(4, 4))
        executor.run_category("mask_stroke")
        controller = service.controller
        controller.bumpMaskGeneration(mask_id, reason="test-stale")
        executor.run_category("mask_stroke_main")
        qapp.processEvents()
        view, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
        assert not np.any(view)
        pending_jobs = service.strokeDebugSnapshot().pending_jobs
        assert not pending_jobs.get(mask_id)
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert mask_id not in preview_tokens
        preview_states = service.strokeDebugSnapshot().preview_state_ids
        assert mask_id not in preview_states
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_stroke_finalize_drops_stale_token(qapp):
    executor = StubExecutor(auto_finish=False)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    service = _mask_service(qpane)
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(0)
    qpane.interaction.brush_size = 5
    tools = qpane._tools_manager
    try:

        def queue_stroke(point: QPoint) -> None:
            tools.signals.undo_state_push_requested.emit()
            tools.signals.stroke_applied.emit(point, point, False)
            tools.signals.stroke_completed.emit()
            qapp.processEvents()

        queue_stroke(QPoint(4, 4))
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        first_token = preview_tokens.get(mask_id)
        assert first_token is not None
        queue_stroke(QPoint(5, 5))
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        second_token = preview_tokens.get(mask_id)
        assert second_token is not None and second_token != first_token
        worker_records = [
            record
            for record in executor.pending_tasks()
            if record.handle.category == "mask_stroke"
        ]
        assert len(worker_records) == 2
        executor.run_task(worker_records[0].handle.task_id)
        executor.run_category("mask_stroke_main")
        qapp.processEvents()
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert preview_tokens.get(mask_id) == second_token
        view, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
        assert not np.any(view)
        remaining_workers = [
            record
            for record in executor.pending_tasks()
            if record.handle.category == "mask_stroke"
        ]
        assert len(remaining_workers) == 1
        executor.run_task(remaining_workers[0].handle.task_id)
        executor.run_category("mask_stroke_main")
        qapp.processEvents()
        view, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
        assert view[5, 5] == 255
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert mask_id not in preview_tokens
        pending_jobs = service.strokeDebugSnapshot().pending_jobs
        assert not pending_jobs.get(mask_id)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_stroke_finalize_uses_qtimer_when_dispatch_missing(qapp):
    executor = StubExecutor(auto_finish=False, supports_main_thread_dispatch=False)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    service = _mask_service(qpane)
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(0)
    qpane.interaction.brush_size = 4
    try:
        _emit_brush_stroke(qpane, QPoint(5, 5))
        executor.run_category("mask_stroke")
        pending_main = [
            record
            for record in executor.pending_tasks()
            if record.handle.category == "mask_stroke_main"
        ]
        assert pending_main == []
        for _ in range(3):
            qapp.processEvents()
        view, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
        assert view[5, 5] == 255
        pending_jobs = service.strokeDebugSnapshot().pending_jobs
        assert not pending_jobs.get(mask_id)
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert mask_id not in preview_tokens
        preview_states = service.strokeDebugSnapshot().preview_state_ids
        assert mask_id not in preview_states
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_feature_paints_via_tool_manager(qapp):
    qpane, image = _prepare_qpane_with_mask_feature()
    service = _mask_service(qpane)
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(Qt.black)
    assert not layer.mask_image.isNull()
    qpane.interaction.brush_size = 4
    _emit_brush_stroke(qpane, QPoint(2, 2))
    assert layer.mask_image.pixelColor(2, 2).value() == 255
    _cleanup_qpane(qpane, qapp)


def test_mask_feature_reinstall_preserves_stroke_binding(qapp):
    from qpane.masks import install as mask

    qpane, image = _prepare_qpane_with_mask_feature()
    # Simulate uninstall
    if qpane.mask_service is not None:
        qpane.detachMaskService()
    qpane.hooks.unregisterTool(QPane.CONTROL_MODE_DRAW_BRUSH)
    qpane.hooks.unregisterCursorProvider(QPane.CONTROL_MODE_DRAW_BRUSH)
    qpane.hooks.unregisterOverlay("mask")
    mask.install_mask_feature(qpane)
    service = _mask_service(qpane)
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(Qt.black)
    assert not layer.mask_image.isNull()
    qpane.interaction.brush_size = 4
    _emit_brush_stroke(qpane, QPoint(1, 1))
    assert layer.mask_image.pixelColor(1, 1).value() == 255


def test_controller_commit_stroke_clears_preview_state(qpane_with_mask, monkeypatch):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_image = QImage(64, 64, QImage.Format_Grayscale8)
    mask_image.fill(0)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    assert qpane.setActiveMaskID(mask_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    mask_view, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
    dirty_rect = QRect(12, 12, 6, 6)
    before_slice = mask_view[12:18, 12:18].copy()
    after_slice = before_slice.copy()
    after_slice[2, 2] = 255
    captured_patches: list[tuple] = []
    original_commit_patches = MaskManager.commit_mask_patches

    def tracking_commit_patches(self, mask_id_arg, patches, **kwargs):
        captured_patches.append(tuple(patches))
        return original_commit_patches(self, mask_id_arg, patches, **kwargs)

    monkeypatch.setattr(MaskManager, "commit_mask_patches", tracking_commit_patches)
    service.controller.recordStrokePatchFromArrays(
        mask_id,
        dirty_rect,
        before_slice,
        after_slice,
    )
    assert service.controller.commitStroke(mask_id)
    assert captured_patches
    for patch_tuple in captured_patches:
        for patch in patch_tuple:
            assert patch.mask.dtype == np.bool_
            assert patch.mask.shape == (
                patch.rect.height(),
                patch.rect.width(),
            )
    mask_after, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
    assert mask_after[14, 14] == 255


def test_mask_service_produces_preview_for_zoomed_out(qpane_with_mask, monkeypatch):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_array = np.arange(64 * 64, dtype=np.uint8).reshape(64, 64)
    mask_qimage = numpy_to_qimage_grayscale8(mask_array)
    mask_id = mask_manager.create_mask(mask_qimage)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    viewport = qpane.view().viewport
    viewport.applyZoom(0.5)
    assert viewport.zoom < 1.0
    controller = service.controller
    captured: dict[str, object] = {}
    original_update = controller.updateMaskRegion

    def tracking_update_mask_region(
        self,
        dirty_image_rect,
        mask_layer,
        *,
        sub_mask_image=None,
        colorized_image=None,
    ):
        copied = sub_mask_image.copy() if sub_mask_image is not None else None
        captured["preview"] = copied
        captured["rect"] = QRect(dirty_image_rect)
        return original_update(
            dirty_image_rect,
            mask_layer,
            sub_mask_image=sub_mask_image,
            colorized_image=colorized_image,
        )

    controller.updateMaskRegion = MethodType(tracking_update_mask_region, controller)
    dirty_rect = QRect(8, 8, 16, 12)
    service.updateMaskRegion(dirty_rect, layer, sub_mask_image=None)
    assert "preview" in captured
    preview_image = captured["preview"]
    assert isinstance(preview_image, QImage)
    stride = int(preview_image.text("qpane_preview_stride"))
    assert stride == max(1, int(round(1.0 / max(viewport.zoom, 1e-6))))
    assert preview_image.text("qpane_preview_provisional") == "1"
    preview_view, _ = qimage_to_numpy_view_grayscale8(preview_image)
    mask_view, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
    y0, x0 = dirty_rect.top(), dirty_rect.left()
    y1, x1 = dirty_rect.bottom() + 1, dirty_rect.right() + 1
    expected_slice = mask_view[y0:y1:stride, x0:x1:stride]
    np.testing.assert_array_equal(preview_view, expected_slice)


def test_handle_generated_mask_requests_async_refresh_for_sam(
    qpane_with_mask, monkeypatch
):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    mask_image = QImage(32, 32, QImage.Format_Grayscale8)
    mask_image.fill(0)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    assert qpane.setActiveMaskID(mask_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    dirty_rect = QRect(0, 0, 12, 10)
    mask_update = MaskReadyUpdate(
        mask_id=mask_id,
        dirty_rect=dirty_rect,
        mask_layer=layer,
        changed=True,
    )
    controller = service.controller
    monkeypatch.setattr(
        controller,
        "handle_mask_ready",
        lambda *args, **kwargs: mask_update,
    )
    captured: dict[str, object] = {}

    def fake_update_region(
        dirty_rect_arg,
        mask_layer_arg,
        *,
        sub_mask_image=None,
        force_async_colorize=False,
    ):
        captured["force_async"] = force_async_colorize
        captured["rect"] = QRect(dirty_rect_arg)
        return None

    monkeypatch.setattr(service, "updateMaskRegion", fake_update_region)
    bbox = np.array(
        [
            dirty_rect.left(),
            dirty_rect.top(),
            dirty_rect.right(),
            dirty_rect.bottom(),
        ],
        dtype=np.int32,
    )
    mask_data = np.ones((dirty_rect.height(), dirty_rect.width()), dtype=np.uint8) * 255
    service.handleGeneratedMask(mask_data, bbox, erase_mode=False)
    assert captured["rect"] == dirty_rect
    assert captured["force_async"] is True


def test_update_mask_region_forces_async_colorize_uses_full_res(
    qpane_with_mask, monkeypatch
):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    mask_array = np.arange(64 * 64, dtype=np.uint8).reshape(64, 64)
    mask_qimage = numpy_to_qimage_grayscale8(mask_array)
    mask_id = mask_manager.create_mask(mask_qimage)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    assert qpane.setActiveMaskID(mask_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    viewport = qpane.view().viewport
    viewport.applyZoom(0.4)
    assert viewport.zoom < 1.0
    scheduled_snippets: list[tuple[uuid.UUID, QRect, QImage]] = []

    def tracking_schedule(self, mask_id_arg, dirty_rect_arg, mask_layer_arg, snippet):
        scheduled_snippets.append((mask_id_arg, QRect(dirty_rect_arg), snippet.copy()))
        return True

    monkeypatch.setattr(
        MaskService,
        "_schedule_snippet_colorize",
        tracking_schedule,
    )
    controller = service.controller
    original_update = controller.updateMaskRegion
    previews: list[QImage] = []

    def tracking_update(
        self,
        dirty_rect_arg,
        mask_layer_arg,
        *,
        sub_mask_image=None,
        **kwargs,
    ):
        if sub_mask_image is not None:
            previews.append(sub_mask_image.copy())
        return original_update(
            dirty_rect_arg,
            mask_layer_arg,
            sub_mask_image=sub_mask_image,
            **kwargs,
        )

    controller.updateMaskRegion = MethodType(tracking_update, controller)
    dirty_rect = QRect(6, 6, 18, 14)
    service.updateMaskRegion(
        dirty_rect,
        layer,
        sub_mask_image=None,
        force_async_colorize=True,
    )
    assert previews, "preview should be generated while zoomed out"
    assert scheduled_snippets, "async colorization should be queued"
    mask_id_arg, rect_arg, snippet_image = scheduled_snippets[-1]
    assert mask_id_arg == mask_id
    assert rect_arg == dirty_rect
    snippet_view, _ = qimage_to_numpy_view_grayscale8(snippet_image)
    assert snippet_view.shape == (dirty_rect.height(), dirty_rect.width())
    mask_view, _ = qimage_to_numpy_view_grayscale8(layer.mask_image)
    y0, x0 = dirty_rect.top(), dirty_rect.left()
    y1, x1 = dirty_rect.bottom() + 1, dirty_rect.right() + 1
    expected_slice = mask_view[y0:y1, x0:x1]
    np.testing.assert_array_equal(snippet_view, expected_slice)


def test_mask_reorder_commit_targets_active_layer(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_image_a = QImage(64, 64, QImage.Format_Grayscale8)
    mask_image_a.fill(0)
    mask_a = mask_manager.create_mask(mask_image_a)
    mask_manager.associate_mask_with_image(mask_a, image_id)
    mask_image_b = QImage(64, 64, QImage.Format_Grayscale8)
    mask_image_b.fill(0)
    mask_b = mask_manager.create_mask(mask_image_b)
    mask_manager.associate_mask_with_image(mask_b, image_id)
    assert qpane.setActiveMaskID(mask_a)
    layer_a = mask_manager.get_layer(mask_a)
    layer_b = mask_manager.get_layer(mask_b)
    assert layer_a is not None and layer_b is not None
    dirty_rect = QRect(5, 5, 8, 8)
    before = np.zeros((8, 8), dtype=np.uint8)
    after = before.copy()
    after[3, 3] = 255
    service.controller.recordStrokePatchFromArrays(mask_a, dirty_rect, before, after)
    assert service.controller.commitStroke(mask_a)
    mask_a_snapshot = layer_a.mask_image.copy()
    assert mask_manager.reorder_mask(image_id, mask_a, 0)
    assert qpane.setActiveMaskID(mask_b)
    after_b = before.copy()
    after_b[1, 6] = 255
    service.controller.recordStrokePatchFromArrays(mask_b, dirty_rect, before, after_b)
    assert service.controller.commitStroke(mask_b)
    mask_a_view, _ = qimage_to_numpy_view_grayscale8(layer_a.mask_image)
    mask_a_expected, _ = qimage_to_numpy_view_grayscale8(mask_a_snapshot)
    np.testing.assert_array_equal(mask_a_view, mask_a_expected)
    mask_b_view, _ = qimage_to_numpy_view_grayscale8(layer_b.mask_image)
    assert mask_b_view[dirty_rect.top() + 1, dirty_rect.left() + 6] == 255


def test_brush_stroke_commit_groups_segments(qpane_with_mask, qapp, monkeypatch):
    qpane, mask_manager, image_id = qpane_with_mask
    commit_patch_calls: list[tuple] = []
    commit_image_calls: list[None] = []
    original_commit_patches = MaskManager.commit_mask_patches
    original_commit_image = MaskManager.commit_mask_image

    def tracking_commit_patches(self, mask_id, patches, **kwargs):
        commit_patch_calls.append(tuple(patches))
        return original_commit_patches(self, mask_id, patches, **kwargs)

    def tracking_commit_image(self, mask_id, new_image, **kwargs):
        commit_image_calls.append(None)
        return original_commit_image(self, mask_id, new_image, **kwargs)

    monkeypatch.setattr(MaskManager, "commit_mask_patches", tracking_commit_patches)
    monkeypatch.setattr(MaskManager, "commit_mask_image", tracking_commit_image)
    service = qpane.mask_service
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(Qt.black)
    assert not layer.mask_image.isNull()
    service.pushActiveMaskState()

    def _paint_segment(start: QPoint, end: QPoint) -> None:
        segment_layer = mask_manager.get_layer(mask_id)
        assert segment_layer is not None
        working_image = segment_layer.mask_image.copy()
        painter = QPainter(working_image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(Qt.white)
        pen.setWidth(4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(start, end)
        painter.end()
        service.controller.updateStrokeImage(mask_id, working_image)

    _paint_segment(QPoint(2, 2), QPoint(2, 2))
    _paint_segment(QPoint(2, 2), QPoint(4, 4))
    _paint_segment(QPoint(4, 4), QPoint(6, 4))
    assert service.controller.commitStroke(mask_id)
    assert layer.mask_image.pixelColor(4, 4).value() > 0
    assert commit_patch_calls
    assert not commit_image_calls
    first_patches = commit_patch_calls[0]
    assert first_patches
    first_patch = first_patches[0]
    assert first_patch.mask.dtype == np.bool_
    assert first_patch.mask.shape == (
        first_patch.rect.height(),
        first_patch.rect.width(),
    )
    assert np.count_nonzero(first_patch.mask) > 0
    assert service.undoActiveMaskEdit()
    assert layer.mask_image.pixelColor(4, 4).value() == 0
    assert service.undoActiveMaskEdit() is False
    assert service.redoActiveMaskEdit()
    assert layer.mask_image.pixelColor(4, 4).value() > 0
    _cleanup_qpane(qpane, qapp)


def test_mask_tool_manager_stroke_undo_sequences(qapp, monkeypatch):
    qpane, image = _prepare_qpane_with_mask_feature()
    service = qpane.mask_service
    assert service is not None
    commit_patch_calls: list[tuple] = []
    commit_image_calls: list[None] = []
    original_commit_patches = MaskManager.commit_mask_patches
    original_commit_image = MaskManager.commit_mask_image

    def tracking_commit_patches(self, mask_id, patches, **kwargs):
        commit_patch_calls.append(tuple(patches))
        return original_commit_patches(self, mask_id, patches, **kwargs)

    def tracking_commit_image(self, mask_id, new_image, **kwargs):
        commit_image_calls.append(None)
        return original_commit_image(self, mask_id, new_image, **kwargs)

    monkeypatch.setattr(MaskManager, "commit_mask_patches", tracking_commit_patches)
    monkeypatch.setattr(MaskManager, "commit_mask_image", tracking_commit_image)
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(Qt.black)
    assert not layer.mask_image.isNull()
    qpane.interaction.brush_size = 6
    paint_start = QPoint(3, 3)
    paint_end = QPoint(10, 10)
    _emit_brush_stroke(qpane, paint_start, paint_end, erase=False)
    assert layer.mask_image.pixelColor(paint_end).value() > 0
    erase_start = QPoint(5, 5)
    erase_end = QPoint(10, 10)
    _emit_brush_stroke(qpane, erase_start, erase_end, erase=True)
    assert layer.mask_image.pixelColor(erase_end).value() == 0
    assert len(commit_patch_calls) == 2
    assert not commit_image_calls
    for patches in commit_patch_calls:
        assert patches
        for patch in patches:
            assert patch.mask.dtype == np.bool_
            assert patch.mask.shape == (
                patch.rect.height(),
                patch.rect.width(),
            )
            assert np.count_nonzero(patch.mask) > 0
    assert service.undoActiveMaskEdit() is True
    assert layer.mask_image.pixelColor(erase_end).value() > 0
    assert service.undoActiveMaskEdit() is True
    assert layer.mask_image.pixelColor(erase_end).value() == 0
    assert service.undoActiveMaskEdit() is False
    _cleanup_qpane(qpane, qapp)


def test_brush_single_click_undo(qpane_with_mask, qapp):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(Qt.black)
    assert not layer.mask_image.isNull()
    service.pushActiveMaskState()
    working_image = layer.mask_image.copy()
    working_image.setPixel(5, 5, 255)
    service.controller.updateStrokeImage(mask_id, working_image)
    assert service.controller.commitStroke(mask_id)
    assert layer.mask_image.pixelColor(5, 5).value() > 0
    assert service.undoActiveMaskEdit()
    assert layer.mask_image.pixelColor(5, 5).value() == 0
    assert service.redoActiveMaskEdit()
    assert layer.mask_image.pixelColor(5, 5).value() > 0
    _cleanup_qpane(qpane, qapp)


class _TrackingUndoProvider(MaskUndoProvider):
    def __init__(self) -> None:
        self.delegate = MaskLayerUndoProvider()
        self.initialized: list[uuid.UUID] = []
        self.disposed: list[uuid.UUID] = []
        self.submitted: list[uuid.UUID] = []
        self.undos: list[uuid.UUID] = []
        self.redos: list[uuid.UUID] = []
        self.limits: list[tuple[uuid.UUID, int]] = []

    def initialize_mask(self, mask_id: uuid.UUID, layer) -> None:
        self.initialized.append(mask_id)
        self.delegate.initialize_mask(mask_id, layer)

    def dispose_mask(self, mask_id: uuid.UUID) -> None:
        self.disposed.append(mask_id)
        self.delegate.dispose_mask(mask_id)

    def submit(self, mask_id: uuid.UUID, command, limit: int) -> None:
        self.submitted.append(mask_id)
        self.delegate.submit(mask_id, command, limit)

    def set_limit(self, mask_id: uuid.UUID, limit: int) -> None:
        self.limits.append((mask_id, limit))
        self.delegate.set_limit(mask_id, limit)

    def get_state(self, mask_id: uuid.UUID):
        return self.delegate.get_state(mask_id)

    def undo(self, mask_id: uuid.UUID):
        change = self.delegate.undo(mask_id)
        if change is not None:
            self.undos.append(mask_id)
        return change

    def redo(self, mask_id: uuid.UUID):
        change = self.delegate.redo(mask_id)
        if change is not None:
            self.redos.append(mask_id)
        return change


def test_mask_service_accepts_custom_undo_provider(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    provider = _TrackingUndoProvider()
    service.setUndoProvider(provider)
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert mask_id in provider.initialized
    assert (mask_id, qpane.settings.mask_undo_limit) in provider.limits
    state = service.getUndoState(mask_id)
    assert state is not None
    assert state.undo_depth == 0
    assert qpane.setActiveMaskID(mask_id)
    assert service.pushActiveMaskState()
    updated_image = QImage(base_image.size(), QImage.Format_Grayscale8)
    updated_image.fill(42)
    assert service.controller.apply_mask_image(mask_id, updated_image)
    state_after_apply = service.getUndoState(mask_id)
    assert state_after_apply is not None
    assert state_after_apply.undo_depth == 1
    assert provider.submitted
    service.undoActiveMaskEdit()
    assert provider.undos[-1] == mask_id
    state_after_undo = service.getUndoState(mask_id)
    assert state_after_undo is not None
    assert state_after_undo.redo_depth == 1
    service.redoActiveMaskEdit()
    assert provider.redos[-1] == mask_id
    state_after_redo = service.getUndoState(mask_id)
    assert state_after_redo is not None
    assert state_after_redo.undo_depth == 1
    assert qpane.removeMaskFromImage(image_id, mask_id)
    assert mask_id in provider.disposed


def test_qpane_emits_mask_undo_signal(qpane_with_mask, qapp):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    base_image = qpane.catalog().currentImage()
    assert base_image is not None
    mask_id = service.createBlankMask(base_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    captured: list[uuid.UUID] = []

    def _listener(mid: uuid.UUID) -> None:
        captured.append(mid)

    qpane.maskUndoStackChanged.connect(_listener)
    try:
        assert service.pushActiveMaskState()
        working = QImage(base_image.size(), QImage.Format_Grayscale8)
        working.fill(128)
        assert service.controller.apply_mask_image(mask_id, working)
        qapp.processEvents()
    finally:
        qpane.maskUndoStackChanged.disconnect(_listener)
    assert captured
    assert captured[-1] == mask_id
    undo_state = qpane.getMaskUndoState(mask_id)
    assert undo_state is not None
    assert undo_state.undo_depth == 1


def test_set_image_preserves_mask_cache(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    assert service is not None
    mask_id = service.createBlankMask(qpane.original_image.size())
    assert mask_id is not None
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(Qt.white)
    pixmap = service.getColorizedMask(layer)
    assert pixmap is not None
    usage_before = service.controller.cache_usage_bytes
    assert usage_before > 0
    new_image = qpane.original_image.copy()
    new_id = uuid.uuid4()
    new_map = QPane.imageMapFromLists([new_image], [None], [new_id])
    qpane.setImagesByID(new_map, new_id)
    usage_after = service.controller.cache_usage_bytes
    assert usage_after == usage_before
    assert usage_after > 0


def test_invalidate_mask_caches_for_image(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    assert service is not None
    mask_id = service.createBlankMask(qpane.original_image.size())
    assert mask_id is not None
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(Qt.white)
    assert service.getColorizedMask(layer) is not None
    assert service.controller.cache_usage_bytes > 0
    service.invalidateMaskCachesForImage(image_id)
    assert service.controller.cache_usage_bytes == 0


def test_mask_patch_undo_updates_overlay_cache_in_place(qpane_with_mask):
    qpane, mask_manager, _ = qpane_with_mask
    service = qpane.mask_service
    assert service is not None
    mask_id = service.createBlankMask(qpane.original_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    controller = service.controller
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(0)
    assert controller.pushUndoState()
    working = layer.mask_image.copy()
    working.setPixel(0, 0, 255)
    controller.updateStrokeImage(mask_id, working)
    assert controller.commitStroke(mask_id)
    service.updateMaskRegion(QRect(0, 0, 1, 1), layer)
    pixmap = service.getColorizedMask(layer)
    assert pixmap is not None
    cache_key = controller._cache_key(mask_id, None)
    cached_pixmap = controller._colorized_mask_cache.get(cache_key)
    assert cached_pixmap is pixmap
    assert pixmap.toImage().pixelColor(0, 0).alpha() > 0
    assert service.undoActiveMaskEdit()
    assert controller._colorized_mask_cache.get(cache_key) is None
    pixmap_after = service.getColorizedMask(layer)
    assert pixmap_after is not None
    assert pixmap_after is not cached_pixmap
    assert pixmap_after.toImage().pixelColor(0, 0).alpha() == 0
    assert layer.mask_image.pixelColor(0, 0).value() == 0


def test_mask_image_command_undo_updates_overlay_cache(qpane_with_mask):
    qpane, mask_manager, _ = qpane_with_mask
    service = qpane.mask_service
    assert service is not None
    mask_id = service.createBlankMask(qpane.original_image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    controller = service.controller
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    base_image = layer.mask_image.copy()
    assert not base_image.isNull()
    new_image = QImage(base_image.size(), QImage.Format_Grayscale8)
    new_image.fill(128)
    assert controller.apply_mask_image(mask_id, new_image, before=base_image)
    service.updateMaskRegion(layer.mask_image.rect(), layer)
    pixmap = service.getColorizedMask(layer)
    assert pixmap is not None
    cache_key = controller._cache_key(mask_id, None)
    cached_pixmap = controller._colorized_mask_cache.get(cache_key)
    assert cached_pixmap is pixmap
    assert pixmap.toImage().pixelColor(0, 0).alpha() > 0
    assert service.undoActiveMaskEdit()
    assert controller._colorized_mask_cache.get(cache_key) is None
    pixmap_after = service.getColorizedMask(layer)
    assert pixmap_after is not None
    assert pixmap_after is not cached_pixmap
    assert pixmap_after.toImage().pixelColor(0, 0).alpha() == 0
    assert layer.mask_image.pixelColor(0, 0).value() == 0


def test_mask_activation_deferral_ratio(qpane_with_mask):
    qpane, mask_manager, _ = qpane_with_mask
    service = qpane.mask_service
    large_image = QImage(64, 64, QImage.Format_Grayscale8)
    large_image.fill(255)
    small_image = QImage(16, 16, QImage.Format_Grayscale8)
    small_image.fill(255)
    large_mask_id = mask_manager.create_mask(large_image)
    small_mask_id = mask_manager.create_mask(small_image)
    assert (
        service._should_defer_activation_signals(large_mask_id, small_mask_id) is True
    )
    assert (
        service._should_defer_activation_signals(small_mask_id, large_mask_id) is False
    )
    assert service._should_defer_activation_signals(None, large_mask_id) is False
    assert service._should_defer_activation_signals(large_mask_id, None) is False


def test_mask_activation_signal_timing(qapp, qpane_with_mask):
    qpane, mask_manager, _ = qpane_with_mask
    service = qpane.mask_service
    controller = service.controller
    large_image_id = uuid.uuid4()
    large_image = QImage(64, 64, QImage.Format_Grayscale8)
    large_image.fill(255)
    large_mask_id = mask_manager.create_mask(large_image)
    mask_manager.associate_mask_with_image(large_mask_id, large_image_id)
    small_image_id = uuid.uuid4()
    small_image = QImage(16, 16, QImage.Format_Grayscale8)
    small_image.fill(255)
    small_mask_id = mask_manager.create_mask(small_image)
    mask_manager.associate_mask_with_image(small_mask_id, small_image_id)
    assert mask_manager.get_mask_ids_for_image(large_image_id) == [large_mask_id]
    assert mask_manager.get_mask_ids_for_image(small_image_id) == [small_mask_id]
    controller.setActiveMaskID(large_mask_id)
    maskless_image_id = uuid.uuid4()
    emissions: list[tuple[str, uuid.UUID | None]] = []

    def on_props() -> None:
        emissions.append(("props", controller.get_active_mask_id()))

    def on_mask(mask_id, _rect) -> None:
        emissions.append(("mask", mask_id))

    controller.active_mask_properties_changed.connect(on_props)
    controller.mask_updated.connect(on_mask)
    try:
        emissions.clear()
        assert service.ensureTopMaskActiveForImage(small_image_id) is True
        assert emissions == []
        qapp.processEvents()
        qapp.processEvents()
        assert ("props", small_mask_id) in emissions
        assert ("mask", small_mask_id) in emissions
        emissions.clear()
        controller.setActiveMaskID(small_mask_id)
        emissions.clear()
        assert service.ensureTopMaskActiveForImage(large_image_id) is True
        assert ("props", large_mask_id) in emissions
        assert ("mask", large_mask_id) in emissions
        emissions.clear()
        controller.setActiveMaskID(large_mask_id)
        emissions.clear()
        assert service.ensureTopMaskActiveForImage(maskless_image_id) is False
        assert ("props", None) in emissions
        assert ("mask", None) in emissions
    finally:
        controller.active_mask_properties_changed.disconnect(on_props)
        controller.mask_updated.disconnect(on_mask)


def test_mask_activation_set_active_flags(qapp, qpane_with_mask, monkeypatch):
    qpane, mask_manager, _ = qpane_with_mask
    service = qpane.mask_service
    controller = service.controller
    large_image_id = uuid.uuid4()
    large_image = QImage(64, 64, QImage.Format_Grayscale8)
    large_image.fill(255)
    large_mask_id = mask_manager.create_mask(large_image)
    mask_manager.associate_mask_with_image(large_mask_id, large_image_id)
    small_image_id = uuid.uuid4()
    small_image = QImage(16, 16, QImage.Format_Grayscale8)
    small_image.fill(255)
    small_mask_id = mask_manager.create_mask(small_image)
    mask_manager.associate_mask_with_image(small_mask_id, small_image_id)
    calls: list[tuple[uuid.UUID | None, bool, bool]] = []
    original = controller.setActiveMaskID

    def capture(mask_id, *, warm_cache=True, emit_signals=True):
        calls.append((mask_id, warm_cache, emit_signals))
        return original(mask_id, warm_cache=warm_cache, emit_signals=emit_signals)

    monkeypatch.setattr(controller, "setActiveMaskID", capture)
    controller.setActiveMaskID(large_mask_id)
    calls.clear()
    assert service.ensureTopMaskActiveForImage(small_image_id) is True
    assert calls
    assert calls[-1] == (small_mask_id, False, False)
    controller.setActiveMaskID(small_mask_id)
    calls.clear()
    assert service.ensureTopMaskActiveForImage(large_image_id) is True
    assert calls
    assert calls[-1] == (large_mask_id, True, True)
    controller.setActiveMaskID(large_mask_id)
    calls.clear()
    maskless_image_id = uuid.uuid4()
    assert service.ensureTopMaskActiveForImage(maskless_image_id) is False
    assert calls
    assert calls[-1] == (None, False, True)


def test_activation_prefetch_runs_when_pending(qapp, qpane_with_mask, monkeypatch):
    qpane, mask_manager, _ = qpane_with_mask
    service = qpane.mask_service
    large_image_id = uuid.uuid4()
    large_mask_image = QImage(512, 512, QImage.Format_Grayscale8)
    large_mask_image.fill(220)
    large_mask_id = mask_manager.create_mask(large_mask_image)
    mask_manager.associate_mask_with_image(large_mask_id, large_image_id)
    small_image_id = uuid.uuid4()
    small_mask_image = QImage(128, 128, QImage.Format_Grayscale8)
    small_mask_image.fill(180)
    small_mask_id = mask_manager.create_mask(small_mask_image)
    mask_manager.associate_mask_with_image(small_mask_id, small_image_id)
    service.controller.setActiveMaskID(large_mask_id)
    service._prefetch_handles[small_image_id] = object()
    calls: list[tuple[uuid.UUID | None, str, tuple]] = []

    def recording_prefetch(image_id_arg, *, reason, scales=None):
        calls.append((image_id_arg, reason, tuple(scales or ())))
        return True

    monkeypatch.setattr(service, "prefetchColorizedMasks", recording_prefetch)
    try:
        result = service.ensureTopMaskActiveForImage(small_image_id)
        assert result is True
        assert calls
        scheduled_image, reason, scales = calls[-1]
        assert scheduled_image == small_image_id
        assert reason == "activation"
    finally:
        service._prefetch_handles.pop(small_image_id, None)


def test_mask_activation_schedule_usage(qapp, qpane_with_mask, monkeypatch):
    qpane, mask_manager, _ = qpane_with_mask
    service = qpane.mask_service
    large_image_id = uuid.uuid4()
    large_image = QImage(64, 64, QImage.Format_Grayscale8)
    large_image.fill(255)
    large_mask_id = mask_manager.create_mask(large_image)
    mask_manager.associate_mask_with_image(large_mask_id, large_image_id)
    small_image_id = uuid.uuid4()
    small_image = QImage(16, 16, QImage.Format_Grayscale8)
    small_image.fill(255)
    small_mask_id = mask_manager.create_mask(small_image)
    mask_manager.associate_mask_with_image(small_mask_id, small_image_id)
    calls: list[tuple[uuid.UUID | None, bool]] = []

    def capture_schedule(
        self,
        mask_id: uuid.UUID | None,
        *,
        warm_cache: bool = False,
        image_id=None,
    ) -> None:
        calls.append((mask_id, warm_cache))

    monkeypatch.setattr(
        MaskService,
        "_schedule_activation_signals",
        capture_schedule,
        raising=False,
    )
    controller = service.controller
    controller.setActiveMaskID(large_mask_id)
    calls.clear()
    assert service.ensureTopMaskActiveForImage(small_image_id) is True
    assert calls == [(small_mask_id, True)]
    calls.clear()
    assert service.ensureTopMaskActiveForImage(large_image_id) is True
    assert calls == []
    maskless_image_id = uuid.uuid4()
    assert service.ensureTopMaskActiveForImage(maskless_image_id) is False
    assert calls == []
    assert service.ensureTopMaskActiveForImage(large_image_id) is True
    assert calls == []


def test_mask_activation_resumes_when_image_has_no_masks(qpane_with_mask, monkeypatch):
    qpane, _mask_manager, _ = qpane_with_mask
    service = qpane.mask_service
    scheduled: list[tuple[uuid.UUID | None, bool, uuid.UUID | None]] = []
    resumed: list[uuid.UUID | None] = []

    def capture_schedule(
        self,
        mask_id: uuid.UUID | None,
        *,
        warm_cache: bool = False,
        image_id=None,
    ) -> None:
        scheduled.append((mask_id, warm_cache, image_id))

    monkeypatch.setattr(
        MaskService,
        "_schedule_activation_signals",
        capture_schedule,
        raising=False,
    )
    monkeypatch.setattr(
        service, "_resume_overlays_cb", lambda image_id: resumed.append(image_id)
    )
    pending_image_id = uuid.uuid4()
    service._pending_activation_images.add(pending_image_id)
    assert service.ensureTopMaskActiveForImage(pending_image_id) is False
    assert scheduled == [(None, False, pending_image_id)]
    assert resumed == []
    scheduled.clear()
    maskless_image_id = uuid.uuid4()
    assert service.ensureTopMaskActiveForImage(maskless_image_id) is False
    assert scheduled == []
    assert resumed == [maskless_image_id]
    service._pending_activation_images.discard(pending_image_id)
    service._pending_activation_images.discard(maskless_image_id)


def test_mask_prefetch_warms_masks(qapp):
    executor = StubExecutor(auto_finish=True)
    qpane = QPane(features=("mask",), task_executor=executor)
    qpane.resize(32, 32)
    try:
        service = qpane.mask_service
        manager = service.manager
        controller = service.controller
        catalog = qpane.catalog()
        image = QImage(8, 8, QImage.Format_Grayscale8)
        image.fill(255)
        image_id = uuid.uuid4()
        mask_id = manager.create_mask(image)
        layer = manager.get_layer(mask_id)
        assert layer is not None
        layer.mask_image.fill(128)
        manager.associate_mask_with_image(mask_id, image_id)
        source_image = QImage(8, 8, QImage.Format_ARGB32)
        source_image.fill(Qt.white)
        catalog.addImage(image_id, source_image, None)
        catalog.setCurrentImageID(image_id)
        assert service.prefetchColorizedMasks(image_id, reason="test") is True
        executor.drain_all()
        executor.drain_all()
        qapp.processEvents()
        metrics = controller.snapshot_metrics()
        assert metrics.prefetch_completed >= 1
        assert metrics.entry_count >= 1
        scale_key = controller._normalize_scale_key(0.25)
        if scale_key is not None:
            cache_key = controller._cache_key(mask_id, scale_key)
            assert cache_key in controller._colorized_mask_cache
            scaled_pixmap = controller._colorized_mask_cache[cache_key]
            assert scaled_pixmap.size() == controller._target_scaled_size(
                layer.mask_image.size(), scale_key
            )
        records = service._diagnostics_provider(qpane)
        prefetch_rows = [
            record for record in records if record.label == "Mask|Prefetch"
        ]
        assert len(prefetch_rows) == 1
        assert "scheduled=" in prefetch_rows[0].value
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_prefetch_respects_deferral_ratio(qapp):
    executor = StubExecutor(auto_finish=True)
    qpane = QPane(features=("mask",), task_executor=executor)
    qpane.resize(32, 32)
    try:
        service = qpane.mask_service
        manager = service.manager
        controller = service.controller
        large = QImage(64, 64, QImage.Format_Grayscale8)
        large.fill(255)
        small = QImage(16, 16, QImage.Format_Grayscale8)
        small.fill(255)
        large_id = uuid.uuid4()
        small_id = uuid.uuid4()
        large_mask = manager.create_mask(large)
        small_mask = manager.create_mask(small)
        manager.associate_mask_with_image(large_mask, large_id)
        manager.associate_mask_with_image(small_mask, small_id)
        controller.setActiveMaskID(large_mask)
        skipped = service.prefetchColorizedMasks(small_id, reason="test")
        assert skipped is False
        assert service._prefetch_stats.skipped >= 1
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_prefetch_diagnostics_available_by_default(qapp):
    executor = StubExecutor(auto_finish=True)
    qpane = QPane(features=("mask",), task_executor=executor)
    qpane.resize(16, 16)
    try:
        service = qpane.mask_service
        records = service._diagnostics_provider(qpane)
        prefetch_rows = [
            record for record in records if record.label == "Mask|Prefetch"
        ]
        assert len(prefetch_rows) == 1
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_prefetch_flag_follows_config(qapp):
    qpane = QPane(features=("mask",))
    qpane.resize(16, 16)
    try:
        service = qpane.mask_service
        assert service is not None
        assert service._prefetch_enabled is True
        qpane.applySettings(mask_prefetch_enabled=False)
        assert service._prefetch_enabled is False
        qpane.applySettings(mask_prefetch_enabled=True)
        assert service._prefetch_enabled is True
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_colorize_metrics_and_logging(qapp, monkeypatch, caplog):
    qpane = QPane(features=("mask",))
    qpane.resize(32, 32)
    try:
        service = qpane.mask_service
        controller = service.controller
        manager = service.manager
        mask_image = QImage(16, 16, QImage.Format_Grayscale8)
        mask_image.fill(200)
        mask_id = manager.create_mask(mask_image)
        layer = manager.get_layer(mask_id)
        assert layer is not None
        metrics = controller.snapshot_metrics()
        assert metrics.colorize_samples == 0
        pixmap = controller.get_colorized_mask(layer)
        assert pixmap is not None
        metrics_after = controller.snapshot_metrics()
        assert metrics_after.colorize_samples >= 1
        assert metrics_after.colorize_last_ms is not None
        assert metrics_after.colorize_last_source == "cache_miss"
        prefetch_image = controller.prepare_colorized_mask(layer, mask_id=mask_id)
        assert prefetch_image is not None
        metrics_prefetch = controller.snapshot_metrics()
        assert metrics_prefetch.colorize_last_source == "prefetch"
        assert metrics_prefetch.colorize_samples == metrics_after.colorize_samples + 1
        original_compose = controller._compose_colorized_image

        def slow_compose(image, color):
            time.sleep(0.03)
            return original_compose(image, color)

        monkeypatch.setattr(controller, "_compose_colorized_image", slow_compose)
        controller.invalidate_mask_cache(mask_id, reason="test")
        slow_before = controller.snapshot_metrics().colorize_slow_count
        with caplog.at_level(logging.WARNING):
            controller.get_colorized_mask(layer)
        metrics_slow = controller.snapshot_metrics()
        assert metrics_slow.colorize_slow_count >= 1
        assert metrics_slow.colorize_slow_count > slow_before
        assert metrics_slow.colorize_max_ms is not None
    finally:
        _cleanup_qpane(qpane, qapp)


def test_commit_prefetched_mask_emits_update(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    controller = service.controller
    mask_image = QImage(8, 8, QImage.Format_Grayscale8)
    mask_image.fill(128)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    colorized = controller.prepare_colorized_mask(layer, mask_id=mask_id)
    assert colorized is not None
    captured: list[tuple[uuid.UUID | None, QRect]] = []

    def _handler(mid, rect):
        captured.append((mid, rect))

    controller.mask_updated.connect(_handler)
    try:
        controller.commit_prefetched_mask(mask_id, layer, colorized)
    finally:
        controller.mask_updated.disconnect(_handler)
    assert captured
    last_id, last_rect = captured[-1]
    assert last_id == mask_id
    assert isinstance(last_rect, QRect)


def test_prefetch_deferred_while_mask_busy(qpane_with_mask):
    """Defer prefetched overlays when stroke work is still in flight."""
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    controller = service.controller
    pipeline = service._stroke_pipeline
    mask_id = service.createBlankMask(qpane.original_image.size())
    assert mask_id is not None
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    colorized = controller.prepare_colorized_mask(layer, mask_id=mask_id)
    assert colorized is not None
    overlay = PrefetchedOverlay(mask_id=mask_id, image=colorized)
    controller._prefetched_images.clear()
    controller._prefetched_scaled_images.clear()
    controller._colorized_mask_cache.clear()
    pipeline._preview_states[mask_id] = object()
    assert pipeline.is_mask_busy(mask_id) is True
    service._consume_prefetch_results(
        image_id=image_id,
        warmed=(overlay,),
        failures={},
        duration_ms=1.0,
        error=None,
    )
    assert mask_id in service._pending_prefetched_overlays
    assert not controller._colorized_mask_cache
    pipeline.reset_state(mask_id, request_redraw=False)
    assert mask_id not in service._pending_prefetched_overlays
    assert controller._colorized_mask_cache


def test_prefetched_overlay_reused_after_eviction(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    controller = service.controller
    mask_image = QImage(8, 8, QImage.Format_Grayscale8)
    mask_image.fill(200)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    prefetch_image = controller.prepare_colorized_mask(layer, mask_id=mask_id)
    assert prefetch_image is not None
    controller.commit_prefetched_mask(mask_id, layer, prefetch_image)
    freed = controller.drop_oldest_cached_mask(reason="test")
    assert freed > 0
    metrics_before = controller.snapshot_metrics()
    pixmap = controller.get_colorized_mask(layer)
    assert pixmap is not None
    metrics_after = controller.snapshot_metrics()
    assert metrics_after.misses == metrics_before.misses
    assert metrics_after.colorize_samples == metrics_before.colorize_samples


def test_prefetched_scaled_overlay_reused_after_eviction(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    controller = service.controller
    controller.clear_cache()
    mask_image = QImage(64, 64, QImage.Format_Grayscale8)
    mask_image.fill(200)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    base_image = controller.prepare_colorized_mask(layer, mask_id=mask_id)
    assert base_image is not None
    scale_value = 0.25
    target_size = controller._target_scaled_size(base_image.size(), scale_value)
    scaled_image = base_image.scaled(
        target_size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    controller.commit_prefetched_mask(
        mask_id, layer, base_image, scaled=[(scale_value, scaled_image)]
    )
    scale_key = controller._normalize_scale_key(scale_value)
    assert scale_key is not None
    controller._colorized_mask_cache.pop(controller._cache_key(mask_id, scale_key))
    metrics_before = controller.snapshot_metrics()
    pixmap = controller.get_colorized_mask(layer, scale=scale_value)
    assert pixmap is not None
    assert pixmap.size() == target_size
    metrics_after = controller.snapshot_metrics()
    assert metrics_after.misses == metrics_before.misses
    assert metrics_after.hits == metrics_before.hits + 1


def test_update_mask_region_clears_prefetched_buffers(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    controller = service.controller
    controller.clear_cache()
    mask_image = QImage(32, 32, QImage.Format_Grayscale8)
    mask_image.fill(220)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    base_image = controller.prepare_colorized_mask(layer, mask_id=mask_id)
    assert base_image is not None
    scale_value = 0.5
    target_size = controller._target_scaled_size(base_image.size(), scale_value)
    scaled_image = base_image.scaled(
        target_size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    controller.commit_prefetched_mask(
        mask_id, layer, base_image, scaled=[(scale_value, scaled_image)]
    )
    controller.setActiveMaskID(mask_id)
    assert mask_id in controller._prefetched_images
    assert mask_id in controller._prefetched_scaled_images
    controller.updateMaskRegion(QRect(0, 0, 4, 4), layer)
    assert mask_id not in controller._prefetched_images
    assert mask_id not in controller._prefetched_scaled_images


def test_ensure_top_mask_defers_when_prefetch_active(monkeypatch, qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    controller = service.controller
    mask_image = QImage(8, 8, QImage.Format_Grayscale8)
    mask_image.fill(128)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    service._prefetch_handles[image_id] = object()
    original_set_active = controller.setActiveMaskID
    captured_call = {}

    def tracking_set_active(mask_id_arg, *, warm_cache, emit_signals):
        captured_call["args"] = (mask_id_arg, warm_cache, emit_signals)
        return original_set_active(
            mask_id_arg, warm_cache=warm_cache, emit_signals=emit_signals
        )

    monkeypatch.setattr(controller, "setActiveMaskID", tracking_set_active)
    scheduled_calls = []

    def tracking_schedule(
        mask_id_arg,
        *,
        warm_cache=False,
        image_id=None,
    ):
        scheduled_calls.append((mask_id_arg, warm_cache))

    monkeypatch.setattr(service, "_schedule_activation_signals", tracking_schedule)
    try:
        assert service.ensureTopMaskActiveForImage(image_id) is True
    finally:
        service._prefetch_handles.pop(image_id, None)
    assert captured_call["args"] == (mask_id, False, False)
    assert scheduled_calls == [(mask_id, False)]


def test_drop_oldest_cached_mask_respects_exclude(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = _mask_service(qpane)
    controller = service.controller
    mask_image = QImage(8, 8, QImage.Format_Grayscale8)
    mask_image.fill(64)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    pixmap = controller.get_colorized_mask(layer)
    assert pixmap is not None
    controller.setActiveMaskID(mask_id)
    metrics_before = controller.snapshot_metrics()
    freed = controller.drop_oldest_cached_mask(reason="test", exclude={mask_id})
    assert freed == 0
    metrics_after = controller.snapshot_metrics()
    assert metrics_after.entry_count == metrics_before.entry_count


def test_scaled_mask_cache_populates_scaled_entry_first(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    assert service is not None
    controller = service.controller
    controller.clear_cache()
    mask_image = QImage(64, 48, QImage.Format_Grayscale8)
    mask_image.fill(180)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    layer = mask_manager.get_layer(mask_id)
    assert layer is not None
    scale_value = 0.25
    scale_key = controller._normalize_scale_key(scale_value)
    assert scale_key is not None
    pixmap = controller.get_colorized_mask(layer, scale=scale_value)
    assert pixmap is not None
    scaled_entry = controller._cache_key(mask_id, scale_key)
    base_entry = controller._cache_key(mask_id, None)
    assert scaled_entry in controller._colorized_mask_cache
    assert base_entry not in controller._colorized_mask_cache
    expected_size = controller._target_scaled_size(layer.mask_image.size(), scale_key)
    assert pixmap.size() == expected_size


def test_swap_defers_update_until_mask_ready(qapp, monkeypatch):
    qpane, mask_manager, service = _make_test_qpane(qapp)
    try:
        catalog = qpane.catalog()
        masks = _masks(qpane)
        first_image = QImage(128, 128, QImage.Format_ARGB32)
        first_image.fill(Qt.white)
        second_image = QImage(48, 48, QImage.Format_ARGB32)
        second_image.fill(Qt.black)
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [first_image, second_image],
            paths=[None, None],
            ids=[first_id, second_id],
        )
        catalog.setImagesByID(image_map, first_id)
        catalog.setCurrentImageID(first_id)
        large_mask = QImage(128, 128, QImage.Format_Grayscale8)
        large_mask.fill(200)
        small_mask = QImage(48, 48, QImage.Format_Grayscale8)
        small_mask.fill(200)
        first_mask_id = mask_manager.create_mask(large_mask)
        mask_manager.associate_mask_with_image(first_mask_id, first_id)
        second_mask_id = mask_manager.create_mask(small_mask)
        mask_manager.associate_mask_with_image(second_mask_id, second_id)
        service.ensureTopMaskActiveForImage(first_id)
        update_calls: list[int] = []
        original_update = qpane.update

        def tracking_update():
            update_calls.append(1)
            return original_update()

        monkeypatch.setattr(qpane, "update", tracking_update)
        captured_schedule: dict[str, tuple] = {}

        def fake_schedule(self, mask_id, *, warm_cache=False, image_id=None):
            captured_schedule["args"] = (
                mask_id,
                warm_cache,
                image_id,
            )
            if image_id is not None:
                self._pending_activation_images.add(image_id)

        monkeypatch.setattr(
            service,
            "_schedule_activation_signals",
            MethodType(fake_schedule, service),
        )
        navigation_event = NavigationEvent(
            reason="unit-test",
            target_id=second_id,
            fit_view=None,
        )
        masks.on_navigation_started(navigation_event)
        catalog.setCurrentImageID(second_id)
        assert service.isActivationPending(second_id) is True
        assert masks.is_activation_pending(second_id) is True
        assert qpane.interaction.overlays_suspended is True
        assert captured_schedule["args"][2] == second_id
        before_resume = len(update_calls)
        service._pending_activation_images.discard(second_id)
        masks.handle_activation_ready(second_id, resumed_with_update=True)
        assert len(update_calls) >= before_resume
        assert service.isActivationPending(second_id) is False
        assert qpane.interaction.overlays_suspended is False
        assert masks.is_activation_pending(second_id) is False
    finally:
        _cleanup_qpane(qpane, qapp)


def test_activation_pending_flag_toggles(qapp, monkeypatch):
    qpane, mask_manager, service = _make_test_qpane(qapp)
    try:
        catalog = qpane.catalog()
        masks = _masks(qpane)
        image = QImage(96, 96, QImage.Format_ARGB32)
        image.fill(Qt.white)
        image_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists([image], paths=[None], ids=[image_id])
        catalog.setImagesByID(image_map, image_id)
        mask_image = QImage(96, 96, QImage.Format_Grayscale8)
        mask_image.fill(220)
        mask_id = mask_manager.create_mask(mask_image)
        mask_manager.associate_mask_with_image(mask_id, image_id)
        service._prefetch_handles[image_id] = object()
        called: list[tuple] = []

        def fake_schedule(self, mask_id, *, warm_cache=False, image_id=None):
            called.append((mask_id, warm_cache, image_id))

        monkeypatch.setattr(
            service,
            "_schedule_activation_signals",
            MethodType(fake_schedule, service),
        )
        assert service.ensureTopMaskActiveForImage(image_id) is True
        assert service.isActivationPending(image_id) is True
        assert called and called[0][2] == image_id
        service._pending_activation_images.discard(image_id)
        masks.handle_activation_ready(image_id, resumed_with_update=False)
        assert service.isActivationPending(image_id) is False
    finally:
        _cleanup_qpane(qpane, qapp)


def test_workflow_handles_activation_ready_without_explicit_image(qapp):
    qpane, _, service = _make_test_qpane(qapp)
    try:
        target_id = uuid.uuid4()
        event = NavigationEvent(
            reason="unit-test",
            target_id=target_id,
            fit_view=None,
        )
        masks = _masks(qpane)
        masks.on_navigation_started(event)
        service._pending_activation_images.add(target_id)
        masks.on_swap_applied(target_id, activation_pending=True)
        assert masks.is_activation_pending(target_id) is True
        assert qpane.interaction.overlays_suspended is True
        assert qpane.interaction.overlays_resume_pending is True
        service._pending_activation_images.discard(target_id)
        masks.handle_activation_ready(None, resumed_with_update=False)
        assert masks.is_activation_pending(target_id) is False
        assert qpane.interaction.overlays_suspended is False
        assert qpane.interaction.overlays_resume_pending is False
    finally:
        _cleanup_qpane(qpane, qapp)


def test_prefetched_masks_avoid_colorize_on_activation(qapp):
    qpane, mask_manager, service = _make_test_qpane(qapp)
    try:
        catalog = qpane.catalog()
        image = QImage(64, 64, QImage.Format_ARGB32)
        image.fill(Qt.white)
        image_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists([image], paths=[None], ids=[image_id])
        catalog.setImagesByID(image_map, image_id)
        catalog.setCurrentImageID(image_id)
        mask_image = QImage(64, 64, QImage.Format_Grayscale8)
        mask_image.fill(150)
        mask_id = mask_manager.create_mask(mask_image)
        mask_manager.associate_mask_with_image(mask_id, image_id)
        controller = service.controller
        layer = mask_manager.get_layer(mask_id)
        assert layer is not None
        base_image = controller.prepare_colorized_mask(layer, mask_id=mask_id)
        assert base_image is not None
        scale_value = 0.25
        target_size = controller._target_scaled_size(base_image.size(), scale_value)
        scaled_image = base_image.scaled(
            target_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        controller.commit_prefetched_mask(
            mask_id, layer, base_image, scaled=[(scale_value, scaled_image)]
        )
        controller.setActiveMaskID(mask_id)
        colorize_calls: list = []
        original_colorize = controller.colorize_mask

        def tracking_colorize(*args, **kwargs):
            colorize_calls.append((args, kwargs))
            return original_colorize(*args, **kwargs)

        controller.colorize_mask = tracking_colorize
        pixmap = service.getColorizedMask(layer, scale=scale_value)
        assert pixmap is not None
        assert colorize_calls == []
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_stroke_worker_matches_render_output(qpane_with_mask):
    qpane, mask_manager, image_id = qpane_with_mask
    service = qpane.mask_service
    controller = service.controller
    mask_image = QImage(16, 16, QImage.Format_Grayscale8)
    mask_image.fill(0)
    mask_id = mask_manager.create_mask(mask_image)
    mask_manager.associate_mask_with_image(mask_id, image_id)
    controller.setActiveMaskID(mask_id)
    dirty_rect = QRect(0, 0, 8, 8)
    segment = MaskStrokeSegmentPayload(
        start=(1, 1),
        end=(6, 6),
        brush_size=5,
        erase=False,
    )
    payload = MaskStrokePayload(
        segments=(segment,),
        stride=1,
        metadata={"segment_count": 1, "source": "unit-test"},
    )
    spec = controller.prepareStrokeJob(mask_id, dirty_rect, payload=payload)
    assert spec is not None
    results: list[MaskStrokeJobResult] = []
    worker = MaskStrokeWorker(spec=spec, finalize=lambda result: results.append(result))
    executor = StubExecutor(auto_finish=True)
    executor.submit(worker, category="mask_stroke")
    assert results, "Worker finalize callback should capture a result"
    result = results[0]
    expected_after, expected_preview = render_stroke_segments(
        before=spec.before,
        dirty_rect=spec.dirty_rect,
        segments=payload.segments,
    )
    assert np.array_equal(result.after, expected_after)
    assert result.preview_image is not None
    preview_np, _ = qimage_to_numpy_view_grayscale8(result.preview_image)
    expected_preview_np, _ = qimage_to_numpy_view_grayscale8(expected_preview)
    assert np.array_equal(preview_np, expected_preview_np)


def test_mask_snippet_worker_preserves_snippet_reference():
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage
    from qpane.masks.mask_service import MaskSnippetWorker

    class DummyController:
        def __init__(self) -> None:
            self.calls = 0

        def _colorize_with_metrics(self, snippet, color, *, mask_id=None, source=None):
            self.calls += 1
            return snippet

        def notify_async_colorize_complete(self, mask_id):
            pass

    class DummyService:
        def __init__(self, controller):
            self._mask_controller = controller

        def _consume_snippet_result(self, **kwargs):
            pass

    controller = DummyController()
    service = DummyService(controller)
    snippet = QImage(4, 4, QImage.Format_Grayscale8)
    snippet.fill(0)
    worker = MaskSnippetWorker(
        mask_id=uuid.uuid4(),
        dirty_rect=QRect(0, 0, 4, 4),
        snippet=snippet,
        color=QColor(Qt.white),
        controller=controller,
        service=service,
    )
    assert worker._snippet is snippet


def test_mask_detach_cancels_pending_strokes(qapp):
    executor = StubExecutor(auto_finish=False)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    service = qpane.mask_service
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    layer.mask_image.fill(0)
    qpane.interaction.brush_size = 5
    try:
        _queue_pending_stroke(qpane, QPoint(4, 4))
        pending_jobs = service.strokeDebugSnapshot().pending_jobs
        handles = tuple(pending_jobs.get(mask_id, ()))
        assert handles
        assert list(executor.pending_tasks())
        qpane.detachMaskService()
        pending_after = service.strokeDebugSnapshot().pending_jobs
        assert not pending_after.get(mask_id)
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert mask_id not in preview_tokens
        preview_states = service.strokeDebugSnapshot().preview_state_ids
        assert mask_id not in preview_states
        forced_drop = set(service.strokeDebugSnapshot().forced_drop_masks)
        assert mask_id in forced_drop
        cancelled_ids = {handle.task_id for handle in executor.cancelled}
        assert {handle.task_id for handle in handles}.issubset(cancelled_ids)
        executor.drain_all()
        assert not list(executor.pending_tasks())
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_switch_releases_pending_jobs(qapp):
    executor = StubExecutor(auto_finish=False)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    service = qpane.mask_service
    assert service is not None
    mask_a = service.createBlankMask(image.size())
    mask_b = service.createBlankMask(image.size())
    assert mask_a is not None and mask_b is not None
    assert qpane.setActiveMaskID(mask_a)
    layer_a = service.manager.get_layer(mask_a)
    layer_b = service.manager.get_layer(mask_b)
    assert layer_a is not None and layer_b is not None
    layer_a.mask_image.fill(0)
    layer_b.mask_image.fill(0)
    qpane.interaction.brush_size = 5
    try:
        _queue_pending_stroke(qpane, QPoint(3, 3))
        pending_jobs = service.strokeDebugSnapshot().pending_jobs
        handles = tuple(pending_jobs.get(mask_a, ()))
        assert handles
        assert service.getActiveMaskId() == mask_a
        qpane.setActiveMaskID(mask_b)
        assert service.getActiveMaskId() == mask_b
        pending_after = service.strokeDebugSnapshot().pending_jobs
        assert not pending_after.get(mask_a)
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert mask_a not in preview_tokens
        forced_drop = set(service.strokeDebugSnapshot().forced_drop_masks)
        assert mask_a in forced_drop
        cancelled_ids = {handle.task_id for handle in executor.cancelled}
        assert {handle.task_id for handle in handles}.issubset(cancelled_ids)
        executor.drain_all()
        assert not list(executor.pending_tasks())
    finally:
        _cleanup_qpane(qpane, qapp)


def test_cycle_masks_invalidates_pending_jobs(qapp):
    executor = StubExecutor(auto_finish=False)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    service = qpane.mask_service
    assert service is not None
    mask_a = service.createBlankMask(image.size())
    mask_b = service.createBlankMask(image.size())
    assert mask_a is not None and mask_b is not None
    assert qpane.setActiveMaskID(mask_a)
    layer_a = service.manager.get_layer(mask_a)
    layer_b = service.manager.get_layer(mask_b)
    assert layer_a is not None and layer_b is not None
    layer_a.mask_image.fill(0)
    layer_b.mask_image.fill(0)
    qpane.interaction.brush_size = 5
    image_id = qpane.catalog().currentImageID()
    assert image_id is not None
    try:
        _queue_pending_stroke(qpane, QPoint(2, 2))
        pending_jobs = service.strokeDebugSnapshot().pending_jobs
        handles = tuple(pending_jobs.get(mask_a, ()))
        assert handles
        service.cycleMasks(image_id, forward=True)
        assert service.getActiveMaskId() == mask_b
        pending_after = service.strokeDebugSnapshot().pending_jobs
        assert not pending_after.get(mask_a)
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert mask_a not in preview_tokens
        forced_drop = set(service.strokeDebugSnapshot().forced_drop_masks)
        assert mask_a in forced_drop
        cancelled_ids = {handle.task_id for handle in executor.cancelled}
        assert {handle.task_id for handle in handles}.issubset(cancelled_ids)
        executor.drain_all()
        assert not list(executor.pending_tasks())
    finally:
        _cleanup_qpane(qpane, qapp)


def test_remove_mask_cancels_pending_jobs(qapp):
    executor = StubExecutor(auto_finish=False)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    service = qpane.mask_service
    assert service is not None
    mask_a = service.createBlankMask(image.size())
    mask_b = service.createBlankMask(image.size())
    assert mask_a is not None and mask_b is not None
    assert qpane.setActiveMaskID(mask_a)
    layer_a = service.manager.get_layer(mask_a)
    layer_b = service.manager.get_layer(mask_b)
    assert layer_a is not None and layer_b is not None
    layer_a.mask_image.fill(0)
    layer_b.mask_image.fill(0)
    qpane.interaction.brush_size = 5
    image_id = qpane.catalog().currentImageID()
    assert image_id is not None
    try:
        _queue_pending_stroke(qpane, QPoint(6, 6))
        pending_jobs = service.strokeDebugSnapshot().pending_jobs
        handles = tuple(pending_jobs.get(mask_a, ()))
        assert handles
        assert qpane.removeMaskFromImage(image_id, mask_a) is True
        pending_after = service.strokeDebugSnapshot().pending_jobs
        assert not pending_after.get(mask_a)
        preview_tokens = service.strokeDebugSnapshot().preview_tokens
        assert mask_a not in preview_tokens
        forced_drop = set(service.strokeDebugSnapshot().forced_drop_masks)
        assert mask_a in forced_drop
        cancelled_ids = {handle.task_id for handle in executor.cancelled}
        assert {handle.task_id for handle in handles}.issubset(cancelled_ids)
        manager = service.manager
        assert manager.get_layer(mask_a) is None
        executor.drain_all()
        assert not list(executor.pending_tasks())
    finally:
        _cleanup_qpane(qpane, qapp)


def test_concurrent_strokes_survive_mask_reorder(qapp):
    """Pending mask jobs should drop cleanly when mask order changes mid-stroke."""
    executor = StubExecutor(auto_finish=False)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    service = qpane.mask_service
    assert service is not None
    image_id = qpane.catalog().currentImageID()
    assert image_id is not None
    mask_a = service.createBlankMask(image.size())
    mask_b = service.createBlankMask(image.size())
    assert mask_a is not None and mask_b is not None
    layer_a = service.manager.get_layer(mask_a)
    layer_b = service.manager.get_layer(mask_b)
    assert layer_a is not None and layer_b is not None
    layer_a.mask_image.fill(0)
    layer_b.mask_image.fill(0)
    qpane.interaction.brush_size = 5
    try:
        assert qpane.setActiveMaskID(mask_a)
        _queue_pending_stroke(qpane, QPoint(2, 2))
        service.cycleMasks(image_id, forward=True)
        assert service.getActiveMaskId() == mask_b
        _queue_pending_stroke(qpane, QPoint(11, 11))
        intermediate_pending, intermediate_tokens = drain_mask_jobs(
            qpane, executor=executor
        )
        assert not intermediate_pending
        assert not intermediate_tokens
        assert qpane.setActiveMaskID(mask_a)
        _queue_pending_stroke(qpane, QPoint(6, 6))
        _queue_pending_stroke(qpane, QPoint(7, 7))
        pending, tokens = drain_mask_jobs(qpane, executor=executor)
        assert not pending
        assert not tokens
        snapshot_a = snapshot_mask_layer(layer_a)
        snapshot_b = snapshot_mask_layer(layer_b)
        assert snapshot_a[7, 7] == 255
        assert snapshot_a[6, 6] == 255
        assert snapshot_a[2, 2] == 0
        assert snapshot_b[11, 11] == 255
        forced_drop = set(service.strokeDebugSnapshot().forced_drop_masks)
        assert mask_a not in forced_drop
        controller = service.controller
        assert controller.getMaskGeneration(mask_a) >= 1
        assert controller.getMaskGeneration(mask_b) >= 1
    finally:
        _cleanup_qpane(qpane, qapp)


@pytest.mark.parametrize("brush_size", [3, 6])
@pytest.mark.parametrize("erase", [False, True])
def test_worker_job_matches_render_result(qapp, monkeypatch, brush_size, erase):
    """Worker-produced slices must match the MaskController merge path byte-for-byte."""
    qpane, image = _prepare_qpane_with_mask_feature()
    service = qpane.mask_service
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    layer = service.manager.get_layer(mask_id)
    assert layer is not None
    fill_value = 255 if erase else 0
    layer.mask_image.fill(fill_value)
    controller = service.controller
    rect = QRect(0, 0, 8, 8)
    segment = MaskStrokeSegmentPayload(
        start=(2, 2),
        end=(6, 6),
        brush_size=brush_size,
        erase=erase,
    )
    payload = MaskStrokePayload(
        segments=(segment,),
        stride=1,
        metadata={"source": "unit-test"},
    )
    spec = controller.prepareStrokeJob(
        mask_id,
        rect,
        payload=payload,
        metadata=dict(payload.metadata),
    )
    assert spec is not None
    after_slice, preview_image = render_stroke_segments(
        before=spec.before,
        dirty_rect=spec.dirty_rect,
        segments=payload.segments,
    )
    original_record = MaskController.recordStrokePatchFromArrays
    patch_calls: list[tuple[uuid.UUID, QRect, np.ndarray, np.ndarray]] = []

    def _tracking_record(self, target_mask_id, target_rect, before_array, after_array):
        patch_calls.append(
            (
                target_mask_id,
                QRect(target_rect),
                np.array(before_array, copy=True),
                np.array(after_array, copy=True),
            )
        )
        return original_record(
            self, target_mask_id, target_rect, before_array, after_array
        )

    monkeypatch.setattr(MaskController, "recordStrokePatchFromArrays", _tracking_record)
    job = MaskStrokeJobResult(
        mask_id=mask_id,
        generation=spec.generation,
        dirty_rect=spec.dirty_rect,
        before=spec.before,
        after=after_slice,
        preview_image=preview_image,
        payload=payload,
        metadata=dict(payload.metadata),
    )
    assert controller.applyStrokeJob(job, emit_mask_updated=False)
    view = snapshot_mask_layer(layer)
    local = view[
        rect.top() : rect.top() + rect.height(),
        rect.left() : rect.left() + rect.width(),
    ]
    assert np.array_equal(local, after_slice)
    assert patch_calls
    recorded_mask, recorded_rect, recorded_before, recorded_after = patch_calls[-1]
    assert recorded_mask == mask_id
    assert recorded_rect == rect
    assert np.array_equal(recorded_before, spec.before)
    assert np.array_equal(recorded_after, after_slice)
