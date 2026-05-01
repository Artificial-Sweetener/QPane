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

"""Tests ensuring full coverage of the QPane public API surface."""

from pathlib import Path
import uuid
import pytest
from PySide6.QtCore import QRectF, QPoint, QSize
from PySide6.QtGui import QImage, Qt
from qpane import QPane, Config, ExtensionTool


def _cleanup_qpane(qpane, qapp):
    qpane.deleteLater()
    qapp.processEvents()


def _solid_image(width=32, height=32, color=Qt.white):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(color)
    return image


def test_placeholder_active_delegates(qapp, tmp_path):
    # Configure a placeholder to ensure it is active
    placeholder_path = tmp_path / "placeholder.png"
    _solid_image().save(str(placeholder_path))
    config = Config(placeholder={"source": str(placeholder_path)})
    qpane = QPane(config=config, features=())
    try:
        assert qpane.placeholderActive() is True
        image = _solid_image()
        image_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists([image], [None], [image_id]), image_id
        )
        assert qpane.placeholderActive() is False
    finally:
        _cleanup_qpane(qpane, qapp)


def test_image_properties_delegate(qapp):
    qpane = QPane(features=())
    try:
        image1 = _solid_image()
        image2 = _solid_image()
        id1 = uuid.uuid4()
        id2 = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists([image1, image2], [None, None], [id1, id2]), id1
        )
        current_image = qpane.currentImage
        assert current_image is not None
        assert current_image.size() == image1.size()
        assert len(qpane.allImages) == 2
        assert len(qpane.allImagePaths) == 2
    finally:
        _cleanup_qpane(qpane, qapp)


def test_diagnostics_getters(qapp):
    qpane = QPane(features=())
    try:
        assert qpane.diagnosticsOverlayEnabled() is False
        qpane.setDiagnosticsOverlayEnabled(True)
        assert qpane.diagnosticsOverlayEnabled() is True
        domains = qpane.diagnosticsDomains()
        assert domains
        domain = domains[0]
        qpane.setDiagnosticsDomainEnabled(domain, True)
        assert qpane.diagnosticsDomainEnabled(domain) is True
        qpane.setDiagnosticsDomainEnabled(domain, False)
        assert qpane.diagnosticsDomainEnabled(domain) is False
    finally:
        _cleanup_qpane(qpane, qapp)


def test_feature_availability(qapp):
    qpane = QPane(features=())
    try:
        assert qpane.samFeatureAvailable() is False
        # To test True, we'd need to mock the feature installation or use the 'sam' feature if available in env
    finally:
        _cleanup_qpane(qpane, qapp)


def test_sam_checkpoint_helpers(qapp):
    qpane = QPane(features=())
    try:
        assert qpane.samCheckpointReady() is False
        assert qpane.samCheckpointPath() is None
        success, message = qpane.refreshSamFeature()
        assert success is False
        assert "SAM tools disabled" in message
    finally:
        _cleanup_qpane(qpane, qapp)


def test_control_modes(qapp):
    qpane = QPane(features=())
    try:
        modes = qpane.availableControlModes()
        assert QPane.CONTROL_MODE_PANZOOM in modes
        assert QPane.CONTROL_MODE_CURSOR in modes
    finally:
        _cleanup_qpane(qpane, qapp)


def test_tool_registration_delegates(qapp):
    qpane = QPane(features=())
    try:

        class MyTool(ExtensionTool):
            def activate(self, deps):
                pass

            def deactivate(self):
                pass

        qpane.registerTool("my_tool", MyTool)
        assert "my_tool" in qpane.availableControlModes()
        qpane.unregisterTool("my_tool")
        assert "my_tool" not in qpane.availableControlModes()
        # Cursor provider test

        class MyCursorProvider:
            def cursor(self):
                return Qt.CursorShape.CrossCursor

        qpane.registerCursorProvider("my_cursor", MyCursorProvider())
        # No direct getter for cursor providers, but unregister shouldn't fail
        qpane.unregisterCursorProvider("my_cursor")
    finally:
        _cleanup_qpane(qpane, qapp)


def test_catalog_snapshot_delegates(qapp):
    qpane = QPane(features=())
    try:
        snapshot = qpane.getCatalogSnapshot()
        assert snapshot is not None
        assert len(snapshot.catalog) == 0
        assert len(snapshot.order) == 0
    finally:
        _cleanup_qpane(qpane, qapp)


def test_set_all_images_linked(qapp):
    qpane = QPane(features=())
    try:
        image1 = _solid_image()
        image2 = _solid_image()
        id1 = uuid.uuid4()
        id2 = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists([image1, image2], [None, None], [id1, id2]), id1
        )
        qpane.setAllImagesLinked(True)
        groups = qpane.linkedGroups()
        assert len(groups) == 1
        assert len(groups[0].members) == 2
        qpane.setAllImagesLinked(False)
        groups = qpane.linkedGroups()
        assert len(groups) == 0
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_delegates_stub(qapp, monkeypatch):
    # Basic check that these methods don't crash when mask feature is missing
    qpane = QPane(features=())
    try:
        assert qpane.listMasksForImage() == ()
        assert qpane.setMaskProperties(uuid.uuid4(), opacity=0.5) is False
        assert qpane.cycleMasksForward() is False
        assert qpane.cycleMasksBackward() is False
        # Mock mask service to verify delegation

        class MockMaskService:
            def __init__(self):
                self.calls = []
                self.manager = type(
                    "MaskManagerStub",
                    (),
                    {"get_mask_ids_for_image": staticmethod(lambda _image_id: tuple())},
                )()

            def setMaskProperties(self, mask_id, color, opacity):
                self.calls.append(("setMaskProperties", mask_id, color, opacity))
                return True

            def cycleMasks(self, current_id, forward):
                self.calls.append(("cycleMasks", current_id, forward))
                return True

            def listMasksForImage(self, image_id):
                return ("mask1",)

            def prefetchColorizedMasks(
                self,
                image_id: uuid.UUID,
                *,
                reason: str = "navigation",
                scales=None,
            ) -> bool:
                self.calls.append(("prefetchColorizedMasks", image_id, reason))
                return False

            def connectUndoStackChanged(self, slot):
                pass

            def disconnectUndoStackChanged(self, slot):
                pass

            def refreshAutosavePolicy(self):
                pass

            def resetStrokePipeline(self, **kwargs):
                pass

            def set_activation_resume_hooks(self, *args):
                pass

            def ensureTopMaskActiveForImage(self, image_id):
                return True

            def isActivationPending(self, image_id):
                return False

            def cancelPrefetch(self, image_id: uuid.UUID | None) -> bool:
                return False

            def get_latest_status_message(self, *_args, **_kwargs):
                return None

            @property
            def controller(self):
                return None

        mock_service = MockMaskService()
        # Mock catalog mask manager to ensure maskFeatureAvailable returns True
        monkeypatch.setattr(qpane.catalog(), "maskManager", lambda: True)
        qpane.attachMaskService(mock_service)
        mask_id = uuid.uuid4()
        qpane.setMaskProperties(mask_id, opacity=0.8)
        assert mock_service.calls[-1] == ("setMaskProperties", mask_id, None, 0.8)
        # Need a current image for cycleMasks
        image_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists([_solid_image()], [None], [image_id]), image_id
        )
        qpane.cycleMasksForward()
        assert mock_service.calls[-1] == ("cycleMasks", image_id, True)
        qpane.cycleMasksBackward()
        assert mock_service.calls[-1] == ("cycleMasks", image_id, False)
        # Mock controller method for listMasksForImage to verify delegation from QPane to Controller
        monkeypatch.setattr(
            qpane._masks_controller,
            "listMasksForImage",
            lambda image_id=None: ("mask1",),
        )
        assert qpane.listMasksForImage() == ("mask1",)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_settings_api(qapp):
    config = Config()
    qpane = QPane(config=config, features=())
    try:
        assert qpane.settings is not None
        new_config = Config(placeholder={"source": "test"})
        qpane.applySettings(config=new_config)
        # Verify settings setter raises
        with pytest.raises(AttributeError):
            qpane.settings = new_config
    finally:
        _cleanup_qpane(qpane, qapp)


def test_image_management_extended(qapp):
    qpane = QPane(features=())
    try:
        assert qpane.hasImages() is False
        assert qpane.imageIDs() == []
        assert qpane.currentImageID() is None
        assert qpane.currentImage is None
        image1 = _solid_image()
        id1 = uuid.uuid4()
        path1 = Path("dummy.png")
        qpane.addImage(id1, image1, path1)
        assert qpane.hasImages() is True
        assert qpane.imageIDs() == [id1]
        # addImage does not select it
        assert qpane.currentImageID() is None
        qpane.setCurrentImageID(id1)
        assert qpane.currentImageID() == id1
        qpane.removeImageByID(id1)
        assert qpane.hasImages() is False
        # Test clearImages
        qpane.addImage(id1, image1, path1)
        qpane.clearImages()
        assert qpane.hasImages() is False
    finally:
        _cleanup_qpane(qpane, qapp)


def test_view_state_api(qapp):
    qpane = QPane(features=())
    try:
        image = _solid_image(100, 100)
        id1 = uuid.uuid4()
        qpane.setImagesByID(QPane.imageMapFromLists([image], [None], [id1]), id1)
        zoom = qpane.currentZoom()
        assert isinstance(zoom, float)
        rect = qpane.currentViewportRect()
        assert isinstance(rect, QRectF)
        # panelHitTest
        # Just verify it doesn't crash and returns None or object
        qpane.panelHitTest(QPoint(10, 10))
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_api_holes(qapp, monkeypatch):
    qpane = QPane(features=())
    try:
        # Mock controller methods to verify delegation
        controller = qpane._masks_controller
        monkeypatch.setattr(controller, "active_mask_id", lambda: uuid.UUID(int=1))
        assert qpane.activeMaskID() == uuid.UUID(int=1)
        monkeypatch.setattr(
            controller, "maskIDsForImage", lambda image_id=None: [uuid.UUID(int=2)]
        )
        assert qpane.maskIDsForImage() == [uuid.UUID(int=2)]
        monkeypatch.setattr(controller, "get_active_mask_image", lambda: QImage())
        assert isinstance(qpane.getActiveMaskImage(), QImage)
        monkeypatch.setattr(
            controller, "get_mask_undo_state", lambda mask_id: "undo_state"
        )
        assert qpane.getMaskUndoState(uuid.uuid4()) == "undo_state"
        monkeypatch.setattr(
            controller, "create_blank_mask", lambda size: uuid.UUID(int=3)
        )
        assert qpane.createBlankMask(QSize(10, 10)) == uuid.UUID(int=3)
        monkeypatch.setattr(
            controller, "load_mask_from_file", lambda path: uuid.UUID(int=4)
        )
        assert qpane.loadMaskFromFile("path") == uuid.UUID(int=4)
        monkeypatch.setattr(
            controller, "remove_mask_from_image", lambda image_id, mask_id: True
        )
        assert qpane.removeMaskFromImage(uuid.uuid4(), uuid.uuid4()) is True
        monkeypatch.setattr(controller, "set_active_mask_id", lambda mask_id: True)
        assert qpane.setActiveMaskID(uuid.uuid4()) is True
        monkeypatch.setattr(
            controller,
            "prefetch_mask_overlays",
            lambda image_id, reason="navigation": True,
        )
        assert qpane.prefetchMaskOverlays(None) is True
        monkeypatch.setattr(controller, "undo_mask_edit", lambda: True)
        assert qpane.undoMaskEdit() is True
        monkeypatch.setattr(controller, "redo_mask_edit", lambda: True)
        assert qpane.redoMaskEdit() is True
    finally:
        _cleanup_qpane(qpane, qapp)


def test_signals_existence(qapp):
    qpane = QPane(features=())
    try:
        # Just verify they exist and can be connected
        def slot(*args):
            pass

        qpane.imageLoaded.connect(slot)
        qpane.zoomChanged.connect(slot)
        qpane.viewportRectChanged.connect(slot)
        qpane.maskSaved.connect(slot)
        qpane.maskUndoStackChanged.connect(slot)
        qpane.currentImageChanged.connect(slot)
        qpane.catalogChanged.connect(slot)
        qpane.catalogSelectionChanged.connect(slot)
        qpane.linkGroupsChanged.connect(slot)
        qpane.diagnosticsOverlayToggled.connect(slot)
        qpane.diagnosticsDomainToggled.connect(slot)
        qpane.comparisonChanged.connect(slot)
        qpane.sceneChanged.connect(slot)
        qpane.samCheckpointStatusChanged.connect(slot)
        qpane.samCheckpointProgress.connect(slot)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_installed_features(qapp):
    qpane = QPane(features=())
    try:
        assert isinstance(qpane.installedFeatures, tuple)
        assert len(qpane.installedFeatures) == 0
    finally:
        _cleanup_qpane(qpane, qapp)


def test_control_modes_extended(qapp):
    qpane = QPane(features=())
    try:
        assert qpane.getControlMode() == QPane.CONTROL_MODE_PANZOOM
        qpane.setControlMode(QPane.CONTROL_MODE_CURSOR)
        assert qpane.getControlMode() == QPane.CONTROL_MODE_CURSOR
    finally:
        _cleanup_qpane(qpane, qapp)


def test_overlays_api(qapp):
    qpane = QPane(features=())
    try:

        def draw_fn(painter, state):
            pass

        qpane.registerOverlay("test_overlay", draw_fn)
        assert "test_overlay" in qpane.contentOverlays()
        qpane.unregisterOverlay("test_overlay")
        assert "test_overlay" not in qpane.contentOverlays()
    finally:
        _cleanup_qpane(qpane, qapp)


def test_overlay_registry_snapshots_are_read_only(qapp):
    """Overlay registry accessors should not expose live mutable registries."""
    qpane = QPane(features=())
    try:

        def draw_fn(painter, state):
            pass

        qpane.registerOverlay("test_overlay", draw_fn)
        qpane.registerSceneOverlay("test_scene_overlay", draw_fn)

        content_snapshot = qpane.contentOverlays()
        scene_snapshot = qpane.sceneOverlays()

        assert "test_overlay" in content_snapshot
        assert "test_scene_overlay" in scene_snapshot
        with pytest.raises(TypeError):
            content_snapshot["late_overlay"] = draw_fn
        with pytest.raises(TypeError):
            scene_snapshot["late_scene_overlay"] = draw_fn
        with pytest.raises(AttributeError):
            content_snapshot.clear()
        with pytest.raises(AttributeError):
            scene_snapshot.clear()

        qpane.unregisterOverlay("test_overlay")
        qpane.unregisterSceneOverlay("test_scene_overlay")

        assert "test_overlay" in content_snapshot
        assert "test_scene_overlay" in scene_snapshot
        assert "test_overlay" not in qpane.contentOverlays()
        assert "test_scene_overlay" not in qpane.sceneOverlays()
    finally:
        _cleanup_qpane(qpane, qapp)


def test_linked_groups_extended(qapp):
    qpane = QPane(features=())
    try:
        from qpane.types import LinkedGroup

        group_id = uuid.uuid4()
        member_id1 = uuid.uuid4()
        member_id2 = uuid.uuid4()
        group = LinkedGroup(group_id=group_id, members=(member_id1, member_id2))
        qpane.setLinkedGroups((group,))
        groups = qpane.linkedGroups()
        assert len(groups) == 1
        assert groups[0].group_id == group_id
    finally:
        _cleanup_qpane(qpane, qapp)
