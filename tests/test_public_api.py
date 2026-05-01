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

"""Ensure the qpane package exports the intended public symbols."""

from __future__ import annotations
import uuid
import qpane
import pytest
from qpane.catalog.catalog import Catalog
from qpane.core.diagnostics_broker import Diagnostics
from qpane.rendering.view import View


def test_public_api_symbols():
    """Verify the public `qpane` export surface matches expectations."""
    expected = sorted(
        [
            "ExtensionTool",
            "CacheMode",
            "PlaceholderScaleMode",
            "ZoomMode",
            "DiagnosticsDomain",
            "ControlMode",
            "ComparisonOrientation",
            "ComparisonState",
            "ComparisonDividerState",
            "CompositionEntry",
            "CompositionSnapshot",
            "CatalogEntry",
            "LinkedGroup",
            "DiagnosticRecord",
            "OverlayState",
            "QPaneScene",
            "QPaneSceneLayer",
            "QPaneSceneRequest",
            "QPaneCatalogImageLayerRequest",
            "QPaneSceneTemplate",
            "QPaneTemplateLayer",
            "QPaneSceneTemplateBindings",
            "QPaneSceneClip",
            "QPaneSceneHit",
            "QPaneSceneOverlayState",
            "QPaneSceneOverlayLayer",
            "MaskInfo",
            "MaskSavedPayload",
            "CatalogMutationEvent",
            "CatalogSnapshot",
            "Config",
            "QPane",
            "ExtensionToolSignals",
            "__version__",
        ]
    )
    assert sorted(qpane.__all__) == expected
    for symbol in expected:
        assert hasattr(qpane, symbol)


def test_qpane_accessors_expose_curated_types(qapp):
    viewer = qpane.QPane(features=())
    try:
        assert isinstance(viewer.catalog(), Catalog)
        assert isinstance(viewer.view(), View)
        assert isinstance(viewer.diagnostics(), Diagnostics)
        assert viewer.availableControlModes()
        assert viewer.maskFeatureAvailable() is False
        assert viewer.samFeatureAvailable() is False
        with pytest.raises(AttributeError):
            viewer.tools  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            viewer.masks  # type: ignore[attr-defined]
    finally:
        viewer.deleteLater()
        qapp.processEvents()


def test_zoom_and_view_state_helpers_are_exposed(qapp):
    viewer = qpane.QPane(features=())
    try:
        viewer.setZoomFit()
        viewer.setZoom1To1()
        mode = viewer.getZoomMode()
        assert isinstance(mode, qpane.rendering.viewport.ViewportZoomMode)
        viewer.saveCurrentViewState()
        viewer.restoreViewStateForImage(uuid.uuid4())
    finally:
        viewer.deleteLater()
        qapp.processEvents()
