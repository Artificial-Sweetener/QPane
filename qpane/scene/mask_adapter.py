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

"""Adapt mask-domain state into internal scene layer descriptors."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from .identity import mask_layer_id
from .model import (
    BlendMode,
    LayerDescriptor,
    LayerHitTest,
    LayerKind,
    LayerPlacement,
    SceneDescriptor,
)
from .providers import SceneContribution
from .sources import MaskLayerSource


class MaskLayerLike(Protocol):
    """Mask layer fields needed by the scene adapter."""

    opacity: float

    @property
    def mask_image(self):  # noqa: ANN201
        """Expose the grayscale mask image for placement."""
        ...


class MaskManagerLike(Protocol):
    """Mask manager methods consumed by scene adaptation."""

    def get_mask_ids_for_image(self, image_id: uuid.UUID) -> list[uuid.UUID]:
        """Return mask identifiers in visual order."""
        ...

    def get_layer(self, mask_id: uuid.UUID) -> MaskLayerLike | None:
        """Return the mask layer for ``mask_id`` when present."""
        ...


class MaskRenderRevisionProvider(Protocol):
    """Provide cache/render revisions for mask render sources."""

    def maskRenderRevision(self, mask_id: uuid.UUID) -> int:
        """Return the current render revision for ``mask_id``."""
        ...


def mask_layers_for_image(
    *,
    scene: SceneDescriptor,
    image_id: uuid.UUID,
    manager: MaskManagerLike,
    revision_provider: MaskRenderRevisionProvider,
) -> tuple[LayerDescriptor, ...]:
    """Return mask layer descriptors for ``image_id`` in render order."""
    descriptors: list[LayerDescriptor] = []
    for mask_id in manager.get_mask_ids_for_image(image_id):
        layer = manager.get_layer(mask_id)
        if layer is None:
            continue
        mask_image = layer.mask_image
        if mask_image is None or mask_image.isNull():
            continue
        revision = max(0, int(revision_provider.maskRenderRevision(mask_id)))
        placement = LayerPlacement(
            x=0.0,
            y=0.0,
            width=float(mask_image.width()),
            height=float(mask_image.height()),
        )
        descriptors.append(
            LayerDescriptor(
                scene_id=scene.scene_id,
                layer_id=mask_layer_id(scene.scene_id, mask_id),
                kind=LayerKind.MASK,
                source=MaskLayerSource(mask_id=mask_id, revision=revision),
                placement=placement,
                visible=True,
                opacity=_clamped_opacity(getattr(layer, "opacity", 1.0)),
                blend_mode=BlendMode.NORMAL,
                clip=None,
                hit_test=LayerHitTest(
                    enabled=True,
                    selectable=False,
                    role="mask",
                ),
                source_revision=revision,
            )
        )
    return tuple(descriptors)


@dataclass(frozen=True, slots=True)
class MaskSceneProvider:
    """Provide scene contributions for masks associated with a catalog image."""

    base_scene: SceneDescriptor
    image_id: uuid.UUID
    manager: MaskManagerLike
    revision_provider: MaskRenderRevisionProvider

    def scene_contribution(self) -> SceneContribution | None:
        """Return mask layers for the active scene, or None when inactive."""
        layers = mask_layers_for_image(
            scene=self.base_scene,
            image_id=self.image_id,
            manager=self.manager,
            revision_provider=self.revision_provider,
        )
        if not layers:
            return None
        return SceneContribution(
            scene=SceneDescriptor(
                scene_id=self.base_scene.scene_id,
                kind=self.base_scene.kind,
                bounds=self.base_scene.bounds,
                layers=layers,
            ),
            order=10,
        )


@dataclass(frozen=True, slots=True)
class MaskServiceSceneProvider:
    """Adapt an installed mask service into the scene provider registry."""

    service: object

    def scene_contribution(
        self,
        base_scene: SceneDescriptor,
        image_id: uuid.UUID | None,
    ) -> SceneContribution | None:
        """Return mask scene content for ``image_id`` when masks are available."""
        if image_id is None:
            return None
        manager = getattr(self.service, "manager", None)
        controller = getattr(self.service, "controller", None)
        if manager is None or controller is None:
            return None
        return MaskSceneProvider(
            base_scene=base_scene,
            image_id=image_id,
            manager=manager,
            revision_provider=controller,
        ).scene_contribution()


def _clamped_opacity(value: object) -> float:
    """Return ``value`` constrained to the descriptor opacity range."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 1.0
    return min(1.0, max(0.0, numeric))
