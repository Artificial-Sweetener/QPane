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

"""Cycle mode behavior for the example QPane."""

from __future__ import annotations

import uuid

from pathlib import Path

from PySide6.QtGui import QImage

from qpane import Config, QPane


def _add_image(qpane: QPane) -> None:
    """Populate the qpane with a single image to disable the placeholder."""
    image = QImage(8, 8, QImage.Format_ARGB32)
    image.fill(0)
    image_id = uuid.uuid4()
    qpane.setImagesByID(QPane.imageMapFromLists([image], [None], [image_id]), image_id)


def _placeholder_path(tmp_path: Path) -> Path:
    image = QImage(8, 8, QImage.Format_ARGB32)
    image.fill(0)
    path = tmp_path / "placeholder.png"
    assert image.save(str(path))
    return path


def _cycle(qpane: QPane) -> None:
    placeholder_active = qpane.placeholderActive()
    placeholder = getattr(qpane.settings, "placeholder", None)
    panzoom_allowed = (not placeholder_active) or bool(
        getattr(placeholder, "panzoom_enabled", False)
    )
    mask_available = qpane.maskFeatureAvailable()
    sam_available = qpane.samFeatureAvailable()
    preferred_order: list[str] = [
        QPane.CONTROL_MODE_CURSOR,
        QPane.CONTROL_MODE_PANZOOM,
    ]
    if mask_available:
        preferred_order.append(QPane.CONTROL_MODE_DRAW_BRUSH)
    if sam_available:
        preferred_order.append(QPane.CONTROL_MODE_SMART_SELECT)
    seen = set(preferred_order)
    for mode in qpane.availableControlModes():
        if mode in seen:
            continue
        preferred_order.append(mode)
        seen.add(mode)

    def _mode_allowed(mode: str) -> bool:
        if mode == QPane.CONTROL_MODE_PANZOOM:
            return panzoom_allowed
        if mode == QPane.CONTROL_MODE_DRAW_BRUSH:
            return mask_available and not placeholder_active
        if mode == QPane.CONTROL_MODE_SMART_SELECT:
            return mask_available and sam_available and not placeholder_active
        if placeholder_active and mode not in {
            QPane.CONTROL_MODE_CURSOR,
            QPane.CONTROL_MODE_PANZOOM,
        }:
            return False
        return True

    ordered_modes = [mode for mode in preferred_order if _mode_allowed(mode)]
    if not ordered_modes:
        return
    current = qpane.getControlMode()
    if current not in ordered_modes:
        next_mode = ordered_modes[0]
    elif len(ordered_modes) == 1:
        return
    else:
        next_index = (ordered_modes.index(current) + 1) % len(ordered_modes)
        next_mode = ordered_modes[next_index]
    if (
        next_mode
        in {
            QPane.CONTROL_MODE_DRAW_BRUSH,
            QPane.CONTROL_MODE_SMART_SELECT,
        }
        and qpane.activeMaskID() is None
    ):
        image = qpane.currentImage
        assert image is not None
        mask_id = qpane.createBlankMask(image.size())
        if mask_id is not None:
            qpane.setActiveMaskID(mask_id)
    qpane.setControlMode(next_mode)


def test_cycle_order_matches_toolbar(qapp):
    config = Config()
    qpane = QPane(config=config, features=("mask", "sam"))
    try:
        _add_image(qpane)
        qpane.setControlMode(QPane.CONTROL_MODE_CURSOR)
        _cycle(qpane)
        assert qpane.getControlMode() == QPane.CONTROL_MODE_PANZOOM
        _cycle(qpane)
        assert qpane.getControlMode() == QPane.CONTROL_MODE_DRAW_BRUSH
        _cycle(qpane)
        if qpane.samFeatureAvailable():
            assert qpane.getControlMode() == QPane.CONTROL_MODE_SMART_SELECT
            _cycle(qpane)
            assert qpane.getControlMode() == QPane.CONTROL_MODE_CURSOR
        else:
            assert qpane.getControlMode() == QPane.CONTROL_MODE_CURSOR
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_cycle_placeholder_panzoom_enabled(qapp, tmp_path: Path):
    config = Config(
        placeholder={
            "source": str(_placeholder_path(tmp_path)),
            "panzoom_enabled": True,
        }
    )
    qpane = QPane(config=config, features=())
    try:
        qpane.setControlMode(QPane.CONTROL_MODE_CURSOR)
        assert qpane.placeholderActive() is True
        _cycle(qpane)
        assert qpane.getControlMode() == QPane.CONTROL_MODE_PANZOOM
        _cycle(qpane)
        assert qpane.getControlMode() == QPane.CONTROL_MODE_CURSOR
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_cycle_placeholder_panzoom_disabled_noop(qapp, tmp_path: Path):
    config = Config(
        placeholder={
            "source": str(_placeholder_path(tmp_path)),
            "panzoom_enabled": False,
        }
    )
    qpane = QPane(config=config, features=())
    try:
        qpane.setControlMode(QPane.CONTROL_MODE_CURSOR)
        assert qpane.placeholderActive() is True
        _cycle(qpane)
        assert qpane.getControlMode() == QPane.CONTROL_MODE_CURSOR
    finally:
        qpane.deleteLater()
        qapp.processEvents()
