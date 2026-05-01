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

"""Scene mutation owner for mask-domain layer descriptors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..scene.model import (
    LayerDescriptor,
    LayerKind,
    SceneDescriptor,
)
from ..scene.mutations import (
    BaseSceneMutationOwner,
    SceneMutationResult,
    SceneMutationStatus,
)
from ..scene.sources import MaskLayerSource

if TYPE_CHECKING:  # pragma: no cover
    from .mask_service import MaskService


class MaskSceneMutationOwner(BaseSceneMutationOwner):
    """Route mask scene-layer mutations back to the mask domain service."""

    name = "masks"

    def __init__(self, service: "MaskService") -> None:
        """Capture the mask service that owns mask workflow state."""
        self._service = service

    def supports_layer(self, scene: SceneDescriptor, layer: LayerDescriptor) -> bool:
        """Return True for scene descriptors backed by mask-domain sources."""
        return layer.kind == LayerKind.MASK and isinstance(
            layer.source, MaskLayerSource
        )

    def remove_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor
    ) -> SceneMutationResult:
        """Remove a mask layer from the active image through MaskService."""
        source = self._mask_source(layer)
        if source is None:
            return self._unsupported(scene, layer, "remove layer")
        changed = self._service.applySceneMaskRemoval(source.mask_id)
        return self._result(scene, layer, changed, "mask removed")

    def reorder_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor, target_index: int
    ) -> SceneMutationResult:
        """Reorder a mask layer through MaskService."""
        source = self._mask_source(layer)
        if source is None:
            return self._unsupported(scene, layer, "reorder layer")
        changed = self._service.applySceneMaskReorder(source.mask_id, target_index)
        return self._result(scene, layer, changed, "mask reordered")

    def set_opacity(
        self, scene: SceneDescriptor, layer: LayerDescriptor, opacity: float
    ) -> SceneMutationResult:
        """Update mask opacity through MaskService."""
        source = self._mask_source(layer)
        if source is None:
            return self._unsupported(scene, layer, "set opacity")
        changed = self._service.applySceneMaskOpacity(source.mask_id, opacity)
        return self._result(scene, layer, changed, "mask opacity updated")

    def request_source_revision(
        self, scene: SceneDescriptor, layer: LayerDescriptor, reason: str
    ) -> SceneMutationResult:
        """Ask the mask domain to advance the render revision for a mask layer."""
        source = self._mask_source(layer)
        if source is None:
            return self._unsupported(scene, layer, "request source revision")
        changed = self._service.applySceneMaskRevisionRequest(
            source.mask_id,
            reason=reason,
        )
        return self._result(scene, layer, changed, "mask revision requested")

    @staticmethod
    def _mask_source(layer: LayerDescriptor) -> MaskLayerSource | None:
        """Return the mask source carried by ``layer`` when present."""
        return layer.source if isinstance(layer.source, MaskLayerSource) else None

    def _result(
        self,
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        changed: bool,
        message: str,
    ) -> SceneMutationResult:
        """Build an owner result for a mask mutation."""
        return SceneMutationResult(
            status=(
                SceneMutationStatus.APPLIED
                if changed
                else SceneMutationStatus.UNCHANGED
            ),
            scene_id=scene.scene_id,
            layer_id=layer.layer_id,
            owner=self.name,
            message=message,
        )
