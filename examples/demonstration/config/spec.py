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

"""FieldSpec catalog for the QPane example config dialog grouped by feature domain.

Add a control by declaring a :class:`FieldSpec`, grouping it under a :class:`FieldGroupSpec`,
and including it in the section builder. Feature-gate fields by setting ``feature_namespace``
so mask/SAM-only controls hide when those extras are unavailable.

Extension seams:
- Group fields by domain (viewer basics, cache/prefetch, masks, SAM, diagnostics, concurrency).
- Keep new fields aligned with the Config tree; wire them through ``config/dialog.py``.
- Use ``feature_namespace`` to hide fields when features are inactive.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Iterator, Literal, Mapping, Sequence

from qpane import (
    CacheMode,
    Config,
    DiagnosticsDomain,
    PlaceholderScaleMode,
    ZoomMode,
)


@dataclass(frozen=True)
class FieldSpec:
    """Describe a single config dialog field and its editor metadata."""

    path: str
    kind: Literal[
        "spin", "double", "checkbox", "line", "combo", "path", "size", "multicheck"
    ]
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | int | None = None
    decimals: int | None = None
    suffix: str | None = None
    placeholder: str | None = None
    tooltip: str | None = None
    label: str | None = None
    special_value_text: str | None = None
    description: str | None = None
    internal: bool = False
    feature_namespace: str | None = None
    options: tuple[str, ...] | None = None


@dataclass(frozen=True)
class FieldGroupSpec:
    """Collect related field specs under a shared heading."""

    title: str
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class SectionSpec:
    """Represent a tab or major section within the dialog."""

    title: str
    groups: tuple[FieldGroupSpec, ...]


CACHE_MODE_OPTIONS: tuple[str, ...] = tuple(mode.value for mode in CacheMode)
PLACEHOLDER_SCALE_OPTIONS: tuple[str, ...] = tuple(
    mode.value for mode in PlaceholderScaleMode
)
PLACEHOLDER_ZOOM_OPTIONS: tuple[str, ...] = tuple(mode.value for mode in ZoomMode)
SAM_DOWNLOAD_MODE_OPTIONS: tuple[str, ...] = ("background", "blocking", "disabled")
DIAGNOSTIC_DOMAIN_OPTIONS: tuple[tuple[str, str, str, str | None], ...] = (
    (
        DiagnosticsDomain.CACHE.value,
        "Cache",
        "Expose cache detail overlays alongside summary counters.",
        "diagnostics",
    ),
    (
        DiagnosticsDomain.SWAP.value,
        "Swap",
        "Show swap loader status, prefetch state, and render paths.",
        "diagnostics",
    ),
    (
        DiagnosticsDomain.MASK.value,
        "Mask",
        "Show mask job diagnostics along with summary rows.",
        "mask",
    ),
    (
        DiagnosticsDomain.EXECUTOR.value,
        "Executor",
        "Show executor utilization statistics in the overlay.",
        "diagnostics",
    ),
    (
        DiagnosticsDomain.RETRY.value,
        "Retry",
        "Show per-category retry statistics.",
        "diagnostics",
    ),
    (
        DiagnosticsDomain.SAM.value,
        "SAM",
        "Show SAM worker pool diagnostics.",
        "sam",
    ),
)


VIEWER_CACHE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="cache.mode",
        kind="combo",
        options=CACHE_MODE_OPTIONS,
        tooltip="Select auto (headroom-driven) or hard (fixed) cache budgeting.",
        label="Cache Mode",
    ),
    FieldSpec(
        path="cache.headroom_percent",
        kind="double",
        minimum=0.0,
        maximum=0.9,
        step=0.01,
        decimals=2,
        suffix="",
        tooltip="Fraction of system memory reserved as headroom in auto mode.",
        label="Headroom Fraction",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.headroom_cap_mb",
        kind="spin",
        minimum=0,
        maximum=65536,
        step=64,
        suffix=" MB",
        tooltip="Maximum MB held aside as headroom in auto mode.",
        label="Headroom Cap (MB)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.budget_mb",
        kind="spin",
        minimum=0,
        maximum=65536,
        step=64,
        suffix=" MB",
        tooltip="Fixed cache budget in hard mode.",
        label="Hard Cap (MB)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.weights.tiles",
        kind="spin",
        minimum=0,
        maximum=100,
        step=1,
        suffix=" %",
        tooltip="Weight of the active budget allocated to tiles.",
        label="Tile Weight (%)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.weights.pyramids",
        kind="spin",
        minimum=0,
        maximum=100,
        step=1,
        suffix=" %",
        tooltip="Weight of the active budget allocated to pyramids.",
        label="Pyramid Weight (%)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.weights.masks",
        kind="spin",
        minimum=0,
        maximum=100,
        step=1,
        suffix=" %",
        tooltip="Weight of the active budget allocated to mask overlays.",
        label="Mask Weight (%)",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="cache.weights.predictors",
        kind="spin",
        minimum=0,
        maximum=100,
        step=1,
        suffix=" %",
        tooltip="Weight of the active budget allocated to SAM predictors.",
        label="Predictor Weight (%)",
        feature_namespace="sam",
    ),
    FieldSpec(
        path="cache.tiles.mb",
        kind="spin",
        minimum=-1,
        maximum=32768,
        step=64,
        suffix=" MB",
        tooltip="Explicit tile cache budget (-1 = auto via weights).",
        label="Tile Override (MB)",
        special_value_text="Auto (weight)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.pyramids.mb",
        kind="spin",
        minimum=-1,
        maximum=32768,
        step=64,
        suffix=" MB",
        tooltip="Explicit pyramid cache budget (-1 = auto via weights).",
        label="Pyramid Override (MB)",
        special_value_text="Auto (weight)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.masks.mb",
        kind="spin",
        minimum=-1,
        maximum=8192,
        step=64,
        suffix=" MB",
        tooltip="Explicit mask overlay cache budget (-1 = auto via weights).",
        label="Mask Override (MB)",
        special_value_text="Auto (weight)",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="cache.predictors.mb",
        kind="spin",
        minimum=-1,
        maximum=8192,
        step=64,
        suffix=" MB",
        tooltip="Explicit SAM predictor cache budget (-1 = auto via weights).",
        label="Predictor Override (MB)",
        special_value_text="Auto (weight)",
        feature_namespace="sam",
    ),
)
VIEWER_PREFETCH_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="cache.prefetch.pyramids",
        kind="spin",
        minimum=-1,
        maximum=8,
        step=1,
        suffix=" neighbors",
        tooltip="Neighbor depth for pyramid prefetch (-1 = all neighbors, 0 = disabled).",
        label="Pyramid Prefetch Depth",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.prefetch.tiles",
        kind="spin",
        minimum=-1,
        maximum=8,
        step=1,
        suffix=" neighbors",
        tooltip="Neighbor depth for tile prefetch (-1 = all neighbors, 0 = disabled).",
        label="Tile Prefetch Depth",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.prefetch.tiles_per_neighbor",
        kind="spin",
        minimum=0,
        maximum=16,
        step=1,
        suffix=" tiles",
        tooltip="Number of tiles fetched per neighbor during tile prefetch (0 disables tile prefetch).",
        label="Tiles Per Neighbor",
        feature_namespace="core",
    ),
    FieldSpec(
        path="cache.prefetch.masks",
        kind="spin",
        minimum=-1,
        maximum=8,
        step=1,
        suffix=" neighbors",
        tooltip="Neighbor depth for mask prefetch (-1 = all neighbors, 0 = disabled).",
        label="Mask Prefetch Depth",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="cache.prefetch.predictors",
        kind="spin",
        minimum=-1,
        maximum=8,
        step=1,
        suffix=" neighbors",
        tooltip="Neighbor depth for predictor prefetch (-1 = all neighbors, 0 = disabled).",
        label="Predictor Prefetch Depth",
        feature_namespace="sam",
    ),
)
VIEWER_VIEWPORT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="tile_size",
        kind="spin",
        minimum=64,
        maximum=4096,
        step=64,
        tooltip="Edge length for generated tiles in pixels.",
        label="Tile Size (px)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="tile_overlap",
        kind="spin",
        minimum=0,
        maximum=512,
        step=4,
        tooltip="Overlap between neighbouring tiles to avoid seams.",
        label="Tile Overlap (px)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="min_view_size_px",
        kind="spin",
        minimum=32,
        maximum=2048,
        step=32,
        tooltip="Fits-to-view keeps this many pixels visible.",
        label="Minimum View Size (px)",
        feature_namespace="core",
    ),
    FieldSpec(
        path="canvas_expansion_factor",
        kind="double",
        minimum=1.0,
        maximum=4.0,
        step=0.1,
        decimals=2,
        tooltip="Canvas may pan slightly beyond the viewport.",
        label="Canvas Expansion Factor",
        feature_namespace="core",
    ),
    FieldSpec(
        path="safe_min_zoom",
        kind="double",
        minimum=0.000001,
        maximum=0.1,
        step=0.0005,
        decimals=6,
        tooltip="Zoom values never drop below this stability floor.",
        label="Safe Minimum Zoom",
        feature_namespace="core",
    ),
    FieldSpec(
        path="drag_out_enabled",
        kind="checkbox",
        tooltip="Allow dragging the current image out to other applications.",
        label="Enable Drag-Out",
        feature_namespace="core",
    ),
    FieldSpec(
        path="normalize_zoom_on_screen_change",
        kind="checkbox",
        tooltip=(
            "Scale zoom and pan when moving the window between monitors with different"
            " device pixel ratios so the viewport covers the same portion of the image."
        ),
        label="Normalize Zoom Across Monitors",
        feature_namespace="core",
    ),
    FieldSpec(
        path="normalize_zoom_for_one_to_one",
        kind="checkbox",
        tooltip=(
            "Apply the same normalization when the viewport runs in 1:1 mode so "
            "native pixel zoom adjusts across monitors."
        ),
        label="Normalize 1:1 Zoom",
        feature_namespace="core",
    ),
)
VIEWER_SMOOTH_ZOOM_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="smooth_zoom_enabled",
        kind="checkbox",
        tooltip="Animate zoom transitions for wheel and double-click navigation.",
        label="Enable Smooth Zoom",
        feature_namespace="core",
    ),
    FieldSpec(
        path="smooth_zoom_duration_ms",
        kind="spin",
        minimum=0,
        maximum=500,
        step=5,
        suffix=" ms",
        tooltip=(
            "Animation duration for normal zoom steps. If shorter than one frame at "
            "the chosen FPS, the zoom applies immediately."
        ),
        label="Zoom Duration",
        feature_namespace="core",
    ),
    FieldSpec(
        path="smooth_zoom_burst_duration_ms",
        kind="spin",
        minimum=0,
        maximum=500,
        step=5,
        suffix=" ms",
        tooltip=(
            "Shorter duration used during rapid wheel bursts. If shorter than one "
            "frame at the chosen FPS, the zoom applies immediately."
        ),
        label="Burst Duration",
        feature_namespace="core",
    ),
    FieldSpec(
        path="smooth_zoom_burst_threshold_ms",
        kind="spin",
        minimum=0,
        maximum=250,
        step=5,
        suffix=" ms",
        tooltip="Wheel events closer than this count as a burst.",
        label="Burst Threshold",
        feature_namespace="core",
    ),
    FieldSpec(
        path="smooth_zoom_use_display_fps",
        kind="checkbox",
        tooltip=(
            "When enabled, target the monitor refresh rate and fall back to the "
            "fallback FPS if detection fails."
        ),
        label="Use Display Refresh Rate",
        feature_namespace="core",
    ),
    FieldSpec(
        path="smooth_zoom_fallback_fps",
        kind="double",
        minimum=1.0,
        maximum=360.0,
        step=1.0,
        decimals=1,
        suffix=" fps",
        tooltip="Fallback FPS used when display refresh isn't available.",
        label="Fallback FPS",
        feature_namespace="core",
    ),
)
PLACEHOLDER_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="placeholder.source",
        kind="path",
        placeholder=":/app/placeholder.png or C:/path/to/image.png",
        tooltip="Resource (qrc) URL or filesystem path for the idle placeholder. Leave blank to disable.",
        label="Placeholder Source",
    ),
    FieldSpec(
        path="placeholder.scale_mode",
        kind="combo",
        options=PLACEHOLDER_SCALE_OPTIONS,
        tooltip="How placeholder size is interpreted. Auto uses image pixels; logical_fit uses Qt logical pixels; physical_fit uses screen device pixels; relative_fit scales the auto-fit zoom.",
        label="Placeholder Sizing",
    ),
    FieldSpec(
        path="placeholder.panzoom_enabled",
        kind="checkbox",
        tooltip="Allow pan/zoom while the placeholder is displayed.",
        label="Pan/Zoom Enabled",
    ),
    FieldSpec(
        path="placeholder.zoom_mode",
        kind="combo",
        options=PLACEHOLDER_ZOOM_OPTIONS,
        tooltip="Zoom policy to apply to the placeholder image.",
        label="Zoom Mode",
    ),
    FieldSpec(
        path="placeholder.locked_zoom",
        kind="double",
        minimum=0.0,
        maximum=20.0,
        step=0.05,
        decimals=3,
        tooltip="Zoom value used when zoom mode is locked_zoom (set to Disabled to clear).",
        label="Locked Zoom",
        special_value_text="Disabled",
    ),
    FieldSpec(
        path="placeholder.scale_factor",
        kind="double",
        minimum=0.01,
        maximum=10.0,
        step=0.05,
        decimals=3,
        tooltip="Multiplier applied to the auto-fit zoom when sizing mode is relative_fit (1.0 = fit).",
        label="Scale Factor",
    ),
    FieldSpec(
        path="placeholder.display_size",
        kind="size",
        minimum=1,
        maximum=32768,
        step=1,
        tooltip="Target width/height used when sizing is logical/physical. Units match the sizing mode.",
        label="Target Size (w × h)",
    ),
    FieldSpec(
        path="placeholder.min_display_size",
        kind="size",
        minimum=1,
        maximum=32768,
        step=1,
        tooltip="Minimum on-screen size when sizing is logical/physical. Units match the sizing mode.",
        label="Min Size (w × h)",
    ),
    FieldSpec(
        path="placeholder.max_display_size",
        kind="size",
        minimum=1,
        maximum=32768,
        step=1,
        tooltip="Maximum on-screen size when sizing is logical/physical. Units match the sizing mode.",
        label="Max Size (w × h)",
    ),
    FieldSpec(
        path="placeholder.locked_size",
        kind="size",
        minimum=1,
        maximum=32768,
        step=1,
        tooltip="Target width/height when zoom mode is locked_size.",
        label="Locked Size (px)",
    ),
    FieldSpec(
        path="placeholder.drag_out_enabled",
        kind="checkbox",
        tooltip="Permit drag-out while the placeholder is visible.",
        label="Drag-out Enabled",
    ),
)
MASK_EDITING_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="default_brush_size",
        kind="spin",
        minimum=1,
        maximum=512,
        step=1,
        suffix=" px",
        tooltip="Brush radius when mask tools activate.",
        label="Default Brush Size",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="brush_scroll_increment",
        kind="spin",
        minimum=1,
        maximum=128,
        step=1,
        tooltip="Brush size delta applied per scroll notch.",
        label="Brush Scroll Increment",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="mask_undo_limit",
        kind="spin",
        minimum=1,
        maximum=200,
        step=1,
        tooltip="Mask undo history depth retained per image.",
        label="Mask Undo Limit",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="smart_select_min_size",
        kind="spin",
        minimum=1,
        maximum=256,
        step=1,
        suffix=" px",
        tooltip="Smallest region Smart Select will keep.",
        label="Smart Select Minimum Size",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="mask_border_enabled",
        kind="checkbox",
        tooltip="Draw a contrasting outline around masks for clarity.",
        label="Show Mask Border",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="mask_prefetch_enabled",
        kind="checkbox",
        tooltip="Prefetch mask overlays for upcoming images to reduce latency.",
        label="Prefetch Masks",
        feature_namespace="mask",
    ),
)
MASK_AUTOSAVE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="mask_autosave_enabled",
        kind="checkbox",
        tooltip="Automatically persist masks after edits.",
        label="Enable Autosave",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="mask_autosave_on_creation",
        kind="checkbox",
        tooltip="Autosave right after creating a mask.",
        label="Autosave On Creation",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="mask_autosave_debounce_ms",
        kind="spin",
        minimum=100,
        maximum=10000,
        step=100,
        suffix=" ms",
        tooltip="Delay before autosave runs once editing stops.",
        label="Autosave Debounce",
        feature_namespace="mask",
    ),
    FieldSpec(
        path="mask_autosave_path_template",
        kind="line",
        placeholder="./saved_masks/{image_name}-{mask_id}.png",
        tooltip="Template for autosave file paths. Use {image_name} and {mask_id} placeholders.",
        label="Autosave Path Template",
        feature_namespace="mask",
    ),
)
SAM_CHECKPOINT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="sam_download_mode",
        kind="combo",
        options=SAM_DOWNLOAD_MODE_OPTIONS,
        tooltip=(
            "Choose how SAM checkpoints are acquired: background download, blocking "
            "download, or disabled (host must provide the file)."
        ),
        label="SAM Download Mode",
        feature_namespace="sam",
    ),
    FieldSpec(
        path="sam_model_path",
        kind="path",
        placeholder="C:/path/to/mobile_sam.pt",
        tooltip=(
            "Optional checkpoint path override; leave blank to use "
            "QStandardPaths.AppDataLocation/mobile_sam.pt."
        ),
        label="SAM Model Path",
        feature_namespace="sam",
    ),
    FieldSpec(
        path="sam_model_url",
        kind="line",
        placeholder="https://.../mobile_sam.pt",
        tooltip="URL to download the MobileSAM checkpoint when downloads are enabled.",
        label="SAM Model URL",
        feature_namespace="sam",
    ),
    FieldSpec(
        path="sam_model_hash",
        kind="line",
        placeholder="default or sha256",
        tooltip=(
            "Optional SHA-256 hash for checkpoint verification; "
            "use 'default' to apply the built-in MobileSAM hash."
        ),
        label="SAM Model Hash",
        feature_namespace="sam",
    ),
)
DIAGNOSTIC_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="diagnostics_overlay_enabled",
        kind="checkbox",
        tooltip="Show the diagnostics overlay in the viewer.",
        label="Enable Overlay",
        feature_namespace="diagnostics",
    ),
    FieldSpec(
        path="diagnostics_domains_enabled",
        kind="multicheck",
        tooltip="Select which diagnostics domains expose detail-tier overlays.",
        label="Detail Domains",
        feature_namespace="diagnostics",
        options=tuple(option[0] for option in DIAGNOSTIC_DOMAIN_OPTIONS),
    ),
    FieldSpec(
        path="draw_tile_grid",
        kind="checkbox",
        tooltip="Show debug grid lines that outline rendered tiles.",
        label="Draw Tile Grid",
        feature_namespace="diagnostics",
    ),
)
CONCURRENCY_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        path="concurrency_max_workers",
        kind="spin",
        minimum=1,
        maximum=128,
        step=1,
        tooltip="Number of worker threads for background tasks.",
        label="Max Workers",
        internal=True,
        feature_namespace="core",
    ),
    FieldSpec(
        path="concurrency_max_pending_total",
        kind="spin",
        minimum=0,
        maximum=10000,
        step=1,
        tooltip="Upper bound for total pending queue (0 = unbounded).",
        label="Max Pending Total",
        internal=True,
        feature_namespace="core",
    ),
)
CONFIG_DIALOG_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        title="Viewer",
        groups=(
            FieldGroupSpec(title="Cache Budgets", fields=VIEWER_CACHE_FIELDS),
            FieldGroupSpec(title="Prefetching", fields=VIEWER_PREFETCH_FIELDS),
            FieldGroupSpec(title="Viewport", fields=VIEWER_VIEWPORT_FIELDS),
            FieldGroupSpec(title="Smooth Zoom", fields=VIEWER_SMOOTH_ZOOM_FIELDS),
            FieldGroupSpec(title="Placeholder", fields=PLACEHOLDER_FIELDS),
        ),
    ),
    SectionSpec(
        title="Masks",
        groups=(
            FieldGroupSpec(title="Editing", fields=MASK_EDITING_FIELDS),
            FieldGroupSpec(title="Autosave", fields=MASK_AUTOSAVE_FIELDS),
        ),
    ),
    SectionSpec(
        title="SAM",
        groups=(FieldGroupSpec(title="Checkpoint", fields=SAM_CHECKPOINT_FIELDS),),
    ),
    SectionSpec(
        title="Diagnostics",
        groups=(FieldGroupSpec(title="Overlay & Logging", fields=DIAGNOSTIC_FIELDS),),
    ),
    SectionSpec(
        title="Concurrency",
        groups=(FieldGroupSpec(title="Executor Settings", fields=CONCURRENCY_FIELDS),),
    ),
)


def iter_field_specs(
    sections: Iterable[SectionSpec] | None = None,
) -> Iterator[FieldSpec]:
    """Yield field specs from the provided sections in display order."""
    source = CONFIG_DIALOG_SECTIONS if sections is None else tuple(sections)
    for section in source:
        for group in section.groups:
            for spec in group.fields:
                yield spec


def active_namespaces_for_features(
    active_features: Sequence[str] | None,
    descriptors: Mapping[str, object] | None = None,
) -> set[str]:
    """Return config namespaces enabled for the provided feature selection."""
    namespace_map = descriptors or Config.feature_descriptors()  # type: ignore[arg-type]
    if active_features is None:
        return {name for name in namespace_map}
    features = set(active_features)
    active: set[str] = set()
    for name, descriptor in namespace_map.items():
        requires = getattr(descriptor, "requires", ())
        if not requires or all(req in features for req in requires):
            active.add(name)
    return active


def filter_sections_for_features(
    active_features: Sequence[str] | None = None,
    *,
    descriptors: Mapping[str, object] | None = None,
    sections: Iterable[SectionSpec] | None = None,
) -> tuple[SectionSpec, ...]:
    """Filter sections and fields so only active feature namespaces remain."""
    source_sections = tuple(CONFIG_DIALOG_SECTIONS if sections is None else sections)
    active_namespaces = active_namespaces_for_features(active_features, descriptors)
    filtered_sections: list[SectionSpec] = []
    for section in source_sections:
        filtered_groups: list[FieldGroupSpec] = []
        for group in section.groups:
            filtered_fields = tuple(
                spec
                for spec in group.fields
                if spec.feature_namespace is None
                or spec.feature_namespace in active_namespaces
            )
            if filtered_fields:
                filtered_groups.append(
                    FieldGroupSpec(title=group.title, fields=filtered_fields)
                )
        if filtered_groups:
            filtered_sections.append(
                SectionSpec(title=section.title, groups=tuple(filtered_groups))
            )
    return tuple(filtered_sections)


def build_sections_for_features(
    active_features: Sequence[str] | None = None,
    *,
    descriptors: Mapping[str, object] | None = None,
) -> tuple[SectionSpec, ...]:
    """Return descriptor-filtered sections for the provided feature set."""
    return filter_sections_for_features(
        active_features, descriptors=descriptors, sections=CONFIG_DIALOG_SECTIONS
    )


def field_sets_for_sections(
    sections: Iterable[SectionSpec],
) -> tuple[set[str], set[str], dict[str, FieldSpec]]:
    """Return ``(all_fields, config_fields, spec_by_path)`` for sections."""
    specs = tuple(iter_field_specs(sections))
    spec_map = {spec.path: spec for spec in specs}
    all_fields = set(spec_map.keys())
    additional_internal: set[str] = {
        "concurrency_category_priorities",
        "concurrency_category_limits",
        "concurrency_pending_limits",
        "concurrency_device_limits",
    }
    internal_only = {path for path, spec in spec_map.items() if spec.internal}
    internal_only.update(additional_internal)
    config_fields = {path for path in all_fields if path not in internal_only}
    return all_fields, config_fields, spec_map


FIELD_SPECS_BY_PATH: dict[str, FieldSpec] = {
    spec.path: spec for spec in iter_field_specs()
}
_ALL_FIELDS: set[str] = set(FIELD_SPECS_BY_PATH.keys())
_ADDITIONAL_INTERNAL_ONLY_FIELDS: set[str] = {
    "concurrency_category_priorities",
    "concurrency_category_limits",
    "concurrency_pending_limits",
    "concurrency_device_limits",
}
_INTERNAL_ONLY_FIELDS: set[str] = {
    path for path, spec in FIELD_SPECS_BY_PATH.items() if spec.internal
}
_INTERNAL_ONLY_FIELDS.update(_ADDITIONAL_INTERNAL_ONLY_FIELDS)
_CONFIG_FIELDS: set[str] = {
    path for path in _ALL_FIELDS if path not in _INTERNAL_ONLY_FIELDS
}
__all__ = [
    "FieldSpec",
    "FieldGroupSpec",
    "SectionSpec",
    "CONFIG_DIALOG_SECTIONS",
    "build_sections_for_features",
    "field_sets_for_sections",
    "filter_sections_for_features",
    "FIELD_SPECS_BY_PATH",
    "iter_field_specs",
    "_INTERNAL_ONLY_FIELDS",
    "_ALL_FIELDS",
    "_CONFIG_FIELDS",
    "DIAGNOSTIC_DOMAIN_OPTIONS",
]
