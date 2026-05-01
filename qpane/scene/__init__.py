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

"""Internal scene and layer descriptors used to plan future composition."""

from __future__ import annotations

from .default_scene import DefaultCatalogSceneProvider, build_default_catalog_scene
from .identity import (
    SceneLayerAssetKey,
    SceneLayerTileKey,
    base_image_layer_id,
    compare_layer_id,
    default_scene_id,
    mask_layer_asset_key,
    mask_layer_id,
    placeholder_layer_id,
    placeholder_scene_id,
    placeholder_source_id,
    scene_image_asset_key,
)
from .mask_adapter import (
    MaskSceneProvider,
    MaskServiceSceneProvider,
    mask_layers_for_image,
)
from .model import (
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
from .mutations import (
    BaseSceneMutationOwner,
    SceneMutationCoordinator,
    SceneMutationOwner,
    SceneMutationResult,
    SceneMutationStatus,
)
from .providers import SceneContribution, SceneProvider, SceneResolver
from .registry import (
    CatalogLayerSourceResolver,
    LayerSourceResolver,
    LayerSourceResolverRegistry,
    SceneContributionProvider,
    SceneProviderRegistry,
    SceneReplacementProvider,
)
from .placeholder_scene import build_placeholder_scene
from .render_plan import (
    RasterLayerRenderItem,
    MaskLayerRenderItem,
    RenderStrategy,
    SceneHitTestItem,
    SceneLayerHitTestResult,
    SceneRenderItem,
    SceneRenderPlan,
    TileRenderData,
)
from .sources import (
    CatalogImageSource,
    LayerSource,
    MaskLayerSource,
    PlaceholderImageSource,
)

__all__ = [
    "BlendMode",
    "CatalogImageSource",
    "CatalogLayerSourceResolver",
    "ClipCoordinateSpace",
    "DefaultCatalogSceneProvider",
    "BaseSceneMutationOwner",
    "LayerClip",
    "LayerDescriptor",
    "LayerHitTest",
    "LayerKind",
    "LayerPlacement",
    "LayerSource",
    "LayerSourceResolver",
    "LayerSourceResolverRegistry",
    "MaskLayerSource",
    "MaskLayerRenderItem",
    "MaskSceneProvider",
    "MaskServiceSceneProvider",
    "PlaceholderImageSource",
    "RasterLayerRenderItem",
    "RenderStrategy",
    "SceneContribution",
    "SceneDescriptor",
    "SceneHitTestItem",
    "SceneLayerHitTestResult",
    "SceneKind",
    "SceneLayerAssetKey",
    "SceneLayerTileKey",
    "SceneMutationCoordinator",
    "SceneMutationOwner",
    "SceneMutationResult",
    "SceneMutationStatus",
    "SceneProvider",
    "SceneContributionProvider",
    "SceneProviderRegistry",
    "SceneReplacementProvider",
    "SceneRenderItem",
    "SceneRenderPlan",
    "SceneResolver",
    "TileRenderData",
    "base_image_layer_id",
    "build_default_catalog_scene",
    "build_placeholder_scene",
    "compare_layer_id",
    "default_scene_id",
    "mask_layer_asset_key",
    "mask_layer_id",
    "mask_layers_for_image",
    "placeholder_layer_id",
    "placeholder_scene_id",
    "placeholder_source_id",
    "scene_image_asset_key",
]
