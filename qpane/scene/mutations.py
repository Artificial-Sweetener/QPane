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

"""Internal scene mutation routing for provider-owned layer state."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .model import LayerDescriptor, SceneDescriptor


class SceneMutationStatus(str, Enum):
    """Outcome categories for internal scene mutation requests."""

    APPLIED = "applied"
    UNCHANGED = "unchanged"
    NO_SCENE = "no-scene"
    SCENE_MISMATCH = "scene-mismatch"
    LAYER_NOT_FOUND = "layer-not-found"
    INVALID_REQUEST = "invalid-request"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class SceneMutationResult:
    """Describe the outcome of one scene mutation request."""

    status: SceneMutationStatus
    scene_id: uuid.UUID | None = None
    layer_id: uuid.UUID | None = None
    owner: str | None = None
    message: str = ""

    @property
    def changed(self) -> bool:
        """Return True when the owner changed authoritative state."""
        return self.status == SceneMutationStatus.APPLIED

    @property
    def accepted(self) -> bool:
        """Return True when the request reached an owner successfully."""
        return self.status in {
            SceneMutationStatus.APPLIED,
            SceneMutationStatus.UNCHANGED,
        }


class SceneMutationOwner(Protocol):
    """Domain owner that applies validated mutations for scene layers."""

    name: str

    def supports_layer(self, scene: SceneDescriptor, layer: LayerDescriptor) -> bool:
        """Return True when this owner owns mutations for ``layer``."""
        ...

    def remove_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor
    ) -> SceneMutationResult:
        """Remove ``layer`` through the authoritative domain owner."""
        ...

    def reorder_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor, target_index: int
    ) -> SceneMutationResult:
        """Move ``layer`` to ``target_index`` through the authoritative owner."""
        ...

    def set_opacity(
        self, scene: SceneDescriptor, layer: LayerDescriptor, opacity: float
    ) -> SceneMutationResult:
        """Update layer opacity through the authoritative owner."""
        ...

    def request_source_revision(
        self, scene: SceneDescriptor, layer: LayerDescriptor, reason: str
    ) -> SceneMutationResult:
        """Ask the source-domain owner to advance its render revision."""
        ...


class BaseSceneMutationOwner:
    """Base owner that rejects unsupported mutation requests explicitly."""

    name = "base"

    def supports_layer(self, scene: SceneDescriptor, layer: LayerDescriptor) -> bool:
        """Return False; subclasses opt in to owned layer variants."""
        return False

    def remove_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor
    ) -> SceneMutationResult:
        """Reject layer removal for unsupported owners."""
        return self._unsupported(scene, layer, "remove layer")

    def reorder_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor, target_index: int
    ) -> SceneMutationResult:
        """Reject layer reordering for unsupported owners."""
        return self._unsupported(scene, layer, "reorder layer")

    def set_opacity(
        self, scene: SceneDescriptor, layer: LayerDescriptor, opacity: float
    ) -> SceneMutationResult:
        """Reject opacity updates for unsupported owners."""
        return self._unsupported(scene, layer, "set opacity")

    def request_source_revision(
        self, scene: SceneDescriptor, layer: LayerDescriptor, reason: str
    ) -> SceneMutationResult:
        """Reject source revision requests for unsupported owners."""
        return self._unsupported(scene, layer, "request source revision")

    def _unsupported(
        self, scene: SceneDescriptor, layer: LayerDescriptor, operation: str
    ) -> SceneMutationResult:
        """Build a standard unsupported result for ``operation``."""
        return SceneMutationResult(
            status=SceneMutationStatus.UNSUPPORTED,
            scene_id=scene.scene_id,
            layer_id=layer.layer_id,
            owner=self.name,
            message=f"{self.name} does not support {operation}",
        )


class SceneMutationCoordinator:
    """Validate scene mutation requests and route them to domain owners."""

    def __init__(
        self,
        scene_provider: Callable[[], SceneDescriptor | None],
        owners: tuple[SceneMutationOwner, ...] = (),
    ) -> None:
        """Capture the active-scene provider and initial mutation owners."""
        self._scene_provider = scene_provider
        self._owners: list[SceneMutationOwner] = list(owners)

    def register_owner(self, owner: SceneMutationOwner) -> SceneMutationOwner:
        """Register ``owner`` for future mutation routing."""
        if owner not in self._owners:
            self._owners.append(owner)
        return owner

    def unregister_owner(self, owner: SceneMutationOwner) -> None:
        """Remove a previously registered mutation owner."""
        self._owners = [
            candidate for candidate in self._owners if candidate is not owner
        ]

    def active_scene(self) -> SceneDescriptor | None:
        """Return the scene snapshot used for validation."""
        return self._scene_provider()

    def find_layer(
        self, predicate: Callable[[LayerDescriptor], bool]
    ) -> tuple[SceneDescriptor, LayerDescriptor] | None:
        """Return the first active-scene layer matching ``predicate``."""
        scene = self.active_scene()
        if scene is None:
            return None
        for layer in scene.layers:
            if predicate(layer):
                return scene, layer
        return None

    def remove_layer(
        self, scene_id: uuid.UUID, layer_id: uuid.UUID
    ) -> SceneMutationResult:
        """Route a layer-removal request by scene and layer ID."""
        resolved = self._layer_for_ids(scene_id, layer_id)
        if isinstance(resolved, SceneMutationResult):
            return resolved
        scene, layer, owner = resolved
        return owner.remove_layer(scene, layer)

    def reorder_layer(
        self, scene_id: uuid.UUID, layer_id: uuid.UUID, target_index: int
    ) -> SceneMutationResult:
        """Route a layer reorder request."""
        resolved = self._layer_for_ids(scene_id, layer_id)
        if isinstance(resolved, SceneMutationResult):
            return resolved
        scene, layer, owner = resolved
        if target_index < 0 or target_index >= len(scene.layers):
            return self._invalid_request(
                scene_id,
                layer_id,
                "target_index must reference an existing scene layer slot",
            )
        return owner.reorder_layer(scene, layer, target_index)

    def set_opacity(
        self, scene_id: uuid.UUID, layer_id: uuid.UUID, opacity: float
    ) -> SceneMutationResult:
        """Route a layer opacity update."""
        try:
            normalized_opacity = float(opacity)
        except (TypeError, ValueError):
            return self._invalid_request(scene_id, layer_id, "opacity must be numeric")
        if not 0.0 <= normalized_opacity <= 1.0:
            return self._invalid_request(
                scene_id,
                layer_id,
                "opacity must be between 0.0 and 1.0",
            )
        resolved = self._layer_for_ids(scene_id, layer_id)
        if isinstance(resolved, SceneMutationResult):
            return resolved
        scene, layer, owner = resolved
        return owner.set_opacity(scene, layer, normalized_opacity)

    def request_source_revision(
        self, scene_id: uuid.UUID, layer_id: uuid.UUID, reason: str
    ) -> SceneMutationResult:
        """Route a source revision request to the layer's domain owner."""
        if not reason:
            return self._invalid_request(scene_id, layer_id, "reason is required")
        resolved = self._layer_for_ids(scene_id, layer_id)
        if isinstance(resolved, SceneMutationResult):
            return resolved
        scene, layer, owner = resolved
        return owner.request_source_revision(scene, layer, reason)

    def _scene_for_id(
        self, scene_id: uuid.UUID
    ) -> SceneDescriptor | SceneMutationResult:
        """Return the active scene when it matches ``scene_id``."""
        scene = self.active_scene()
        if scene is None:
            return SceneMutationResult(
                status=SceneMutationStatus.NO_SCENE,
                scene_id=scene_id,
                message="no active scene is available",
            )
        if scene.scene_id != scene_id:
            return SceneMutationResult(
                status=SceneMutationStatus.SCENE_MISMATCH,
                scene_id=scene_id,
                message="request scene does not match the active scene",
            )
        return scene

    def _layer_for_ids(
        self, scene_id: uuid.UUID, layer_id: uuid.UUID
    ) -> (
        tuple[SceneDescriptor, LayerDescriptor, SceneMutationOwner]
        | SceneMutationResult
    ):
        """Return scene, layer, and owner for validated IDs."""
        scene = self._scene_for_id(scene_id)
        if isinstance(scene, SceneMutationResult):
            return scene
        layer = next(
            (candidate for candidate in scene.layers if candidate.layer_id == layer_id),
            None,
        )
        if layer is None:
            return SceneMutationResult(
                status=SceneMutationStatus.LAYER_NOT_FOUND,
                scene_id=scene_id,
                layer_id=layer_id,
                message="layer is not present in the active scene",
            )
        owner = self._owner_for_layer(scene, layer)
        if owner is None:
            return self._unsupported(scene, layer)
        return scene, layer, owner

    def _owner_for_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor
    ) -> SceneMutationOwner | None:
        """Return the registered owner responsible for ``layer``."""
        for owner in self._owners:
            if owner.supports_layer(scene, layer):
                return owner
        return None

    @staticmethod
    def _invalid_request(
        scene_id: uuid.UUID, layer_id: uuid.UUID | None, message: str
    ) -> SceneMutationResult:
        """Build a standard invalid-request result."""
        return SceneMutationResult(
            status=SceneMutationStatus.INVALID_REQUEST,
            scene_id=scene_id,
            layer_id=layer_id,
            message=message,
        )

    @staticmethod
    def _unsupported(
        scene: SceneDescriptor, layer: LayerDescriptor
    ) -> SceneMutationResult:
        """Build a standard unsupported result when no owner claims a layer."""
        return SceneMutationResult(
            status=SceneMutationStatus.UNSUPPORTED,
            scene_id=scene.scene_id,
            layer_id=layer.layer_id,
            message="no mutation owner is registered for this layer",
        )
