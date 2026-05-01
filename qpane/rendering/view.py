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

"""Compose rendering, catalog, link, and swap collaborators behind QPane.view()."""

from __future__ import annotations


from typing import TYPE_CHECKING


from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize

from ..cache.registry import CacheRegistry
from ..catalog import CatalogController, ImageCatalog, LinkManager
from ..core import (
    CacheSettings,
    Config,
    QPaneState,
    PrefetchSettings,
)

from ..swap import SwapDelegate

from ..swap.diagnostics import swap_progress_provider, swap_summary_provider

from .diagnostics import rendering_retry_provider

from .coordinates import PanelHitTest

from .presenter import RenderingPresenter

from ..scene.identity import SceneLayerTileKey


if TYPE_CHECKING:  # pragma: no cover - import guard for typing only

    from ..concurrency import TaskExecutorProtocol
    from ..qpane import QPane
    from ..scene.render_plan import SceneRenderPlan
    from ..scene.render_plan import SceneContentSnapshot
    from ..scene.render_plan import SceneLayerHitTestResult
    from ..scene.model import SceneDescriptor
    from ..rendering import Renderer
    from ..core.diagnostics_broker import Diagnostics


class View:
    """Own the rendering surface collaborators while QPane serves as a thin facade."""

    def __init__(
        self,
        *,
        qpane: "QPane",
        state: QPaneState,
        catalog: ImageCatalog,
        executor: "TaskExecutorProtocol",
    ) -> None:
        """Wire rendering/swap/catalog collaborators owned by QPane.view()."""
        self._qpane = qpane
        self._state = state
        self._catalog = catalog
        self._executor = executor
        self._cache_registry: CacheRegistry | None = state.cache_registry
        self._attach_pyramid_manager()
        self.presenter = RenderingPresenter(
            qpane=qpane,
            catalog=catalog,
            cache_registry=self._cache_registry,
            executor=executor,
        )
        self.viewport = self.presenter.viewport
        self.tile_manager = self.presenter.tile_manager
        self.renderer = self.presenter.renderer
        self.link_manager = LinkManager()
        self.swap_delegate = SwapDelegate(
            qpane=qpane,
            catalog=catalog,
            viewport=self.viewport,
            tile_manager=self.tile_manager,
            rendering=self.presenter,
            prefetch_settings=self._prefetch_settings_from_config(qpane.settings),
            mark_dirty=self.mark_dirty,
        )
        self.catalog_controller = CatalogController(
            qpane=qpane,
            catalog=catalog,
            viewport=self.viewport,
            tile_manager=self.tile_manager,
            link_manager=self.link_manager,
            swap_delegate=self.swap_delegate,
        )
        self.presenter.set_placeholder_content_provider(
            self.catalog_controller.placeholder_content
        )
        self.swap_delegate.attach_catalog_controller(self.catalog_controller)
        self._connect_rendering_signals()

    def replace_renderer(self, renderer: "Renderer") -> None:
        """Replace the renderer while keeping presenter and view references in sync."""
        self.presenter.renderer = renderer
        self.renderer = renderer

    def calculateRenderPlan(
        self,
        *,
        use_pan: QPointF | None = None,
        is_blank: bool = False,
    ) -> "SceneRenderPlan | None":
        """Delegate scene render-plan calculation to the presenter."""
        return self.presenter.calculateRenderPlan(use_pan=use_pan, is_blank=is_blank)

    def current_content_snapshot(self) -> "SceneContentSnapshot | None":
        """Expose the current rendered content geometry."""
        return self.presenter.current_content_snapshot()

    def current_scene_descriptor(self) -> "SceneDescriptor | None":
        """Expose the active scene descriptor for internal mutation validation."""
        return self.presenter.current_scene_descriptor()

    def invalidate_content_cache(self) -> None:
        """Drop cached scene/content geometry in the presenter."""
        self.presenter.invalidate_content_cache()

    def has_renderable_content(self) -> bool:
        """Return True when rendering can resolve scene content."""
        return self.presenter.has_renderable_content()

    def content_rect(self) -> QRect:
        """Return the base content rectangle in content coordinates."""
        return self.presenter.content_rect()

    def mark_dirty(self, dirty_rect: QRect | QRectF | None = None) -> None:
        """Forward dirty-region tracking to the presenter."""
        self.presenter.mark_dirty(dirty_rect)

    def ensure_view_alignment(self, *, force: bool = False) -> None:
        """Keep the viewport aligned via the presenter helper."""
        self.presenter.ensure_view_alignment(force=force)

    def allocate_buffers(self) -> None:
        """Ask the presenter to allocate renderer buffers."""
        self.presenter.allocate_buffers()

    def physical_viewport_rect(self) -> QRectF:
        """Return the current viewport rectangle in physical pixels."""
        return self.presenter.physical_viewport_rect()

    def panel_to_image_point(self, panel_pos: QPoint) -> QPoint | None:
        """Convert panel coordinates to image coordinates via the presenter."""
        return self.presenter.panel_to_image_point(panel_pos)

    def panel_hit_test(self, panel_pos: QPoint) -> PanelHitTest | None:
        """Expose viewport hit testing for panel coordinates."""
        return self.presenter.panel_hit_test(panel_pos)

    def scene_hit_test(self, panel_pos: QPoint) -> "SceneLayerHitTestResult | None":
        """Expose internal scene hit testing for panel coordinates."""
        return self.presenter.scene_hit_test(panel_pos)

    def image_to_panel_point(self, image_point: QPoint) -> QPointF | None:
        """Convert image coordinates to panel coordinates via the presenter."""
        return self.presenter.image_to_panel_point(image_point)

    def minimum_size_hint(self) -> QSize:
        """Expose the presenter minimum size hint."""
        return self.presenter.minimum_size_hint()

    def register_diagnostics(self, broker: "Diagnostics") -> None:
        """Install rendering and swap diagnostics providers via the diagnostics manager."""
        broker.register_swap_providers(swap_summary_provider, tier="core")
        broker.register_swap_providers(swap_progress_provider)
        broker.register_provider(
            rendering_retry_provider,
            domain="retry",
            tier="detail",
        )

    def handle_tile_ready(self, key: SceneLayerTileKey) -> None:
        """Forward tile-ready signals to the swap delegate."""
        self.swap_delegate.handle_tile_ready(key)

    def handle_pyramid_ready(self, asset_key: object | None) -> None:
        """Bridge pyramid-ready notifications from the catalog to swap plumbing."""
        self.swap_delegate.handle_pyramid_ready(asset_key)

    def _attach_pyramid_manager(self) -> None:
        """Wire the catalog's pyramid manager into the shared cache registry."""
        registry = self._cache_registry
        if registry is None:
            return
        registry.attach_pyramid_manager(self._catalog.pyramid_manager)

    def _connect_rendering_signals(self) -> None:
        """Connect tile/pyramid events directly to the rendering stack."""
        self.tile_manager.tileReady.connect(self.handle_tile_ready)
        self._catalog.pyramidReady.connect(self.handle_pyramid_ready)

    def _prefetch_settings_from_config(self, config: Config) -> PrefetchSettings:
        """Return a PrefetchSettings clone from config.cache, enforcing the expected shape."""
        cache_settings = getattr(config, "cache", None)
        if not isinstance(cache_settings, CacheSettings):
            raise TypeError("config.cache must be a CacheSettings instance")
        prefetch = cache_settings.prefetch
        if not isinstance(prefetch, PrefetchSettings):
            raise TypeError("config.cache.prefetch must be a PrefetchSettings instance")
        return prefetch.clone()
