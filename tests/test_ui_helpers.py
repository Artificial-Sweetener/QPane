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

"""Unit tests for qpane.ui helper modules."""

from __future__ import annotations
from PySide6.QtCore import QSize, QSizeF
from qpane import Config, QPane
from qpane.rendering import ViewportZoomMode
from qpane.ui import dragdrop
from qpane.ui.dragdrop import drag_out_image, is_drag_out_allowed


class _StubQPane:
    def __init__(self, *, drag_out_enabled: bool = True) -> None:
        self.settings = type("Settings", (), {"drag_out_enabled": drag_out_enabled})()


def test_is_drag_out_allowed_checks_zoom(qapp):
    image_size = QSize(10, 10)
    viewport_size = QSizeF(20, 20)
    assert (
        is_drag_out_allowed(
            image_size=image_size,
            zoom=1.0,
            zoom_mode=ViewportZoomMode.FIT,
            viewport_size=viewport_size,
        )
        is True
    )
    constrained_view = QSizeF(15, 15)
    assert (
        is_drag_out_allowed(
            image_size=image_size,
            zoom=2.0,
            zoom_mode=ViewportZoomMode.CUSTOM,
            viewport_size=constrained_view,
        )
        is False
    )
    assert (
        is_drag_out_allowed(
            image_size=image_size,
            zoom=0.5,
            zoom_mode=ViewportZoomMode.CUSTOM,
            viewport_size=viewport_size,
        )
        is True
    )


def test_is_drag_out_allowed_accepts_large_size_without_image_allocation(qapp):
    image_size = QSize(100_000, 100_000)
    viewport_size = QSizeF(250_000.0, 250_000.0)

    assert (
        is_drag_out_allowed(
            image_size=image_size,
            zoom=2.0,
            zoom_mode=ViewportZoomMode.CUSTOM,
            viewport_size=viewport_size,
        )
        is True
    )


def test_is_drag_out_allowed_rejects_empty_size(qapp):
    assert (
        is_drag_out_allowed(
            image_size=QSize(),
            zoom=1.0,
            zoom_mode=ViewportZoomMode.CUSTOM,
            viewport_size=QSizeF(20, 20),
        )
        is False
    )


def test_drag_out_image_delegates(monkeypatch):
    received: list[tuple[object, object, object, object]] = []

    def fake_start(qpane, event, *, image=None, path=None):
        received.append((qpane, event, image, path))

    monkeypatch.setattr(dragdrop, "maybeStartDrag", fake_start)
    qpane = _StubQPane()
    event = object()
    drag_out_image(qpane, event)
    assert received == [(qpane, event, None, None)]


def test_drag_out_image_respects_config(monkeypatch, qapp):
    calls: list[tuple[object, object]] = []

    def fake_start(qpane, event, *, image=None, path=None):
        calls.append((qpane, event))

    monkeypatch.setattr(dragdrop, "maybeStartDrag", fake_start)
    config = Config()
    config.drag_out_enabled = False
    qpane = QPane(config=config, features=())
    try:
        drag_out_image(qpane, None)
        assert calls == []
    finally:
        qpane.deleteLater()
        qapp.processEvents()
