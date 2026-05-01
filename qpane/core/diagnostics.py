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

"""Diagnostics primitives used by QPane overlays."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..types import DiagnosticRecord

if TYPE_CHECKING:  # pragma: no cover - only needed for static analysis
    from ..qpane import QPane
    from ..scene.render_plan import RasterLayerRenderItem, SceneRenderPlan
else:  # pragma: no cover - fallback for runtime to avoid cycles
    RasterLayerRenderItem = Any
    SceneRenderPlan = Any
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiagnosticsSnapshot:
    """Immutable collection of diagnostic records for a QPane overlay."""

    records: tuple[DiagnosticRecord, ...]

    def rows(self) -> tuple[tuple[str, str], ...]:
        """Return the diagnostics as ordered (label, value) tuples."""
        return tuple((record.label, record.value) for record in self.records)

    def renderStrings(self) -> tuple[str, ...]:
        """Return formatted diagnostics preserving provider order."""
        return tuple(record.formatted() for record in self.records)


DiagnosticsProvider = Callable[["QPane"], Iterable[DiagnosticRecord]]
"""Callable that can contribute diagnostic records for a QPane instance."""


class DiagnosticsRegistry:
    """Track diagnostics providers and collect their output safely."""

    def __init__(self, qpane: "QPane") -> None:
        """Initialize the registry with the owning QPane reference."""
        self._qpane = qpane
        self._providers: list[tuple[int, DiagnosticsProvider]] = []
        self._provider_failures: dict[DiagnosticsProvider, str] = {}
        self._registered_priorities: dict[DiagnosticsProvider, int] = {}

    def register(self, provider: DiagnosticsProvider, *, priority: int = 0) -> None:
        """Register a new provider if it is not already tracked.

        Args:
            provider: Callable that yields diagnostics rows for the owning qpane.
            priority: Ordering key used when rendering diagnostics; lower values
                appear earlier in the overlay.
        """
        if provider in self._registered_priorities:
            return
        self._registered_priorities[provider] = priority
        self._providers.append((priority, provider))
        self._providers.sort(key=lambda item: item[0])

    def gather(self) -> DiagnosticsSnapshot:
        """Collect diagnostics from each provider while logging failures once per signature."""
        entries: list[DiagnosticRecord] = []
        for _priority, provider in self._providers:
            try:
                candidate = provider(self._qpane)
            except Exception as exc:  # pragma: no cover - defensive guard
                self._record_failure(provider, "call", exc)
                continue
            provider_entries: list[DiagnosticRecord] = []
            try:
                for record in candidate:
                    if not isinstance(record, DiagnosticRecord):
                        raise TypeError(
                            "Diagnostics providers must yield DiagnosticRecord instances"
                        )
                    provider_entries.append(record)
            except TypeError as exc:
                self._record_failure(provider, "type", exc)
                continue
            except Exception as exc:  # pragma: no cover - defensive guard
                self._record_failure(provider, "iteration", exc)
                continue
            self._provider_failures.pop(provider, None)
            entries.extend(provider_entries)
        return DiagnosticsSnapshot(tuple(entries))

    def providers(self) -> tuple[DiagnosticsProvider, ...]:
        """Return the registered providers for inspection/testing."""
        return tuple(provider for _priority, provider in self._providers)

    # Internal helpers
    def _record_failure(
        self, provider: DiagnosticsProvider, stage: str, exc: Exception
    ) -> None:
        """Remember ``provider`` failures so duplicate warnings are suppressed."""
        signature = f"{stage}:{type(exc).__name__}:{exc}"
        previous = self._provider_failures.get(provider)
        if previous == signature:
            return
        self._provider_failures[provider] = signature
        logger.warning(
            "Diagnostics provider %s failed during %s stage: %s",
            self._describe_provider(provider),
            stage,
            exc,
            exc_info=stage != "type",
        )

    @staticmethod
    def _describe_provider(provider: DiagnosticsProvider) -> str:
        """Return a human-readable identifier for ``provider``."""
        name = getattr(provider, "__qualname__", None) or getattr(
            provider, "__name__", None
        )
        if name:
            return name
        return repr(provider)


def build_core_diagnostics(
    *,
    renderer: Any = None,
    viewport: Any = None,
    tile_manager: Any = None,
    pyramid_manager: Any = None,
    cache_snapshot: dict[str, object] | None = None,
) -> tuple[DiagnosticRecord, ...]:
    """Build diagnostics rows for available render and cache collaborators.

    Args:
        renderer: Renderer that exposes paint timing and render-state helpers.
        viewport: Viewport used to infer zoom percentage.
        tile_manager: Source for tile cache usage and limits. (unused)
        pyramid_manager: Source for pyramid cache usage and limits. (unused)
        cache_snapshot: Optional cache coordinator snapshot used to display the
            aggregate cache budget and usage without recomputing it in the
            provider.

    Returns:
        A tuple of :class:`DiagnosticRecord` entries for the collaborators that
        were supplied.
    """
    records: list[DiagnosticRecord] = []
    render_plan: SceneRenderPlan | None = None
    if renderer is not None:
        last_ms, average_ms, max_ms = renderer.paint_stats()
        paint_parts = [f"{last_ms:.1f} ms"]
        if average_ms > 0.0:
            paint_parts.append(f"avg={average_ms:.1f}")
        if max_ms > 0.0:
            paint_parts.append(f"max={max_ms:.1f}")
        records.append(DiagnosticRecord("Paint", " ".join(paint_parts)))
        render_plan = renderer.get_current_render_plan()
    if cache_snapshot is not None:
        aggregate = _aggregate_cache_record(cache_snapshot)
        if aggregate is not None:
            records.append(aggregate)
    if viewport is not None:
        records.append(DiagnosticRecord("Zoom", f"{viewport.zoom * 100:.1f}%"))
        smooth_zoom = None
        smooth_zoom_fn = getattr(viewport, "smooth_zoom_diagnostics", None)
        if callable(smooth_zoom_fn):
            smooth_zoom = smooth_zoom_fn()
        if smooth_zoom is not None:
            fps_label = f"{smooth_zoom.fps_hz:.0f}Hz"
            if smooth_zoom.using_fallback_fps:
                fps_label = f"{fps_label} (Fallback)"
            records.append(DiagnosticRecord("Smooth Zoom FPS", fps_label))
            frame_label = f"{smooth_zoom.frame_ms:.1f}ms ({smooth_zoom.mode})"
            records.append(DiagnosticRecord("Smooth Zoom Frame", frame_label))
    if render_plan is not None:
        pyramid_record = pyramid_level_record(render_plan)
        if pyramid_record is not None:
            records.append(pyramid_record)
    return tuple(records)


def pyramid_level_record(
    plan_or_item: SceneRenderPlan | RasterLayerRenderItem,
) -> DiagnosticRecord | None:
    """Describe the rendered pyramid level when plan and source images are valid."""
    item = _base_raster_item(plan_or_item)
    if item is None:
        return None
    source_image = item.source_image
    if source_image.isNull():
        return None
    source_width = source_image.width()
    if source_width <= 0:
        return None
    base_width = 0
    if _is_scene_render_plan(plan_or_item):
        base_width = plan_or_item.content_snapshot.base_image_size.width()
    scale = item.pyramid_scale
    has_scale = scale > 0
    is_native = has_scale and abs(scale - 1.0) < 1e-3
    if base_width > 0 and has_scale and not is_native:
        scale_text = f"{scale:.3f}x"
        level_text = f"{source_width}px of {base_width}px ({scale_text})"
    elif base_width > 0 and is_native:
        level_text = f"{source_width}px of {base_width}px (native)"
    elif has_scale and not is_native:
        level_text = f"{source_width}px ({scale:.3f}x)"
    elif is_native:
        level_text = f"{source_width}px (native)"
    else:
        level_text = f"{source_width}px"
    return DiagnosticRecord("Pyramid Level", level_text)


def _base_raster_item(
    plan_or_item: SceneRenderPlan | RasterLayerRenderItem,
) -> RasterLayerRenderItem | None:
    """Return a raster item from either a render plan or a direct item."""
    direct_source = getattr(plan_or_item, "source_image", None)
    if direct_source is not None:
        return plan_or_item
    base_item = getattr(plan_or_item, "base_raster_item", None)
    return base_item if base_item is not None else None


def _is_scene_render_plan(
    plan_or_item: SceneRenderPlan | RasterLayerRenderItem,
) -> bool:
    """Return True when ``plan_or_item`` carries scene-level content geometry."""
    return getattr(plan_or_item, "content_snapshot", None) is not None


def _aggregate_cache_record(snapshot: dict[str, object]) -> DiagnosticRecord | None:
    """Return the aggregate cache usage/budget record if valid inputs exist."""
    budget_bytes = int(snapshot.get("budget_bytes", 0))
    usage_bytes = int(snapshot.get("usage_bytes", 0))
    hard_cap = bool(snapshot.get("hard_cap"))
    if budget_bytes <= 0:
        return DiagnosticRecord("Cache", "0.0/0.0 MB (0%)")
    budget_mb = budget_bytes / (1024 * 1024)
    usage_mb = usage_bytes / (1024 * 1024)
    utilization = usage_mb / budget_mb if budget_mb else 0.0
    label = "Cache"
    value = f"{usage_mb:.1f}/{budget_mb:.1f} MB ({utilization * 100:.0f}%)"
    if hard_cap:
        value = f"{value} | (hard)"
    return DiagnosticRecord(label, value)
