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
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from qpane.rendering import ViewportZoomMode
from qpane.ui.dragout import maybeStartDrag
from qpane.ui.dragdrop import is_drag_out_allowed

pytestmark = pytest.mark.usefixtures("qapp")


def _make_image() -> QImage:
    image = QImage(16, 16, QImage.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    return image


class _FakeSize:
    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height


class _FakeGeometry:
    def __init__(self, width: int, height: int) -> None:
        self._size = _FakeSize(width, height)

    def size(self) -> _FakeSize:
        return self._size


class _FakeScreen:
    def __init__(self, width: int, height: int) -> None:
        self._geometry = _FakeGeometry(width, height)

    def availableGeometry(self) -> _FakeGeometry:
        return self._geometry


class _StaticCatalog:
    def __init__(self, path: Path | None) -> None:
        self._path = path

    def currentImagePath(self) -> Path | None:
        return self._path


class _PanelStub:
    def __init__(
        self,
        *,
        catalog: _StaticCatalog,
        image: QImage,
        screen,
    ) -> None:
        self._catalog = catalog
        self._image = image
        self._screen = screen

    @property
    def currentImagePath(self) -> Path | None:
        return self._catalog.currentImagePath()

    @property
    def currentImage(self) -> QImage:
        return self._image

    def screen(self):
        return self._screen


def test_maybe_start_drag_aborts_without_path(caplog):
    panel = _PanelStub(
        catalog=_StaticCatalog(None),
        image=_make_image(),
        screen=_FakeScreen(1920, 1080),
    )
    caplog.set_level("WARNING", logger="qpane.ui.dragout")
    maybeStartDrag(panel, None)
    assert "current image path is missing" in caplog.text


def test_maybe_start_drag_aborts_when_path_missing(tmp_path, caplog):
    missing_path = tmp_path / "missing.png"
    panel = _PanelStub(
        catalog=_StaticCatalog(missing_path),
        image=_make_image(),
        screen=_FakeScreen(1920, 1080),
    )
    caplog.set_level("WARNING", logger="qpane.ui.dragout")
    maybeStartDrag(panel, None)
    assert f"{missing_path}" in caplog.text


def test_maybe_start_drag_aborts_without_screen(tmp_path, monkeypatch, caplog):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"not-real-image")
    panel = _PanelStub(
        catalog=_StaticCatalog(image_path),
        image=_make_image(),
        screen=None,
    )
    monkeypatch.setattr(
        "qpane.ui.dragout.QGuiApplication.primaryScreen",
        lambda: None,
    )
    caplog.set_level("WARNING", logger="qpane.ui.dragout")
    maybeStartDrag(panel, None)
    assert "no screen available" in caplog.text


def test_maybe_start_drag_builds_drag_payload(tmp_path, monkeypatch, caplog):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"placeholder")
    panel = _PanelStub(
        catalog=_StaticCatalog(image_path),
        image=_make_image(),
        screen=_FakeScreen(2560, 1440),
    )

    class _DummyDrag:
        instances: list[_DummyDrag] = []

        def __init__(self, parent):
            self.parent = parent
            self.mime_data = None
            self.pixmap = None
            self.exec_arg = None
            _DummyDrag.instances.append(self)

        def setMimeData(self, mime_data):
            self.mime_data = mime_data

        def setPixmap(self, pixmap):
            self.pixmap = pixmap

        def exec(self, action):
            self.exec_arg = action
            return action

    monkeypatch.setattr("qpane.ui.dragout.QDrag", _DummyDrag)
    caplog.set_level("WARNING", logger="qpane.ui.dragout")
    maybeStartDrag(panel, None)
    assert "aborted" not in caplog.text
    drag = _DummyDrag.instances[-1]
    assert drag.exec_arg == Qt.DropAction.CopyAction
    urls = drag.mime_data.urls()
    assert len(urls) == 1
    assert Path(urls[0].toLocalFile()) == image_path
    max_width = int(2560 * 0.15)
    max_height = int(1440 * 0.15)
    assert 0 < drag.pixmap.width() <= max_width
    assert 0 < drag.pixmap.height() <= max_height


def test_is_drag_out_allowed_blocks_when_scaled_larger() -> None:
    """Drag-out should be blocked when the scaled image exceeds the viewport."""
    image = QImage(200, 200, QImage.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    viewport_box = _FakeGeometry(50, 50)
    assert not is_drag_out_allowed(
        image_size=image.size(),
        zoom=1.0,
        zoom_mode=ViewportZoomMode.FIT,
        viewport_size=viewport_box.size(),
    )
    assert not is_drag_out_allowed(
        image_size=image.size(),
        zoom=2.0,
        zoom_mode=ViewportZoomMode.CUSTOM,
        viewport_size=viewport_box.size(),
    )
