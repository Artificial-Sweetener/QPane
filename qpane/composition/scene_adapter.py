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

"""Adapt stored layered compositions into internal scene contributions."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from ..scene.model import (
    BlendMode,
    ClipCoordinateSpace,
    LayerClip,
    LayerDescriptor,
    LayerHitTest,
    LayerKind,
    LayerPlacement,
    SceneDescriptor,
    SceneKind,
)
from ..scene.providers import SceneContribution
from ..scene.render_plan import SceneLayerHitTestResult
from ..scene.sources import CatalogImageSource
from ..types import QPaneSceneClip, QPaneSceneHit, QPaneSceneLayer
from .model import CompositionKind, CompositionRecord, CompositionSceneLayer
from .service import CompositionService

_LAYERED_COMPOSITION_ORDER = -30


class CompositionSceneCatalogLookup(Protocol):
    """Catalog operations needed to resolve layered composition scene layers."""

    def getImage(self, image_id: uuid.UUID) -> QImage | None:
        """Return the catalog image for ``image_id``."""
        ...

    def getPath(self, image_id: uuid.UUID) -> Path | None:
        """Return the catalog path for ``image_id``."""
        ...

    def getRevision(self, image_id: uuid.UUID) -> int | None:
        """Return the catalog revision for ``image_id``."""
        ...


class CompositionSceneAdapter:
    """Expose the active layered composition as private scene-provider data."""

    def __init__(
        self,
        *,
        compositions: CompositionService,
        catalog: CompositionSceneCatalogLookup,
    ) -> None:
        """Capture composition and catalog collaborators."""
        self._compositions = compositions
        self._catalog = catalog

    def scene_contribution(self) -> SceneContribution | None:
        """Return the active layered composition as a replacement contribution."""
        record = self._active_layered_record()
        if record is None or record.scene is None:
            return None
        layers = tuple(
            descriptor
            for layer in record.scene.layers
            if (descriptor := self._descriptor_for_layer(record, layer)) is not None
        )
        if not layers:
            return None
        scene = SceneDescriptor(
            scene_id=record.composition_id,
            kind=SceneKind.EXPLICIT,
            bounds=_placement_from_rect(record.scene.bounds),
            layers=layers,
        )
        return SceneContribution(scene=scene, order=_LAYERED_COMPOSITION_ORDER)

    def hit_from_result(
        self, result: SceneLayerHitTestResult | None
    ) -> QPaneSceneHit | None:
        """Map an internal scene-layer hit to the active composition snapshot."""
        record = self._active_layered_record()
        if record is None or record.scene is None or result is None:
            return None
        if result.scene_id != record.composition_id:
            return None
        layer = self._record_layer_for_id(record, result.layer_id)
        if layer is None:
            return None
        return QPaneSceneHit(
            composition_id=record.composition_id,
            scene_id=record.composition_id,
            layer_id=result.layer_id,
            image_id=layer.image_id,
            role=layer.role,
            metadata=layer.metadata,
            panel_point=result.panel_point,
            scene_point=result.scene_point,
            source_point=result.source_point,
        )

    def layer_for_id(self, layer_id: uuid.UUID) -> QPaneSceneLayer | None:
        """Return the active public scene layer snapshot for ``layer_id``."""
        record = self._active_layered_record()
        if record is None:
            return None
        layer = self._record_layer_for_id(record, layer_id)
        if layer is None:
            return None
        return QPaneSceneLayer(
            layer_id=layer.layer_id,
            image_id=layer.image_id,
            placement=QRectF(layer.placement),
            visible=layer.visible,
            opacity=layer.opacity,
            clip=_copy_public_clip(layer.clip),
            hit_test=layer.hit_test,
            role=layer.role,
            metadata=layer.metadata,
        )

    def _descriptor_for_layer(
        self,
        record: CompositionRecord,
        layer: CompositionSceneLayer,
    ) -> LayerDescriptor | None:
        """Convert one stored layer into an internal descriptor."""
        if not layer.visible:
            return None
        image = self._catalog.getImage(layer.image_id)
        if image is None or image.isNull():
            return None
        revision = max(0, int(self._catalog.getRevision(layer.image_id) or 0))
        return LayerDescriptor(
            scene_id=record.composition_id,
            layer_id=layer.layer_id,
            kind=LayerKind.IMAGE,
            source=CatalogImageSource(
                image_id=layer.image_id,
                source_path=self._catalog.getPath(layer.image_id),
                revision=revision,
            ),
            placement=_placement_from_rect(layer.placement),
            visible=layer.visible,
            opacity=layer.opacity,
            blend_mode=BlendMode.NORMAL,
            clip=_internal_clip(layer.clip),
            hit_test=LayerHitTest(
                enabled=layer.hit_test,
                selectable=False,
                role=layer.role,
            ),
            source_revision=revision,
        )

    def _active_layered_record(self) -> CompositionRecord | None:
        """Return the active layered composition record, if one is active."""
        record = self._compositions.active_record()
        if record is None or record.kind != CompositionKind.LAYERED_SCENE:
            return None
        return record

    @staticmethod
    def _record_layer_for_id(
        record: CompositionRecord, layer_id: uuid.UUID
    ) -> CompositionSceneLayer | None:
        """Return the stored layer with ``layer_id`` from ``record``."""
        if record.scene is None:
            return None
        return next(
            (layer for layer in record.scene.layers if layer.layer_id == layer_id),
            None,
        )


def _internal_clip(clip: object | None) -> LayerClip | None:
    """Convert a public scene clip into an internal layer clip."""
    if clip is None:
        return None
    space = ClipCoordinateSpace(getattr(clip, "coordinate_space"))
    rect = getattr(clip, "rect")
    return LayerClip(
        coordinate_space=space,
        x=rect.x(),
        y=rect.y(),
        width=rect.width(),
        height=rect.height(),
    )


def _copy_public_clip(clip: QPaneSceneClip | None) -> QPaneSceneClip | None:
    """Return a detached copy of a public scene clip."""
    if clip is None:
        return None
    return QPaneSceneClip(
        coordinate_space=clip.coordinate_space,
        rect=QRectF(clip.rect),
    )


def _placement_from_rect(rect: QRectF) -> LayerPlacement:
    """Convert a Qt rectangle to internal scene placement."""
    return LayerPlacement(
        x=rect.x(),
        y=rect.y(),
        width=rect.width(),
        height=rect.height(),
    )
