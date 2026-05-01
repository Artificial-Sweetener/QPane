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

"""In-memory catalog that tracks images, paths, and mask/pyramid state."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ..concurrency import TaskExecutorProtocol
from ..core import Config
from ..rendering import PyramidManager
from ..scene.identity import SceneLayerAssetKey, default_catalog_asset_key
from .image_map import ImageMap
from ..types import CatalogEntry
from .image_utils import images_differ

if TYPE_CHECKING:
    from ..masks.mask import MaskManager
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CatalogMutationResult:
    """Describe internal catalog mutation effects for cache coordination."""

    removed_ids: tuple[uuid.UUID, ...] = ()
    content_changed_ids: tuple[uuid.UUID, ...] = ()
    path_changed_ids: tuple[uuid.UUID, ...] = ()
    cache_asset_keys_to_evict: tuple[SceneLayerAssetKey, ...] = ()


@dataclass(frozen=True, slots=True)
class _CatalogImageRecord:
    """Store catalog image metadata owned by ImageCatalog."""

    image: QImage
    path: Path | None
    revision: int


class ImageCatalog(QObject):
    """Qt-backed data model tracking catalog images, paths, masks, and pyramids."""

    pyramidReady = Signal(object)

    def __init__(
        self,
        config: Config,
        executor: TaskExecutorProtocol,
        parent=None,
        mask_manager: "MaskManager" | None = None,
    ):
        """Initialize the Qt-backed catalog and its managers.

        Args:
            config: Active configuration snapshot for cache/pyramid policies.
            executor: Shared task executor powering pyramid generation.
            parent: Optional QObject parent used for Qt ownership.
            mask_manager: Mask manager wired to catalog lifecycle events.
        """
        super().__init__(parent)
        self._config = config
        self._image_order: List[uuid.UUID] = []
        self._records_by_id: Dict[uuid.UUID, _CatalogImageRecord] = {}
        self._current_id: uuid.UUID | None = None
        self.mask_manager: "MaskManager" | None = mask_manager
        self.pyramid_manager = PyramidManager(
            config=config, parent=self, executor=executor
        )
        self.pyramid_manager.pyramidReady.connect(self.pyramidReady)

    def apply_config(self, config: Config) -> None:
        """Propagate configuration updates to dependent managers.

        Args:
            config: Updated configuration snapshot.
        """
        self._config = config
        self.pyramid_manager.apply_config(config)
        current_id = self._current_id
        if current_id is None:
            return
        record = self._records_by_id.get(current_id)
        if record is not None and not record.image.isNull():
            self.pyramid_manager.generate_pyramid_for_asset(
                self._asset_key_for_record(current_id, record), record.image
            )

    def set_mask_manager(self, mask_manager: "MaskManager" | None) -> None:
        """Assign or replace the mask manager backend.

        Args:
            mask_manager: Manager responsible for responding to catalog events.
        """
        self.mask_manager = mask_manager

    def setImagesByID(
        self,
        image_map: ImageMap,
        current_id: uuid.UUID,
    ) -> CatalogMutationResult:
        """Replace the entire catalog while keeping pyramids and masks in sync.

        Args:
            image_map: Ordered mapping of IDs to ``CatalogEntry`` records.
            current_id: Identifier that should become the current selection.

        Returns:
            Mutation effects needed by cache and feature coordinators.

        Raises:
            ValueError: If ``image_map`` is empty.
            KeyError: If ``current_id`` is not contained within ``image_map``.
            TypeError: If entries are not ``CatalogEntry`` instances with QImage payloads.

        Side effects:
            Clears pyramids for removed/changed paths and notifies the mask manager
            about removed images before regenerating new pyramids.
        """
        if not image_map:
            raise ValueError("image_map must not be empty")
        if current_id not in image_map:
            raise KeyError("current_id must be a key in image_map")
        formatted: Dict[uuid.UUID, QImage] = {}
        for iid, entry in image_map.items():
            if not isinstance(entry, CatalogEntry):
                raise TypeError("image_map values must be CatalogEntry instances")
            if not isinstance(entry.image, QImage):
                raise TypeError("CatalogEntry.image must be a QImage instance")
            formatted[iid] = self._ensureArgb32(entry.image)
        new_ids = set(image_map.keys())
        ids_to_remove = [iid for iid in self._image_order if iid not in new_ids]
        mask_manager = self.mask_manager
        for iid in ids_to_remove:
            if mask_manager:
                mask_manager.handle_image_removal(iid)
        ids_with_changed_content: list[uuid.UUID] = []
        ids_with_changed_paths: list[uuid.UUID] = []
        asset_keys_to_evict: list[SceneLayerAssetKey] = []
        next_records: dict[uuid.UUID, _CatalogImageRecord] = {}
        for iid, entry in image_map.items():
            existing = self._records_by_id.get(iid)
            existing_image = existing.image if existing is not None else None
            content_changed = images_differ(existing_image, formatted[iid])
            if content_changed:
                ids_with_changed_content.append(iid)
            if existing is not None and existing.path != entry.path:
                ids_with_changed_paths.append(iid)
            if existing is not None and (
                content_changed or existing.path != entry.path
            ):
                asset_keys_to_evict.append(self._asset_key_for_record(iid, existing))
            revision = self._next_revision(existing, content_changed)
            next_records[iid] = _CatalogImageRecord(
                image=formatted[iid],
                path=entry.path,
                revision=revision,
            )
        for iid in ids_to_remove:
            existing = self._records_by_id.get(iid)
            if existing is not None:
                asset_keys_to_evict.append(self._asset_key_for_record(iid, existing))
        for asset_key in asset_keys_to_evict:
            self.pyramid_manager.remove_pyramid(asset_key)
        for iid in image_map.keys():
            record = next_records[iid]
            image = record.image
            if image.isNull():
                continue
            self.pyramid_manager.generate_pyramid_for_asset(
                self._asset_key_for_record(iid, record), image
            )
        self._image_order = list(image_map.keys())
        self._records_by_id = next_records
        self._current_id = current_id
        return CatalogMutationResult(
            removed_ids=tuple(ids_to_remove),
            content_changed_ids=tuple(ids_with_changed_content),
            path_changed_ids=tuple(ids_with_changed_paths),
            cache_asset_keys_to_evict=tuple(dict.fromkeys(asset_keys_to_evict)),
        )

    def addImage(
        self,
        image_id: uuid.UUID,
        image: QImage,
        path: Path | None,
    ) -> CatalogMutationResult:
        """Add or replace a single image entry without touching the rest of the catalog.

        Args:
            image_id: Identifier to create or overwrite.
            image: Image data to store; must not be null.
            path: Optional filesystem path that triggers pyramid generation when new.

        Raises:
            ValueError: If ``image`` is null.

        Side effects:
            Removes pyramids for displaced paths, regenerates pyramids for new or
            updated content, and leaves mask state intact unless IDs are removed.
        """
        if image.isNull():
            logger.error("addImage called with null QImage for %s", image_id)
            raise ValueError("image must not be null")
        formatted_image = self._ensureArgb32(image)
        existing = self._records_by_id.get(image_id)
        existing_image = existing.image if existing is not None else None
        content_changed = images_differ(existing_image, formatted_image)
        path_changed = existing is not None and existing.path != path
        if content_changed and existing is not None:
            self.pyramid_manager.remove_pyramid(
                self._asset_key_for_record(image_id, existing)
            )
        elif path_changed:
            self.pyramid_manager.remove_pyramid(
                self._asset_key_for_record(image_id, existing)
            )
        next_record = _CatalogImageRecord(
            image=formatted_image,
            path=path,
            revision=self._next_revision(existing, content_changed),
        )
        self.pyramid_manager.generate_pyramid_for_asset(
            self._asset_key_for_record(image_id, next_record), formatted_image
        )
        if image_id not in self._image_order:
            self._image_order.append(image_id)
        self._records_by_id[image_id] = next_record
        asset_keys_to_evict = (
            (self._asset_key_for_record(image_id, existing),)
            if existing is not None and (content_changed or path_changed)
            else ()
        )
        return CatalogMutationResult(
            content_changed_ids=(image_id,) if content_changed else (),
            path_changed_ids=(image_id,) if path_changed else (),
            cache_asset_keys_to_evict=asset_keys_to_evict,
        )

    def updateCurrentEntry(
        self,
        *,
        image: QImage | None = None,
        path: Path | None = None,
    ) -> CatalogMutationResult:
        """Refresh the stored image/path for the active selection, updating caches.

        Args:
            image: Replacement pixels for the current ID.
            path: Replacement filesystem path for the current ID.

        Side effects:
            Rebuilds pyramids for replaced paths and regenerates the pyramid when
            image content changes.

        Returns:
            Mutation effects needed by cache and feature coordinators.
        """
        current_id = self._current_id
        if current_id is None:
            if self._image_order and (image is not None or path is not None):
                logger.warning(
                    "Ignoring updateCurrentEntry because no current image is selected"
                )
            return CatalogMutationResult()
        existing = self._records_by_id.get(current_id)
        previous_image = existing.image if existing is not None else None
        formatted_image: QImage | None = None
        if image is not None and not image.isNull():
            formatted_image = self._ensureArgb32(image)
        old_path = existing.path if existing is not None else None
        reference_image = formatted_image or previous_image
        content_changed = formatted_image is not None and images_differ(
            previous_image, formatted_image
        )
        path_changed = old_path != path
        if path_changed or content_changed:
            if existing is not None:
                self.pyramid_manager.remove_pyramid(
                    self._asset_key_for_record(current_id, existing)
                )
            if reference_image is not None and not reference_image.isNull():
                next_revision = self._next_revision(existing, content_changed)
                next_record = _CatalogImageRecord(
                    image=reference_image,
                    path=path,
                    revision=next_revision,
                )
                self.pyramid_manager.generate_pyramid_for_asset(
                    self._asset_key_for_record(current_id, next_record),
                    reference_image,
                )
        if existing is not None or reference_image is not None or path is not None:
            image_to_store = (
                reference_image if reference_image is not None else QImage()
            )
            next_revision = self._next_revision(existing, content_changed)
            self._records_by_id[current_id] = _CatalogImageRecord(
                image=image_to_store,
                path=path,
                revision=next_revision,
            )
        cache_asset_keys_to_evict = (
            (self._asset_key_for_record(current_id, existing),)
            if existing is not None and (content_changed or path_changed)
            else ()
        )
        return CatalogMutationResult(
            content_changed_ids=(current_id,) if content_changed else (),
            path_changed_ids=(current_id,) if path_changed else (),
            cache_asset_keys_to_evict=cache_asset_keys_to_evict,
        )

    def removeImageByID(self, image_id: uuid.UUID):
        """Remove the image and its metadata for ``image_id``.

        Args:
            image_id: Identifier to remove.

        Raises:
            KeyError: If ``image_id`` is not known to the catalog.

        Side effects:
            Removes pyramids and mask state for the image and updates the current
            selection when the removed image was active.
        """
        if image_id not in self._records_by_id:
            raise KeyError("image_id not found")
        # Pyramid and mask cleanup
        record = self._records_by_id[image_id]
        self.pyramid_manager.remove_pyramid(
            self._asset_key_for_record(image_id, record)
        )
        if self.mask_manager:
            self.mask_manager.handle_image_removal(image_id)
        # Remove from stores
        self._records_by_id.pop(image_id, None)
        if image_id in self._image_order:
            self._image_order.remove(image_id)
        # Update current selection
        if not self._image_order:
            self._current_id = None
        elif self._current_id == image_id:
            self._current_id = self._image_order[0]

    def clearImages(self):
        """Reset the catalog, pyramids, and masks to an empty state.

        Side effects:
            Clears pyramids, mask state, catalog ordering, and the active selection.
        """
        self.pyramid_manager.clear()
        if self.mask_manager:
            self.mask_manager.clear_all()
        self._image_order = []
        self._records_by_id = {}
        self._current_id = None

    def setCurrentImageID(self, image_id: uuid.UUID | None):
        """Update the current image selection by UUID.

        Args:
            image_id: Identifier that should become current, or None to deselect.

        Raises:
            KeyError: If ``image_id`` is not found and is not None.
        """
        if image_id is not None and image_id not in self._records_by_id:
            raise KeyError("image_id not found")
        self._current_id = image_id

    def getImage(self, image_id: uuid.UUID) -> QImage | None:
        """Return the QImage for a specific image ID."""
        record = self._records_by_id.get(image_id)
        return record.image if record is not None else None

    def getPath(self, image_id: uuid.UUID) -> Path | None:
        """Return the filesystem Path for a specific image ID."""
        record = self._records_by_id.get(image_id)
        return record.path if record is not None else None

    def getRevision(self, image_id: uuid.UUID) -> int | None:
        """Return the catalog content revision for ``image_id`` when known."""
        record = self._records_by_id.get(image_id)
        return record.revision if record is not None else None

    def defaultAssetKeyForImage(self, image_id: uuid.UUID) -> SceneLayerAssetKey | None:
        """Return the default-scene cache key for a catalog image."""
        record = self._records_by_id.get(image_id)
        if record is None:
            return None
        return self._asset_key_for_record(image_id, record)

    def getCurrentImage(self) -> QImage | None:
        """Return the QImage for the currently selected image, if any."""
        return self.getImage(self._current_id) if self._current_id else None

    def getCurrentPath(self) -> Path | None:
        """Return the filesystem Path for the current image, if any."""
        return self.getPath(self._current_id) if self._current_id else None

    def getCurrentId(self) -> uuid.UUID | None:
        """Return the UUID for the currently selected image, if any."""
        return self._current_id

    def getCurrentRevision(self) -> int | None:
        """Return the content revision for the current catalog image."""
        return self.getRevision(self._current_id) if self._current_id else None

    def get_mask_manager(self) -> "MaskManager" | None:
        """Expose the mask manager currently associated with this catalog."""
        return self.mask_manager

    def containsImage(self, image_id: uuid.UUID) -> bool:
        """Return True when the catalog stores an image for ``image_id``."""
        return image_id in self._records_by_id

    def getImageIds(self) -> List[uuid.UUID]:
        """Return a copy of the catalog's UUID ordering."""
        return list(self._image_order)

    def hasImages(self) -> bool:
        """Return ``True`` when at least one image is stored."""
        return bool(self._image_order)

    def getAllImages(self) -> List[QImage]:
        """Return each stored QImage preserving insertion order."""
        return [
            self._records_by_id[iid].image
            for iid in self._image_order
            if iid in self._records_by_id
        ]

    def getAllPaths(self) -> List[Path | None]:
        """Return filesystem paths aligned with :meth:`getAllImages`."""
        return [self.getPath(iid) for iid in self._image_order]

    def getBestFitImageForAsset(
        self, asset_key: SceneLayerAssetKey | None, target_width: float
    ) -> QImage | None:
        """Retrieve the best-fit pyramid image for the requested width.

        Args:
            asset_key: Scene layer asset key that identifies the pyramid.
            target_width: Desired width in device pixels.

        Returns:
            Approximated image level or ``None`` when unavailable.
        """
        if asset_key is None:
            return None
        return self.pyramid_manager.get_best_fit_image_for_asset(
            asset_key, target_width
        )

    @staticmethod
    def _next_revision(
        existing: _CatalogImageRecord | None,
        content_changed: bool,
    ) -> int:
        """Return the next monotonic content revision for a catalog record."""
        current_revision = existing.revision if existing is not None else 0
        return current_revision + 1 if content_changed else current_revision

    @staticmethod
    def _asset_key_for_record(
        image_id: uuid.UUID, record: _CatalogImageRecord
    ) -> SceneLayerAssetKey:
        """Return the default-scene cache identity for a catalog record."""
        return default_catalog_asset_key(
            image_id,
            revision=record.revision,
            source_path=record.path,
        )

    def _ensureArgb32(self, image: QImage) -> QImage:
        """Return a QImage in ARGB32_Premultiplied format; gracefully handle null images."""
        if image.isNull() or image.format() == QImage.Format_ARGB32_Premultiplied:
            return image
        return image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
