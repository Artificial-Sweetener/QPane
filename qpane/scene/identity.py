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

"""Deterministic scene/layer identities and future cache key primitives."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_SCENE_NAMESPACE = uuid.UUID("efdbe9f6-075c-51ba-8391-715cbf8d6c17")
_BASE_LAYER_NAMESPACE = uuid.UUID("4952143f-d857-5d1c-98a3-3423f7122264")
_PLACEHOLDER_SCENE_NAMESPACE = uuid.UUID("96d6c459-5bd7-51b9-b7da-994caaf27218")
_PLACEHOLDER_LAYER_NAMESPACE = uuid.UUID("2a82ab02-42ed-5269-9f16-21d2b48556ea")
_PLACEHOLDER_SOURCE_NAMESPACE = uuid.UUID("af86f75f-e94d-5fb6-88fd-5ee9878ff977")
_MASK_LAYER_NAMESPACE = uuid.UUID("80ae57c6-cf1c-5e9a-971f-5161839f0c7f")
_COMPARE_LAYER_NAMESPACE = uuid.UUID("e618d770-1769-571f-8471-5df8cf28ab15")


def default_scene_id(image_id: uuid.UUID) -> uuid.UUID:
    """Return the deterministic default-scene ID for a catalog image."""
    return uuid.uuid5(_DEFAULT_SCENE_NAMESPACE, str(image_id))


def base_image_layer_id(image_id: uuid.UUID) -> uuid.UUID:
    """Return the deterministic base-image layer ID for a catalog image."""
    return uuid.uuid5(_BASE_LAYER_NAMESPACE, str(image_id))


def default_catalog_asset_key(
    image_id: uuid.UUID,
    *,
    revision: int,
    source_path: Path | None,
) -> "SceneLayerAssetKey":
    """Return the default-scene asset key for a catalog image."""
    return SceneLayerAssetKey(
        scene_id=default_scene_id(image_id),
        layer_id=base_image_layer_id(image_id),
        source_id=image_id,
        source_kind="catalog-image",
        source_revision=revision,
        source_path=source_path,
    )


def placeholder_source_id(source_path: Path | None) -> uuid.UUID:
    """Return the deterministic source ID for a configured placeholder image."""
    identity = (
        str(source_path) if source_path is not None else "<anonymous-placeholder>"
    )
    return uuid.uuid5(_PLACEHOLDER_SOURCE_NAMESPACE, identity)


def placeholder_scene_id(source_id: uuid.UUID) -> uuid.UUID:
    """Return the deterministic scene ID for a placeholder image source."""
    return uuid.uuid5(_PLACEHOLDER_SCENE_NAMESPACE, str(source_id))


def placeholder_layer_id(source_id: uuid.UUID) -> uuid.UUID:
    """Return the deterministic layer ID for a placeholder image source."""
    return uuid.uuid5(_PLACEHOLDER_LAYER_NAMESPACE, str(source_id))


def mask_layer_id(scene_id: uuid.UUID, mask_id: uuid.UUID) -> uuid.UUID:
    """Return the deterministic scene-layer ID for a mask source."""
    return uuid.uuid5(_MASK_LAYER_NAMESPACE, f"{scene_id}:{mask_id}")


def mask_layer_asset_key(
    *,
    scene_id: uuid.UUID,
    mask_id: uuid.UUID,
    revision: int,
) -> "SceneLayerAssetKey":
    """Return the asset key for a mask layer in a resolved scene."""
    return SceneLayerAssetKey(
        scene_id=scene_id,
        layer_id=mask_layer_id(scene_id, mask_id),
        source_id=mask_id,
        source_kind="mask-layer",
        source_revision=revision,
        source_path=None,
    )


def compare_layer_id(scene_id: uuid.UUID, source_id: uuid.UUID) -> uuid.UUID:
    """Return the deterministic comparison layer ID for a source in a scene."""
    return uuid.uuid5(_COMPARE_LAYER_NAMESPACE, f"{scene_id}:{source_id}")


def scene_image_asset_key(
    *,
    scene_id: uuid.UUID,
    layer_id: uuid.UUID,
    source_id: uuid.UUID,
    source_kind: str,
    revision: int,
    source_path: Path | None,
) -> "SceneLayerAssetKey":
    """Return an image asset key for a non-default scene layer."""
    return SceneLayerAssetKey(
        scene_id=scene_id,
        layer_id=layer_id,
        source_id=source_id,
        source_kind=source_kind,
        source_revision=revision,
        source_path=source_path,
    )


@dataclass(frozen=True, slots=True)
class SceneLayerAssetKey:
    """Identify source-domain raster assets for scene/layer-aware caches."""

    scene_id: uuid.UUID
    layer_id: uuid.UUID
    source_id: uuid.UUID
    source_kind: str
    source_revision: int
    source_path: Path | None = None

    def __post_init__(self) -> None:
        """Validate revision metadata used for cache invalidation."""
        if self.source_revision < 0:
            raise ValueError("source revision must be non-negative")


@dataclass(frozen=True, slots=True)
class SceneLayerTileKey:
    """Identify a single tile generated from a scene layer asset."""

    asset_key: SceneLayerAssetKey
    pyramid_asset_key: SceneLayerAssetKey
    pyramid_scale: float
    row: int
    col: int

    def __post_init__(self) -> None:
        """Validate tile grid metadata."""
        if self.pyramid_scale <= 0:
            raise ValueError("pyramid scale must be positive")
        if self.row < 0 or self.col < 0:
            raise ValueError("tile row and column must be non-negative")
