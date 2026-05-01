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

"""Private registries for feature-owned scene providers and source resolvers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PySide6.QtGui import QImage

from .identity import SceneLayerAssetKey
from .model import SceneDescriptor
from .providers import SceneContribution
from .sources import LayerSource


class SceneContributionProvider(Protocol):
    """Provider that can contribute layers to a base scene."""

    def scene_contribution(
        self,
        base_scene: SceneDescriptor,
        image_id: uuid.UUID | None,
    ) -> SceneContribution | None:
        """Return scene content for ``base_scene`` or None when inactive."""
        ...


class SceneReplacementProvider(Protocol):
    """Provider that can replace default catalog scene resolution."""

    def scene_contribution(self) -> SceneContribution | None:
        """Return a replacement scene contribution or None when inactive."""
        ...


class SceneGeometryAdapter(Protocol):
    """Provider that can adapt base scene geometry before layer contribution."""

    def adapt_base_scene(
        self,
        base_scene: SceneDescriptor,
        image_id: uuid.UUID | None,
    ) -> SceneDescriptor:
        """Return the base scene geometry that feature contributions should target."""
        ...


class LayerSourceResolver(Protocol):
    """Resolve pixels and cache metadata for a layer source variant."""

    def supports_source(self, source: LayerSource) -> bool:
        """Return True when this resolver owns ``source``."""
        ...

    def source_image(self, source: LayerSource) -> QImage | None:
        """Return full-resolution pixels for ``source``."""
        ...

    def source_path(self, source: LayerSource) -> Path | None:
        """Return a path for ``source`` when one exists."""
        ...

    def best_fit_image(
        self,
        source: LayerSource,
        *,
        asset_key: SceneLayerAssetKey,
        pyramid_asset_key: SceneLayerAssetKey,
        full_image: QImage,
        target_width: float,
    ) -> QImage:
        """Return the best available raster for rendering ``source``."""
        ...


@dataclass(frozen=True, slots=True)
class CatalogLayerSourceResolver:
    """Resolve catalog image sources through the catalog owner."""

    catalog: object

    def supports_source(self, source: LayerSource) -> bool:
        """Return True for catalog image sources."""
        from .sources import CatalogImageSource

        return isinstance(source, CatalogImageSource)

    def source_image(self, source: LayerSource) -> QImage | None:
        """Return catalog pixels for ``source``."""
        image_getter = getattr(self.catalog, "getImage", None)
        if not callable(image_getter):
            return None
        return image_getter(source.image_id)  # type: ignore[union-attr]

    def source_path(self, source: LayerSource) -> Path | None:
        """Return the catalog path for ``source``."""
        path_getter = getattr(self.catalog, "getPath", None)
        if not callable(path_getter):
            return None
        return path_getter(source.image_id)  # type: ignore[union-attr]

    def best_fit_image(
        self,
        source: LayerSource,
        *,
        asset_key: SceneLayerAssetKey,
        pyramid_asset_key: SceneLayerAssetKey,
        full_image: QImage,
        target_width: float,
    ) -> QImage:
        """Return a catalog pyramid raster when available."""
        best_fit = getattr(self.catalog, "getBestFitImageForAsset", None)
        if callable(best_fit):
            source_image = best_fit(pyramid_asset_key, target_width)
            if source_image is not None and not source_image.isNull():
                return source_image
        return full_image


class SceneProviderRegistry:
    """Own ordered private scene providers registered by feature domains."""

    def __init__(self) -> None:
        """Initialize an empty provider registry."""
        self._replacement_providers: list[SceneReplacementProvider] = []
        self._geometry_adapters: list[SceneGeometryAdapter] = []
        self._contribution_providers: list[SceneContributionProvider] = []

    def register_replacement(
        self, provider: SceneReplacementProvider
    ) -> SceneReplacementProvider:
        """Register a provider that can replace default scene resolution."""
        if provider not in self._replacement_providers:
            self._replacement_providers.append(provider)
        return provider

    def unregister_replacement(self, provider: SceneReplacementProvider) -> None:
        """Remove a replacement provider."""
        self._replacement_providers = [
            candidate
            for candidate in self._replacement_providers
            if candidate is not provider
        ]

    def register_geometry_adapter(
        self, provider: SceneGeometryAdapter
    ) -> SceneGeometryAdapter:
        """Register a provider that can adapt default scene geometry."""
        if provider not in self._geometry_adapters:
            self._geometry_adapters.append(provider)
        return provider

    def unregister_geometry_adapter(self, provider: SceneGeometryAdapter) -> None:
        """Remove a previously registered scene geometry adapter."""
        self._geometry_adapters = [
            candidate
            for candidate in self._geometry_adapters
            if candidate is not provider
        ]

    def register_contribution(
        self, provider: SceneContributionProvider
    ) -> SceneContributionProvider:
        """Register a provider that can contribute layers to a scene."""
        if provider not in self._contribution_providers:
            self._contribution_providers.append(provider)
        return provider

    def unregister_contribution(self, provider: SceneContributionProvider) -> None:
        """Remove a contribution provider."""
        self._contribution_providers = [
            candidate
            for candidate in self._contribution_providers
            if candidate is not provider
        ]

    def replacement_contributions(self) -> tuple[SceneContribution, ...]:
        """Return active replacement scene contributions."""
        return tuple(
            contribution
            for provider in self._replacement_providers
            if (contribution := provider.scene_contribution()) is not None
        )

    def adapt_base_scene(
        self, base_scene: SceneDescriptor, image_id: uuid.UUID | None
    ) -> SceneDescriptor:
        """Return ``base_scene`` after registered geometry adapters have run."""
        adapted = base_scene
        for provider in self._geometry_adapters:
            adapted = provider.adapt_base_scene(adapted, image_id)
        return adapted

    def contributions_for(
        self, base_scene: SceneDescriptor, image_id: uuid.UUID | None
    ) -> tuple[SceneContribution, ...]:
        """Return active feature contributions for ``base_scene``."""
        return tuple(
            contribution
            for provider in self._contribution_providers
            if (contribution := provider.scene_contribution(base_scene, image_id))
            is not None
        )


class LayerSourceResolverRegistry:
    """Route layer-source lookups to their authoritative domain owner."""

    def __init__(self) -> None:
        """Initialize an empty resolver registry."""
        self._resolvers: list[LayerSourceResolver] = []

    def register(self, resolver: LayerSourceResolver) -> LayerSourceResolver:
        """Register a source resolver if it is not already present."""
        if resolver not in self._resolvers:
            self._resolvers.append(resolver)
        return resolver

    def unregister(self, resolver: LayerSourceResolver) -> None:
        """Remove a previously registered source resolver."""
        self._resolvers = [
            candidate for candidate in self._resolvers if candidate is not resolver
        ]

    def resolver_for(self, source: LayerSource) -> LayerSourceResolver | None:
        """Return the resolver that owns ``source``."""
        for resolver in self._resolvers:
            if resolver.supports_source(source):
                return resolver
        return None

    def source_image(self, source: LayerSource) -> QImage | None:
        """Return source pixels through the owning resolver."""
        resolver = self.resolver_for(source)
        return None if resolver is None else resolver.source_image(source)

    def source_path(self, source: LayerSource) -> Path | None:
        """Return source path metadata through the owning resolver."""
        resolver = self.resolver_for(source)
        return None if resolver is None else resolver.source_path(source)

    def best_fit_image(
        self,
        source: LayerSource,
        *,
        asset_key: SceneLayerAssetKey,
        pyramid_asset_key: SceneLayerAssetKey,
        full_image: QImage,
        target_width: float,
    ) -> QImage:
        """Return the best available raster through the owning resolver."""
        resolver = self.resolver_for(source)
        if resolver is None:
            return full_image
        return resolver.best_fit_image(
            source,
            asset_key=asset_key,
            pyramid_asset_key=pyramid_asset_key,
            full_image=full_image,
            target_width=target_width,
        )
