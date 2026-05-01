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

"""Catalog interactions and linked-view coordination for the QPane widget."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QGuiApplication, QImage, QPixmap

from ..core.config import PlaceholderSettings
from ..rendering import CoordinateContext, NormalizedViewState, ViewportZoomMode
from ..scene.identity import SceneLayerAssetKey
from ..tools import Tools
from ..ui import copyToClipboard, drag_out_image
from .image_map import ImageMap
from ..types import LinkedGroup

if TYPE_CHECKING:  # pragma: no cover
    from ..masks.mask_service import MaskService
    from ..qpane import QPane
    from ..rendering import Viewport
    from ..sam import SamManager
    from ..swap import SwapDelegate
    from ..tiles import TileManager
    from .image_catalog import ImageCatalog
    from .link import LinkManager
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaceholderDisplayOptions:
    """Sanitized placeholder behaviour applied when the catalog is empty."""

    source: Path | None
    panzoom_enabled: bool
    drag_out_enabled: bool
    zoom_mode: str
    locked_zoom: float | None
    locked_size: QSize | None
    scale_mode: str
    display_size: QSize | None
    min_display_size: QSize | None
    max_display_size: QSize | None
    scale_factor: float


@dataclass(frozen=True)
class PlaceholderContent:
    """Catalog-owned placeholder pixels and metadata for internal rendering."""

    image: QImage
    source_path: Path | None
    revision: int = 0


class CatalogController:
    """Encapsulate catalog mutations, cache cleanup, and linked view state."""

    def __init__(
        self,
        *,
        qpane: QPane,
        catalog: ImageCatalog,
        viewport: Viewport,
        tile_manager: TileManager,
        link_manager: LinkManager,
        swap_delegate: SwapDelegate,
    ) -> None:
        """Wire QPane, viewport, tile, and swap collaborators for catalog orchestration.

        Args:
            qpane: Owning QPane widget so UI state can be updated during mutations.
            catalog: Backing :class:`ImageCatalog` storing image data.
            viewport: Viewport used to determine current view state/resets.
            tile_manager: Tile manager that needs trimming when catalog paths change.
            link_manager: Manager that tracks linked-view group assignments.
            swap_delegate: Delegate responsible for loading and presenting images.
        """
        self._qpane = qpane
        self.catalog = catalog
        self._viewport = viewport
        self._tile_manager = tile_manager
        self.link_manager = link_manager
        self._swap_delegate = swap_delegate
        self._placeholder_image: QImage | None = None
        self._placeholder_policy: PlaceholderDisplayOptions | None = None
        self._placeholder_active = False
        self._placeholder_previous_mode: str | None = None

    # Catalog mutations and cache maintenance
    def setImagesByID(
        self, image_map: ImageMap, current_id: uuid.UUID, *, display: bool = True
    ) -> None:
        """Replace the catalog contents and refresh caches for removed paths.

        Args:
            image_map: Ordered mapping of IDs to CatalogEntry records.
            current_id: Identifier that should become active after replacement.
            display: When True, immediately render the current catalog image.

        Side effects:
            Evicts tile/SAM caches for removed or changed paths and optionally
            triggers rendering of the new current image.
        """
        mutation = self.catalog.setImagesByID(image_map, current_id)
        self._evict_catalog_assets(mutation.cache_asset_keys_to_evict)
        self._evict_sam_images(
            tuple(dict.fromkeys(mutation.removed_ids + mutation.content_changed_ids))
        )
        if display:
            self._display_current_catalog_image()

    def addImage(self, image_id: uuid.UUID, image: QImage, path: Path | None) -> None:
        """Add or replace a single catalog entry.

        Args:
            image_id: Identifier to create or overwrite.
            image: Image data to persist.
            path: Optional filesystem path associated with ``image``.

        Raises:
            ValueError: If ``image`` is null.

        Side effects:
            Regenerates pyramids for new/updated paths and evicts stale pyramids
            and tile/SAM caches when content changes or paths are displaced.
        """
        if image.isNull():
            logger.error("addImage called with null QImage for %s", image_id)
            raise ValueError("image must not be null")
        mutation = self.catalog.addImage(image_id, image, path)
        self._evict_catalog_assets(mutation.cache_asset_keys_to_evict)
        self._evict_sam_images(mutation.content_changed_ids)

    def removeImageByID(self, image_id: uuid.UUID) -> None:
        """Remove a single image and clean up dependent caches.

        Args:
            image_id: Identifier that should be removed.

        Side effects:
            Evicts tile/SAM caches, clears mask state for the image, updates
            linked-view groups, and renders the placeholder when the catalog
            becomes empty.
        """
        self._remove_images((image_id,), log_context=f"removing image {image_id}")

    def removeImagesByID(self, image_ids: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
        """Remove multiple images and report which IDs were removed.

        Args:
            image_ids: Identifiers to deduplicate and remove.

        Returns:
            Removed IDs in the order they were processed.

        Side effects:
            Evicts tile/SAM caches for removed paths, updates link groups, and
            renders the placeholder once when the catalog becomes empty.
        """
        return self._remove_images(image_ids, log_context="batch removal")

    def clearImages(self) -> None:
        """Reset the catalog, caches, and linked views before showing a placeholder.

        Side effects:
            Clears tile caches, mask render caches, SAM caches, link groups, and
            renders the configured placeholder or blanks the qpane.
        """
        self._tile_manager.clear_caches()
        mask_service = self._mask_service
        if mask_service is not None:
            mask_service.clearRenderCache()
        sam_manager = self._sam_manager
        if sam_manager is not None:
            sam_manager.clearCache()
        self.catalog.clearImages()
        self.link_manager.clear()
        logger.info(
            "Cleared catalog images; rendering configured placeholder when idle"
        )
        self.renderPlaceholderOrBlank(reason="catalog_cleared")

    def setCurrentImageID(self, image_id: uuid.UUID | None) -> None:
        """Delegate navigation to the swap coordinator.

        Args:
            image_id: Identifier that should become current.
        """
        self._swap_delegate.set_current_image(image_id)

    def deselectImage(self) -> None:
        """Deselect the current image and revert to the placeholder state."""
        self.catalog.setCurrentImageID(None)
        self._swap_delegate.reset()
        self.renderPlaceholderOrBlank(reason="deselected")

    def setLinkedGroups(self, groups: Iterable[LinkedGroup]) -> None:
        """Persist link groups using the link manager."""
        self.link_manager.setGroups(groups)

    def setAllImagesLinked(self, enabled: bool) -> None:
        """Toggle pan/zoom synchronization across every catalog image.

        Args:
            enabled: ``True`` links all images; ``False`` clears groups.
        """
        image_ids = tuple(self.catalog.getImageIds())
        if enabled and len(image_ids) >= 2:
            existing = self.link_manager.getGroupRecords()
            existing_id = None
            for group in existing:
                if set(group.members) == set(image_ids):
                    existing_id = group.group_id
                    break
            group_id = existing_id if existing_id is not None else uuid.uuid4()
            self.setLinkedGroups((LinkedGroup(group_id=group_id, members=image_ids),))
            return
        self.setLinkedGroups(tuple())

    def apply_placeholder_config(self, settings: PlaceholderSettings) -> None:
        """Resolve and store placeholder assets/policies from configuration.

        Side effects:
            Loads placeholder images from disk, updates cached policies, and
            renders or blanks the qpane when the catalog is empty.
        """
        placeholder, policy = self._resolve_placeholder(
            settings, self._placeholder_image, self._placeholder_policy
        )
        self._placeholder_image = placeholder
        self._placeholder_policy = policy
        should_render_placeholder = placeholder is not None
        if not self.catalog.hasImages() and should_render_placeholder:
            self.renderPlaceholderOrBlank(reason="config_update")
        if placeholder is None and not self.catalog.hasImages():
            self._blank_qpane()

    def _resolve_placeholder(
        self,
        settings: PlaceholderSettings,
        existing_image: QImage | None = None,
        existing_policy: PlaceholderDisplayOptions | None = None,
    ) -> tuple[QImage | None, PlaceholderDisplayOptions | None]:
        """Return the configured placeholder image and sanitized policy."""
        if settings is None or settings.source in (None, ""):
            return None, None
        source = settings.source
        try:
            resolved_source = str(source)
        except Exception:
            resolved_source = None
        if not resolved_source:
            return None, None
        placeholder = self._load_placeholder_image(resolved_source)
        if placeholder is None:
            return existing_image, existing_policy
        zoom_mode = self._sanitize_zoom_mode(settings.zoom_mode)
        locked_zoom = self._sanitize_locked_zoom(settings.locked_zoom)
        locked_size = self._sanitize_locked_size(settings.locked_size)
        scale_mode = self._sanitize_scale_mode(settings.scale_mode)
        display_size = self._sanitize_locked_size(settings.display_size)
        min_display_size = self._sanitize_locked_size(settings.min_display_size)
        max_display_size = self._sanitize_locked_size(settings.max_display_size)
        scale_factor = self._sanitize_scale_factor(settings.scale_factor)
        if zoom_mode == "locked_zoom" and locked_zoom is None:
            zoom_mode = "fit"
        if zoom_mode == "locked_size" and locked_size is None:
            zoom_mode = "fit"
        policy = PlaceholderDisplayOptions(
            source=Path(resolved_source),
            panzoom_enabled=bool(getattr(settings, "panzoom_enabled", False)),
            drag_out_enabled=bool(settings.drag_out_enabled),
            zoom_mode=zoom_mode,
            locked_zoom=locked_zoom if zoom_mode == "locked_zoom" else None,
            locked_size=locked_size if zoom_mode == "locked_size" else None,
            scale_mode=scale_mode,
            display_size=display_size,
            min_display_size=min_display_size,
            max_display_size=max_display_size,
            scale_factor=scale_factor,
        )
        return placeholder, policy

    def _load_placeholder_image(self, source: str) -> QImage | None:
        """Load a placeholder from ``source`` using QImageReader when available."""
        if not source:
            return None
        # Normalize filesystem paths where possible.
        if not source.startswith(":"):
            try:
                path_obj = Path(source).expanduser()
                source = str(path_obj)
                if not path_obj.exists():
                    logger.warning(
                        "Placeholder source does not exist on disk: %s", path_obj
                    )
            except Exception:
                pass
        placeholder = QImage(source)
        if placeholder.isNull():
            from PySide6.QtGui import QImageReader  # local import to avoid startup cost

            reader = QImageReader(source)
            reader.setAutoTransform(True)
            placeholder = reader.read()
        if placeholder.isNull():
            logger.warning(
                "Failed to load placeholder image from %s; keeping previous placeholder",
                source,
            )
            return None
        logger.info(
            "Loaded placeholder image from %s (%dx%d)",
            source,
            placeholder.width(),
            placeholder.height(),
        )
        return placeholder

    def _sanitize_zoom_mode(self, zoom_mode: str | None) -> str:
        """Clamp zoom policy to known values."""
        allowed = {"fit", "locked_zoom", "locked_size"}
        candidate = (zoom_mode or "").strip().lower()
        if candidate in allowed:
            return candidate
        logger.warning(
            "Invalid placeholder zoom_mode '%s'; defaulting to fit", zoom_mode
        )
        return "fit"

    def _sanitize_locked_zoom(self, locked_zoom: float | None) -> float | None:
        """Validate locked zoom values."""
        if locked_zoom is None:
            return None
        try:
            numeric = float(locked_zoom)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid placeholder locked_zoom '%s'; ignoring value", locked_zoom
            )
            return None
        if numeric <= 0:
            logger.warning(
                "Invalid placeholder locked_zoom '%s'; must be positive", locked_zoom
            )
            return None
        return numeric

    def _sanitize_locked_size(self, size: tuple[int, int] | None) -> QSize | None:
        """Convert a size tuple to ``QSize`` when valid."""
        if size is None:
            return None
        try:
            width, height = int(size[0]), int(size[1])
        except (TypeError, ValueError, IndexError):
            logger.warning(
                "Invalid placeholder locked_size '%s'; expected (width, height) tuple",
                size,
            )
            return None
        if width <= 0 or height <= 0:
            logger.warning(
                "Invalid placeholder locked_size '%s'; dimensions must be positive",
                size,
            )
            return None
        return QSize(width, height)

    def _sanitize_scale_mode(self, scale_mode: str | None) -> str:
        """Clamp placeholder scale mode to supported values."""
        allowed = {"auto", "logical_fit", "physical_fit", "relative_fit"}
        candidate = (scale_mode or "").strip().lower()
        if candidate in allowed:
            return candidate
        logger.warning(
            "Invalid placeholder scale_mode '%s'; defaulting to auto", scale_mode
        )
        return "auto"

    def _sanitize_scale_factor(self, factor: float | None) -> float:
        """Validate scale factor used in relative scaling."""
        if factor is None:
            return 1.0
        try:
            numeric = float(factor)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid placeholder scale_factor '%s'; defaulting to 1.0", factor
            )
            return 1.0
        if numeric <= 0:
            logger.warning(
                "Invalid placeholder scale_factor '%s'; defaulting to 1.0", factor
            )
            return 1.0
        return numeric

    # View-state persistence helpers
    def saveZoomPanForCurrentImage(self) -> None:
        """Capture and store the current normalized view state."""
        state = self._capture_normalized_view_state()
        if not state:
            return
        current_id = self.catalog.getCurrentId()
        if current_id is None:
            return
        self.link_manager.updateViewState(current_id, state)

    def restoreZoomPanForNewImage(self, image_id: uuid.UUID) -> None:
        """Apply the stored view state for ``image_id`` when present.

        Args:
            image_id: Identifier whose linked-view state should be restored.
        """
        state = self.link_manager.getViewState(image_id)
        if state:
            if state.zoom_mode == ViewportZoomMode.FIT:
                self._viewport.setZoomFit()
                return
            self._apply_normalized_view_state(state, image_id)
            if state.zoom_mode == ViewportZoomMode.ONE_TO_ONE:
                self._viewport.zoom_mode = ViewportZoomMode.ONE_TO_ONE
            return
        self._viewport.setZoomFit()

    # UI helpers (drag-out, clipboard, display)
    def handleDragRequest(self, event) -> None:
        """Forward drag requests through the helper that emits a QDrag.

        Args:
            event: Drag event supplied by the host widget toolkit.

        Side effects:
            Initiates a drag-out unless suppressed by the active placeholder policy.
        """
        if self._placeholder_active:
            policy = self._placeholder_policy
            if policy is None or not getattr(policy, "drag_out_enabled", False):
                logger.info("Drag-out suppressed while placeholder is active")
                return
            placeholder = self.placeholder_content()
            if placeholder is None:
                return
            drag_out_image(
                self._qpane,
                event,
                image=placeholder.image,
                path=placeholder.source_path,
            )
            return
        drag_out_image(
            self._qpane,
            event,
            image=self.catalog.getCurrentImage(),
            path=self.catalog.getCurrentPath(),
        )

    def copyCurrentImageToClipboard(self) -> bool:
        """Copy the current base catalog or placeholder image to the system clipboard."""
        image_path = self.catalog.getCurrentPath()
        image = self.catalog.getCurrentImage()
        if image is None or image.isNull():
            placeholder = self.placeholder_content()
            if placeholder is not None:
                image = placeholder.image
                image_path = placeholder.source_path
        if image is None or image.isNull():
            logger.warning("Clipboard copy skipped because no base image is available")
            return False
        try:
            copied = copyToClipboard(QPixmap.fromImage(image))
        except RuntimeError as exc:
            logger.error(
                "Clipboard copy failed for %s: %s",
                image_path or "<unsaved>",
                exc,
            )
            return False
        if not copied:
            logger.warning(
                "Clipboard copy skipped for %s because the pixmap was null",
                image_path or "<unsaved>",
            )
            return False
        return True

    def displayCurrentCatalogImage(self, *, fit_view: bool = True) -> None:
        """Render the catalog's current image or blank the qpane when absent.

        Args:
            fit_view: Forwarded to the swap delegate's display logic.
        """
        self._display_current_catalog_image(fit_view=fit_view)

    # Internal helpers
    def renderPlaceholderOrBlank(self, *, reason: str) -> None:
        """Display the placeholder or blank qpane when catalog is empty or deselected."""
        if self._display_placeholder_image():
            return
        logger.info(
            "No placeholder image configured; blanking qpane (reason=%s)",
            reason,
        )
        self.exit_placeholder_mode()
        self._blank_qpane()

    def _display_placeholder_image(self) -> bool:
        """Render the stored placeholder when available."""
        placeholder = self._placeholder_image
        policy = self._placeholder_policy
        if placeholder is None or placeholder.isNull() or policy is None:
            return False
        logger.info(
            "Displaying placeholder image while catalog is empty (%dx%d)",
            placeholder.width(),
            placeholder.height(),
        )
        self._qpane.original_image = placeholder
        fit_view = policy.zoom_mode == "fit"
        self._swap_delegate.apply_image(
            placeholder,
            source_path=policy.source,
            image_id=None,
            fit_view=fit_view,
        )
        self._apply_placeholder_view_policy(placeholder, policy)
        self.enter_placeholder_mode()
        return True

    def _apply_placeholder_view_policy(
        self, placeholder: QImage, policy: PlaceholderDisplayOptions
    ) -> None:
        """Apply zoom/size rules for placeholder rendering before locking."""
        viewport = self._viewport
        viewport.setContentSize(placeholder.size())
        screen = (
            self._qpane.windowHandle().screen() if self._qpane.windowHandle() else None
        )
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio()) if screen is not None else 1.0
        target_size = self._target_display_size(policy, dpr)
        min_size = self._convert_size(policy.min_display_size, policy.scale_mode, dpr)
        max_size = self._convert_size(policy.max_display_size, policy.scale_mode, dpr)
        if policy.zoom_mode == "locked_zoom":
            zoom = policy.locked_zoom or self._qpane.settings.safe_min_zoom
            zoom = max(zoom, self._qpane.settings.safe_min_zoom)
        elif policy.zoom_mode == "locked_size":
            zoom = self._zoom_for_target_size(
                self._convert_size(policy.locked_size, policy.scale_mode, dpr),
                placeholder,
            )
            if zoom is None:
                zoom = None
        else:
            zoom = self._zoom_for_target_size(target_size, placeholder)
            if zoom is not None and policy.scale_mode == "relative_fit":
                zoom *= policy.scale_factor
        if zoom is None:
            viewport.setZoomFit()
            self._apply_zoom_clamps(viewport, placeholder, min_size, max_size)
            return
        zoom = self._apply_zoom_clamps_value(zoom, placeholder, min_size, max_size)
        viewport.setZoomAndPan(zoom, QPointF(0, 0))
        viewport.zoom_mode = (
            ViewportZoomMode.CUSTOM
            if policy.zoom_mode in {"locked_zoom", "locked_size"}
            else ViewportZoomMode.FIT
        )

    def _zoom_for_target_size(
        self, target_size: QSize | None, placeholder: QImage
    ) -> float | None:
        """Return a zoom factor that fits ``placeholder`` into ``target_size``."""
        if target_size is None or target_size.isEmpty():
            return None
        width = placeholder.width()
        height = placeholder.height()
        if width <= 0 or height <= 0:
            return None
        zoom_w = target_size.width() / width
        zoom_h = target_size.height() / height
        zoom = min(zoom_w, zoom_h)
        return max(zoom, self._qpane.settings.safe_min_zoom)

    def _target_display_size(
        self, policy: PlaceholderDisplayOptions, screen_dpr: float
    ) -> QSize | None:
        """Return the target display size derived from the scale mode."""
        if policy.display_size is None:
            return None
        return self._convert_size(policy.display_size, policy.scale_mode, screen_dpr)

    @staticmethod
    def _convert_size(size: QSize | None, scale_mode: str, screen_dpr: float) -> QSize:
        """Normalize size into logical pixels according to the scale mode."""
        if size is None:
            return QSize()
        if scale_mode == "physical_fit" and screen_dpr > 0:
            return QSize(
                int(round(size.width() / screen_dpr)),
                int(round(size.height() / screen_dpr)),
            )
        return size

    def _apply_zoom_clamps(
        self,
        viewport,
        placeholder: QImage,
        min_size: QSize | None,
        max_size: QSize | None,
    ) -> None:
        """Clamp zoom by applying bounds after a zoom-fit when applicable."""
        if (min_size is None or min_size.isEmpty()) and (
            max_size is None or max_size.isEmpty()
        ):
            return
        zoom = viewport.zoom
        clamped = self._apply_zoom_clamps_value(zoom, placeholder, min_size, max_size)
        if clamped != zoom:
            viewport.setZoomAndPan(clamped, QPointF(0, 0))

    def _apply_zoom_clamps_value(
        self,
        zoom: float,
        placeholder: QImage,
        min_size: QSize | None,
        max_size: QSize | None,
    ) -> float:
        """Return zoom clamped to min/max display sizes when provided."""
        if zoom <= 0:
            return zoom
        width = placeholder.width()
        height = placeholder.height()
        if width <= 0 or height <= 0:
            return zoom
        min_zoom = zoom
        max_zoom = zoom
        if min_size is not None and not min_size.isEmpty():
            min_zoom = max(
                min_zoom,
                min_size.width() / width if width else min_zoom,
                min_size.height() / height if height else min_zoom,
            )
        if max_size is not None and not max_size.isEmpty():
            max_zoom = min(
                max_zoom,
                max_size.width() / width if width else max_zoom,
                max_size.height() / height if height else max_zoom,
            )
        clamped = max(min_zoom, min(max_zoom, zoom))
        return max(clamped, self._qpane.settings.safe_min_zoom)

    def _blank_qpane(self) -> None:
        """Clear the QWidget when no placeholder image is configured."""
        self.exit_placeholder_mode()
        self._qpane.original_image = QImage()
        self._qpane.blank()

    # Placeholder lifecycle helpers
    def placeholder_active(self) -> bool:
        """Return True when the placeholder policy is currently applied."""
        return self._placeholder_active

    def placeholder_policy(self) -> PlaceholderDisplayOptions | None:
        """Return the sanitized placeholder policy from configuration."""
        return self._placeholder_policy

    def placeholder_content(self) -> PlaceholderContent | None:
        """Return renderable placeholder content while placeholder mode is active."""
        if not self._placeholder_active:
            return None
        placeholder = self._placeholder_image
        policy = self._placeholder_policy
        if placeholder is None or placeholder.isNull() or policy is None:
            return None
        return PlaceholderContent(
            image=placeholder,
            source_path=policy.source,
            revision=0,
        )

    def enter_placeholder_mode(self) -> None:
        """Apply interaction locks defined by the placeholder policy."""
        policy = self._placeholder_policy
        if policy is None:
            return
        qpane = self._qpane
        self._placeholder_active = True
        self._placeholder_previous_mode = qpane.interaction.get_control_mode()
        panzoom_enabled = bool(getattr(policy, "panzoom_enabled", False))
        lock_pan_zoom = not panzoom_enabled
        mask_modes = {Tools.CONTROL_MODE_DRAW_BRUSH, Tools.CONTROL_MODE_SMART_SELECT}
        qpane.setPanZoomLocked(lock_pan_zoom)
        if not panzoom_enabled:
            qpane.setControlMode(Tools.CONTROL_MODE_CURSOR)
        elif panzoom_enabled:
            qpane.setControlMode(Tools.CONTROL_MODE_PANZOOM)
        if qpane.getControlMode() in mask_modes:
            qpane.setControlMode(Tools.CONTROL_MODE_PANZOOM)
        qpane.refreshCursor()

    def exit_placeholder_mode(self) -> None:
        """Restore interaction defaults after leaving placeholder mode."""
        if not self._placeholder_active:
            return
        qpane = self._qpane
        previous_mode = self._placeholder_previous_mode
        self._placeholder_active = False
        self._placeholder_previous_mode = None
        qpane.setPanZoomLocked(False)
        if previous_mode:
            try:
                qpane.setControlMode(previous_mode)
            except Exception:
                logger.exception(
                    "Failed to restore control mode after placeholder exit"
                )
        qpane.refreshCursor()

    def _capture_normalized_view_state(self) -> NormalizedViewState | None:
        """Return the normalized view parameters for the active image."""
        if not self.catalog.hasImages():
            return None
        img = self.catalog.getCurrentImage()
        if img is None:
            return None
        w, h = img.width(), img.height()
        zoom = self._viewport.zoom
        if w == 0 or h == 0 or zoom == 0:
            return NormalizedViewState(
                center_x=0.5,
                center_y=0.5,
                zoom_frac=1.0,
                zoom_mode=self._viewport.get_zoom_mode(),
            )
        center_x = 0.5 - (self._viewport.pan.x() / (w * zoom))
        center_y = 0.5 - (self._viewport.pan.y() / (h * zoom))
        context = CoordinateContext(self._qpane)
        panel_w_phys = context.logical_to_physical(self._qpane.width())
        zoom_frac = panel_w_phys / (w * zoom)
        return NormalizedViewState(
            center_x,
            center_y,
            zoom_frac,
            self._viewport.get_zoom_mode(),
        )

    def _apply_normalized_view_state(
        self, state: NormalizedViewState, image_id: uuid.UUID
    ) -> None:
        """Apply a custom view state to the viewport, falling back to zoom-fit.

        Args:
            state: Normalized view state captured from another qpane/image.
            image_id: Identifier whose image dimensions guide translation.
        """
        img = self.catalog.getImage(image_id)
        if img is None:
            return
        w, h = img.width(), img.height()
        context = CoordinateContext(self._qpane)
        panel_w_phys = context.logical_to_physical(self._qpane.width())
        if w == 0 or h == 0 or state.zoom_frac == 0:
            self._viewport.setZoomFit()
            return
        zoom = panel_w_phys / (w * state.zoom_frac)
        pan_x = (0.5 - state.center_x) * (w * zoom)
        pan_y = (0.5 - state.center_y) * (h * zoom)
        self._viewport.zoom_mode = ViewportZoomMode.CUSTOM
        self._viewport.setZoomAndPan(zoom, QPointF(pan_x, pan_y))

    def _evict_catalog_assets(self, asset_keys: Iterable[SceneLayerAssetKey]) -> None:
        """Purge tile caches for the provided catalog source asset keys.

        Args:
            asset_keys: Source identities to drop from tile caches.
        """
        for asset_key in asset_keys:
            self._tile_manager.remove_tiles_for_source_asset(asset_key)

    def _evict_sam_images(self, image_ids: Iterable[uuid.UUID]) -> None:
        """Purge SAM caches for the provided catalog image IDs."""
        sam_manager = self._sam_manager
        if sam_manager is None:
            return
        for image_id in image_ids:
            sam_manager.removeFromCache(image_id)

    def _asset_key_for_image(self, image_id: uuid.UUID) -> SceneLayerAssetKey | None:
        """Return the current default-scene asset key for ``image_id``."""
        getter = getattr(self.catalog, "defaultAssetKeyForImage", None)
        if not callable(getter):
            return None
        return getter(image_id)

    def _remove_images(
        self, image_ids: Iterable[uuid.UUID], *, log_context: str
    ) -> tuple[uuid.UUID, ...]:
        """Remove provided images and coordinate cache/link cleanup."""
        ordered_ids = tuple(dict.fromkeys(image_ids))
        removed: list[uuid.UUID] = []
        ids_to_evict: set[uuid.UUID] = set()
        asset_keys_to_evict: list[SceneLayerAssetKey] = []
        mask_service = self._mask_service
        for image_id in ordered_ids:
            if not self.catalog.containsImage(image_id):
                continue
            removed.append(image_id)
            ids_to_evict.add(image_id)
            asset_key = self._asset_key_for_image(image_id)
            if asset_key is not None:
                asset_keys_to_evict.append(asset_key)
            if mask_service is not None:
                mask_service.invalidateMaskCachesForImage(image_id)
            self.catalog.removeImageByID(image_id)
            self.link_manager.handleImageRemoved(image_id)
        if not removed:
            return tuple()
        if not self.catalog.hasImages():
            logger.info(
                "Catalog empty after %s; clearing images and using placeholder",
                log_context,
            )
            self.clearImages()
        else:
            self._evict_catalog_assets(asset_keys_to_evict)
            self._evict_sam_images(ids_to_evict)
            self._display_current_catalog_image()
        return tuple(removed)

    def _display_current_catalog_image(self, *, fit_view: bool = True) -> None:
        """Render the swap delegate's current image respecting ``fit_view``.

        Args:
            fit_view: Whether the swap delegate should fit the viewport.
        """
        self._swap_delegate.display_current_image(fit_view=fit_view)

    @property
    def _sam_manager(self) -> SamManager | None:
        """Return the SAM manager attached to the qpane, if any."""
        accessor = getattr(self._qpane, "samManager", None)
        return accessor() if callable(accessor) else None

    @property
    def _mask_service(self) -> MaskService | None:
        """Return the mask service attached to the qpane, if any."""
        return getattr(self._qpane, "mask_service", None)
