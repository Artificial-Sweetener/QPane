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

"""Phase 2 tests for private scene descriptors and default catalog scenes."""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QSize

from qpane.scene.default_scene import (
    DefaultCatalogSceneProvider,
    build_default_catalog_scene,
)
from qpane.scene.identity import (
    SceneLayerAssetKey,
    SceneLayerTileKey,
    base_image_layer_id,
    default_catalog_asset_key,
    default_scene_id,
)
from qpane.scene.model import BlendMode, LayerKind, SceneKind
from qpane.scene.providers import SceneResolver
from qpane.scene.sources import CatalogImageSource


def test_default_scene_and_base_layer_ids_are_deterministic() -> None:
    """Default scene and base layer IDs should derive only from image ID."""
    image_id = uuid.uuid4()
    assert default_scene_id(image_id) == default_scene_id(image_id)
    assert base_image_layer_id(image_id) == base_image_layer_id(image_id)
    assert default_scene_id(image_id) != base_image_layer_id(image_id)
    assert default_scene_id(image_id) != default_scene_id(uuid.uuid4())
    asset_key = default_catalog_asset_key(
        image_id,
        revision=2,
        source_path=Path("current.png"),
    )
    assert asset_key.scene_id == default_scene_id(image_id)
    assert asset_key.layer_id == base_image_layer_id(image_id)
    assert asset_key.source_id == image_id
    assert asset_key.source_revision == 2


def test_default_catalog_scene_contains_one_full_bounds_image_layer() -> None:
    """Default scenes should preserve current one-image viewer behavior."""
    image_id = uuid.uuid4()
    path = Path("current.png")
    scene = build_default_catalog_scene(
        image_id=image_id,
        image_size=QSize(320, 200),
        source_path=path,
        revision=3,
    )
    assert scene.scene_id == default_scene_id(image_id)
    assert scene.kind == SceneKind.DEFAULT_CATALOG_IMAGE
    assert scene.bounds.width == 320.0
    assert scene.bounds.height == 200.0
    assert len(scene.layers) == 1
    layer = scene.layers[0]
    assert layer.scene_id == scene.scene_id
    assert layer.layer_id == base_image_layer_id(image_id)
    assert layer.kind == LayerKind.IMAGE
    assert isinstance(layer.source, CatalogImageSource)
    assert layer.source.image_id == image_id
    assert layer.source.source_path == path
    assert layer.source.revision == 3
    assert layer.source_revision == 3
    assert layer.placement == scene.bounds
    assert layer.visible is True
    assert layer.opacity == 1.0
    assert layer.blend_mode == BlendMode.NORMAL
    assert layer.clip is None
    assert layer.hit_test.enabled is True


def test_scene_layer_asset_identity_is_scene_layer_revision_and_path_sensitive() -> (
    None
):
    """Future cache keys should avoid collisions across scenes, layers, and paths."""
    source_id = uuid.uuid4()
    first_scene = uuid.uuid4()
    second_scene = uuid.uuid4()
    layer_id = uuid.uuid4()
    first = SceneLayerAssetKey(
        scene_id=first_scene,
        layer_id=layer_id,
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=1,
        source_path=Path("a.png"),
    )
    assert first != SceneLayerAssetKey(
        scene_id=second_scene,
        layer_id=layer_id,
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=1,
        source_path=Path("a.png"),
    )
    assert first != SceneLayerAssetKey(
        scene_id=first_scene,
        layer_id=uuid.uuid4(),
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=1,
        source_path=Path("a.png"),
    )
    assert first != SceneLayerAssetKey(
        scene_id=first_scene,
        layer_id=layer_id,
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=2,
        source_path=Path("a.png"),
    )
    assert first != SceneLayerAssetKey(
        scene_id=first_scene,
        layer_id=layer_id,
        source_id=source_id,
        source_kind="catalog-image",
        source_revision=1,
        source_path=Path("b.png"),
    )
    assert SceneLayerTileKey(
        first, pyramid_asset_key=first, pyramid_scale=1.0, row=0, col=0
    ) != (
        SceneLayerTileKey(
            first, pyramid_asset_key=first, pyramid_scale=0.5, row=0, col=0
        )
    )


def test_scene_resolver_returns_ordered_default_provider_scene() -> None:
    """The initial resolver should deterministically resolve the default scene."""
    image_id = uuid.uuid4()
    provider = DefaultCatalogSceneProvider(
        image_id=image_id,
        image_size=QSize(64, 48),
        source_path=None,
        revision=1,
    )
    resolver = SceneResolver(providers=(provider,))
    scene = resolver.resolve()
    assert scene is not None
    assert scene.scene_id == default_scene_id(image_id)
    assert scene.layers[0].source == CatalogImageSource(
        image_id=image_id,
        source_path=None,
        revision=1,
    )
