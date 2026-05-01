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

"""Phase 8 tests for internal scene mutation routing."""

from __future__ import annotations

import uuid

from PySide6.QtCore import QSize

from qpane.scene.default_scene import build_default_catalog_scene
from qpane.scene.identity import mask_layer_id
from qpane.scene.model import (
    BlendMode,
    LayerDescriptor,
    LayerHitTest,
    LayerKind,
    LayerPlacement,
    SceneDescriptor,
)
from qpane.scene.mutations import (
    BaseSceneMutationOwner,
    SceneMutationCoordinator,
    SceneMutationResult,
    SceneMutationStatus,
)
from qpane.scene.sources import MaskLayerSource


class RecordingMaskOwner(BaseSceneMutationOwner):
    """Record mask-layer mutation requests for coordinator tests."""

    name = "recording-mask"

    def __init__(self) -> None:
        """Initialize an empty request log."""
        self.calls: list[tuple[str, uuid.UUID, object]] = []

    def supports_layer(self, scene: SceneDescriptor, layer: LayerDescriptor) -> bool:
        """Claim mask layers only."""
        return layer.kind == LayerKind.MASK

    def set_opacity(
        self, scene: SceneDescriptor, layer: LayerDescriptor, opacity: float
    ) -> SceneMutationResult:
        """Record an opacity request."""
        self.calls.append(("opacity", layer.layer_id, opacity))
        return SceneMutationResult(
            status=SceneMutationStatus.APPLIED,
            scene_id=scene.scene_id,
            layer_id=layer.layer_id,
            owner=self.name,
        )

    def reorder_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor, target_index: int
    ) -> SceneMutationResult:
        """Record a reorder request."""
        self.calls.append(("reorder", layer.layer_id, target_index))
        return SceneMutationResult(
            status=SceneMutationStatus.UNCHANGED,
            scene_id=scene.scene_id,
            layer_id=layer.layer_id,
            owner=self.name,
        )

    def request_source_revision(
        self, scene: SceneDescriptor, layer: LayerDescriptor, reason: str
    ) -> SceneMutationResult:
        """Record a source revision request."""
        self.calls.append(("revision", layer.layer_id, reason))
        return SceneMutationResult(
            status=SceneMutationStatus.APPLIED,
            scene_id=scene.scene_id,
            layer_id=layer.layer_id,
            owner=self.name,
        )


def test_scene_mutation_rejects_unowned_default_catalog_layer() -> None:
    """Default catalog layers should not be mutable without a domain owner."""
    image_id = uuid.uuid4()
    scene = build_default_catalog_scene(
        image_id=image_id,
        image_size=QSize(10, 8),
        source_path=None,
        revision=0,
    )
    coordinator = SceneMutationCoordinator(lambda: scene)

    result = coordinator.set_opacity(scene.scene_id, scene.layers[0].layer_id, 0.5)

    assert result.status == SceneMutationStatus.UNSUPPORTED
    assert result.changed is False


def test_scene_mutation_routes_mask_layer_requests_to_owner() -> None:
    """Mask scene mutations should reach the registered mask-domain owner."""
    scene, mask_layer = _scene_with_mask_layer()
    owner = RecordingMaskOwner()
    coordinator = SceneMutationCoordinator(lambda: scene, owners=(owner,))

    opacity = coordinator.set_opacity(scene.scene_id, mask_layer.layer_id, 0.25)
    reorder = coordinator.reorder_layer(scene.scene_id, mask_layer.layer_id, 1)
    revision = coordinator.request_source_revision(
        scene.scene_id,
        mask_layer.layer_id,
        "unit-test",
    )

    assert opacity.changed is True
    assert reorder.accepted is True
    assert reorder.changed is False
    assert revision.changed is True
    assert owner.calls == [
        ("opacity", mask_layer.layer_id, 0.25),
        ("reorder", mask_layer.layer_id, 1),
        ("revision", mask_layer.layer_id, "unit-test"),
    ]


def test_scene_mutation_validates_scene_layer_and_opacity() -> None:
    """Coordinator validation should fail before calling owners."""
    scene, mask_layer = _scene_with_mask_layer()
    owner = RecordingMaskOwner()
    coordinator = SceneMutationCoordinator(lambda: scene, owners=(owner,))

    wrong_scene = coordinator.set_opacity(uuid.uuid4(), mask_layer.layer_id, 0.5)
    missing_layer = coordinator.set_opacity(scene.scene_id, uuid.uuid4(), 0.5)
    invalid_opacity = coordinator.set_opacity(
        scene.scene_id,
        mask_layer.layer_id,
        2.0,
    )

    assert wrong_scene.status == SceneMutationStatus.SCENE_MISMATCH
    assert missing_layer.status == SceneMutationStatus.LAYER_NOT_FOUND
    assert invalid_opacity.status == SceneMutationStatus.INVALID_REQUEST
    assert owner.calls == []


def _scene_with_mask_layer() -> tuple[SceneDescriptor, LayerDescriptor]:
    """Build a default scene plus one mask layer descriptor."""
    image_id = uuid.uuid4()
    mask_id = uuid.uuid4()
    base_scene = build_default_catalog_scene(
        image_id=image_id,
        image_size=QSize(10, 8),
        source_path=None,
        revision=0,
    )
    mask_layer = LayerDescriptor(
        scene_id=base_scene.scene_id,
        layer_id=mask_layer_id(base_scene.scene_id, mask_id),
        kind=LayerKind.MASK,
        source=MaskLayerSource(mask_id=mask_id, revision=3),
        placement=LayerPlacement(0.0, 0.0, 10.0, 8.0),
        visible=True,
        opacity=0.75,
        blend_mode=BlendMode.NORMAL,
        clip=None,
        hit_test=LayerHitTest(enabled=True, selectable=False, role="mask"),
        source_revision=3,
    )
    return (
        SceneDescriptor(
            scene_id=base_scene.scene_id,
            kind=base_scene.kind,
            bounds=base_scene.bounds,
            layers=(*base_scene.layers, mask_layer),
        ),
        mask_layer,
    )
