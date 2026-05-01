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

"""Swap orchestration utilities used by :class:`qpane.qpane.QPane`."""

from __future__ import annotations


import logging

import time

import uuid

from collections import deque

from dataclasses import dataclass

from pathlib import Path

from typing import TYPE_CHECKING, Callable, Collection, Sequence, TypeVar


from PySide6.QtGui import QImage


from ..catalog import ImageCatalog

from ..core import CacheSettings, Config, PrefetchSettings

from ..core.config_features import SamConfigSlice, require_sam_config

from ..features import FeatureInstallError

from ..rendering import Viewport
from ..scene.identity import (
    SceneLayerAssetKey,
    SceneLayerTileKey,
    default_catalog_asset_key,
)
from .contracts import (
    MaskManagerView,
    MaskPrefetchService,
    PyramidPrefetchManager,
    SamPredictorManager,
    TilePrefetchManager,
)


if TYPE_CHECKING:
    from ..qpane import QPane
logger = logging.getLogger(__name__)

PYRAMID_RESUBMIT_COOLDOWN_SEC = 1.0


_PendingItem = TypeVar("_PendingItem")


@dataclass(frozen=True)
class SwapCoordinatorMetrics:
    """Expose swap-related counters for diagnostics overlays."""

    pending_mask_prefetch: int
    pending_predictors: int
    pending_pyramid_prefetch: int
    pending_tile_prefetch: int
    last_navigation_ms: float | None


class SwapCoordinator:
    """Coordinate navigation, neighbor prefetching, and predictor warm-ups."""

    def __init__(
        self,
        *,
        qpane: QPane,
        catalog: ImageCatalog,
        viewport: Viewport,
        tile_manager: TilePrefetchManager,
        prefetch_settings: PrefetchSettings | None = None,
        mask_service: MaskPrefetchService | None = None,
        sam_manager: SamPredictorManager | None = None,
    ) -> None:
        """Wire collaborators needed to manage swaps and their background work.

        Args:
            qpane: Owning QPane widget emitting navigation and render events.
            catalog: Catalog storing image data and metadata.
            viewport: Viewport supplying view state for prefetch sizing.
            tile_manager: Tile manager notified about prefetch and cancellation.
            prefetch_settings: Optional overrides for pyramid/tile/mask/predictor depth.
            mask_service: Optional mask service used for activation and prefetch hooks.
            sam_manager: Optional SAM manager used to warm predictors alongside swaps.

        Side effects:
            Subscribes to tile and pyramid ready signals; managers must expose
            ``tileReady`` and ``pyramidReady``.
        """
        self._qpane = qpane
        self._catalog = catalog
        self._viewport = viewport
        if not isinstance(tile_manager, TilePrefetchManager):
            raise TypeError("tile_manager must implement TilePrefetchManager")
        self._tile_manager: TilePrefetchManager = tile_manager
        self._mask_service: MaskPrefetchService | None = None
        self._sam_manager: SamPredictorManager | None = None
        self._navigation_history: deque[uuid.UUID] = deque(maxlen=16)
        self._pending_mask_prefetch_ids: set[uuid.UUID] = set()
        self._pending_predictor_ids: set[uuid.UUID] = set()
        self._pending_pyramid_ids: set[SceneLayerAssetKey] = set()
        self._pyramid_prefetch_recent: dict[SceneLayerAssetKey, float] = {}
        self._pending_tile_prefetch_ids: set[SceneLayerTileKey] = set()
        self._navigation_inflight_start_ns: int | None = None
        self._last_navigation_duration_ms: float | None = None
        self._current_image_id: uuid.UUID | None = None
        self._pyramid_prefetch_depth = 0
        self._tile_prefetch_depth = 0
        self._mask_prefetch_depth = -1
        self._predictor_prefetch_depth = -1
        self._tiles_per_neighbor = 0
        self._diagnostics_missing_logged = False
        self._apply_prefetch_settings(prefetch_settings or PrefetchSettings())
        self._tile_manager.tileReady.connect(self._on_tile_ready)
        try:
            pyramid_manager = catalog.pyramid_manager
        except AttributeError as exc:  # pragma: no cover - defensive guard
            raise AttributeError("Catalog must expose pyramid_manager") from exc
        if not isinstance(pyramid_manager, PyramidPrefetchManager):
            raise TypeError(
                "catalog.pyramid_manager must implement PyramidPrefetchManager"
            )
        self._pyramid_manager: PyramidPrefetchManager = pyramid_manager
        self._pyramid_manager.pyramidReady.connect(self._on_pyramid_ready)
        if mask_service is not None:
            self.on_mask_service_attached(mask_service)
        if sam_manager is not None:
            self.on_sam_manager_attached(sam_manager)

    def apply_config(self, config: Config | object) -> None:
        """Update prefetch tuning from cache and SAM settings in ``config``."""
        cache_settings = getattr(config, "cache", None)
        if isinstance(cache_settings, CacheSettings):
            self._apply_prefetch_settings(cache_settings.prefetch)
        self._apply_sam_config(config)

    def set_current_image(
        self,
        image_id: uuid.UUID,
        *,
        fit_view: bool | None = None,
        save_view: bool = True,
    ) -> None:
        """Activate ``image_id``, render it, and restart neighbor prefetching.

        Args:
            image_id: Catalog image identifier to make current.
            fit_view: Force zoom-to-fit for the new image when True.
            save_view: Persist the outgoing viewport transform before navigation.

        Side effects:
            Cancels unrelated mask/pyramid/tile/predictor prefetches, synchronizes
            mask activation, emits ``currentImageChanged``, displays the target
            image, triggers neighbor prefetching, and notifies the mask workflow
            when available.
        """
        qpane = self._qpane
        self._navigation_inflight_start_ns = time.perf_counter_ns()
        qpane._is_blank = False
        qpane.refreshCursor()
        if save_view:
            qpane._save_zoom_pan_for_current_image()
        self._catalog.setCurrentImageID(image_id)
        self._record_navigation_history(image_id)
        activation_result = qpane._sync_mask_activation_for_image(image_id)
        qpane.currentImageChanged.emit(image_id)
        neighbor_ids = self._candidate_prefetch_ids(image_id)
        skip_ids = set(neighbor_ids) | {image_id}
        skip_predictor_ids: set[uuid.UUID] = {image_id}
        service = self._mask_service
        mask_manager: MaskManagerView | None = (
            service.manager if service is not None else None
        )
        if mask_manager is not None:
            for candidate_id in neighbor_ids:
                mask_ids = mask_manager.get_mask_ids_for_image(candidate_id)
                if not mask_ids:
                    continue
                skip_predictor_ids.add(candidate_id)
        self._cancel_mask_prefetches(reason="navigation", skip=skip_ids)
        self._cancel_pending_predictors(
            reason="navigation",
            skip=skip_predictor_ids or None,
        )
        prefetch_skip_assets = self._asset_keys_for_image_ids(skip_predictor_ids)
        self._cancel_pyramid_prefetches(
            reason="navigation",
            skip=prefetch_skip_assets,
        )
        self._cancel_tile_prefetches(reason="navigation", skip=prefetch_skip_assets)
        fit_view = False if fit_view is None else bool(fit_view)
        self.display_current_image(fit_view=fit_view)
        self.prefetch_neighbors(image_id, candidates=neighbor_ids)
        qpane._restore_zoom_pan_for_new_image(image_id)
        self._current_image_id = image_id
        workflow = qpane._masks_controller
        activation_pending = False
        if activation_result is not None:
            activation_pending = getattr(activation_result, "activation_pending", False)
        try:
            workflow.on_swap_applied(
                image_id,
                activation_pending=activation_pending,
            )
        except Exception:
            logger.exception("Mask workflow swap hook failed (image_id=%s)", image_id)
        self._mark_diagnostics_dirty()
        self._record_navigation_duration()

    def reset(self) -> None:
        """Cancel all pending work and clear the current image selection."""
        self._cancel_mask_prefetches(reason="reset")
        self._cancel_pending_predictors(reason="reset")
        self._cancel_pyramid_prefetches(reason="reset")
        self._cancel_tile_prefetches(reason="reset")
        self._current_image_id = None

    def display_current_image(self, *, fit_view: bool) -> None:
        """Render the catalog's current image or blank the qpane when absent."""
        image = self._catalog.getCurrentImage()
        if image is None or image.isNull():
            self._qpane.original_image = QImage()
            self._qpane.blank()
            return
        current_path = self._catalog.getCurrentPath()
        self.apply_image(
            image,
            current_path,
            image_id=self._catalog.getCurrentId(),
            fit_view=fit_view,
        )

    def apply_image(
        self,
        image: QImage,
        source_path: Path | None,
        *,
        image_id: uuid.UUID | None,
        fit_view: bool,
    ) -> None:
        """Display ``image`` from ``source_path`` and refresh view state.

        Args:
            image: Image to render in the qpane.
            source_path: Filesystem path associated with ``image``, when known.
            image_id: Catalog identifier associated with ``image`` when available.
            fit_view: Fit the viewport to the image when True.

        Side effects:
            Resets blank state, requests SAM predictor warm-ups, updates the
            catalog entry, allocates render buffers, emits ``imageLoaded``, and
            realigns the view.
        """
        qpane = self._qpane
        qpane.catalog().exitPlaceholderMode()
        qpane._is_blank = False
        qpane.refreshCursor()
        qpane.setUpdatesEnabled(False)
        try:
            qpane.resetActiveSamPredictor()
            if image_id is not None and self._sam_manager is not None:
                try:
                    self._sam_manager.requestPredictor(
                        image,
                        image_id,
                        source_path=source_path,
                    )
                except Exception:
                    logger.exception(
                        "SAM predictor request failed (image_id=%s)",
                        image_id,
                    )
                else:
                    self._pending_predictor_ids.add(image_id)
            qpane.original_image = image
            self._viewport.setContentSize(image.size())
            if fit_view:
                self._viewport.setZoomFit()
            mutation = self._catalog.updateCurrentEntry(image=image, path=source_path)
            cache_changed_ids = set(mutation.content_changed_ids) | set(
                mutation.path_changed_ids
            )
            for asset_key in mutation.cache_asset_keys_to_evict:
                self._tile_manager.remove_tiles_for_source_asset(asset_key)
            if image_id is not None and image_id in cache_changed_ids:
                if self._sam_manager is not None:
                    remove_cache = getattr(self._sam_manager, "removeFromCache", None)
                    if callable(remove_cache):
                        remove_cache(image_id)
            qpane.setMinimumSize(qpane.minimumSizeHint())
            qpane.view().allocate_buffers()
            qpane.imageLoaded.emit(source_path or Path())
            qpane.refreshCursor()
        finally:
            qpane.setUpdatesEnabled(True)
            qpane.view().ensure_view_alignment(force=True)

    def prefetch_neighbors(
        self, image_id: uuid.UUID, *, candidates: Sequence[uuid.UUID] | None = None
    ) -> None:
        """Warm mask, predictor, pyramid, and tile prefetching around ``image_id``."""
        neighbor_ids = (
            list(candidates)
            if candidates is not None
            else self._candidate_prefetch_ids(image_id)
        )
        self._prefetch_neighbor_masks(neighbor_ids)
        self._prefetch_neighbor_predictors(image_id, neighbor_ids)
        self._maybe_prefetch_pyramids(image_id, neighbor_ids)
        self._maybe_prefetch_tiles(image_id, neighbor_ids)
        self._mark_diagnostics_dirty()

    def on_mask_service_attached(self, service: MaskPrefetchService) -> None:
        """Attach the mask service and refresh neighbor prefetching."""
        if not isinstance(service, MaskPrefetchService):
            raise TypeError("mask_service must implement MaskPrefetchService")
        self._mask_service = service
        if self._current_image_id is not None:
            self.prefetch_neighbors(self._current_image_id)

    def on_mask_service_detached(self) -> None:
        """Detach the mask service and cancel queued mask prefetch work."""
        self._cancel_mask_prefetches(reason="mask-detached")
        self._mask_service = None
        self._pending_mask_prefetch_ids.clear()

    def on_sam_manager_attached(self, manager: SamPredictorManager) -> None:
        """Attach the SAM manager used for predictor requests and cancellation."""
        if not isinstance(manager, SamPredictorManager):
            raise TypeError("sam_manager must implement SamPredictorManager")
        self._sam_manager = manager

    def on_sam_manager_detached(self) -> None:
        """Cancel outstanding predictor warm-ups and drop the SAM manager."""
        self._cancel_pending_predictors(reason="sam-detached")
        self._sam_manager = None
        self._pending_predictor_ids.clear()

    def snapshot_metrics(self) -> SwapCoordinatorMetrics:
        """Return counters describing outstanding swap and prefetch work."""
        return SwapCoordinatorMetrics(
            pending_mask_prefetch=len(self._pending_mask_prefetch_ids),
            pending_predictors=len(self._pending_predictor_ids),
            pending_pyramid_prefetch=len(self._pending_pyramid_ids),
            pending_tile_prefetch=len(self._pending_tile_prefetch_ids),
            last_navigation_ms=self._last_navigation_duration_ms,
        )

    def _mark_diagnostics_dirty(self) -> None:
        """Notify diagnostics that swap metrics changed."""
        diagnostics_accessor = getattr(self._qpane, "diagnostics", None)
        diagnostics = None
        if callable(diagnostics_accessor):
            try:
                diagnostics = diagnostics_accessor()
            except Exception as exc:
                logger.exception(
                    "Swap diagnostics accessor failed; metrics dirty signal dropped",
                    exc_info=exc,
                )
                return
        elif diagnostics_accessor is not None:
            diagnostics = diagnostics_accessor
        if diagnostics is None:
            if not self._diagnostics_missing_logged:
                logger.warning(
                    "Swap diagnostics broker unavailable; metrics dirty signal dropped"
                )
                self._diagnostics_missing_logged = True
            return
        set_dirty = getattr(diagnostics, "set_dirty", None)
        if not callable(set_dirty):
            if not self._diagnostics_missing_logged:
                logger.warning(
                    "Swap diagnostics broker missing set_dirty; metrics dirty signal dropped"
                )
                self._diagnostics_missing_logged = True
            return
        try:
            set_dirty("swap")
        except Exception as exc:
            logger.exception(
                "Swap diagnostics dirty signal failed; metrics may be stale",
                exc_info=exc,
            )
        else:
            self._diagnostics_missing_logged = False

    def _apply_prefetch_settings(self, settings: PrefetchSettings) -> None:
        """Clamp and store prefetch limits derived from ``settings``.

        Side effects:
            Cancels tracked pyramid or tile prefetches when their depths are set to zero.
        """
        cloned = settings.clone()
        self._pyramid_prefetch_depth = self._clamp_depth(cloned.pyramids)
        self._tile_prefetch_depth = self._clamp_depth(cloned.tiles)
        self._mask_prefetch_depth = self._clamp_depth(cloned.masks)
        self._predictor_prefetch_depth = self._clamp_depth(cloned.predictors)
        if self._pyramid_prefetch_depth == 0:
            self._cancel_pyramid_prefetches(reason="config-update", skip=None)
        if self._tile_prefetch_depth == 0:
            self._cancel_tile_prefetches(reason="config-update", skip=None)
        try:
            self._tiles_per_neighbor = max(0, int(cloned.tiles_per_neighbor))
        except (TypeError, ValueError):
            self._tiles_per_neighbor = 0
        self._mark_diagnostics_dirty()

    def _apply_sam_config(self, source: object) -> None:
        """Override predictor prefetch depth when the SAM slice provides one."""
        try:
            sam_settings = require_sam_config(source)
        except FeatureInstallError:
            return
        self._apply_sam_prefetch_settings(sam_settings)

    def _apply_sam_prefetch_settings(self, settings: SamConfigSlice) -> None:
        """Apply SAM-specific predictor prefetch depth overrides."""
        depth = getattr(settings, "sam_prefetch_depth", None)
        if depth is None:
            return
        self._predictor_prefetch_depth = self._clamp_depth(depth)

    @staticmethod
    def _clamp_depth(raw: object) -> int:
        """Normalize ``raw`` into a non-negative depth or ``-1`` for unlimited."""
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return -1
        if value < 0:
            return -1
        return value

    def _on_tile_ready(self, key: SceneLayerTileKey) -> None:
        """Drop completed tile prefetch identifiers from tracking."""
        self._pending_tile_prefetch_ids.discard(key)
        self._mark_diagnostics_dirty()

    def _on_pyramid_ready(self, asset_key: SceneLayerAssetKey) -> None:
        """Stop tracking pyramid prefetches once they are ready."""
        self._pending_pyramid_ids.discard(asset_key)
        self._mark_diagnostics_dirty()

    def _cancel_pending_items(
        self,
        *,
        pending: set[_PendingItem],
        skip: Collection[_PendingItem] | None,
        cancel_fn: Callable[[_PendingItem], bool] | None,
        reason: str,
        log_name: str,
        item_label: str,
        missing_hint: str,
    ) -> set[_PendingItem]:
        """Request cancellation for tracked items not listed in ``skip``.

        Returns:
            The subset of items kept for tracking after cancellations.
        """
        skip_set = set(skip or ())
        if not pending:
            return skip_set
        if cancel_fn is None:
            if pending:
                logger.warning(
                    "%s cancellation skipped (%s pending, reason=%s, cause=%s)",
                    log_name,
                    len(pending),
                    reason,
                    missing_hint,
                )
            return skip_set
        for item in list(pending):
            if item in skip_set:
                continue
            cancelled = False
            try:
                cancelled = bool(cancel_fn(item))
            except Exception:
                logger.exception(
                    "%s cancellation failed (%s=%s, reason=%s)",
                    log_name,
                    item_label,
                    item,
                    reason,
                )
            finally:
                logger.info(
                    "%s cancellation requested (%s=%s, reason=%s, cancelled=%s)",
                    log_name,
                    item_label,
                    item,
                    reason,
                    cancelled,
                )
                pending.discard(item)
        self._mark_diagnostics_dirty()
        return skip_set

    def _cancel_mask_prefetches(
        self, *, reason: str, skip: Collection[uuid.UUID] | None = None
    ) -> None:
        """Cancel queued mask prefetches except those listed in ``skip``."""
        service = self._mask_service
        skip_set = set(skip or ())
        if service is None:
            self._pending_mask_prefetch_ids = skip_set
            return
        self._pending_mask_prefetch_ids = self._cancel_pending_items(
            pending=self._pending_mask_prefetch_ids,
            skip=skip_set,
            cancel_fn=service.cancelPrefetch,
            reason=reason,
            log_name="Mask prefetch",
            item_label="image_id",
            missing_hint="mask service missing cancelPrefetch",
        )
        self._mark_diagnostics_dirty()

    def _cancel_pending_predictors(
        self, *, reason: str, skip: Collection[uuid.UUID] | None = None
    ) -> None:
        """Cancel scheduled SAM predictor warm-ups except for IDs listed in ``skip``."""
        manager = self._sam_manager
        skip_ids = set(skip or ())
        if manager is None:
            self._pending_predictor_ids = skip_ids
            return
        self._pending_predictor_ids = self._cancel_pending_items(
            pending=self._pending_predictor_ids,
            skip=skip_ids,
            cancel_fn=manager.cancelPendingPredictor,
            reason=reason,
            log_name="SAM predictor",
            item_label="image_id",
            missing_hint="SAM manager missing cancelPendingPredictor",
        )
        self._mark_diagnostics_dirty()

    def _cancel_pyramid_prefetches(
        self, *, reason: str, skip: Collection[SceneLayerAssetKey] | None = None
    ) -> None:
        """Cancel tracked pyramid prefetches except those in ``skip``."""
        manager = self._pyramid_manager
        skip_keys = set(skip or ())
        if not self._pending_pyramid_ids:
            return
        pending = set(self._pending_pyramid_ids)
        cancel_keys = [key for key in pending if key not in skip_keys]
        if cancel_keys:
            try:
                manager.cancel_prefetch(cancel_keys, reason=reason)
            except Exception:
                logger.exception(
                    "Pyramid prefetch cancellation failed (count=%s, reason=%s)",
                    len(cancel_keys),
                    reason,
                )
        self._pending_pyramid_ids = {key for key in pending if key in skip_keys}
        self._mark_diagnostics_dirty()

    def _cancel_tile_prefetches(
        self, *, reason: str, skip: Collection[SceneLayerAssetKey] | None = None
    ) -> None:
        """Cancel tile prefetch jobs whose asset keys are not in ``skip``."""
        manager = self._tile_manager
        skip_keys = set(skip or ())
        if not self._pending_tile_prefetch_ids:
            return
        cancel_idents = [
            ident
            for ident in self._pending_tile_prefetch_ids
            if ident.asset_key not in skip_keys
        ]
        if cancel_idents:
            try:
                manager.cancel_prefetch(cancel_idents, reason=reason)
            except Exception:
                logger.exception(
                    "Tile prefetch cancellation failed (count=%s, reason=%s)",
                    len(cancel_idents),
                    reason,
                )
            for ident in cancel_idents:
                self._pending_tile_prefetch_ids.discard(ident)
        self._pending_tile_prefetch_ids = {
            ident
            for ident in self._pending_tile_prefetch_ids
            if ident.asset_key in skip_keys
        }
        self._mark_diagnostics_dirty()

    def _record_navigation_history(self, image_id: uuid.UUID) -> None:
        """Track the last few navigated image IDs for smarter prefetching."""
        if not isinstance(image_id, uuid.UUID):
            return
        history = self._navigation_history
        if history and history[-1] == image_id:
            return
        history.append(image_id)

    def _record_navigation_duration(self) -> None:
        """Capture the elapsed time for the most recent navigation request."""
        if self._navigation_inflight_start_ns is None:
            return
        end_ns = time.perf_counter_ns()
        duration_ms = (end_ns - self._navigation_inflight_start_ns) / 1_000_000.0
        self._last_navigation_duration_ms = max(0.0, duration_ms)
        self._navigation_inflight_start_ns = None

    def _candidate_prefetch_ids(self, current_id: uuid.UUID) -> list[uuid.UUID]:
        """Return neighbor IDs drawn from adjacency, link groups, and history."""
        catalog_ids = list(self._catalog.getImageIds())
        candidates: list[uuid.UUID] = []
        try:
            index = catalog_ids.index(current_id)
        except ValueError:
            index = -1
        if index >= 0:
            if index + 1 < len(catalog_ids):
                candidates.append(catalog_ids[index + 1])
            if index - 1 >= 0:
                candidates.append(catalog_ids[index - 1])
        for group in self._qpane.linkedGroups():
            if current_id in group.members:
                candidates.extend(mid for mid in group.members if mid != current_id)
                break
        history = list(self._navigation_history)
        if len(history) >= 2:
            previous = history[-2]
            if previous != current_id:
                candidates.append(previous)
        seen: set[uuid.UUID] = set()
        ordered: list[uuid.UUID] = []
        for candidate in candidates:
            if (
                isinstance(candidate, uuid.UUID)
                and candidate not in seen
                and candidate != current_id
            ):
                seen.add(candidate)
                ordered.append(candidate)
        return ordered

    def _asset_keys_for_image_ids(
        self, image_ids: Collection[uuid.UUID]
    ) -> set[SceneLayerAssetKey]:
        """Return current default-scene asset keys for known catalog image IDs."""
        asset_keys: set[SceneLayerAssetKey] = set()
        for image_id in image_ids:
            asset_key = self._asset_key_for_catalog_image(image_id)
            if asset_key is not None:
                asset_keys.add(asset_key)
        return asset_keys

    def _asset_key_for_catalog_image(
        self, image_id: uuid.UUID
    ) -> SceneLayerAssetKey | None:
        """Return the current default-scene asset key for a catalog image."""
        revision_getter = getattr(self._catalog, "getRevision", None)
        if not callable(revision_getter):
            return None
        revision = revision_getter(image_id)
        if revision is None:
            return None
        return default_catalog_asset_key(
            image_id,
            revision=max(0, int(revision)),
            source_path=self._catalog.getPath(image_id),
        )

    def _prefetch_neighbor_masks(self, candidates: Sequence[uuid.UUID]) -> None:
        """Submit mask prefetch jobs for likely navigation targets respecting depth limits."""
        service = self._mask_service
        self._pending_mask_prefetch_ids.clear()
        if service is None:
            return
        depth = self._mask_prefetch_depth
        candidate_list = list(candidates)
        if depth == 0:
            return
        if depth > 0:
            candidate_list = candidate_list[:depth]
        for candidate in candidate_list:
            try:
                scheduled = service.prefetchColorizedMasks(candidate, reason="neighbor")
            except Exception:
                logger.exception(
                    "Mask prefetch failed (image_id=%s)",
                    candidate,
                )
                scheduled = False
            if scheduled:
                self._pending_mask_prefetch_ids.add(candidate)

    def _prefetch_neighbor_predictors(
        self, current_id: uuid.UUID, neighbors: Sequence[uuid.UUID]
    ) -> None:
        """Warm up SAM predictors for neighbor images subject to prefetch depth."""
        manager = self._sam_manager
        if manager is None:
            return
        depth = self._predictor_prefetch_depth
        neighbor_list = list(neighbors)
        if depth == 0:
            return
        if depth > 0:
            neighbor_list = neighbor_list[:depth]
        for neighbor_id in neighbor_list:
            if neighbor_id == current_id:
                continue
            if neighbor_id in self._pending_predictor_ids:
                continue
            image = self._catalog.getImage(neighbor_id)
            if image is None or image.isNull():
                continue
            path = self._catalog.getPath(neighbor_id)
            try:
                manager.requestPredictor(
                    image,
                    neighbor_id,
                    source_path=path,
                )
            except Exception:
                logger.exception(
                    "SAM predictor prefetch failed (image_id=%s)",
                    neighbor_id,
                )
                continue
            self._pending_predictor_ids.add(neighbor_id)

    def _maybe_prefetch_pyramids(
        self, current_id: uuid.UUID, candidates: Sequence[uuid.UUID]
    ) -> None:
        """Schedule pyramid prefetch for neighbor candidates with cooldown and depth checks."""
        if not candidates:
            return
        manager = self._pyramid_manager
        depth = self._pyramid_prefetch_depth
        if depth == 0:
            return
        neighbor_ids = list(candidates)
        if depth > 0:
            neighbor_ids = neighbor_ids[:depth]
        for neighbor_id in neighbor_ids:
            if neighbor_id == current_id:
                continue
            asset_key = self._asset_key_for_catalog_image(neighbor_id)
            if asset_key is None:
                continue
            recent_ns = self._pyramid_prefetch_recent.get(asset_key)
            now_sec = time.monotonic()
            if (
                recent_ns is not None
                and now_sec - recent_ns < PYRAMID_RESUBMIT_COOLDOWN_SEC
            ):
                logger.debug(
                    "Skipping pyramid prefetch for %s; scheduled %.2fs ago",
                    neighbor_id,
                    now_sec - recent_ns,
                )
                continue
            if asset_key in self._pending_pyramid_ids:
                continue
            image = self._catalog.getImage(neighbor_id)
            if image is None or image.isNull():
                continue
            try:
                scheduled = bool(
                    manager.prefetch_pyramid(
                        asset_key,
                        image,
                        reason="neighbor",
                    )
                )
            except Exception:
                logger.exception(
                    "Pyramid prefetch submission failed (image_id=%s)",
                    neighbor_id,
                )
                continue
            if scheduled:
                self._pending_pyramid_ids.add(asset_key)
                self._pyramid_prefetch_recent[asset_key] = now_sec
                # prune stale entries
                stale_cutoff = now_sec - (PYRAMID_RESUBMIT_COOLDOWN_SEC * 4)
                self._pyramid_prefetch_recent = {
                    k: v
                    for k, v in self._pyramid_prefetch_recent.items()
                    if v >= stale_cutoff
                }
                logger.info("Pyramid prefetch scheduled for %s", neighbor_id)

    def _maybe_prefetch_tiles(
        self, current_id: uuid.UUID, candidates: Sequence[uuid.UUID]
    ) -> None:
        """Schedule background tile generation for neighbor candidates within cache and depth limits."""
        if not candidates:
            return
        manager = self._tile_manager
        depth = self._tile_prefetch_depth
        if depth == 0:
            return
        cache_limit = getattr(manager, "cache_limit_bytes", 0)
        cache_usage = getattr(manager, "cache_usage_bytes", 0)
        neighbor_ids = list(candidates)
        if depth > 0:
            neighbor_ids = neighbor_ids[:depth]
        for neighbor_id in neighbor_ids:
            if neighbor_id == current_id:
                continue
            if cache_limit and cache_usage >= cache_limit:
                logger.debug(
                    "Skipping tile prefetch (cache full) for %s",
                    neighbor_id,
                )
                break
            image = self._catalog.getImage(neighbor_id)
            if image is None or image.isNull():
                continue
            prepared = self._prepare_tile_prefetch(
                image_id=neighbor_id,
                image=image,
            )
            if prepared is None:
                continue
            source_image, identifiers = prepared
            pending = list(identifiers)
            if not pending:
                continue
            scheduled: Sequence[SceneLayerTileKey] = ()
            try:
                scheduled = manager.prefetch_tiles(
                    pending, source_image, reason="neighbor"
                )
            except Exception:
                logger.exception(
                    "Tile prefetch submission failed (image_id=%s)",
                    neighbor_id,
                )
                continue
            scheduled_list = list(scheduled)
            if not scheduled_list:
                continue
            for ident in scheduled_list:
                self._pending_tile_prefetch_ids.add(ident)
            cache_usage = getattr(manager, "cache_usage_bytes", cache_usage)
            logger.info(
                "Tile prefetch scheduled for %s (%s tiles)",
                neighbor_id,
                len(scheduled_list),
            )

    def _prepare_tile_prefetch(
        self, *, image_id: uuid.UUID, image: QImage
    ) -> tuple[QImage, list[SceneLayerTileKey]] | None:
        """Return a source image and centered tile identifiers for neighbor prefetching."""
        manager = self._tile_manager
        asset_key = self._asset_key_for_catalog_image(image_id)
        if asset_key is None:
            return None
        width = image.width()
        height = image.height()
        if width <= 0 or height <= 0:
            return None
        tile_budget = max(0, self._tiles_per_neighbor)
        if tile_budget <= 0:
            return None
        zoom = self._viewport.zoom if self._viewport.zoom > 0 else 1.0
        target_width = width * zoom
        source_image = self._catalog.getBestFitImageForAsset(asset_key, target_width)
        if source_image is None or source_image.isNull():
            source_image = image
        if source_image.isNull():
            return None
        base_width = width if width > 0 else 1
        pyramid_scale = source_image.width() / base_width if base_width else 1.0
        cols, rows = manager.calculate_grid_dimensions(
            source_image.width(), source_image.height()
        )
        if cols <= 0 or rows <= 0:
            return None
        center_row = max(0, min(rows - 1, rows // 2))
        center_col = max(0, min(cols - 1, cols // 2))
        offsets = [
            (0, 0),
            (0, 1),
            (1, 0),
            (0, -1),
            (-1, 0),
            (1, 1),
            (-1, 1),
            (1, -1),
        ]
        identifiers: list[SceneLayerTileKey] = []
        for dr, dc in offsets:
            if len(identifiers) >= tile_budget:
                break
            row = center_row + dr
            col = center_col + dc
            if row < 0 or row >= rows or col < 0 or col >= cols:
                continue
            ident = SceneLayerTileKey(
                asset_key=asset_key,
                pyramid_asset_key=asset_key,
                pyramid_scale=pyramid_scale,
                row=row,
                col=col,
            )
            if ident not in identifiers:
                identifiers.append(ident)
        if not identifiers:
            identifiers.append(
                SceneLayerTileKey(
                    asset_key=asset_key,
                    pyramid_asset_key=asset_key,
                    pyramid_scale=pyramid_scale,
                    row=center_row,
                    col=center_col,
                )
            )
        return source_image, identifiers
