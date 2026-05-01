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

"""Unit tests for CatalogController internal helpers."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QImage

from qpane.catalog.controller import CatalogController
from qpane.rendering import NormalizedViewState, ViewportZoomMode
from qpane.scene.identity import SceneLayerAssetKey, default_catalog_asset_key


class _ViewportStub:
    """Minimal viewport stub exposing pan/zoom operations."""

    def __init__(self) -> None:
        self.zoom = 0.0
        self.pan = QPointF(0, 0)
        self.zoom_mode = ViewportZoomMode.FIT
        self.zoom_fit_calls = 0
        self.zoom_pan_calls: list[tuple[float, QPointF]] = []

    def get_zoom_mode(self):
        return self.zoom_mode

    def setZoomFit(self) -> None:
        self.zoom_fit_calls += 1

    def setZoomAndPan(self, zoom: float, pan: QPointF) -> None:
        self.zoom = zoom
        self.pan = pan
        self.zoom_pan_calls.append((zoom, pan))

    def setContentSize(self, _size: QSize) -> None:
        return None


class _CatalogStub:
    """Catalog stub supporting the controller helper paths."""

    def __init__(self, image: QImage | None, image_id: uuid.UUID | None) -> None:
        self._image = image
        self._image_id = image_id
        self._paths: dict[uuid.UUID, str] = {}
        self._image_ids: list[uuid.UUID] = []

    def hasImages(self) -> bool:
        return self._image_id is not None or bool(self._image_ids)

    def getCurrentImage(self) -> QImage | None:
        return self._image

    def getCurrentId(self) -> uuid.UUID | None:
        return self._image_id

    def getImageIds(self):
        return tuple(self._image_ids)

    def getImage(self, image_id: uuid.UUID) -> QImage | None:
        return self._image if image_id == self._image_id else None

    def containsImage(self, image_id: uuid.UUID) -> bool:
        return image_id in self._paths

    def getPath(self, image_id: uuid.UUID):
        return self._paths.get(image_id)

    def defaultAssetKeyForImage(self, image_id: uuid.UUID) -> SceneLayerAssetKey | None:
        path = self.getPath(image_id)
        return default_catalog_asset_key(
            image_id,
            revision=0,
            source_path=Path(path) if path is not None else None,
        )

    def removeImageByID(self, image_id: uuid.UUID) -> None:
        self._paths.pop(image_id, None)
        if image_id in self._image_ids:
            self._image_ids.remove(image_id)
        if self._image_id == image_id:
            self._image_id = None
            self._image = None

    def getAllPaths(self):
        return set(self._paths.values())

    def clearImages(self) -> None:
        self._paths.clear()
        self._image_id = None
        self._image = None
        self._image_ids = []


class _TileManagerStub:
    """Track cache removals triggered by catalog mutations."""

    def __init__(self) -> None:
        self.removed: list[SceneLayerAssetKey] = []
        self.cleared = False

    def remove_tiles_for_asset(self, asset_key: SceneLayerAssetKey) -> None:
        self.removed.append(asset_key)

    def remove_tiles_for_source_asset(self, asset_key: SceneLayerAssetKey) -> None:
        self.removed.append(asset_key)

    def clear_caches(self) -> None:
        self.cleared = True


class _LinkManagerStub:
    """Track link manager mutations."""

    def __init__(self) -> None:
        self.removed: list[uuid.UUID] = []
        self.cleared = False
        self.groups: tuple[object, ...] = tuple()
        self.records: list[object] = []

    def handleImageRemoved(self, image_id: uuid.UUID) -> None:
        self.removed.append(image_id)

    def clear(self) -> None:
        self.cleared = True

    def setGroups(self, groups) -> None:
        self.groups = tuple(groups)

    def getGroupRecords(self):
        return list(self.records)


class _SwapDelegateStub:
    """Stub swap delegate that records display requests."""

    def __init__(self) -> None:
        self.display_calls = 0
        self.set_current_calls: list[uuid.UUID | None] = []

    def display_current_image(self, *, fit_view: bool = True) -> None:
        self.display_calls += 1

    def reset(self) -> None:
        return None

    def apply_image(self, *_args, **_kwargs) -> None:
        return None

    def set_current_image(self, image_id: uuid.UUID | None) -> None:
        self.set_current_calls.append(image_id)


def _make_controller(
    *,
    image: QImage | None,
    image_id: uuid.UUID | None,
    mask_service: object | None = None,
    sam_manager: object | None = None,
) -> tuple[CatalogController, _CatalogStub, _TileManagerStub, _LinkManagerStub]:
    viewport = _ViewportStub()
    catalog = _CatalogStub(image=image, image_id=image_id)
    tile_manager = _TileManagerStub()
    link_manager = _LinkManagerStub()
    swap_delegate = _SwapDelegateStub()
    qpane = SimpleNamespace(
        settings=SimpleNamespace(safe_min_zoom=0.1),
        original_image=image or QImage(),
        devicePixelRatioF=lambda: 1.0,
        width=lambda: 100,
        height=lambda: 100,
        size=lambda: QSize(100, 100),
        view=lambda: SimpleNamespace(viewport=viewport),
        blank=lambda: None,
        interaction=SimpleNamespace(get_control_mode=lambda: None),
        setPanZoomLocked=lambda _locked: None,
        setControlMode=lambda _mode: None,
        refreshCursor=lambda: None,
        windowHandle=lambda: None,
        samManager=lambda: sam_manager,
        mask_service=mask_service,
    )
    controller = CatalogController(
        qpane=qpane,
        catalog=catalog,
        viewport=viewport,
        tile_manager=tile_manager,
        link_manager=link_manager,
        swap_delegate=swap_delegate,
    )
    return controller, catalog, tile_manager, link_manager


def test_placeholder_sanitizers_fallback_to_safe_defaults() -> None:
    """Placeholder sanitizers should clamp invalid values."""
    controller, _, _, _ = _make_controller(image=None, image_id=None)
    assert controller._sanitize_zoom_mode("nope") == "fit"
    assert controller._sanitize_locked_zoom(-1) is None
    assert controller._sanitize_locked_size(("bad",)) is None
    assert controller._sanitize_scale_factor(0) == 1.0


def test_capture_normalized_view_state_defaults_on_zero_values() -> None:
    """Zero-sized images or zero zoom should fall back to center defaults."""
    image = QImage(0, 0, QImage.Format_ARGB32)
    image_id = uuid.uuid4()
    controller, _, _, _ = _make_controller(image=image, image_id=image_id)
    controller._viewport.zoom = 0.0
    state = controller._capture_normalized_view_state()
    assert state is not None
    assert state.center_x == 0.5
    assert state.center_y == 0.5
    assert state.zoom_frac == 1.0


def test_apply_normalized_view_state_uses_fit_for_zero_zoom_frac() -> None:
    """Zero zoom fractions should force a fit zoom."""
    image = QImage(10, 10, QImage.Format_ARGB32)
    image_id = uuid.uuid4()
    controller, catalog, _, _ = _make_controller(image=image, image_id=image_id)
    state = NormalizedViewState(
        center_x=0.5,
        center_y=0.5,
        zoom_frac=0.0,
        zoom_mode=ViewportZoomMode.CUSTOM,
    )
    controller._apply_normalized_view_state(state, image_id)
    assert controller._viewport.zoom_fit_calls == 1


def test_remove_images_dedupes_and_clears_on_empty() -> None:
    """Removing duplicates should evict once and clear when catalog empties."""
    image_id = uuid.uuid4()
    controller, catalog, tile_manager, link_manager = _make_controller(
        image=QImage(5, 5, QImage.Format_ARGB32),
        image_id=image_id,
    )
    catalog._paths[image_id] = "path.png"
    removed = controller.removeImagesByID((image_id, image_id))
    assert removed == (image_id,)
    assert link_manager.removed == [image_id]
    assert tile_manager.cleared is True
    assert link_manager.cleared is True


def test_set_all_images_linked_reuses_existing_group() -> None:
    """setAllImagesLinked should reuse the matching group id when present."""
    image_id = uuid.uuid4()
    controller, catalog, _, link_manager = _make_controller(
        image=QImage(5, 5, QImage.Format_ARGB32),
        image_id=image_id,
    )
    ids = (uuid.uuid4(), uuid.uuid4())
    catalog._image_ids = list(ids)
    existing_group = SimpleNamespace(group_id=uuid.uuid4(), members=ids)
    link_manager.records = [existing_group]
    controller.setAllImagesLinked(True)
    assert len(link_manager.groups) == 1
    assert link_manager.groups[0].group_id == existing_group.group_id


def test_set_all_images_linked_clears_for_single_image() -> None:
    """setAllImagesLinked should clear groups when fewer than two images exist."""
    image_id = uuid.uuid4()
    controller, catalog, _, link_manager = _make_controller(
        image=QImage(5, 5, QImage.Format_ARGB32),
        image_id=image_id,
    )
    catalog._image_ids = [uuid.uuid4()]
    controller.setAllImagesLinked(True)
    assert link_manager.groups == tuple()


def test_set_current_image_delegates_to_swap_delegate() -> None:
    """setCurrentImageID should forward to the swap delegate."""
    controller, _, _, _ = _make_controller(image=None, image_id=None)
    controller.setCurrentImageID(None)
    controller.setCurrentImageID(uuid.uuid4())
    swap_delegate = controller._swap_delegate
    assert swap_delegate.set_current_calls[0] is None
    assert isinstance(swap_delegate.set_current_calls[1], uuid.UUID)


def test_remove_images_evicts_mask_and_sam_caches() -> None:
    """Removing images should invalidate mask caches and SAM entries."""
    image_id = uuid.uuid4()
    other_id = uuid.uuid4()
    mask_calls: list[uuid.UUID] = []
    sam_calls: list[uuid.UUID] = []
    mask_service = SimpleNamespace(
        invalidateMaskCachesForImage=lambda mid: mask_calls.append(mid)
    )
    sam_manager = SimpleNamespace(
        removeFromCache=lambda image_id: sam_calls.append(image_id)
    )
    controller, catalog, tile_manager, link_manager = _make_controller(
        image=QImage(5, 5, QImage.Format_ARGB32),
        image_id=image_id,
        mask_service=mask_service,
        sam_manager=sam_manager,
    )
    catalog._paths[image_id] = "first.png"
    catalog._paths[other_id] = "second.png"
    catalog._image_ids = [image_id, other_id]
    removed = controller.removeImagesByID((image_id,))
    assert removed == (image_id,)
    assert mask_calls == [image_id]
    assert [key.source_id for key in tile_manager.removed] == [image_id]
    assert sam_calls == [image_id]
    assert link_manager.removed == [image_id]
    assert controller._swap_delegate.display_calls == 1
