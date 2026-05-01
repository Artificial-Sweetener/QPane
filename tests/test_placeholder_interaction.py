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

"""Placeholder interaction and drag-out behaviour driven by config."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, Qt
from qpane import Config, QPane
from qpane.scene import PlaceholderImageSource


def _placeholder_path(tmp_path: Path) -> Path:
    image = QImage(8, 8, QImage.Format_ARGB32)
    image.fill(0)
    path = tmp_path / "placeholder.png"
    assert image.save(str(path))
    return path


def test_placeholder_panzoom_allows_navigation_and_drag(qapp, tmp_path: Path) -> None:
    """Pan/zoom placeholder policy should unlock viewport and honor drag-out."""
    path = _placeholder_path(tmp_path)
    config = Config(
        placeholder={
            "source": str(path),
            "panzoom_enabled": True,
            "drag_out_enabled": True,
        }
    )
    qpane = QPane(config=config, features=())
    try:
        catalog = qpane.catalog()
        assert catalog.placeholderActive()
        assert not qpane.view().viewport.is_locked()
        assert qpane.getControlMode() == qpane.CONTROL_MODE_PANZOOM
        policy = catalog.placeholderPolicy()
        assert policy is not None and policy.drag_out_enabled
        assert qpane.isDragOutAllowed()
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_placeholder_all_tools_retains_control_mode(qapp, tmp_path: Path) -> None:
    """Placeholder keeps pan/zoom unlocked when configured and preserves current tool."""
    path = _placeholder_path(tmp_path)
    config = Config(
        placeholder={
            "source": str(path),
            "panzoom_enabled": True,
            "drag_out_enabled": True,
        }
    )
    qpane = QPane(config=config, features=())
    try:
        default_mode = qpane.getControlMode()
        assert qpane.catalog().placeholderActive()
        assert not qpane.view().viewport.is_locked()
        assert qpane.getControlMode() == default_mode
        assert qpane.currentImagePath == path
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_placeholder_resolves_to_scene_render_plan(qapp, tmp_path: Path) -> None:
    """Active placeholders should render without becoming catalog images."""
    path = _placeholder_path(tmp_path)
    qpane = QPane(config=Config(placeholder={"source": str(path)}), features=())
    qpane.resize(32, 32)
    try:
        assert qpane.placeholderActive()
        assert qpane.currentImageID() is None
        assert qpane.currentImage is None
        assert not qpane.original_image.isNull()

        plan = qpane.view().calculateRenderPlan()

        assert plan is not None
        assert plan.scene_id is not None
        assert plan.scene_bounds.width == 8.0
        assert plan.scene_bounds.height == 8.0
        assert len(plan.render_items) == 1
        assert len(plan.hit_test_items) == 1
        item = plan.base_raster_item
        assert item is not None
        assert isinstance(item.descriptor.source, PlaceholderImageSource)
        assert item.asset_key.source_id == item.descriptor.source.source_id
        assert item.asset_key.source_kind == "placeholder-image"
        assert item.asset_key.source_path == path
        assert item.source_image.size() == qpane.original_image.size()
        assert qpane.imageIDs() == []
        assert plan.current_pan == QPointF(0.0, 0.0)
        assert item.descriptor.visible is True
        assert item.descriptor.opacity == 1.0
        assert item.descriptor.placement.width == 8.0
        assert item.descriptor.placement.height == 8.0
        assert item.descriptor.source.revision == 0
        assert item.descriptor.source_revision == 0
        assert item.strategy.value in {"direct", "tile"}
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_placeholder_scene_plan_is_painted(qapp, tmp_path: Path) -> None:
    """Presenter painting should dispatch configured placeholders to the renderer."""
    path = _placeholder_path(tmp_path)
    qpane = QPane(config=Config(placeholder={"source": str(path)}), features=())
    qpane.resize(32, 32)

    class RecordingRenderer:
        """Capture scene plans submitted by the presenter."""

        def __init__(self) -> None:
            """Create a renderer test double with a matching base buffer."""
            self.plans = []
            self.buffer = QImage(32, 32, QImage.Format_ARGB32_Premultiplied)
            self.buffer.fill(Qt.transparent)

        def paint(self, plan) -> None:
            """Record the scene plan submitted for painting."""
            self.plans.append(plan)

        def get_base_buffer(self) -> QImage:
            """Return a paintable buffer for presenter compositing."""
            return self.buffer

        def get_subpixel_pan_offset(self) -> QPointF:
            """Return no scroll reuse offset."""
            return QPointF(0.0, 0.0)

        def allocate_buffers(self, size, _dpr) -> None:
            """Resize the test buffer when the presenter requests allocation."""
            self.buffer = QImage(size, QImage.Format_ARGB32_Premultiplied)
            self.buffer.fill(Qt.transparent)

    try:
        renderer = RecordingRenderer()
        qpane.view().presenter.renderer = renderer

        rendered_plan = qpane.view().presenter.paint(
            is_blank=False,
            content_overlays={},
            overlays_suspended=True,
            draw_tool_overlay=None,
        )

        assert rendered_plan is not None
        assert renderer.plans == [rendered_plan]
        assert rendered_plan.base_raster_item is not None
        assert (
            rendered_plan.base_raster_item.asset_key.source_kind == "placeholder-image"
        )
    finally:
        qpane.deleteLater()
        qapp.processEvents()
