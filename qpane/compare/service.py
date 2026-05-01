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

"""Comparison state owner and scene contribution adapter."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from PySide6.QtGui import QImage

from ..scene.identity import base_image_layer_id, compare_layer_id
from ..scene.model import (
    BlendMode,
    ClipCoordinateSpace,
    LayerClip,
    LayerDescriptor,
    LayerHitTest,
    LayerKind,
    LayerPlacement,
    SceneDescriptor,
)
from ..scene.providers import SceneContribution
from ..scene.sources import CatalogImageSource
from ..types import ComparisonOrientation, ComparisonState


class ComparisonChangeKind(str, Enum):
    """Internal comparison change categories used for render invalidation."""

    SPLIT = "split"
    ORIENTATION = "orientation"
    SOURCE = "source"
    ENABLED = "enabled"


@dataclass(frozen=True, slots=True)
class ComparisonChange:
    """Internal comparison mutation details for targeted dirtying."""

    kind: ComparisonChangeKind
    previous: ComparisonState
    current: ComparisonState


class CatalogImageLookup(Protocol):
    """Catalog operations needed by comparison workflows."""

    def getImage(self, image_id: uuid.UUID) -> QImage | None:
        """Return catalog pixels for ``image_id``."""
        ...

    def containsImage(self, image_id: uuid.UUID) -> bool:
        """Return whether ``image_id`` exists in the catalog."""
        ...

    def getPath(self, image_id: uuid.UUID) -> Path | None:
        """Return the catalog path for ``image_id``."""
        ...

    def getRevision(self, image_id: uuid.UUID) -> int | None:
        """Return the catalog revision for ``image_id``."""
        ...


class CompositionComparisonLookup(Protocol):
    """Composition operations needed by comparison workflows."""

    def set_catalog_comparison(
        self,
        source_id: uuid.UUID,
        *,
        path: Path | None,
    ) -> bool:
        """Set a catalog comparison source on the active composition."""
        ...

    def clear_comparison(self) -> bool:
        """Clear comparison state on the active composition."""
        ...

    def set_comparison_split(
        self,
        position: float,
        orientation: ComparisonOrientation,
    ) -> bool:
        """Update active composition comparison split settings."""
        ...

    def comparison_state(self) -> ComparisonState:
        """Return active composition comparison state."""
        ...

    def active_record(self):
        """Return the active composition record."""
        ...

    def remove_catalog_images(self, image_ids: set[uuid.UUID]) -> bool:
        """Remove composition references to removed catalog images."""
        ...


class CompareService:
    """Adapt active-composition comparison state into scene contributions."""

    def __init__(
        self,
        *,
        catalog: CatalogImageLookup,
        compositions: CompositionComparisonLookup,
        changed_callback: Callable[[ComparisonChange], None],
    ) -> None:
        """Capture collaborators used to resolve comparison sources."""
        self._catalog = catalog
        self._compositions = compositions
        self._changed_callback = changed_callback

    def set_catalog_image(self, image_id: uuid.UUID) -> None:
        """Use a catalog image as the comparison reveal source."""
        if not isinstance(image_id, uuid.UUID):
            raise TypeError("image_id must be a UUID")
        if not self._catalog.containsImage(image_id):
            raise KeyError("comparison image_id must exist in the catalog")
        previous = self._compositions.comparison_state()
        changed = self._compositions.set_catalog_comparison(
            image_id,
            path=self._catalog.getPath(image_id),
        )
        if changed:
            self._notify_changed(
                ComparisonChangeKind.SOURCE,
                previous=previous,
            )

    def clear(self) -> None:
        """Disable comparison rendering."""
        previous = self._compositions.comparison_state()
        if self._compositions.clear_comparison():
            self._notify_changed(ComparisonChangeKind.ENABLED, previous=previous)

    def set_split(
        self,
        position: float,
        orientation: ComparisonOrientation | str | None = None,
    ) -> None:
        """Update the reveal split position and optional orientation."""
        try:
            next_position = float(position)
        except (TypeError, ValueError) as exc:
            raise ValueError("comparison split position must be numeric") from exc
        next_position = min(1.0, max(0.0, next_position))
        next_orientation = (
            self._coerce_orientation(orientation)
            if orientation is not None
            else self._compositions.comparison_state().orientation
        )
        previous = self._compositions.comparison_state()
        changed = self._compositions.set_comparison_split(
            next_position,
            next_orientation,
        )
        if changed:
            kind = (
                ComparisonChangeKind.SPLIT
                if previous.enabled
                and previous.source_id is not None
                and previous.source_id
                == self._compositions.comparison_state().source_id
                and previous.orientation == next_orientation
                else ComparisonChangeKind.ORIENTATION
            )
            self._notify_changed(kind, previous=previous)

    def state(self) -> ComparisonState:
        """Return the public comparison state snapshot."""
        return self._compositions.comparison_state()

    def source_revision(self) -> int:
        """Return the active comparison source revision for render invalidation."""
        state = self._compositions.comparison_state()
        if (
            not state.enabled
            or state.source_kind != "catalog"
            or state.source_id is None
        ):
            return 0
        return max(0, int(self._catalog.getRevision(state.source_id) or 0))

    def adapt_base_scene(
        self,
        base_scene: SceneDescriptor,
        image_id: uuid.UUID | None,
    ) -> SceneDescriptor:
        """Return ``base_scene`` sized by the larger compared catalog image."""
        authority_bounds = self._comparison_authority_bounds(
            base_scene=base_scene,
            image_id=image_id,
        )
        if authority_bounds is None:
            return base_scene
        return SceneDescriptor(
            scene_id=base_scene.scene_id,
            kind=base_scene.kind,
            bounds=authority_bounds,
            layers=tuple(
                self._adapt_base_image_layer(
                    layer,
                    image_id=image_id,
                    authority_bounds=authority_bounds,
                )
                for layer in base_scene.layers
            ),
        )

    def scene_contribution(
        self, base_scene: SceneDescriptor, image_id: uuid.UUID | None = None
    ) -> SceneContribution | None:
        """Return the comparison layer contribution for ``base_scene``."""
        source = self._active_layer_source()
        if source is None:
            return None
        source_id, layer_source, revision = source
        clip = self._comparison_clip()
        layer = LayerDescriptor(
            scene_id=base_scene.scene_id,
            layer_id=compare_layer_id(base_scene.scene_id, source_id),
            kind=LayerKind.IMAGE,
            source=layer_source,
            placement=base_scene.bounds,
            visible=True,
            opacity=1.0,
            blend_mode=BlendMode.NORMAL,
            clip=clip,
            hit_test=LayerHitTest(
                enabled=True,
                selectable=False,
                role="comparison-image",
            ),
            source_revision=revision,
        )
        return SceneContribution(
            scene=SceneDescriptor(
                scene_id=base_scene.scene_id,
                kind=base_scene.kind,
                bounds=base_scene.bounds,
                layers=(layer,),
            ),
            order=5,
        )

    def remove_catalog_images(self, image_ids: set[uuid.UUID]) -> None:
        """Clear catalog comparison state when its source image is removed."""
        previous = self._compositions.comparison_state()
        if self._compositions.remove_catalog_images(image_ids):
            self._notify_changed(ComparisonChangeKind.SOURCE, previous=previous)

    def reconcile_catalog(self) -> None:
        """Clear stale catalog comparison state after catalog replacement."""
        state = self._compositions.comparison_state()
        if (
            state.enabled
            and state.source_kind == "catalog"
            and state.source_id is not None
            and not self._catalog.containsImage(state.source_id)
        ):
            self.clear()

    def _active_layer_source(
        self,
    ) -> tuple[uuid.UUID, CatalogImageSource, int] | None:
        """Return the active source triple used for a comparison layer."""
        record = self._compositions.active_record()
        if record is None or record.comparison is None:
            return None
        comparison = record.comparison
        if comparison.source_kind == "catalog":
            image_id = comparison.source_id
            if not self._catalog.containsImage(image_id):
                return None
            revision = max(0, int(self._catalog.getRevision(image_id) or 0))
            return (
                image_id,
                CatalogImageSource(
                    image_id=image_id,
                    source_path=self._catalog.getPath(image_id),
                    revision=revision,
                ),
                revision,
            )
        return None

    def _comparison_authority_bounds(
        self,
        *,
        base_scene: SceneDescriptor,
        image_id: uuid.UUID | None,
    ) -> LayerPlacement | None:
        """Return larger comparison bounds when comparison should resize the scene."""
        if image_id is None:
            return None
        record = self._compositions.active_record()
        if record is None or record.comparison is None:
            return None
        comparison = record.comparison
        if comparison.source_kind != "catalog":
            return None
        base_image = self._catalog.getImage(image_id)
        comparison_image = self._catalog.getImage(comparison.source_id)
        if (
            base_image is None
            or comparison_image is None
            or base_image.isNull()
            or comparison_image.isNull()
        ):
            return None
        base_area = base_image.width() * base_image.height()
        comparison_area = comparison_image.width() * comparison_image.height()
        if comparison_area <= base_area:
            return None
        return LayerPlacement(
            x=base_scene.bounds.x,
            y=base_scene.bounds.y,
            width=float(comparison_image.width()),
            height=float(comparison_image.height()),
        )

    @staticmethod
    def _adapt_base_image_layer(
        layer: LayerDescriptor,
        *,
        image_id: uuid.UUID | None,
        authority_bounds: LayerPlacement,
    ) -> LayerDescriptor:
        """Return ``layer`` with authority placement when it is the base image."""
        if image_id is None:
            return layer
        is_base_layer = (
            layer.kind == LayerKind.IMAGE
            and isinstance(layer.source, CatalogImageSource)
            and layer.source.image_id == image_id
            and (
                layer.hit_test.role == "base-image"
                or layer.layer_id == base_image_layer_id(image_id)
            )
        )
        if not is_base_layer:
            return layer
        return replace(layer, placement=authority_bounds)

    def _comparison_clip(self) -> LayerClip:
        """Return the normalized reveal clip for the current split."""
        state = self._compositions.comparison_state()
        position = state.split_position
        if state.orientation == ComparisonOrientation.HORIZONTAL:
            return LayerClip(
                coordinate_space=ClipCoordinateSpace.NORMALIZED_SCENE,
                x=0.0,
                y=position,
                width=1.0,
                height=1.0 - position,
            )
        return LayerClip(
            coordinate_space=ClipCoordinateSpace.NORMALIZED_SCENE,
            x=position,
            y=0.0,
            width=1.0 - position,
            height=1.0,
        )

    def _notify_changed(
        self,
        kind: ComparisonChangeKind,
        *,
        previous: ComparisonState,
    ) -> None:
        """Notify the owning widget that compare state affects rendering."""
        self._changed_callback(
            ComparisonChange(
                kind=kind,
                previous=previous,
                current=self._compositions.comparison_state(),
            )
        )

    @staticmethod
    def _coerce_orientation(
        orientation: ComparisonOrientation | str,
    ) -> ComparisonOrientation:
        """Return a valid comparison orientation enum."""
        if isinstance(orientation, ComparisonOrientation):
            return orientation
        try:
            return ComparisonOrientation(str(orientation))
        except ValueError as exc:
            raise ValueError("unknown comparison orientation") from exc
