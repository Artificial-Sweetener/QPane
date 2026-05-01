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

"""Regression tests for public composition browsing and comparison scoping."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from qpane import CompositionSnapshot, ComparisonOrientation, QPane


def _image(color: QColor) -> QImage:
    """Return a simple non-null image for catalog tests."""
    image = QImage(16, 16, QImage.Format_ARGB32)
    image.fill(color)
    return image


def _seed_two_images(qpane: QPane) -> tuple[uuid.UUID, uuid.UUID]:
    """Load two catalog images and return their IDs."""
    first = uuid.uuid4()
    second = uuid.uuid4()
    qpane.setImagesByID(
        QPane.imageMapFromLists(
            [_image(QColor("red")), _image(QColor("blue"))],
            paths=[Path("first.png"), Path("second.png")],
            ids=[first, second],
        ),
        first,
    )
    return first, second


def test_catalog_load_creates_default_compositions(qapp) -> None:
    """setImagesByID creates stable default compositions without changing catalog APIs."""
    viewer = QPane(features=())
    try:
        first, second = _seed_two_images(viewer)
        snapshot = viewer.getCompositionSnapshot()
        assert isinstance(snapshot, CompositionSnapshot)
        assert viewer.imageIDs() == [first, second]
        assert len(snapshot.order) == 2
        assert viewer.currentImageID() == first
        assert viewer.currentCompositionID() == snapshot.order[0]
        first_entry = snapshot.compositions[snapshot.order[0]]
        assert first_entry.kind == "default-image"
        assert first_entry.source_image_ids == (first,)
        assert first_entry.current_image_id == first
        assert first_entry.comparison.enabled is False
        viewer.setImagesByID(
            QPane.imageMapFromLists(
                [_image(QColor("red")), _image(QColor("blue"))],
                paths=[Path("first.png"), Path("second.png")],
                ids=[first, second],
            ),
            second,
        )
        assert viewer.compositionIDs() == list(snapshot.order)
    finally:
        viewer.deleteLater()
        qapp.processEvents()


def test_compose_creates_and_opens_explicit_composition(qapp) -> None:
    """compose creates a persistent public composition and opens it."""
    viewer = QPane(features=())
    try:
        first, second = _seed_two_images(viewer)
        composition_id = viewer.compose(images=[first, second], title="A/B Review")
        snapshot = viewer.getCompositionSnapshot()
        entry = snapshot.compositions[composition_id]
        assert viewer.currentCompositionID() == composition_id
        assert viewer.currentImageID() == first
        assert entry.kind == "explicit"
        assert entry.title == "A/B Review"
        assert entry.source_image_ids == (first, second)
        assert entry.comparison.enabled is True
        assert entry.comparison.source_id == second
    finally:
        viewer.deleteLater()
        qapp.processEvents()


def test_set_current_image_opens_default_composition_and_clears_compare(qapp) -> None:
    """Catalog navigation opens default compositions and does not leak comparison."""
    viewer = QPane(features=())
    try:
        first, second = _seed_two_images(viewer)
        viewer.setComparisonImageID(second)
        compared_composition = viewer.currentCompositionID()
        assert viewer.comparisonState().enabled is True
        viewer.setCurrentImageID(second)
        assert viewer.currentImageID() == second
        assert viewer.currentCompositionID() != compared_composition
        assert viewer.comparisonState().enabled is False
        viewer.openComposition(compared_composition)
        assert viewer.currentImageID() == first
        assert viewer.comparisonState().enabled is True
        assert viewer.comparisonState().source_id == second
    finally:
        viewer.deleteLater()
        qapp.processEvents()


def test_comparison_split_is_composition_scoped(qapp) -> None:
    """Comparison split and orientation are restored with their composition."""
    viewer = QPane(features=())
    try:
        first, second = _seed_two_images(viewer)
        composition_id = viewer.compose(images=[first, second], title=None)
        viewer.setComparisonSplit(0.25, ComparisonOrientation.HORIZONTAL)
        viewer.setCurrentImageID(second)
        assert viewer.comparisonState().enabled is False
        viewer.openComposition(composition_id)
        state = viewer.comparisonState()
        assert state.enabled is True
        assert state.split_position == pytest.approx(0.25)
        assert state.orientation == ComparisonOrientation.HORIZONTAL
    finally:
        viewer.deleteLater()
        qapp.processEvents()
