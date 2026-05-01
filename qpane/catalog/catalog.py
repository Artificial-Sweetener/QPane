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

"""Expose the host-facing catalog facade plus link and navigation helpers."""

from __future__ import annotations

import logging
import uuid
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence, TYPE_CHECKING

from PySide6.QtGui import QImage

from ..core.config import Config, PlaceholderSettings
from .controller import CatalogController, PlaceholderDisplayOptions
from .image_catalog import ImageCatalog
from .image_map import ImageMap, image_map_from_lists
from .link import LinkManager
from ..types import LinkedGroup

if TYPE_CHECKING:  # pragma: no cover - typed during development only
    from ..qpane import QPane
    from ..swap import SwapDelegate
    from ..masks.mask import MaskManager
logger = logging.getLogger(__name__)

CatalogMutationCallback = Callable[["CatalogMutationEvent"], None]
NavigationCallback = Callable[["NavigationEvent"], None]


@dataclass(frozen=True)
class CatalogMutationEvent:
    """Describe a catalog mutation that occurred through the facade."""

    reason: str
    affected_ids: tuple[uuid.UUID, ...]
    current_id: uuid.UUID | None


@dataclass(frozen=True)
class NavigationEvent:
    """Describe a navigation request routed through the facade."""

    reason: str
    target_id: uuid.UUID
    fit_view: bool | None


class Catalog:
    """Expose catalog mutation, metadata, and navigation helpers for hosts.

    The facade sequences catalog writes, notifies observers, and relays
    navigation into the swap delegate so QPane stays a thin QWidget wrapper.
    QPane instantiates this facade and forwards catalog-related APIs through it.
    """

    @staticmethod
    def imageMapFromLists(
        images: Iterable[QImage],
        paths: Iterable[Path | None] | None = None,
        ids: Iterable[uuid.UUID] | None = None,
    ) -> ImageMap:
        """Build an ImageMap from aligned iterables using the shared helper.

        Args:
            images: Ordered sequence of images to store.
            paths: Optional sequence of filesystem paths aligned with ``images``.
            ids: Optional sequence of UUIDs aligned with ``images``.

        Returns:
            Mapping suitable for :meth:`Catalog.setImagesByID`, with ``CatalogEntry`` values.

        Raises:
            ValueError: If ``paths`` or ``ids`` lengths do not match ``images``.
            TypeError: If images are not ``QImage`` instances.
        """
        return image_map_from_lists(images, paths=paths, ids=ids)

    def __init__(
        self,
        *,
        catalog: ImageCatalog,
        controller: CatalogController,
        link_manager: LinkManager,
        swap_delegate: "SwapDelegate",
        qpane: "QPane" | None = None,
    ) -> None:
        """Initialize the facade with its backing collaborators.

        Args:
            catalog: In-memory catalog that owns paths, images, and mask hooks.
            controller: Coordinates cache eviction, rendering, and linked views.
            link_manager: Persists linked-view groupings and stored view states.
            swap_delegate: Loads, displays, and persists swap-related view state.
            qpane: Optional QPane so callbacks can safely reach UI-owned state.
        """
        self._catalog = catalog
        self._controller = controller
        self._link_manager = link_manager
        self._swap_delegate = swap_delegate
        self._qpane_ref: weakref.ReferenceType["QPane"] | None = (
            weakref.ref(qpane) if qpane is not None else None
        )
        self._navigation_callbacks: list[NavigationCallback] = []
        self._mutation_listener: CatalogMutationCallback | None = None

    def linkManager(self) -> LinkManager:
        """Return the underlying LinkManager instance."""
        return self._link_manager

    def maskManager(self) -> "MaskManager" | None:
        """Expose the catalog's mask manager when available."""
        return self._catalog.get_mask_manager()

    def pyramidManager(self):
        """Expose the pyramid manager used for diagnostics and prefetching."""
        return self._catalog.pyramid_manager

    def imageCatalog(self) -> ImageCatalog:
        """Return the underlying ImageCatalog for low-level integrations."""
        return self._catalog

    def setMaskManager(self, manager: "MaskManager" | None) -> None:
        """Install or clear the catalog's mask manager instance."""
        self._catalog.set_mask_manager(manager)

    def qpane(self) -> "QPane" | None:
        """Return the weakly referenced QPane when still alive."""
        if self._qpane_ref is None:
            return None
        return self._qpane_ref()

    # Catalog mutation hooks
    def setMutationListener(
        self, callback: CatalogMutationCallback | None
    ) -> CatalogMutationCallback | None:
        """Assign a single mutation listener for internal coordination."""
        self._mutation_listener = callback
        return callback

    def onNavigationStarted(self, callback: NavigationCallback) -> NavigationCallback:
        """Register ``callback`` to be invoked before navigation is dispatched."""
        self._navigation_callbacks.append(callback)
        return callback

    def removeNavigationHook(self, callback: NavigationCallback) -> None:
        """Remove a previously registered navigation hook."""
        self._navigation_callbacks = [
            cb for cb in self._navigation_callbacks if cb != callback
        ]

    # Public catalog helpers (mutation + metadata)
    def setImagesByID(self, image_map: ImageMap, current_id: uuid.UUID) -> None:
        """Replace the catalog contents and navigate to ``current_id``.

        Args:
            image_map: Ordered mapping of IDs to ``CatalogEntry`` records.
            current_id: Identifier to display after the catalog is replaced.

        Side effects:
            Suspends and resumes observers through mutation/navigation callbacks
            and instructs the swap delegate to render ``current_id``.

        Raises:
            ValueError: If ``image_map`` is empty.
            KeyError: If ``current_id`` is not contained in ``image_map``.
            TypeError: If entries are not ``CatalogEntry`` instances with QImage payloads.
        """
        self._controller.setImagesByID(image_map, current_id, display=False)
        self._emit_catalog_mutation("setImagesByID", affected_ids=image_map.keys())
        self._navigate_to("setImagesByID", current_id, fit_view=True)

    def addImage(self, image_id: uuid.UUID, image: QImage, path: Path | None) -> None:
        """Add or replace a catalog entry without altering the selection.

        Args:
            image_id: Identifier to create or overwrite.
            image: Image data to store.
            path: Optional filesystem path associated with ``image``.

        Raises:
            ValueError: If ``image`` is null.
        """
        self._controller.addImage(image_id, image, path)
        self._emit_catalog_mutation("addImage", affected_ids=(image_id,))

    def removeImageByID(self, image_id: uuid.UUID) -> None:
        """Remove ``image_id`` when present and leave navigation to the host.

        Args:
            image_id: Identifier that should be removed from the catalog.

        Side effects:
            Evicts controller caches and emits a mutation event when removal
            succeeds. The current selection is left unchanged so hosts can pick
            their preferred follow-up navigation.
        """
        if not self._catalog.containsImage(image_id):
            logger.warning("Attempted to remove unknown image_id: %s", image_id)
            return
        self._controller.removeImageByID(image_id)
        self._emit_catalog_mutation("removeImageByID", affected_ids=(image_id,))

    def removeImagesByID(self, image_ids: Iterable[uuid.UUID]) -> None:
        """Remove multiple images without duplicating work for repeated IDs.

        Args:
            image_ids: Iterable containing the identifiers to remove.

        Side effects:
            Emits a catalog mutation event listing the surviving removed IDs and
            leaves navigation decisions to the host.
        """
        deduped_ids = tuple(dict.fromkeys(image_ids))
        if not deduped_ids:
            logger.info("removeImagesByID called with no ids; skipping mutation")
            return
        removed_ids = self._controller.removeImagesByID(deduped_ids)
        if not removed_ids:
            logger.warning("Attempted to remove unknown image_ids: %s", deduped_ids)
            return
        self._emit_catalog_mutation(
            "removeImagesByID",
            affected_ids=removed_ids,
        )

    def clearImages(self) -> None:
        """Reset the catalog, linked views, and caches before showing a placeholder."""
        previous_ids = tuple(self.imageIDs())
        self._controller.clearImages()
        self._emit_catalog_mutation("clearImages", affected_ids=previous_ids)

    def applyConfig(self, config: "Config") -> None:
        """Propagate configuration updates to the underlying catalog.

        Args:
            config: Updated configuration snapshot to apply.
        """
        placeholder_settings = getattr(config, "placeholder", PlaceholderSettings())
        if placeholder_settings is None:
            placeholder_settings = PlaceholderSettings()
        self._controller.apply_placeholder_config(placeholder_settings)
        self._catalog.apply_config(config)

    def placeholderActive(self) -> bool:
        """Return True when the placeholder policy is applied."""
        return self._controller.placeholder_active()

    def placeholderPolicy(self) -> PlaceholderDisplayOptions | None:
        """Expose the sanitized placeholder policy from configuration."""
        return self._controller.placeholder_policy()

    def enterPlaceholderMode(self) -> None:
        """Apply the placeholder policy to the qpane."""
        self._controller.enter_placeholder_mode()

    def exitPlaceholderMode(self) -> None:
        """Restore interaction defaults after leaving placeholder mode."""
        self._controller.exit_placeholder_mode()

    def setCurrentImageID(
        self, image_id: uuid.UUID | None, *, fit_view: bool | None = None
    ) -> None:
        """Navigate to ``image_id`` via the swap delegate.

        Args:
            image_id: Identifier that should become current, or None to deselect.
            fit_view: Optional override for the swap delegate's fit-view logic.
        """
        if image_id is None:
            self._controller.deselectImage()
            return
        if not self._catalog.containsImage(image_id):
            logger.warning("Attempted to select unknown image_id: %s", image_id)
            return
        self._navigate_to("setCurrentImageID", image_id, fit_view=fit_view)

    def linkedGroups(self) -> tuple[LinkedGroup, ...]:
        """Expose the current linked-view groups paired with their identifiers."""
        return self._link_manager.getGroupRecords()

    def setLinkedGroups(self, groups: Iterable[LinkedGroup]) -> None:
        """Persist linked-view groups using the controller and link manager."""
        self._controller.setLinkedGroups(groups)
        self._emit_catalog_mutation("setLinkedGroups", affected_ids=tuple())

    def setAllImagesLinked(self, enabled: bool) -> None:
        """Toggle linked-view synchronization across the entire catalog."""
        self._controller.setAllImagesLinked(enabled)
        self._emit_catalog_mutation("setAllImagesLinked", affected_ids=tuple())

    def saveZoomPanForCurrentImage(self) -> None:
        """Persist the current normalized view state through the controller."""
        self._controller.saveZoomPanForCurrentImage()

    def restoreZoomPanForNewImage(self, image_id: uuid.UUID) -> None:
        """Restore the saved view state for ``image_id`` via the controller.

        Args:
            image_id: Identifier whose stored view state should be applied.
        """
        self._controller.restoreZoomPanForNewImage(image_id)

    def handleDragRequest(self, event) -> None:
        """Forward drag requests through the controller helper.

        Args:
            event: QDrag-aware event from the host toolkit.
        """
        self._controller.handleDragRequest(event)

    def copyCurrentImageToClipboard(self) -> bool:
        """Copy the currently displayed image to the system clipboard."""
        return self._controller.copyCurrentImageToClipboard()

    def displayCurrentCatalogImage(self, *, fit_view: bool = True) -> None:
        """Render the catalog's current image or blank the qpane when absent.

        Args:
            fit_view: Forwarded to the controller to control viewport fitting.
        """
        self._controller.displayCurrentCatalogImage(fit_view=fit_view)

    def currentImage(self) -> QImage | None:
        """Return the currently selected image, or None when absent."""
        return self._catalog.getCurrentImage()

    def currentImagePath(self) -> Path | None:
        """Return the filesystem path for the current image when known."""
        path = self._catalog.getCurrentPath()
        if path is not None:
            return path
        if self._controller.placeholder_active():
            policy = self._controller.placeholder_policy()
            if policy is not None:
                return policy.source
        return None

    def imagePath(self, image_id: uuid.UUID | None) -> Path | None:
        """Return the filesystem path for ``image_id`` when known."""
        if image_id is None:
            return None
        return self._catalog.getPath(image_id)

    def currentImageID(self) -> uuid.UUID | None:
        """Return the UUID of the currently selected image."""
        return self._catalog.getCurrentId()

    def allImages(self) -> list[QImage]:
        """Return a copy of every stored QImage preserving catalog order."""
        return self._catalog.getAllImages()

    def allImagePaths(self) -> list[Path | None]:
        """Return filesystem paths for every catalog entry in order."""
        return self._catalog.getAllPaths()

    def imageIDs(self) -> list[uuid.UUID]:
        """Return the ordered list of image UUIDs managed by the catalog."""
        return self._catalog.getImageIds()

    def imageCount(self) -> int:
        """Return the number of images currently managed by the catalog."""
        return len(self.imageIDs())

    def hasImages(self) -> bool:
        """Return True when the catalog contains at least one image."""
        return self._catalog.hasImages()

    def linkedViewGroupID(self, image_id: uuid.UUID) -> uuid.UUID | None:
        """Return the linked-view group identifier for ``image_id`` when present."""
        return self._link_manager.getGroupIdForImage(image_id)

    # Internal helpers
    def _emit_catalog_mutation(
        self,
        reason: str,
        *,
        affected_ids: Iterable[uuid.UUID] | Sequence[uuid.UUID],
    ) -> None:
        """Notify registered callbacks about catalog mutations.

        Args:
            reason: Human-readable explanation suitable for observers.
            affected_ids: Sequence of IDs whose metadata or existence changed.
        """
        current_id = self._catalog.getCurrentId()
        event = CatalogMutationEvent(
            reason=reason,
            affected_ids=tuple(affected_ids),
            current_id=current_id,
        )
        callback = self._mutation_listener
        if callback is None:
            return
        try:
            callback(event)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Catalog mutation hook failed (reason=%s)", reason)

    def _emit_navigation_event(self, event: NavigationEvent) -> None:
        """Invoke registered navigation hooks with defensive logging.

        Args:
            event: Navigation payload describing the target and reason.
        """
        for callback in list(self._navigation_callbacks):
            try:
                callback(event)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Navigation hook failed (reason=%s)", event.reason)

    def _navigate_to(
        self, reason: str, image_id: uuid.UUID, *, fit_view: bool | None
    ) -> None:
        """Emit a NavigationEvent and instruct the swap delegate to display it.

        Args:
            reason: Mutation or request responsible for the navigation.
            image_id: Identifier to display.
            fit_view: Optional fit-view override propagated to the delegate.
        """
        event = NavigationEvent(reason=reason, target_id=image_id, fit_view=fit_view)
        self._emit_navigation_event(event)
        save_view = reason != "setImagesByID"
        self._swap_delegate.set_current_image(
            image_id, fit_view=fit_view, save_view=save_view
        )
