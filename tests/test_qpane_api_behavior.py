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

"""Tests asserting specific behavioral contracts documented in the API reference."""

import json
import uuid
import pytest
from PySide6.QtGui import QImage, Qt
from qpane import QPane, Config


def _cleanup_qpane(qpane, qapp):
    qpane.deleteLater()
    qapp.processEvents()


def _solid_image(width=32, height=32, color=Qt.white):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(color)
    return image


def test_config_behavior():
    """Assert Config object behavior: copy, as_dict, configure, validation."""
    # Test initialization and as_dict
    cfg = Config(placeholder={"source": "init"})
    assert cfg.as_dict()["placeholder"]["source"] == "init"
    json.dumps(cfg.as_dict())
    # Test copy
    cfg_copy = cfg.copy()
    assert cfg_copy is not cfg
    # Compare attributes since objects might not compare equal if __eq__ isn't defined or if deepcopy changes identity
    assert cfg_copy.as_dict()["placeholder"] == cfg.as_dict()["placeholder"]
    # Test configure with merge
    cfg.configure(placeholder={"scale_mode": "auto"})
    assert cfg.as_dict()["placeholder"]["source"] == "init"
    assert cfg.as_dict()["placeholder"]["scale_mode"] == "auto"
    # Test configure with keyword overrides
    cfg.configure(placeholder={"source": "override"}, drag_out_enabled=False)
    assert cfg.as_dict()["placeholder"]["source"] == "override"
    assert getattr(cfg, "drag_out_enabled") is False
    # Test unknown keys raise
    with pytest.raises(ValueError):
        Config(unknown_key="value")
    with pytest.raises(ValueError):
        cfg.configure(unknown_key="value")


def test_qpane_apply_settings_overrides(qapp):
    """Assert QPane.applySettings merges keyword overrides."""
    qpane = QPane(features=())
    try:
        # Initial state
        assert getattr(qpane.settings, "drag_out_enabled", True) is True
        # Apply with override
        qpane.applySettings(drag_out_enabled=False)
        assert getattr(qpane.settings, "drag_out_enabled") is False
        # Apply with config object AND override
        new_config = Config(drag_out_enabled=True)
        qpane.applySettings(config=new_config, drag_out_enabled=False)
        # Override should win
        assert getattr(qpane.settings, "drag_out_enabled") is False
    finally:
        _cleanup_qpane(qpane, qapp)


def test_control_mode_behavior(qapp):
    """Assert setControlMode raises ValueError for unknown IDs."""
    qpane = QPane(features=())
    try:
        initial_mode = qpane.getControlMode()
        with pytest.raises(ValueError):
            qpane.setControlMode("unknown_mode_id")
        assert qpane.getControlMode() == initial_mode
    finally:
        _cleanup_qpane(qpane, qapp)


def test_image_map_validation(qapp):
    """Assert imageMapFromLists raises on length mismatch."""
    images = [_solid_image()]
    ids = [uuid.uuid4(), uuid.uuid4()]  # Mismatch
    with pytest.raises(ValueError):
        QPane.imageMapFromLists(images, ids=ids)


def test_navigation_behavior(qapp):
    """Assert setCurrentImageID no-ops on unknown ID."""
    qpane = QPane(features=())
    try:
        image = _solid_image()
        uid = uuid.uuid4()
        qpane.setImagesByID(QPane.imageMapFromLists([image], [None], [uid]), uid)
        assert qpane.currentImageID() == uid
        # Unknown ID should not change selection
        unknown_id = uuid.uuid4()
        qpane.setCurrentImageID(unknown_id)
        assert qpane.currentImageID() == uid
        # None should clear selection
        qpane.setCurrentImageID(None)
        assert qpane.currentImageID() is None
        assert qpane.currentImage is None
    finally:
        _cleanup_qpane(qpane, qapp)


def test_linking_behavior(qapp):
    """Assert setAllImagesLinked requires 2+ entries."""
    qpane = QPane(features=())
    try:
        # 1 image
        image = _solid_image()
        uid = uuid.uuid4()
        qpane.setImagesByID(QPane.imageMapFromLists([image], [None], [uid]), uid)
        qpane.setAllImagesLinked(True)
        assert len(qpane.linkedGroups()) == 0
        # 2 images
        image2 = _solid_image()
        uid2 = uuid.uuid4()
        qpane.addImage(uid2, image2, None)
        qpane.setAllImagesLinked(True)
        assert len(qpane.linkedGroups()) == 1
        assert len(qpane.linkedGroups()[0].members) == 2
    finally:
        _cleanup_qpane(qpane, qapp)


def test_diagnostics_validation(qapp):
    """Assert diagnostics methods raise on unknown domains."""
    qpane = QPane(features=())
    try:
        with pytest.raises(ValueError):
            qpane.diagnosticsDomainEnabled("unknown_domain")
        with pytest.raises(ValueError):
            qpane.setDiagnosticsDomainEnabled("unknown_domain", True)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_mask_active_id_clearing(qapp, monkeypatch):
    """Assert setActiveMaskID(None) clears the active mask."""
    qpane = QPane(features=())
    try:
        # Mock controller
        class MockController:
            def __init__(self):
                self.active_id = uuid.uuid4()

            def set_active_mask_id(self, mask_id):
                self.active_id = mask_id
                return True

            def getActiveMaskID(self):
                return self.active_id

            # Stubs for other calls

            def attachMaskService(self, s):
                pass

            def detachMaskService(self):
                pass

            def refreshMaskAutosavePolicy(self):
                pass

            def mask_feature_available(self):
                return True

            def sam_feature_available(self):
                return False

            def maskIDsForImage(self, i):
                return []

            def listMasksForImage(self, i):
                return ()

            def get_active_mask_image(self):
                return None

            def get_mask_undo_state(self, i):
                return None

            def create_blank_mask(self, s):
                return None

            def load_mask_from_file(self, p):
                return None

            def remove_mask_from_image(self, i, m):
                return False

            def set_mask_properties(self, m, c, o):
                return False

            def prefetch_mask_overlays(self, i, reason):
                return False

            def cycle_masks_forward(self):
                return False

            def cycle_masks_backward(self):
                return False

            def undo_mask_edit(self):
                return False

            def redo_mask_edit(self):
                return False

            def is_activation_pending(self, i):
                return False

            def resetActiveSamPredictor(self):
                pass

            def sync_mask_activation_for_image(self, i):
                return None

        mock_controller = MockController()
        monkeypatch.setattr(qpane, "_masks", mock_controller)
        assert qpane.activeMaskID() is not None
        qpane.setActiveMaskID(None)
        assert qpane.activeMaskID() is None
    finally:
        _cleanup_qpane(qpane, qapp)


def test_extensibility_behavior(qapp):
    """Assert unregisterOverlay no-ops on absent overlay and unregisterTool protections."""
    qpane = QPane(features=())
    try:
        # Unregister absent overlay (should not raise)
        qpane.unregisterOverlay("non_existent_overlay")
        # Try to unregister built-in tool (should raise ValueError)
        with pytest.raises(ValueError):
            qpane.unregisterTool(QPane.CONTROL_MODE_PANZOOM)
        # Try to unregister active mode
        # Register a custom tool first
        from qpane import ExtensionTool

        class MyTool(ExtensionTool):
            def activate(self, deps):
                pass

            def deactivate(self):
                pass

            def mouseMoveEvent(self, e):
                pass

            def mousePressEvent(self, e):
                pass

            def mouseReleaseEvent(self, e):
                pass

            def wheelEvent(self, e):
                pass

        qpane.registerTool("my_tool", MyTool)
        qpane.setControlMode("my_tool")
        # Try to unregister while active (should raise RuntimeError)
        with pytest.raises(RuntimeError):
            qpane.unregisterTool("my_tool")
        # Should still be there because it's active
        assert "my_tool" in qpane.availableControlModes()
        assert qpane.getControlMode() == "my_tool"
        # Switch away and unregister
        qpane.setControlMode(QPane.CONTROL_MODE_PANZOOM)
        qpane.unregisterTool("my_tool")
        assert "my_tool" not in qpane.availableControlModes()
    finally:
        _cleanup_qpane(qpane, qapp)
