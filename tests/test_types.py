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

"""Tests for the public types and enums exposed by the qpane facade."""

from pathlib import Path
from dataclasses import FrozenInstanceError
import uuid

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from qpane import (
    CacheMode,
    CatalogEntry,
    ControlMode,
    DiagnosticsDomain,
    LinkedGroup,
    PlaceholderScaleMode,
    QPaneCatalogImageLayerRequest,
    QPaneScene,
    QPaneSceneLayer,
    QPaneSceneRequest,
    QPaneSceneTemplate,
    QPaneSceneTemplateBindings,
    QPaneTemplateLayer,
    ZoomMode,
)
from qpane.types import DiagnosticRecord, __all__ as exported_types


def test_type_exports_are_listed() -> None:
    expected = {
        "CacheMode",
        "PlaceholderScaleMode",
        "ZoomMode",
        "DiagnosticsDomain",
        "ControlMode",
        "CatalogEntry",
        "LinkedGroup",
        "DiagnosticRecord",
        "MaskInfo",
        "MaskSavedPayload",
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
    }
    assert expected.issubset(set(exported_types))


def test_enum_values_match_facade_contract() -> None:
    assert {mode.value for mode in CacheMode} == {"auto", "hard"}
    assert {mode.value for mode in PlaceholderScaleMode} == {
        "auto",
        "logical_fit",
        "physical_fit",
        "relative_fit",
    }
    assert {mode.value for mode in ZoomMode} == {"fit", "locked_zoom", "locked_size"}
    assert {mode.value for mode in DiagnosticsDomain} == {
        "cache",
        "swap",
        "mask",
        "executor",
        "retry",
        "sam",
    }
    assert {mode.value for mode in ControlMode} == {
        "cursor",
        "panzoom",
        "draw-brush",
        "smart-select",
    }


def test_catalog_entry_is_frozen_and_slotted() -> None:
    image = QImage(1, 1, QImage.Format_ARGB32)
    entry = CatalogEntry(image=image, path=None)
    assert entry.image is image
    assert entry.path is None
    with pytest.raises(FrozenInstanceError):
        entry.path = Path("other.png")  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        entry.extra = "forbidden"  # type: ignore[attr-defined]


def test_linked_group_is_frozen_and_preserves_members() -> None:
    group_id = uuid.uuid4()
    members = (uuid.uuid4(), uuid.uuid4())
    group = LinkedGroup(group_id=group_id, members=members)
    assert group.group_id == group_id
    assert group.members == members
    with pytest.raises(FrozenInstanceError):
        group.members = tuple()  # type: ignore[misc]


def test_diagnostic_record_formatting() -> None:
    record = DiagnosticRecord("Label", "Value")
    assert record.formatted() == "Label: Value"
    assert str(record) == "Label: Value"
    standalone = DiagnosticRecord("", "Solo")
    assert standalone.formatted() == "Solo"


def test_scene_layer_copies_metadata_and_geometry() -> None:
    """Public scene types should preserve snapshots instead of caller-owned objects."""
    image_id = uuid.uuid4()
    layer_id = uuid.uuid4()
    placement = QRectF(0, 0, 10, 10)
    metadata = {"record": {"id": 1}}

    layer = QPaneSceneLayer(
        layer_id=layer_id,
        image_id=image_id,
        placement=placement,
        metadata=metadata,
    )
    scene = QPaneScene(
        composition_id=uuid.uuid4(),
        scene_id=uuid.uuid4(),
        title="Scene",
        bounds=QRectF(0, 0, 10, 10),
        layers=(layer,),
    )
    request = QPaneSceneRequest(
        composition_id=None,
        title=None,
        bounds=QRectF(0, 0, 10, 10),
        layers=(
            QPaneCatalogImageLayerRequest(
                layer_id=layer_id,
                image_id=image_id,
                placement=placement,
                metadata=metadata,
            ),
        ),
    )
    template = QPaneSceneTemplate(
        template_id=uuid.uuid4(),
        bounds=QRectF(0, 0, 10, 10),
        layers=(
            QPaneTemplateLayer(
                layer_id=uuid.uuid4(),
                source_slot="image",
                placement=placement,
                metadata=metadata,
            ),
        ),
    )
    bindings = QPaneSceneTemplateBindings(
        composition_id=None,
        catalog_images={"image": image_id},
        metadata={"image": metadata},
    )
    placement.setWidth(50)
    metadata["other"] = True

    assert layer.placement.width() == 10
    assert "other" not in layer.metadata
    assert scene.layers == (layer,)
    assert request.layers[0].placement.width() == 10
    assert "other" not in request.layers[0].metadata
    assert template.layers[0].placement.width() == 10
    assert "other" not in template.layers[0].metadata
    assert "other" not in bindings.metadata["image"]
    with pytest.raises(TypeError):
        layer.metadata["other"] = False  # type: ignore[index]
