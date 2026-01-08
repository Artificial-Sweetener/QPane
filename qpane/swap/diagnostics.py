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

"""Swap diagnostics provider for the QPane status overlay."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable

from ..types import DiagnosticRecord

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..qpane import QPane
    from .coordinator import SwapCoordinatorMetrics
logger = logging.getLogger(__name__)
_failure_logged: set[str] = set()


def swap_summary_provider(qpane: "QPane") -> Iterable[DiagnosticRecord]:
    """Return the navigation latency row for the core diagnostics tier."""
    metrics = _resolve_swap_metrics(qpane)
    if metrics is None:
        return tuple()
    summary = _format_swap_summary(metrics)
    if not summary:
        return tuple()
    return (DiagnosticRecord("Swap|Summary", summary),)


def swap_progress_provider(qpane: "QPane") -> Iterable[DiagnosticRecord]:
    """Return swap-related diagnostics rows sourced from the swap delegate."""
    metrics = _resolve_swap_metrics(qpane)
    if metrics is None:
        return tuple()
    rows: list[DiagnosticRecord] = []
    prefetch = _format_prefetch_line(metrics)
    if prefetch:
        rows.append(DiagnosticRecord("Swap|Prefetch", prefetch))
    renderer_line = _format_renderer_metrics(qpane)
    if renderer_line:
        rows.append(DiagnosticRecord("Swap|Renderer", renderer_line))
    mask_line = _format_mask_metrics(qpane)
    if mask_line:
        rows.append(DiagnosticRecord("Swap|Masks", mask_line))
    tile_line = _format_tile_metrics(qpane)
    if tile_line:
        rows.append(DiagnosticRecord("Swap|Tiles", tile_line))
    pyramid_line = _format_pyramid_metrics(qpane)
    if pyramid_line:
        rows.append(DiagnosticRecord("Swap|Pyramids", pyramid_line))
    sam_line = _format_sam_metrics(qpane)
    if sam_line:
        rows.append(DiagnosticRecord("Swap|SAM", sam_line))
    return tuple(rows)


def _resolve_swap_metrics(qpane: "QPane") -> "SwapCoordinatorMetrics" | None:
    """Return the swap metrics snapshot from the delegate when present."""
    try:
        delegate = qpane.swapDelegate
    except Exception as exc:  # pragma: no cover - defensive guard
        _log_snapshot_failure("swap_metrics", "Swap metrics delegate missing", exc)
        return None
    metrics = _safe_snapshot(
        delegate, label="swap_metrics", message="Swap metrics snapshot failed"
    )
    if metrics is None:
        return None
    _clear_snapshot_failure("swap_metrics")
    return metrics


def _format_swap_summary(metrics: "SwapCoordinatorMetrics") -> str:
    """Format the swap summary row emphasising last navigation latency."""
    last_nav = metrics.last_navigation_ms
    if last_nav is None or last_nav < 0:
        return "nav=-"
    return f"nav={last_nav:.0f}ms"


def _format_prefetch_line(metrics: "SwapCoordinatorMetrics") -> str:
    """Format pending prefetch counters for the diagnostics overlay."""
    parts: list[str] = []
    if metrics.pending_mask_prefetch > 0:
        parts.append(f"mask_prefetch={metrics.pending_mask_prefetch}")
    if metrics.pending_predictors > 0:
        parts.append(f"predictors={metrics.pending_predictors}")
    if metrics.pending_pyramid_prefetch > 0:
        parts.append(f"pyramids={metrics.pending_pyramid_prefetch}")
    if metrics.pending_tile_prefetch > 0:
        parts.append(f"tiles={metrics.pending_tile_prefetch}")
    return " | ".join(parts)


def _format_renderer_metrics(qpane: "QPane") -> str:
    """Build the renderer metrics row for the diagnostics overlay."""
    try:
        renderer = qpane.view().presenter.renderer
    except Exception as exc:  # pragma: no cover - defensive guard
        _log_snapshot_failure("renderer", "Renderer unavailable for diagnostics", exc)
        return ""
    snapshot = _safe_snapshot(
        renderer, label="renderer", message="Renderer metrics snapshot failed"
    )
    if snapshot is None:
        return ""
    _clear_snapshot_failure("renderer")
    parts: list[str] = []
    if snapshot.base_buffer_allocations > 0:
        parts.append(f"alloc={snapshot.base_buffer_allocations}")
    if snapshot.scroll_attempts >= 0:
        parts.append(f"scroll={snapshot.scroll_hits}/{snapshot.scroll_attempts}")
    if snapshot.scroll_misses > 0:
        parts.append(f"miss={snapshot.scroll_misses}")
    if snapshot.full_redraws or snapshot.partial_redraws:
        parts.append(f"redraws={snapshot.full_redraws}F/{snapshot.partial_redraws}P")
    if snapshot.last_paint_ms >= 0.0:
        parts.append(f"paint={snapshot.last_paint_ms:.0f}ms")
    return " | ".join(parts)


def _format_mask_metrics(qpane: "QPane") -> str:
    """Build the diagnostics row describing mask cache usage."""
    controller = qpane.mask_controller
    if controller is None:
        return ""
    snapshot = _safe_snapshot(
        controller, label="mask", message="Mask metrics snapshot failed"
    )
    if snapshot is None:
        return ""
    _clear_snapshot_failure("mask")
    usage_mb = _to_mb(snapshot.cache_bytes)
    parts = [f"usage={usage_mb:.1f}MB"]
    if snapshot.hits > 0:
        parts.append(f"hit={snapshot.hits}")
    if snapshot.misses > 0:
        parts.append(f"miss={snapshot.misses}")
    if snapshot.colorize_last_ms is not None and snapshot.colorize_last_ms > 0:
        parts.append(f"colorize={snapshot.colorize_last_ms:.0f}ms")
    return " | ".join(parts)


def _format_tile_metrics(qpane: "QPane") -> str:
    """Summarize tile cache usage and retry counts for diagnostics."""
    try:
        manager = qpane.view().tile_manager
    except Exception as exc:  # pragma: no cover - defensive guard
        _log_snapshot_failure("tiles", "Tile manager unavailable for diagnostics", exc)
        return ""
    snapshot = _safe_snapshot(
        manager, label="tiles", message="Tile metrics snapshot failed"
    )
    if snapshot is None:
        return ""
    _clear_snapshot_failure("tiles")
    usage_mb = _to_mb(snapshot.cache_bytes)
    limit_mb = _to_mb(snapshot.cache_limit)
    parts = [f"usage={usage_mb:.1f}/{limit_mb:.1f}MB"]
    if snapshot.hits > 0:
        parts.append(f"hit={snapshot.hits}")
    if snapshot.misses > 0:
        parts.append(f"miss={snapshot.misses}")
    if snapshot.pending_retries > 0:
        parts.append(f"retry={snapshot.pending_retries}")
    return " | ".join(parts)


def _format_pyramid_metrics(qpane: "QPane") -> str:
    """Summarize pyramid cache status for the diagnostics overlay."""
    try:
        manager = qpane.catalog().pyramid_manager
    except Exception as exc:  # pragma: no cover - defensive guard
        _log_snapshot_failure(
            "pyramid", "Pyramid manager unavailable for diagnostics", exc
        )
        return ""
    snapshot = _safe_snapshot(
        manager, label="pyramid", message="Pyramid metrics snapshot failed"
    )
    if snapshot is None:
        return ""
    _clear_snapshot_failure("pyramid")
    usage_mb = _to_mb(snapshot.cache_bytes)
    limit_mb = _to_mb(snapshot.cache_limit)
    parts = [f"usage={usage_mb:.1f}/{limit_mb:.1f}MB"]
    if snapshot.active_jobs > 0:
        parts.append(f"active={snapshot.active_jobs}")
    return " | ".join(parts)


def _format_sam_metrics(qpane: "QPane") -> str:
    """Format cache stats for the SAM predictor workflow."""
    manager = qpane.samManager()
    if manager is None:
        return ""
    snapshot = _safe_snapshot(
        manager, label="sam", message="SAM metrics snapshot failed"
    )
    if snapshot is None:
        return ""
    _clear_snapshot_failure("sam")
    usage_mb = _to_mb(snapshot.cache_bytes)
    parts = [f"usage={usage_mb:.1f}MB"]
    parts.append(f"cached={snapshot.cache_count}")
    if snapshot.pending_retries > 0:
        parts.append(f"retry={snapshot.pending_retries}")
    return " | ".join(parts)


_MB = 1024 * 1024


def _to_mb(value: int | float) -> float:
    """Convert byte counts to megabytes for diagnostics display."""
    return float(value) / _MB if value else 0.0


def _safe_snapshot(provider, *, label: str, message: str):
    """Call snapshot_metrics on ``provider`` and guard against failures."""
    try:
        return provider.snapshot_metrics()
    except Exception as exc:  # pragma: no cover - defensive guard
        _log_snapshot_failure(label, message, exc)
        return None


def _log_snapshot_failure(label: str, message: str, exc: BaseException) -> None:
    """Emit a once-per-failure diagnostic when a snapshot hook raises."""
    if label in _failure_logged:
        return
    _failure_logged.add(label)
    logger.exception("%s", message, exc_info=exc)


def _clear_snapshot_failure(label: str) -> None:
    """Reset failure tracking once a snapshot hook succeeds again."""
    _failure_logged.discard(label)
