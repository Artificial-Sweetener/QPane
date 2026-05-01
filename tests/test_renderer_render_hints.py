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

from __future__ import annotations
import types
import pytest
from PySide6.QtCore import QPointF, QRect, QSize
from PySide6.QtGui import (
    QImage,
    QPainter,
    QRegion,
    Qt,
)
from qpane.rendering import Renderer
from tests.helpers.render_plan import make_render_plan


class _StubQPane:
    def __init__(self, size: QSize):
        self._size = size
        self.viewport = types.SimpleNamespace(zoom=1.0, pan=QPointF(0.0, 0.0))
        self._view = types.SimpleNamespace(viewport=self.viewport)
        self.original_image = QImage(size, QImage.Format_ARGB32_Premultiplied)
        self.original_image.fill(Qt.white)

    def devicePixelRatioF(self) -> float:
        return 1.0

    def size(self) -> QSize:
        return self._size

    def view(self):
        return self._view


def _make_plan(qpane_rect: QRect, *, render_hint_enabled: bool):
    """Build a one-layer render plan with the requested hint setting."""
    source_image = QImage(qpane_rect.size(), QImage.Format_ARGB32_Premultiplied)
    source_image.fill(Qt.white)
    return make_render_plan(
        qpane_rect,
        source_image=source_image,
        render_hint_enabled=render_hint_enabled,
        current_pan=QPointF(0.0, 0.0),
    )


@pytest.mark.parametrize("render_hint_enabled, expected_calls", [(True, 1), (False, 0)])
def test_redraw_base_image_buffer_toggles_render_hint(
    monkeypatch, render_hint_enabled, expected_calls
):
    qpane_rect = QRect(0, 0, 48, 48)
    qpane = _StubQPane(qpane_rect.size())
    renderer = Renderer(qpane)
    renderer._base_image_buffer = QImage(
        qpane_rect.size(), QImage.Format_ARGB32_Premultiplied
    )
    renderer._base_image_buffer.fill(Qt.transparent)
    plan = _make_plan(qpane_rect, render_hint_enabled=render_hint_enabled)
    dirty_region = QRegion(qpane_rect)
    calls = []
    original = QPainter.setRenderHint

    def fake_set_render_hint(self, hint, on=True):
        if hint == QPainter.RenderHint.SmoothPixmapTransform:
            calls.append(on)
        return original(self, hint, on)

    monkeypatch.setattr(QPainter, "setRenderHint", fake_set_render_hint, raising=False)
    renderer._redraw_base_image_buffer(dirty_region, plan)
    assert len(calls) == expected_calls


@pytest.mark.parametrize("render_hint_enabled, expected_calls", [(True, 1), (False, 0)])
def test_repair_base_buffer_strips_toggles_render_hint(
    monkeypatch, render_hint_enabled, expected_calls
):
    qpane_rect = QRect(0, 0, 48, 48)
    qpane = _StubQPane(qpane_rect.size())
    renderer = Renderer(qpane)
    renderer._base_image_buffer = QImage(
        qpane_rect.size(), QImage.Format_ARGB32_Premultiplied
    )
    renderer._base_image_buffer.fill(Qt.transparent)
    plan = _make_plan(qpane_rect, render_hint_enabled=render_hint_enabled)
    repair_rects = [QRect(0, 0, 10, 10)]
    calls = []
    original = QPainter.setRenderHint

    def fake_set_render_hint(self, hint, on=True):
        if hint == QPainter.RenderHint.SmoothPixmapTransform:
            calls.append(on)
        return original(self, hint, on)

    monkeypatch.setattr(QPainter, "setRenderHint", fake_set_render_hint, raising=False)
    renderer._repair_base_buffer_strips(repair_rects, plan)
    assert len(calls) == expected_calls
