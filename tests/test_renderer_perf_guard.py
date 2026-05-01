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

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QImage
from qpane import QPane
from tests.helpers.render_plan import make_render_plan


def _cleanup_qpane(qpane, qapp):
    qpane.deleteLater()
    qapp.processEvents()


def test_renderer_paint_duration_updates(qapp):
    qpane = QPane(features=())
    qpane.resize(32, 32)
    try:
        renderer = qpane.view().renderer
        renderer.allocate_buffers(QSize(16, 16), 1.0)
        calls = []

        def fake_redraw(region, plan):
            calls.append((region, plan))

        renderer._redraw_base_image_buffer = fake_redraw  # type: ignore[attr-defined]
        renderer.markDirty()
        source_image = QImage(16, 16, QImage.Format_ARGB32)
        source_image.fill(0)
        plan = make_render_plan(
            QRect(0, 0, 16, 16),
            source_image=source_image,
        )
        renderer.paint(plan)
        first_duration = renderer.get_last_paint_duration_ms()
        assert first_duration >= 0.0
        renderer.paint(plan)
        second_duration = renderer.get_last_paint_duration_ms()
        assert second_duration >= 0.0
        assert calls, "renderer should invoke buffer redraw when dirty"
    finally:
        _cleanup_qpane(qpane, qapp)
