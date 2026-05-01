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

"""Composition state owner for QPane."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from PySide6.QtCore import QRectF

from ..types import (
    ComparisonOrientation,
    ComparisonState,
    CompositionEntry,
    CompositionSnapshot,
    QPaneCatalogImageLayerRequest,
    QPaneScene,
    QPaneSceneClip,
    QPaneSceneLayer,
    QPaneSceneRequest,
    QPaneSceneTemplate,
    QPaneSceneTemplateBindings,
)
from .model import (
    CompositionComparison,
    CompositionKind,
    CompositionRecord,
    CompositionScene,
    CompositionSceneLayer,
)

_DEFAULT_COMPOSITION_NAMESPACE = uuid.UUID("2c03b45a-07f7-48d6-b039-14e59e7b8d4e")
_VALID_CLIP_SPACES = {
    "scene",
    "normalized-scene",
    "viewport",
    "normalized-viewport",
}


class CompositionService:
    """Own persistent compositions and active-composition state."""

    def __init__(self) -> None:
        """Initialize an empty composition collection."""
        self._records: dict[uuid.UUID, CompositionRecord] = {}
        self._order: list[uuid.UUID] = []
        self._default_by_image_id: dict[uuid.UUID, uuid.UUID] = {}
        self._active_id: uuid.UUID | None = None
        self._default_split_position = 0.5
        self._default_orientation = ComparisonOrientation.VERTICAL
        self._revision = 0

    def sync_catalog(
        self,
        image_ids: Iterable[uuid.UUID],
        *,
        path_lookup: Callable[[uuid.UUID], Path | None],
    ) -> bool:
        """Synchronize generated default compositions with catalog images."""
        ordered_ids = tuple(image_ids)
        valid_ids = set(ordered_ids)
        changed = self._remove_invalid_catalog_references(valid_ids, touch=False)
        for index, image_id in enumerate(ordered_ids):
            if image_id in self._default_by_image_id:
                continue
            composition_id = self.default_composition_id(image_id)
            title = self._default_title(path_lookup(image_id), index)
            record = CompositionRecord(
                composition_id=composition_id,
                kind=CompositionKind.DEFAULT_IMAGE,
                title=title,
                source_image_ids=(image_id,),
                primary_image_id=image_id,
            )
            self._records[composition_id] = record
            self._default_by_image_id[image_id] = composition_id
            self._order.append(composition_id)
            changed = True
        if self._active_id is not None and self._active_id not in self._records:
            self._active_id = None
            changed = True
        if changed:
            self._touch()
        return changed

    def clear(self) -> bool:
        """Remove every composition."""
        if not self._records and self._active_id is None:
            return False
        self._records.clear()
        self._order.clear()
        self._default_by_image_id.clear()
        self._active_id = None
        self._touch()
        return True

    def clear_selection(self) -> bool:
        """Clear the active composition without removing records."""
        if self._active_id is None:
            return False
        self._active_id = None
        self._touch()
        return True

    def compose(
        self,
        image_ids: Iterable[uuid.UUID],
        *,
        title: str | None,
        path_lookup: Callable[[uuid.UUID], Path | None],
    ) -> CompositionRecord:
        """Create and activate an explicit one-image or two-image composition."""
        source_ids = tuple(image_ids)
        if not 1 <= len(source_ids) <= 2:
            raise ValueError("compose currently accepts one or two catalog image IDs")
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("compose image IDs must be unique")
        composition_id = uuid.uuid4()
        comparison = None
        if len(source_ids) == 2:
            comparison_id = source_ids[1]
            comparison = CompositionComparison(
                source_id=comparison_id,
                source_path=path_lookup(comparison_id),
                source_kind="catalog",
                split_position=self._default_split_position,
                orientation=self._default_orientation,
            )
        record = CompositionRecord(
            composition_id=composition_id,
            kind=CompositionKind.EXPLICIT,
            title=self._explicit_title(title, source_ids, path_lookup),
            source_image_ids=source_ids,
            primary_image_id=source_ids[0],
            comparison=comparison,
        )
        self._records[composition_id] = record
        self._order.append(composition_id)
        self._active_id = composition_id
        self._touch()
        return record

    def compose_scene(
        self,
        request: QPaneSceneRequest,
        *,
        catalog_contains: Callable[[uuid.UUID], bool],
        activate: bool,
    ) -> CompositionRecord:
        """Create or replace a stored layered scene composition."""
        record = self._record_from_scene_request(
            request,
            catalog_contains=catalog_contains,
        )
        existing = self._records.get(record.composition_id)
        if existing is not None and existing.kind == CompositionKind.DEFAULT_IMAGE:
            raise ValueError("default catalog compositions cannot be replaced")
        self._records[record.composition_id] = record
        if record.composition_id not in self._order:
            self._order.append(record.composition_id)
        if activate:
            self._active_id = record.composition_id
        self._touch()
        return record

    def compose_scene_from_template(
        self,
        template: QPaneSceneTemplate,
        bindings: QPaneSceneTemplateBindings,
        *,
        catalog_contains: Callable[[uuid.UUID], bool],
        activate: bool,
    ) -> CompositionRecord:
        """Expand a host-owned template into a stored layered composition."""
        return self.compose_scene(
            self._request_from_template(template, bindings),
            catalog_contains=catalog_contains,
            activate=activate,
        )

    def open_composition(self, composition_id: uuid.UUID) -> CompositionRecord:
        """Activate and return an existing composition record."""
        record = self.record(composition_id)
        if self._active_id != composition_id:
            self._active_id = composition_id
            self._touch()
        return record

    def open_default_for_image(self, image_id: uuid.UUID) -> CompositionRecord:
        """Activate the generated default composition for ``image_id``."""
        composition_id = self._default_by_image_id.get(image_id)
        if composition_id is None:
            raise KeyError("catalog image does not have a default composition")
        return self.open_composition(composition_id)

    def remove_composition(self, composition_id: uuid.UUID) -> bool:
        """Remove an explicit or layered composition and update active selection."""
        record = self.record(composition_id)
        if record.kind == CompositionKind.DEFAULT_IMAGE:
            raise ValueError("default catalog compositions cannot be removed directly")
        self._records.pop(composition_id, None)
        self._order = [item for item in self._order if item != composition_id]
        if self._active_id == composition_id:
            self._active_id = self._order[0] if self._order else None
        self._touch()
        return True

    def set_catalog_comparison(
        self,
        source_id: uuid.UUID,
        *,
        path: Path | None,
    ) -> bool:
        """Set a catalog comparison source on the active composition."""
        record = self.active_record()
        if record is None:
            raise RuntimeError("no active composition")
        if record.kind == CompositionKind.LAYERED_SCENE:
            raise RuntimeError("comparison images require an image composition")
        current = record.comparison
        comparison = CompositionComparison(
            source_id=source_id,
            source_path=path,
            source_kind="catalog",
            split_position=(
                current.split_position if current else self._default_split_position
            ),
            orientation=current.orientation if current else self._default_orientation,
        )
        return self._replace_active_comparison(comparison)

    def clear_comparison(self) -> bool:
        """Clear comparison settings from the active composition."""
        record = self.active_record()
        if record is None:
            return False
        if record.kind == CompositionKind.LAYERED_SCENE:
            raise RuntimeError("comparison images require an image composition")
        if record.comparison is None:
            return False
        return self._replace_active_comparison(None)

    def set_comparison_split(
        self,
        position: float,
        orientation: ComparisonOrientation,
    ) -> bool:
        """Update comparison split state on the active composition."""
        record = self.active_record()
        if record is not None and record.kind == CompositionKind.LAYERED_SCENE:
            raise RuntimeError("comparison split requires an image composition")
        default_changed = (
            self._default_split_position != position
            or self._default_orientation != orientation
        )
        self._default_split_position = position
        self._default_orientation = orientation
        if record is None or record.comparison is None:
            if default_changed:
                self._touch()
            return default_changed
        comparison = record.comparison.with_split(position, orientation)
        return self._replace_active_comparison(comparison)

    def active_record(self) -> CompositionRecord | None:
        """Return the active composition record, if any."""
        if self._active_id is None:
            return None
        return self._records.get(self._active_id)

    def record(self, composition_id: uuid.UUID) -> CompositionRecord:
        """Return a composition record or raise for unknown IDs."""
        if not isinstance(composition_id, uuid.UUID):
            raise TypeError("composition_id must be a UUID")
        record = self._records.get(composition_id)
        if record is None:
            raise KeyError("composition_id does not exist")
        return record

    def composition_ids(self) -> tuple[uuid.UUID, ...]:
        """Return composition IDs in browser order."""
        return tuple(self._order)

    def current_composition_id(self) -> uuid.UUID | None:
        """Return the active composition ID."""
        return self._active_id

    def default_composition_for_image(self, image_id: uuid.UUID) -> uuid.UUID | None:
        """Return the generated default composition ID for a catalog image."""
        return self._default_by_image_id.get(image_id)

    def comparison_state(self) -> ComparisonState:
        """Return public comparison state for the active composition."""
        comparison = self._active_comparison()
        if comparison is None:
            return ComparisonState(
                enabled=False,
                source_id=None,
                source_path=None,
                source_kind=None,
                split_position=self._default_split_position,
                orientation=self._default_orientation,
            )
        return ComparisonState(
            enabled=True,
            source_id=comparison.source_id,
            source_path=comparison.source_path,
            source_kind=comparison.source_kind,
            split_position=comparison.split_position,
            orientation=comparison.orientation,
        )

    def snapshot(self) -> CompositionSnapshot:
        """Return a public immutable snapshot of all compositions."""
        compositions: dict[uuid.UUID, CompositionEntry] = {}
        for composition_id in self._order:
            record = self._records.get(composition_id)
            if record is None:
                continue
            compositions[composition_id] = self._entry(record)
        return CompositionSnapshot(
            compositions=compositions,
            order=tuple(compositions),
            current_composition_id=self._active_id,
        )

    def scene_snapshot(self, composition_id: uuid.UUID) -> QPaneScene | None:
        """Return the normalized public scene snapshot for a composition."""
        return self._scene_snapshot_for_record(self.record(composition_id))

    def active_scene_snapshot(self) -> QPaneScene | None:
        """Return the normalized public scene snapshot for the active composition."""
        record = self.active_record()
        if record is None:
            return None
        return self._scene_snapshot_for_record(record)

    def remove_catalog_images(self, image_ids: set[uuid.UUID]) -> bool:
        """Remove compositions and comparison sources tied to missing catalog images."""
        return self._remove_invalid_catalog_references(
            set(self._catalog_ids()) - image_ids
        )

    def revision(self) -> int:
        """Return a revision for render-relevant composition state."""
        return self._revision

    @staticmethod
    def default_composition_id(image_id: uuid.UUID) -> uuid.UUID:
        """Return the stable generated composition ID for a catalog image."""
        return uuid.uuid5(_DEFAULT_COMPOSITION_NAMESPACE, image_id.hex)

    def _record_from_scene_request(
        self,
        request: QPaneSceneRequest,
        *,
        catalog_contains: Callable[[uuid.UUID], bool],
    ) -> CompositionRecord:
        """Validate a scene request and return a layered composition record."""
        if not isinstance(request, QPaneSceneRequest):
            raise TypeError("request must be a QPaneSceneRequest")
        if request.composition_id is not None and not isinstance(
            request.composition_id, uuid.UUID
        ):
            raise TypeError("composition_id must be a UUID")
        if request.title is not None and not isinstance(request.title, str):
            raise TypeError("title must be a string")
        if request.bounds.width() <= 0.0 or request.bounds.height() <= 0.0:
            raise ValueError("scene bounds must be positive")
        if not request.layers:
            raise ValueError("scene layers must not be empty")
        layer_ids: set[uuid.UUID] = set()
        layers: list[CompositionSceneLayer] = []
        visible_positive = False
        for layer in request.layers:
            normalized = self._normalize_scene_layer(
                layer,
                catalog_contains=catalog_contains,
            )
            if normalized.layer_id in layer_ids:
                raise ValueError("scene layer IDs must be unique")
            layer_ids.add(normalized.layer_id)
            visible_positive = visible_positive or (
                normalized.visible
                and normalized.placement.width() > 0.0
                and normalized.placement.height() > 0.0
            )
            layers.append(normalized)
        if not visible_positive:
            raise ValueError("scene requests require a visible positive-area layer")
        composition_id = request.composition_id or uuid.uuid4()
        title = (
            request.title.strip()
            if request.title and request.title.strip()
            else "Scene"
        )
        return CompositionRecord(
            composition_id=composition_id,
            kind=CompositionKind.LAYERED_SCENE,
            title=title,
            source_image_ids=self._unique_source_ids(
                layer.image_id for layer in layers
            ),
            primary_image_id=None,
            comparison=None,
            scene=CompositionScene(bounds=QRectF(request.bounds), layers=tuple(layers)),
        )

    def _normalize_scene_layer(
        self,
        layer: QPaneCatalogImageLayerRequest,
        *,
        catalog_contains: Callable[[uuid.UUID], bool],
    ) -> CompositionSceneLayer:
        """Validate and normalize one catalog image scene layer request."""
        if not isinstance(layer, QPaneCatalogImageLayerRequest):
            raise TypeError(
                "scene layers must be QPaneCatalogImageLayerRequest instances"
            )
        if not isinstance(layer.layer_id, uuid.UUID):
            raise TypeError("layer_id must be a UUID")
        if not isinstance(layer.image_id, uuid.UUID):
            raise TypeError("image_id must be a UUID")
        if not catalog_contains(layer.image_id):
            raise KeyError("scene layer image_id must exist in the catalog")
        if layer.placement.width() < 0.0 or layer.placement.height() < 0.0:
            raise ValueError("layer placement dimensions must be non-negative")
        if not 0.0 <= layer.opacity <= 1.0:
            raise ValueError("layer opacity must be between 0.0 and 1.0")
        if not isinstance(layer.role, str):
            raise TypeError("layer role must be a string")
        self._validate_clip(layer.clip)
        return CompositionSceneLayer(
            layer_id=layer.layer_id,
            image_id=layer.image_id,
            placement=QRectF(layer.placement),
            visible=bool(layer.visible),
            opacity=float(layer.opacity),
            clip=_copy_scene_clip(layer.clip),
            hit_test=bool(layer.hit_test),
            role=layer.role,
            metadata=dict(layer.metadata),
        )

    def _request_from_template(
        self,
        template: QPaneSceneTemplate,
        bindings: QPaneSceneTemplateBindings,
    ) -> QPaneSceneRequest:
        """Validate template inputs and expand them into a scene request."""
        if not isinstance(template, QPaneSceneTemplate):
            raise TypeError("template must be a QPaneSceneTemplate")
        if not isinstance(bindings, QPaneSceneTemplateBindings):
            raise TypeError("bindings must be a QPaneSceneTemplateBindings")
        if not isinstance(template.template_id, uuid.UUID):
            raise TypeError("template_id must be a UUID")
        if bindings.composition_id is not None and not isinstance(
            bindings.composition_id, uuid.UUID
        ):
            raise TypeError("composition_id must be a UUID")
        if template.bounds.width() <= 0.0 or template.bounds.height() <= 0.0:
            raise ValueError("template bounds must be positive")
        if not template.layers:
            raise ValueError("template layers must not be empty")
        layer_ids: set[uuid.UUID] = set()
        request_layers: list[QPaneCatalogImageLayerRequest] = []
        for layer in template.layers:
            if not isinstance(layer.layer_id, uuid.UUID):
                raise TypeError("template layer_id must be a UUID")
            if layer.layer_id in layer_ids:
                raise ValueError("template layer IDs must be unique")
            layer_ids.add(layer.layer_id)
            if not isinstance(layer.source_slot, str) or not layer.source_slot:
                raise ValueError("template source_slot must be a non-empty string")
            if layer.source_slot not in bindings.catalog_images:
                raise ValueError("template source_slot is missing a catalog binding")
            image_id = bindings.catalog_images[layer.source_slot]
            if not isinstance(image_id, uuid.UUID):
                raise TypeError("bound catalog image IDs must be UUIDs")
            binding_metadata = bindings.metadata.get(layer.source_slot, {})
            if not isinstance(binding_metadata, Mapping):
                raise TypeError("template binding metadata values must be mappings")
            metadata = dict(layer.metadata)
            metadata.update(dict(binding_metadata))
            request_layers.append(
                QPaneCatalogImageLayerRequest(
                    layer_id=layer.layer_id,
                    image_id=image_id,
                    placement=QRectF(layer.placement),
                    visible=layer.visible,
                    opacity=layer.opacity,
                    clip=_copy_scene_clip(layer.clip),
                    hit_test=layer.hit_test,
                    role=layer.role,
                    metadata=metadata,
                )
            )
        title = bindings.title if bindings.title is not None else template.title
        return QPaneSceneRequest(
            composition_id=bindings.composition_id,
            title=title,
            bounds=QRectF(template.bounds),
            layers=tuple(request_layers),
        )

    def _replace_active_comparison(
        self, comparison: CompositionComparison | None
    ) -> bool:
        """Replace active comparison state and report whether it changed."""
        record = self.active_record()
        if record is None:
            raise RuntimeError("no active composition")
        if record.comparison == comparison:
            return False
        self._records[record.composition_id] = record.with_comparison(comparison)
        self._touch()
        return True

    def _active_comparison(self) -> CompositionComparison | None:
        """Return the active composition comparison payload."""
        record = self.active_record()
        if record is None:
            return None
        return record.comparison

    def _remove_invalid_catalog_references(
        self, valid_ids: set[uuid.UUID], *, touch: bool = True
    ) -> bool:
        """Drop records that reference catalog images outside ``valid_ids``."""
        changed = False
        for image_id, composition_id in list(self._default_by_image_id.items()):
            if image_id in valid_ids:
                continue
            self._default_by_image_id.pop(image_id, None)
            self._records.pop(composition_id, None)
            changed = True
        for composition_id, record in list(self._records.items()):
            if record.kind == CompositionKind.DEFAULT_IMAGE:
                continue
            if any(image_id not in valid_ids for image_id in record.source_image_ids):
                self._records.pop(composition_id, None)
                changed = True
                continue
            comparison = record.comparison
            if (
                comparison is not None
                and comparison.source_kind == "catalog"
                and comparison.source_id not in valid_ids
            ):
                self._records[composition_id] = record.with_comparison(None)
                changed = True
        self._order = [
            composition_id
            for composition_id in self._order
            if composition_id in self._records
        ]
        if self._active_id is not None and self._active_id not in self._records:
            self._active_id = self._order[0] if self._order else None
            changed = True
        if changed and touch:
            self._touch()
        return changed

    def _catalog_ids(self) -> tuple[uuid.UUID, ...]:
        """Return catalog IDs referenced by existing compositions."""
        image_ids: list[uuid.UUID] = []
        for record in self._records.values():
            image_ids.extend(record.source_image_ids)
            comparison = record.comparison
            if comparison is not None and comparison.source_kind == "catalog":
                image_ids.append(comparison.source_id)
        return tuple(image_ids)

    def _entry(self, record: CompositionRecord) -> CompositionEntry:
        """Convert an internal record into a public snapshot entry."""
        scene_layer_count = 0
        scene_bounds = None
        if record.kind == CompositionKind.LAYERED_SCENE and record.scene is not None:
            scene_layer_count = len(record.scene.layers)
            scene_bounds = QRectF(record.scene.bounds)
        return CompositionEntry(
            composition_id=record.composition_id,
            kind=record.kind.value,
            title=record.title,
            source_image_ids=record.source_image_ids,
            current_image_id=record.primary_image_id,
            comparison=self._state_for_record(record),
            scene_layer_count=scene_layer_count,
            scene_bounds=scene_bounds,
        )

    def _scene_snapshot_for_record(
        self, record: CompositionRecord
    ) -> QPaneScene | None:
        """Convert a layered composition record into a public scene snapshot."""
        if record.kind != CompositionKind.LAYERED_SCENE or record.scene is None:
            return None
        return QPaneScene(
            composition_id=record.composition_id,
            scene_id=record.composition_id,
            title=record.title,
            bounds=QRectF(record.scene.bounds),
            layers=tuple(
                QPaneSceneLayer(
                    layer_id=layer.layer_id,
                    image_id=layer.image_id,
                    placement=QRectF(layer.placement),
                    visible=layer.visible,
                    opacity=layer.opacity,
                    clip=_copy_scene_clip(layer.clip),
                    hit_test=layer.hit_test,
                    role=layer.role,
                    metadata=layer.metadata,
                )
                for layer in record.scene.layers
            ),
        )

    def _state_for_record(self, record: CompositionRecord) -> ComparisonState:
        """Return comparison state for one record."""
        comparison = record.comparison
        if comparison is None:
            return ComparisonState(
                enabled=False,
                source_id=None,
                source_path=None,
                source_kind=None,
                split_position=self._default_split_position,
                orientation=self._default_orientation,
            )
        return ComparisonState(
            enabled=True,
            source_id=comparison.source_id,
            source_path=comparison.source_path,
            source_kind=comparison.source_kind,
            split_position=comparison.split_position,
            orientation=comparison.orientation,
        )

    @staticmethod
    def _validate_clip(clip: object | None) -> None:
        """Validate clip geometry used by scene layers."""
        if clip is None:
            return
        coordinate_space = getattr(clip, "coordinate_space", None)
        rect = getattr(clip, "rect", None)
        if coordinate_space not in _VALID_CLIP_SPACES:
            raise ValueError(
                f"unsupported scene clip coordinate space: {coordinate_space}"
            )
        if rect is None or rect.width() < 0.0 or rect.height() < 0.0:
            raise ValueError("layer clip dimensions must be non-negative")

    @staticmethod
    def _unique_source_ids(image_ids: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
        """Return image IDs in first-use order with duplicates removed."""
        seen: set[uuid.UUID] = set()
        ordered: list[uuid.UUID] = []
        for image_id in image_ids:
            if image_id in seen:
                continue
            seen.add(image_id)
            ordered.append(image_id)
        return tuple(ordered)

    def _touch(self) -> None:
        """Advance the composition revision."""
        self._revision += 1

    @staticmethod
    def _default_title(path: Path | None, index: int) -> str:
        """Return the generated title for a default image composition."""
        if path is not None:
            return path.name
        return f"Image {index + 1}"

    @staticmethod
    def _explicit_title(
        title: str | None,
        source_ids: tuple[uuid.UUID, ...],
        path_lookup: Callable[[uuid.UUID], Path | None],
    ) -> str:
        """Return a host-provided or generated title for an explicit composition."""
        if title is not None and title.strip():
            return title.strip()
        labels = [
            (
                path.name
                if (path := path_lookup(image_id)) is not None
                else image_id.hex[:8]
            )
            for image_id in source_ids
        ]
        return " / ".join(labels)


def _copy_scene_clip(clip: QPaneSceneClip | None) -> QPaneSceneClip | None:
    """Return a detached copy of a public scene clip."""
    if clip is None:
        return None
    return QPaneSceneClip(
        coordinate_space=clip.coordinate_space,
        rect=QRectF(clip.rect),
    )
