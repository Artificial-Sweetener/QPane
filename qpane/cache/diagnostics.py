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

"""Diagnostics helpers that summarize shared cache budgets and counters."""

from __future__ import annotations


import logging

from dataclasses import dataclass

from typing import TYPE_CHECKING, Iterable


from ..types import DiagnosticRecord


if TYPE_CHECKING:  # pragma: no cover - import cycle guard

    from ..qpane import QPane
logger = logging.getLogger(__name__)


MB = 1024 * 1024


_MISSING_COORDINATOR_LOGGED = False

_MISSING_DETAIL_MANAGER_LOGS: set[str] = set()

_MISSING_DETAIL_SNAPSHOT_LOGS: set[str] = set()

_MISSING_TILE_VIEW_LOGGED = False

_MISSING_TILE_MANAGER_LOGGED = False

_MISSING_CATALOG_LOGGED = False

_MISSING_PYRAMID_ACCESSOR_LOGGED = False


@dataclass(frozen=True)
class _CacheMetricsBundle:
    """Container that mirrors the optional counters exposed by cache managers."""

    cache_bytes: int | None = None
    cache_limit: int | None = None
    hits: int | None = None
    misses: int | None = None
    evictions: int | None = None
    evicted_bytes: int | None = None
    pending_retries: int | None = None
    last_eviction_reason: str | None = None
    last_eviction_timestamp: float | None = None
    prefetch_requested: int | None = None
    prefetch_completed: int | None = None
    prefetch_failed: int | None = None
    last_prefetch_ms: float | None = None
    colorize_last_ms: float | None = None
    colorize_avg_ms: float | None = None
    colorize_max_ms: float | None = None
    colorize_samples: int | None = None
    colorize_slow_count: int | None = None
    colorize_threshold_ms: float | None = None
    colorize_last_source: str | None = None


_CONSUMER_LABELS = {
    "tiles": ("Tiles", "tiles"),
    "pyramids": ("Pyramids", "pyramids"),
    "mask_overlays": ("Mask Overlays", "masks"),
    "predictors": ("SAM Predictors", "predictors"),
}


def cache_diagnostics_provider(qpane: "QPane") -> Iterable[DiagnosticRecord]:
    """Emit lightweight cache health records using cached coordinator snapshots."""
    coordinator = _resolve_coordinator(qpane)
    if coordinator is None:
        return tuple()
    snapshot = _safe_snapshot(coordinator, caller="cache_diagnostics_provider")
    if snapshot is None:
        return tuple()
    records: list[DiagnosticRecord] = []
    aggregate = _build_aggregate_record(snapshot)
    if aggregate is not None:
        records.append(aggregate)
    return tuple(records)


def cache_detail_provider(qpane: "QPane") -> Iterable[DiagnosticRecord]:
    """Emit per-consumer cache stats as single labeled rows."""
    coordinator = _resolve_coordinator(qpane)
    if coordinator is None:
        return tuple()
    snapshot = _safe_snapshot(coordinator, caller="cache_detail_provider")
    if snapshot is None:
        return tuple()
    records: list[DiagnosticRecord] = []
    consumers = snapshot.get("consumers", {})
    for consumer_id, state in sorted(consumers.items()):
        consumer_label = _CONSUMER_LABELS.get(consumer_id, (consumer_id.title(),))[0]
        usage_mb = _to_mb(state.get("usage_bytes"))
        entitlement_mb = _to_mb(state.get("entitlement_bytes"))
        overage_mb = _to_mb(state.get("overage_bytes"))
        override_mb = _to_mb(state.get("override_bytes"))
        last_trim = state.get("last_trim") or {}
        last_reason = last_trim.get("reason") or ""
        parts = [f"usage={usage_mb:.1f}MB"]
        if entitlement_mb > 0.0:
            parts.append(f"ent={entitlement_mb:.1f}MB")
        if override_mb > 0.0:
            parts.append(f"override={override_mb:.1f}MB")
        if overage_mb > 0.0:
            parts.append(f"over={overage_mb:.1f}MB")
        if last_reason:
            parts.append(f"last={last_reason}")
        counters = _format_counters(_bundle_metrics(qpane, consumer_id))
        if counters:
            parts.append(counters)
        records.append(DiagnosticRecord(f"Cache|{consumer_label}", " ".join(parts)))
    return tuple(records)


def _safe_snapshot(coordinator, *, caller: str) -> dict[str, object] | None:
    """Return the coordinator snapshot while guarding against failures."""
    try:
        return coordinator.snapshot()
    except Exception:  # pragma: no cover - defensive guard
        logger.warning(
            "Cache diagnostics snapshot failed | caller=%s",
            caller,
            exc_info=True,
        )
        return None


def _resolve_coordinator(qpane: "QPane"):
    """Return the cache coordinator while logging missing state once."""
    global _MISSING_COORDINATOR_LOGGED
    coordinator = qpane.cacheCoordinator
    if coordinator is None:
        if not _MISSING_COORDINATOR_LOGGED:
            logger.warning("Cache diagnostics unavailable; cache coordinator missing")
            _MISSING_COORDINATOR_LOGGED = True
        return None
    return coordinator


def _build_aggregate_record(snapshot: dict[str, object]) -> DiagnosticRecord | None:
    """Return the aggregate cache usage/budget record."""
    budget_bytes = int(snapshot.get("budget_bytes", 0))
    usage_bytes = int(snapshot.get("usage_bytes", 0))
    hard_cap = bool(snapshot.get("hard_cap"))
    admissions_blocked = bool(snapshot.get("admissions_blocked"))
    headroom = snapshot.get("headroom") or {}
    budget_mb = _to_mb(budget_bytes)
    usage_mb = _to_mb(usage_bytes)
    if budget_mb <= 0.0:
        return DiagnosticRecord("Cache", "0.0/0.0 MB (0%)")
    utilization = usage_mb / budget_mb if budget_mb else 0.0
    headroom_parts: list[str] = []
    available_mb = _to_mb(headroom.get("available_bytes"))
    total_mb = _to_mb(headroom.get("total_bytes"))
    if available_mb > 0.0 and total_mb > 0.0:
        headroom_parts.append(
            f"headroom {available_mb:.1f}/{total_mb:.1f} MB ({(available_mb/total_mb)*100:.0f}%)"
        )
    swap_total_mb = _to_mb(headroom.get("swap_total_bytes"))
    swap_free_mb = _to_mb(headroom.get("swap_free_bytes"))
    if swap_total_mb > 0.0 and swap_free_mb >= 0.0:
        headroom_parts.append(f"swap {swap_free_mb:.1f}/{swap_total_mb:.1f} MB")
    status_parts = [f"{usage_mb:.1f}/{budget_mb:.1f} MB ({utilization * 100:.0f}%)"]
    if hard_cap:
        status_parts.append("(hard)")
    if admissions_blocked:
        status_parts.append("admissions blocked")
    if headroom_parts:
        status_parts.extend(headroom_parts)
    return DiagnosticRecord("Cache", " | ".join(status_parts))


def _to_mb(value: int | float | None) -> float:
    """Convert ``value`` to megabytes while handling ``None`` safely."""
    if value in (None, 0):
        return 0.0
    try:
        return float(value) / MB
    except (TypeError, ValueError):  # pragma: no cover - defensive guard
        return 0.0


def _bundle_metrics(qpane: "QPane", consumer_id: str) -> _CacheMetricsBundle:
    """Collect snapshot metrics for the requested consumer when available."""
    if consumer_id == "tiles":
        manager = _stack_tile_manager(qpane)
    elif consumer_id == "pyramids":
        manager = _catalog_pyramid_manager(qpane)
    elif consumer_id == "mask_overlays":
        manager = qpane.mask_controller
    elif consumer_id == "predictors":
        manager = qpane.samManager()
    else:  # pragma: no cover - defensive guard
        manager = None
    if manager is None:
        if consumer_id not in _MISSING_DETAIL_MANAGER_LOGS:
            logger.warning(
                "Cache diagnostics missing manager for consumer %s",
                consumer_id,
            )
            _MISSING_DETAIL_MANAGER_LOGS.add(consumer_id)
        return _CacheMetricsBundle()
    try:
        snapshot = manager.snapshot_metrics()
    except Exception:  # pragma: no cover - defensive guard
        logger.exception(
            "Cache diagnostics failed to snapshot metrics for consumer %s",
            consumer_id,
        )
        return _CacheMetricsBundle()
    return _coerce_metrics(snapshot)


def _coerce_metrics(snapshot: object) -> _CacheMetricsBundle:
    """Convert snapshot objects into a unified cache metrics bundle."""
    try:
        from ..rendering.cache_metrics import CacheManagerMetrics
        from ..masks.mask_controller import MaskOverlayMetrics
        from ..sam.manager import SamPredictorMetrics
    except Exception:
        CacheManagerMetrics = MaskOverlayMetrics = SamPredictorMetrics = ()  # type: ignore[assignment]
    if isinstance(snapshot, CacheManagerMetrics):
        return _CacheMetricsBundle(
            cache_bytes=snapshot.cache_bytes,
            cache_limit=snapshot.cache_limit,
            hits=snapshot.hits,
            misses=snapshot.misses,
            evictions=snapshot.evictions,
            evicted_bytes=snapshot.evicted_bytes,
            pending_retries=snapshot.pending_retries,
            last_eviction_reason=snapshot.last_eviction_reason,
            last_eviction_timestamp=snapshot.last_eviction_timestamp,
            prefetch_requested=snapshot.prefetch_requested,
            prefetch_completed=snapshot.prefetch_completed,
            prefetch_failed=snapshot.prefetch_failed,
            last_prefetch_ms=snapshot.last_prefetch_ms,
        )
    if isinstance(snapshot, MaskOverlayMetrics):
        return _CacheMetricsBundle(
            cache_bytes=snapshot.cache_bytes,
            cache_limit=snapshot.cache_limit,
            hits=snapshot.hits,
            misses=snapshot.misses,
            evictions=snapshot.evictions,
            evicted_bytes=snapshot.evicted_bytes,
            pending_retries=snapshot.pending_retries,
            last_eviction_reason=snapshot.last_eviction_reason,
            last_eviction_timestamp=snapshot.last_eviction_timestamp,
            prefetch_requested=snapshot.prefetch_requested,
            prefetch_completed=snapshot.prefetch_completed,
            prefetch_failed=snapshot.prefetch_failed,
            last_prefetch_ms=snapshot.last_prefetch_ms,
            colorize_last_ms=snapshot.colorize_last_ms,
            colorize_avg_ms=snapshot.colorize_avg_ms,
            colorize_max_ms=snapshot.colorize_max_ms,
            colorize_samples=snapshot.colorize_samples,
            colorize_slow_count=snapshot.colorize_slow_count,
            colorize_threshold_ms=snapshot.colorize_threshold_ms,
            colorize_last_source=snapshot.colorize_last_source,
        )
    if isinstance(snapshot, SamPredictorMetrics):
        return _CacheMetricsBundle(
            cache_bytes=snapshot.cache_bytes,
            cache_limit=snapshot.cache_limit,
            hits=snapshot.hits,
            misses=snapshot.misses,
            evictions=snapshot.evictions,
            evicted_bytes=snapshot.evicted_bytes,
            pending_retries=snapshot.pending_retries,
            last_eviction_reason=snapshot.last_eviction_reason,
            last_eviction_timestamp=snapshot.last_eviction_timestamp,
            prefetch_requested=snapshot.prefetch_requested,
            prefetch_completed=snapshot.prefetch_completed,
            prefetch_failed=snapshot.prefetch_failed,
            last_prefetch_ms=snapshot.last_prefetch_ms,
        )
    return _CacheMetricsBundle()


def _format_counters(bundle: _CacheMetricsBundle) -> str:
    """Build a single-line summary from the optional metric bundle."""
    parts: list[str] = []
    if bundle.hits is not None and bundle.hits > 0:
        parts.append(f"hit={bundle.hits}")
    if bundle.misses is not None and bundle.misses > 0:
        parts.append(f"miss={bundle.misses}")
    if bundle.evictions is not None and bundle.evictions > 0:
        parts.append(f"evict={bundle.evictions}")
    if bundle.pending_retries:
        parts.append(f"retry={bundle.pending_retries}")
    if bundle.prefetch_requested:
        completed = bundle.prefetch_completed or 0
        parts.append(f"prefetch={completed}/{bundle.prefetch_requested}")
    elif bundle.prefetch_completed:
        parts.append(f"prefetch={bundle.prefetch_completed}")
    if bundle.prefetch_failed:
        parts.append(f"prefetch_fail={bundle.prefetch_failed}")
    if bundle.last_prefetch_ms and bundle.last_prefetch_ms > 0:
        parts.append(f"prefetch_ms={bundle.last_prefetch_ms:.0f}")
    if bundle.colorize_last_ms and bundle.colorize_last_ms > 0:
        suffix = (
            f"@{bundle.colorize_last_source}" if bundle.colorize_last_source else ""
        )
        parts.append(f"colorize={bundle.colorize_last_ms:.1f}ms{suffix}")
    if bundle.colorize_avg_ms and (bundle.colorize_samples or 0) > 1:
        parts.append(f"avg={bundle.colorize_avg_ms:.1f}ms")
    if bundle.colorize_max_ms and (bundle.colorize_max_ms != bundle.colorize_last_ms):
        parts.append(f"max={bundle.colorize_max_ms:.1f}ms")
    if bundle.colorize_samples:
        parts.append(f"samples={bundle.colorize_samples}")
    if bundle.colorize_slow_count:
        threshold = bundle.colorize_threshold_ms or 0.0
        parts.append(f"slow>{threshold:.0f}ms={bundle.colorize_slow_count}")
    return " ".join(parts)


def _stack_tile_manager(qpane: "QPane"):
    """Return the tile manager owned by the view or ``None`` when unavailable."""
    global _MISSING_TILE_VIEW_LOGGED, _MISSING_TILE_MANAGER_LOGGED
    try:
        view = qpane.view()
    except AttributeError:
        if not _MISSING_TILE_VIEW_LOGGED:
            logger.warning(
                "QPane view() unavailable; cache diagnostics missing tile manager"
            )
            _MISSING_TILE_VIEW_LOGGED = True
        return None
    try:
        manager = view.tile_manager
    except AttributeError:
        manager = None
    if manager is None and not _MISSING_TILE_MANAGER_LOGGED:
        logger.warning("View lacks tile_manager attribute for cache diagnostics")
        _MISSING_TILE_MANAGER_LOGGED = True
    return manager


def _catalog_pyramid_manager(qpane: "QPane"):
    """Return the pyramid manager surfaced by the catalog facade when available."""
    global _MISSING_CATALOG_LOGGED, _MISSING_PYRAMID_ACCESSOR_LOGGED
    try:
        catalog_facade = qpane.catalog()
    except AttributeError:
        if not _MISSING_CATALOG_LOGGED:
            logger.warning(
                "QPane catalog() unavailable; cache diagnostics missing pyramid manager"
            )
            _MISSING_CATALOG_LOGGED = True
        return None
    try:
        return catalog_facade.pyramidManager()
    except AttributeError:
        if not _MISSING_PYRAMID_ACCESSOR_LOGGED:
            logger.warning(
                "Catalog facade lacks pyramidManager accessor for cache diagnostics"
            )
            _MISSING_PYRAMID_ACCESSOR_LOGGED = True
        return None
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("Catalog.pyramidManager failed during cache diagnostics")
        return None
