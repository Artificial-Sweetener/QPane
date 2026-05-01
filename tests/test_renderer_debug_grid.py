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

"""Tests for renderer debug grid behaviour."""

from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QImage
from qpane.rendering import Renderer
from qpane.scene.render_plan import RenderStrategy
from tests.helpers.render_plan import make_render_plan


class _StubQPane:
    """Provides just enough surface for Renderer tests."""

    def _get_tile_draw_position(self, identifier):
        """Return the top-left position for a tile identifier."""
        return QPointF(identifier.col * 64, identifier.row * 64)


class _PainterStub:
    """Records rectangles drawn by the renderer."""

    def __init__(self):
        self.rects = []

    def setPen(self, pen):
        """Store the pen (unused)."""
        self.pen = pen

    def setBrush(self, brush):
        """Store the brush (unused)."""
        self.brush = brush

    def drawRect(self, rect):
        """Record the drawn rectangle."""
        self.rects.append(rect)


def _make_render_plan(draw_grid: bool):
    """Build a minimal render plan for exercising the grid overlay."""
    image = QImage(256, 256, QImage.Format_RGB32)
    image.fill(0)
    return make_render_plan(
        QRect(0, 0, 256, 256),
        source_image=image,
        strategy=RenderStrategy.TILE,
        debug_draw_tile_grid=draw_grid,
        tile_size=64,
        max_tile_cols=4,
        max_tile_rows=4,
        visible_tile_range=(0, 3, 0, 3),
    )


def test_debug_grid_skips_when_flag_disabled():
    """Renderer should skip drawing when the state flag is false."""
    renderer = Renderer(_StubQPane())
    painter = _PainterStub()
    plan = _make_render_plan(draw_grid=False)
    renderer._draw_tile_debug_overlay(painter, plan, plan.base_raster_item)
    assert painter.rects == []


def test_debug_grid_draws_when_flag_enabled():
    """Renderer should draw tile outlines when the state flag is true."""
    renderer = Renderer(_StubQPane())
    painter = _PainterStub()
    plan = _make_render_plan(draw_grid=True)
    renderer._draw_tile_debug_overlay(painter, plan, plan.base_raster_item)
    assert painter.rects, "Expected at least one debug rectangle to be drawn"
