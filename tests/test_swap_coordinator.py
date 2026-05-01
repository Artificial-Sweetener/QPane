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

"""Unit tests for the SwapCoordinator orchestration logic."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from qpane.catalog.image_catalog import CatalogMutationResult
from qpane.core import CacheSettings
from qpane.core.config_features import SamConfigSlice
from qpane.masks.workflow import MaskActivationSyncResult
from qpane.rendering import ViewportZoomMode
from qpane.scene.identity import (
    SceneLayerAssetKey,
    SceneLayerTileKey,
    default_catalog_asset_key,
)
from qpane.swap.coordinator import SwapCoordinator


class RecordingMaskService:
    """Minimal mask service stub that records prefetch and cancel requests."""

    def __init__(self) -> None:
        self.controller = object()
        self.prefetch_calls: list[tuple[uuid.UUID, str]] = []
        self.cancel_calls: list[uuid.UUID | None] = []
        self.manager = type(
            "MaskManagerStub",
            (),
            {"get_mask_ids_for_image": staticmethod(lambda _image_id: [])},
        )()

    def connectUndoStackChanged(self, _callback) -> None:  # pragma: no cover - no-op
        return

    def disconnectUndoStackChanged(self, _callback) -> None:  # pragma: no cover - no-op
        return

    def refreshAutosavePolicy(self) -> None:  # pragma: no cover - no-op
        return

    def applyConfig(self, *_args, **_kwargs) -> None:  # pragma: no cover - no-op
        return

    def configureStrokeDiagnostics(
        self, *_args, **_kwargs
    ) -> None:  # pragma: no cover - no-op
        return

    def ensureTopMaskActiveForImage(self, _image_id: uuid.UUID) -> bool:
        return True

    def isActivationPending(self, _image_id: uuid.UUID | None) -> bool:
        return False

    def prefetchColorizedMasks(
        self,
        image_id: uuid.UUID | None,
        *,
        reason: str,
        scales=None,
    ) -> bool:
        if image_id is None:
            return False
        self.prefetch_calls.append((image_id, reason))
        return True

    def cancelPrefetch(self, image_id: uuid.UUID | None) -> bool:
        self.cancel_calls.append(image_id)
        return image_id is not None


class RecordingSamManager:
    """Stub SAM manager that remembers predictor lifecycle events."""

    def __init__(self) -> None:
        self.requested: list[uuid.UUID] = []
        self.cancelled: list[uuid.UUID] = []
        self.removed: list[uuid.UUID] = []

    def requestPredictor(
        self, _image: QImage, image_id: uuid.UUID, *, source_path: Path | None
    ) -> None:
        self.requested.append(image_id)

    def cancelPendingPredictor(self, image_id: uuid.UUID) -> bool:
        self.cancelled.append(image_id)
        return True

    def removeFromCache(self, image_id: uuid.UUID) -> None:
        self.removed.append(image_id)


class _SamAwareConfig:
    """Minimal settings stub exposing cache and SAM slices."""

    def __init__(self, *, predictor_depth: int, sam_depth: int | None) -> None:
        self.cache = CacheSettings()
        self.cache.prefetch.predictors = predictor_depth
        sam_slice = SamConfigSlice()
        sam_slice.sam_prefetch_depth = sam_depth
        self._sam_slice = sam_slice

    def for_feature(self, namespace: str) -> SamConfigSlice:
        if namespace != "sam":
            raise RuntimeError("unexpected namespace request")
        return self._sam_slice


def _solid_image(color: Qt.GlobalColor) -> QImage:
    image = QImage(64, 64, QImage.Format_ARGB32)
    image.fill(color)
    return image


class DummySignal:
    """Minimal Qt-like signal stub."""

    def __init__(self) -> None:
        self._callbacks: list = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs) -> None:
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


class StubTileManager:
    """Tile manager stub exposing the methods SwapCoordinator expects."""

    def __init__(self) -> None:
        self.tileReady = DummySignal()
        self.prefetch_calls: list[tuple[tuple[SceneLayerTileKey, ...], str]] = []
        self.cancel_calls: list[tuple[tuple[SceneLayerTileKey, ...], str]] = []
        self.removed_assets: list[SceneLayerAssetKey] = []
        self.cache_limit_bytes = 0
        self.cache_usage_bytes = 0

    def prefetch_tiles(self, identifiers, source_image, *, reason="prefetch"):
        batch = tuple(identifiers)
        self.prefetch_calls.append((batch, reason))
        return identifiers

    def cancel_prefetch(self, identifiers, *, reason: str) -> None:
        batch = tuple(identifiers)
        self.cancel_calls.append((batch, reason))

    def remove_tiles_for_asset(self, asset_key: SceneLayerAssetKey) -> None:
        self.removed_assets.append(asset_key)

    def remove_tiles_for_source_asset(self, asset_key: SceneLayerAssetKey) -> None:
        self.removed_assets.append(asset_key)

    def calculate_grid_dimensions(self, width: int, height: int) -> tuple[int, int]:
        cols = max(1, width // 64)
        rows = max(1, height // 64)
        return cols, rows


class StubPyramidManager:
    """Pyramid manager stub capturing prefetch and cancel requests."""

    def __init__(self) -> None:
        self.pyramidReady = DummySignal()
        self.prefetch_calls: list[tuple[SceneLayerAssetKey, str]] = []
        self.prefetch_payloads: list[tuple[SceneLayerAssetKey, str]] = []
        self.cancel_calls: list[tuple[tuple[SceneLayerAssetKey, ...], str]] = []
        self.cache_limit_bytes = 0
        self.cache_usage_bytes = 0

    def prefetch_pyramid(
        self,
        asset_key: SceneLayerAssetKey,
        image: QImage,
        reason: str = "prefetch",
    ) -> bool:
        self.prefetch_calls.append((asset_key, reason))
        self.prefetch_payloads.append((asset_key, reason))
        return True

    def cancel_prefetch(self, asset_keys, *, reason: str) -> None:
        batch = tuple(asset_keys)
        self.cancel_calls.append((batch, reason))


class StubViewport:
    """Viewport stub tracking zoom mode changes."""

    def __init__(self) -> None:
        self.zoom = 1.0
        self._zoom_mode = ViewportZoomMode.FIT
        self.content_size = None

    def setContentSize(self, size) -> None:
        self.content_size = size

    def setZoomFit(self) -> None:
        self._zoom_mode = ViewportZoomMode.FIT

    def get_zoom_mode(self) -> ViewportZoomMode:
        return self._zoom_mode


class StubView:
    """QPane view stub providing tile manager and viewport accessors."""

    def __init__(self, viewport: StubViewport, tile_manager: StubTileManager) -> None:
        self.viewport = viewport
        self.tile_manager = tile_manager
        self.swap_delegate = SimpleNamespace()
        self.buffers_allocated = False
        self.aligned = False

    def allocate_buffers(self) -> None:
        self.buffers_allocated = True

    def ensure_view_alignment(self, *, force: bool = False) -> None:
        self.aligned = bool(force)


class StubCatalogFacade:
    """Facade mimicking QPane.catalog() semantics."""

    def __init__(self, catalog: "StubCatalog") -> None:
        self._catalog = catalog

    def exitPlaceholderMode(self) -> None:
        return

    def imageCatalog(self) -> "StubCatalog":
        return self._catalog


class StubMaskWorkflow:
    """Mask workflow stub recording activations and swap notifications."""

    def __init__(self) -> None:
        self.activation_calls: list[uuid.UUID | None] = []
        self.swap_calls: list[tuple[uuid.UUID | None, bool]] = []

    def sync_mask_activation_for_image(self, image_id: uuid.UUID):
        self.activation_calls.append(image_id)
        return MaskActivationSyncResult(activation_pending=False)

    def on_swap_applied(self, image_id: uuid.UUID, *, activation_pending: bool) -> None:
        self.swap_calls.append((image_id, activation_pending))


@dataclass
class _CatalogEntry:
    image: QImage
    path: Path
    revision: int = 0


class StubCatalog:
    """Lightweight image catalog for coordinator tests."""

    def __init__(self) -> None:
        self._entries: dict[uuid.UUID, _CatalogEntry] = {}
        self._order: list[uuid.UUID] = []
        self._path_index: dict[Path, uuid.UUID] = {}
        self.current_id: uuid.UUID | None = None
        self.pyramid_manager = StubPyramidManager()

    def add_image(self, image_id: uuid.UUID, image: QImage, path: Path) -> None:
        self._entries[image_id] = _CatalogEntry(image=image, path=path)
        self._order.append(image_id)
        self._path_index[path] = image_id

    def setCurrentImageID(self, image_id: uuid.UUID) -> None:
        self.current_id = image_id

    def getCurrentImage(self) -> QImage | None:
        if self.current_id is None:
            return None
        return self._entries[self.current_id].image

    def getCurrentId(self) -> uuid.UUID | None:
        return self.current_id

    def getCurrentPath(self) -> Path | None:
        if self.current_id is None:
            return None
        return self._entries[self.current_id].path

    def getImageIds(self) -> list[uuid.UUID]:
        return list(self._order)

    def getPath(self, image_id: uuid.UUID) -> Path | None:
        entry = self._entries.get(image_id)
        return None if entry is None else entry.path

    def getImage(self, image_id: uuid.UUID) -> QImage | None:
        entry = self._entries.get(image_id)
        return None if entry is None else entry.image

    def updateCurrentEntry(
        self,
        *,
        image: QImage | None = None,
        path: Path | None = None,
    ) -> CatalogMutationResult:
        if self.current_id is None:
            return CatalogMutationResult()
        entry = self._entries[self.current_id]
        content_changed = False
        path_changed = False
        if image is not None and image is not entry.image:
            old_asset_key = self._asset_key_for_image(self.current_id)
            entry.image = image
            entry.revision += 1
            content_changed = True
        else:
            old_asset_key = self._asset_key_for_image(self.current_id)
        if path is not None and path != entry.path:
            self._path_index.pop(entry.path, None)
            entry.path = path
            self._path_index[path] = self.current_id
            path_changed = True
        asset_keys_to_evict = (
            (old_asset_key,)
            if old_asset_key is not None and (content_changed or path_changed)
            else ()
        )
        return CatalogMutationResult(
            content_changed_ids=(self.current_id,) if content_changed else (),
            path_changed_ids=(self.current_id,) if path_changed else (),
            cache_asset_keys_to_evict=asset_keys_to_evict,
        )

    def getRevision(self, image_id: uuid.UUID) -> int | None:
        entry = self._entries.get(image_id)
        return None if entry is None else entry.revision

    def getBestFitImageForAsset(
        self, asset_key: SceneLayerAssetKey | None, target_width: int
    ) -> QImage | None:
        if asset_key is None:
            return None
        entry = self._entries.get(asset_key.source_id)
        return None if entry is None else entry.image

    def _asset_key_for_image(
        self, image_id: uuid.UUID | None
    ) -> SceneLayerAssetKey | None:
        if image_id is None:
            return None
        entry = self._entries.get(image_id)
        if entry is None:
            return None
        return default_catalog_asset_key(
            image_id,
            revision=entry.revision,
            source_path=entry.path,
        )

    def exitPlaceholderMode(self) -> None:
        return

    def imageCatalog(self) -> "StubCatalog":
        return self


class StubQPane:
    """QPane stub exposing only the surface touched by SwapCoordinator."""

    def __init__(
        self,
        catalog: StubCatalog,
        viewport: StubViewport,
        tile_manager: StubTileManager,
    ) -> None:
        self._catalog_impl = catalog
        self._view = StubView(viewport, tile_manager)
        self._masks_controller = StubMaskWorkflow()
        self._diagnostics = SimpleNamespace(set_dirty=lambda *_args, **_kwargs: None)
        self.currentImageChanged = DummySignal()
        self.imageLoaded = DummySignal()
        self.interaction = SimpleNamespace(overlays_resume_pending=False)
        self._is_blank = False
        self.original_image = QImage()
        self._updates_enabled = True

    def refreshCursor(self) -> None:
        return

    def _save_zoom_pan_for_current_image(self) -> None:
        return

    def _restore_zoom_pan_for_new_image(self, _image_id: uuid.UUID) -> None:
        return

    def _sync_mask_activation_for_image(self, image_id: uuid.UUID):
        result = self._masks_controller.sync_mask_activation_for_image(image_id)
        pending = getattr(result, "activation_pending", False)
        self.interaction.overlays_resume_pending = bool(pending)
        return result

    def blank(self) -> None:
        self._is_blank = True

    def catalog(self) -> StubCatalogFacade:
        return StubCatalogFacade(self._catalog_impl)

    def view(self) -> StubView:
        return self._view

    def linkedGroups(self):
        return []

    def diagnostics(self):
        return self._diagnostics

    def setUpdatesEnabled(self, enabled: bool) -> None:
        self._updates_enabled = enabled

    def resetActiveSamPredictor(self) -> None:
        return

    def setMinimumSize(self, *_args, **_kwargs) -> None:
        return

    def minimumSizeHint(self):
        return (0, 0)


class SwapCoordinatorHarness:
    """Fixture-style helper to construct a coordinator with stubs."""

    def __init__(self) -> None:
        self.catalog = StubCatalog()
        self.viewport = StubViewport()
        self.tile_manager = StubTileManager()
        self.qpane = StubQPane(self.catalog, self.viewport, self.tile_manager)
        self.coordinator = SwapCoordinator(
            qpane=self.qpane,
            catalog=self.catalog,
            viewport=self.viewport,
            tile_manager=self.tile_manager,
        )
        self.qpane.view().swap_delegate.coordinator = self.coordinator

    def add_image(
        self, *, color: Qt.GlobalColor, path: Path, image_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        identifier = image_id or uuid.uuid4()
        self.catalog.add_image(identifier, _solid_image(color), path)
        return identifier

    def set_current_image(self, image_id: uuid.UUID, **kwargs) -> None:
        self.coordinator.set_current_image(image_id, **kwargs)


@pytest.fixture()
def harness():
    return SwapCoordinatorHarness()


def test_set_current_image_prefetches_neighbors(harness):
    mask_service = RecordingMaskService()
    harness.coordinator.on_mask_service_attached(mask_service)
    mask_calls: list[tuple[uuid.UUID | None, bool]] = []
    workflow = harness.qpane._masks_controller
    original_on_swap_applied = workflow.on_swap_applied

    def tracking_on_swap_applied(image_id, *, activation_pending):
        mask_calls.append((image_id, activation_pending))
        return original_on_swap_applied(
            image_id,
            activation_pending=activation_pending,
        )

    workflow.on_swap_applied = tracking_on_swap_applied
    first_id = harness.add_image(color=Qt.red, path=Path("first.png"))
    second_id = harness.add_image(color=Qt.blue, path=Path("second.png"))
    harness.set_current_image(first_id)
    mask_calls.clear()
    emissions: list[uuid.UUID] = []
    harness.qpane.currentImageChanged.connect(emissions.append)
    baseline_prefetch_count = len(mask_service.prefetch_calls)
    harness.set_current_image(second_id)
    assert emissions == [second_id]
    assert mask_calls
    assert mask_calls[-1] == (second_id, False)
    recent_prefetch_calls = mask_service.prefetch_calls[baseline_prefetch_count:]
    assert recent_prefetch_calls, "expected neighbor prefetch to be scheduled"
    scheduled_ids = {image_id for image_id, reason in recent_prefetch_calls}
    assert scheduled_ids == {first_id}
    assert all(reason == "neighbor" for _, reason in recent_prefetch_calls)
    metrics = harness.coordinator.snapshot_metrics()
    assert metrics.pending_mask_prefetch == len(recent_prefetch_calls)
    assert metrics.pending_predictors >= 0


def test_set_current_image_cancels_only_irrelevant_work(harness):
    mask_service = RecordingMaskService()
    harness.coordinator.on_mask_service_attached(mask_service)
    sam_manager = RecordingSamManager()
    harness.coordinator.on_sam_manager_attached(sam_manager)
    cache_settings = CacheSettings()
    cache_settings.prefetch.predictors = -1
    harness.coordinator.apply_config(SimpleNamespace(cache=cache_settings))
    path1, path2, path3 = (
        Path("first.png"),
        Path("second.png"),
        Path("third.png"),
    )
    first_id = harness.add_image(color=Qt.white, path=path1)
    harness.add_image(color=Qt.black, path=path2)
    third_id = harness.add_image(color=Qt.green, path=path3)
    harness.set_current_image(third_id)
    assert third_id in sam_manager.requested
    assert third_id in harness.coordinator._pending_predictor_ids
    extra_mask_id = uuid.uuid4()
    extra_id = uuid.uuid4()
    harness.coordinator._pending_mask_prefetch_ids.add(extra_mask_id)
    harness.coordinator._pending_predictor_ids.add(extra_id)
    harness.set_current_image(first_id)
    assert extra_mask_id in mask_service.cancel_calls
    assert third_id not in mask_service.cancel_calls
    assert extra_id in sam_manager.cancelled
    assert third_id in sam_manager.cancelled
    assert third_id in harness.coordinator._pending_predictor_ids
    harness.set_current_image(third_id)
    assert third_id in sam_manager.requested
    assert sam_manager.requested.count(third_id) >= 2


def test_set_current_image_reports_pending_activation(harness):
    mask_service = RecordingMaskService()
    harness.coordinator.on_mask_service_attached(mask_service)
    first_id = harness.add_image(color=Qt.red, path=Path("first.png"))
    second_id = harness.add_image(color=Qt.blue, path=Path("second.png"))
    harness.set_current_image(first_id)
    activation_requests: list[uuid.UUID | None] = []

    def fake_sync(image_id):
        activation_requests.append(image_id)
        return MaskActivationSyncResult(activation_pending=True)

    workflow = harness.qpane._masks_controller
    workflow.sync_mask_activation_for_image = fake_sync
    mask_calls: list[tuple[uuid.UUID | None, bool]] = []
    original_on_swap_applied = workflow.on_swap_applied

    def tracking_on_swap_applied(image_id, *, activation_pending):
        mask_calls.append((image_id, activation_pending))
        return original_on_swap_applied(
            image_id,
            activation_pending=activation_pending,
        )

    workflow.on_swap_applied = tracking_on_swap_applied
    harness.set_current_image(second_id)
    assert activation_requests[-1] == second_id
    assert mask_calls[-1] == (second_id, True)
    assert harness.qpane.interaction.overlays_resume_pending is True


def test_apply_image_emits_loaded_and_fits_view(harness):
    image = _solid_image(Qt.green)
    source_path = Path("applied.png")
    image_id = uuid.uuid4()
    emissions: list[Path] = []
    harness.qpane.imageLoaded.connect(emissions.append)
    harness.coordinator.apply_image(
        image, source_path, image_id=image_id, fit_view=True
    )
    assert emissions == [source_path]
    assert harness.viewport.get_zoom_mode() == ViewportZoomMode.FIT
    assert harness.qpane.original_image is image


def test_apply_image_evicts_tile_and_sam_cache_for_changed_current_image(harness):
    image_id = harness.add_image(color=Qt.red, path=Path("first.png"))
    harness.catalog.setCurrentImageID(image_id)
    sam_manager = RecordingSamManager()
    harness.coordinator.on_sam_manager_attached(sam_manager)
    replacement = _solid_image(Qt.blue)
    harness.coordinator.apply_image(
        replacement,
        Path("first.png"),
        image_id=image_id,
        fit_view=False,
    )
    assert [key.source_id for key in harness.tile_manager.removed_assets] == [image_id]
    assert sam_manager.removed == [image_id]
    assert harness.qpane.original_image is replacement


def test_prefetch_neighbors_tracks_tiles_and_pyramids(harness):
    first_id = harness.add_image(color=Qt.red, path=Path("first.png"))
    harness.add_image(color=Qt.blue, path=Path("second.png"))
    harness.add_image(color=Qt.green, path=Path("third.png"))
    harness.set_current_image(first_id)
    coordinator = harness.coordinator
    scheduled_pyramids: list[SceneLayerAssetKey] = []
    scheduled_tiles: list[list[SceneLayerTileKey]] = []

    def fake_prefetch_pyramid(asset_key, image, reason="prefetch"):
        scheduled_pyramids.append(asset_key)
        return True

    def fake_prefetch_tiles(identifiers, source_image, *, reason="prefetch"):
        batch = list(identifiers)
        scheduled_tiles.append(batch)
        return batch

    harness.catalog.pyramid_manager.prefetch_pyramid = fake_prefetch_pyramid
    harness.tile_manager.prefetch_tiles = fake_prefetch_tiles
    coordinator._pending_pyramid_ids.clear()
    coordinator._pyramid_prefetch_recent.clear()
    coordinator._pending_tile_prefetch_ids.clear()
    coordinator.prefetch_neighbors(first_id)
    metrics = coordinator.snapshot_metrics()
    assert metrics.pending_pyramid_prefetch == len(scheduled_pyramids)
    total_tiles = sum(len(batch) for batch in scheduled_tiles)
    assert metrics.pending_tile_prefetch == total_tiles
    pending_tile_ids = coordinator._pending_tile_prefetch_ids
    assert metrics.pending_tile_prefetch == len(pending_tile_ids)
    coordinator._cancel_tile_prefetches(reason="test-clear")
    coordinator._cancel_pyramid_prefetches(reason="test-clear")
    metrics_after = coordinator.snapshot_metrics()
    assert metrics_after.pending_pyramid_prefetch == 0
    assert metrics_after.pending_tile_prefetch == 0


def test_prefetch_neighbors_uses_neighbor_image_ids_and_paths(harness):
    cache_settings = CacheSettings()
    cache_settings.prefetch.pyramids = 1
    cache_settings.prefetch.tiles = 1
    cache_settings.prefetch.tiles_per_neighbor = 2
    harness.coordinator.apply_config(SimpleNamespace(cache=cache_settings))
    first_id = harness.add_image(color=Qt.red, path=Path("first.png"))
    second_id = harness.add_image(color=Qt.blue, path=Path("second.png"))
    harness.set_current_image(first_id)
    harness.catalog.pyramid_manager.prefetch_calls.clear()
    harness.catalog.pyramid_manager.prefetch_payloads.clear()
    harness.tile_manager.prefetch_calls.clear()
    harness.coordinator._pending_pyramid_ids.clear()
    harness.coordinator._pyramid_prefetch_recent.clear()
    harness.coordinator._pending_tile_prefetch_ids.clear()
    harness.coordinator.prefetch_neighbors(first_id)
    assert len(harness.catalog.pyramid_manager.prefetch_payloads) == 1
    asset_key, reason = harness.catalog.pyramid_manager.prefetch_payloads[0]
    assert asset_key.source_id == second_id
    assert asset_key.source_path == Path("second.png")
    assert reason == "neighbor"
    assert harness.tile_manager.prefetch_calls
    tile_batch, reason = harness.tile_manager.prefetch_calls[-1]
    assert reason == "neighbor"
    assert tile_batch
    assert all(identifier.asset_key.source_id == second_id for identifier in tile_batch)
    assert all(
        identifier.asset_key.source_path == Path("second.png")
        for identifier in tile_batch
    )


def test_prefetch_tiles_depth_zero_disables_prefetch(harness):
    first_id = harness.add_image(color=Qt.red, path=Path("first.png"))
    harness.add_image(color=Qt.blue, path=Path("second.png"))
    harness.set_current_image(first_id)
    coordinator = harness.coordinator
    calls: list[list[SceneLayerTileKey]] = []

    def fake_prefetch_tiles(identifiers, source_image, *, reason="prefetch"):
        batch = list(identifiers)
        calls.append(batch)
        return batch

    harness.tile_manager.prefetch_tiles = fake_prefetch_tiles
    harness.catalog.pyramid_manager.prefetch_pyramid = lambda *args, **kwargs: False
    cache_settings = CacheSettings()
    cache_settings.prefetch.tiles = 0
    coordinator.apply_config(SimpleNamespace(cache=cache_settings))
    coordinator.prefetch_neighbors(first_id)
    assert calls == []
    metrics_snapshot = coordinator.snapshot_metrics()
    assert metrics_snapshot.pending_tile_prefetch == 0


def test_prefetch_tiles_respects_tiles_per_neighbor(harness):
    harness.add_image(color=Qt.red, path=Path("first.png"))
    second_id = harness.add_image(color=Qt.blue, path=Path("second.png"))
    harness.add_image(color=Qt.green, path=Path("third.png"))
    harness.set_current_image(second_id)
    coordinator = harness.coordinator
    cache_settings = CacheSettings()
    cache_settings.prefetch.tiles = 2
    cache_settings.prefetch.tiles_per_neighbor = 2
    coordinator.apply_config(SimpleNamespace(cache=cache_settings))
    scheduled_tiles: list[list[SceneLayerTileKey]] = []

    def fake_prefetch_tiles(identifiers, source_image, *, reason="prefetch"):
        batch = list(identifiers)
        scheduled_tiles.append(batch)
        return batch

    harness.tile_manager.prefetch_tiles = fake_prefetch_tiles
    coordinator.prefetch_neighbors(second_id)
    assert scheduled_tiles
    assert all(len(batch) <= 2 for batch in scheduled_tiles)
    total_tiles = sum(len(batch) for batch in scheduled_tiles)
    assert total_tiles <= 4


def test_prefetch_pyramids_skips_recent_duplicates(harness, monkeypatch):
    first_id = harness.add_image(color=Qt.red, path=Path("first.png"))
    harness.add_image(color=Qt.blue, path=Path("second.png"))
    manager = harness.catalog.pyramid_manager
    calls: list[SceneLayerAssetKey] = []

    def fake_prefetch(asset_key, image, reason="prefetch"):
        calls.append(asset_key)
        return True

    manager.prefetch_pyramid = fake_prefetch
    harness.set_current_image(first_id)
    from qpane.swap import coordinator as swap_coordinator_module

    clock = {"value": 1000.0}

    def fake_monotonic():
        return clock["value"]

    monkeypatch.setattr(
        swap_coordinator_module.time,
        "monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        swap_coordinator_module,
        "PYRAMID_RESUBMIT_COOLDOWN_SEC",
        1.0,
    )
    swap_coordinator = harness.coordinator
    swap_coordinator._pending_pyramid_ids.clear()
    swap_coordinator._pyramid_prefetch_recent.clear()
    swap_coordinator.prefetch_neighbors(first_id)
    assert calls
    swap_coordinator._pending_pyramid_ids.clear()
    baseline = len(calls)
    clock["value"] += 0.2
    swap_coordinator.prefetch_neighbors(first_id)
    assert len(calls) == baseline
    swap_coordinator._pending_pyramid_ids.clear()
    clock["value"] += 2.0
    swap_coordinator.prefetch_neighbors(first_id)
    assert len(calls) > baseline
    scheduled_tiles: list[list[SceneLayerTileKey]] = []

    def fake_prefetch_tiles(identifiers, source_image, *, reason="prefetch"):
        batch = list(identifiers)
        scheduled_tiles.append(batch)
        return batch

    harness.tile_manager.prefetch_tiles = fake_prefetch_tiles
    harness.catalog.pyramid_manager.prefetch_pyramid = lambda *args, **kwargs: False
    cache_settings = CacheSettings()
    cache_settings.prefetch.tiles = 1
    cache_settings.prefetch.tiles_per_neighbor = 1
    cache_settings.prefetch.pyramids = 0
    swap_coordinator.apply_config(SimpleNamespace(cache=cache_settings))
    swap_coordinator.prefetch_neighbors(first_id)
    metrics = swap_coordinator.snapshot_metrics()
    total_tiles = sum(len(batch) for batch in scheduled_tiles)
    assert total_tiles == metrics.pending_tile_prefetch
    assert all(len(batch) <= 1 for batch in scheduled_tiles)
    assert len(scheduled_tiles) <= 1


def test_sam_prefetch_depth_overrides_cache_when_configured(harness):
    coordinator = harness.coordinator
    coordinator.apply_config(_SamAwareConfig(predictor_depth=3, sam_depth=None))
    assert coordinator._predictor_prefetch_depth == 3
    coordinator.apply_config(_SamAwareConfig(predictor_depth=3, sam_depth=1))
    assert coordinator._predictor_prefetch_depth == 1
    coordinator.apply_config(_SamAwareConfig(predictor_depth=0, sam_depth=-1))
    assert coordinator._predictor_prefetch_depth == -1
