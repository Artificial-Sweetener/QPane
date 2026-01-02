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

"""Feature-aware configuration descriptors and slice schemas.

Provide declarative descriptors describing which config namespaces belong to each
feature so installers and demos can reason about their settings explicitly.
"""

from __future__ import annotations

import os

from copy import deepcopy
from dataclasses import dataclass, field
from typing import (
    Callable,
    Dict,
    Mapping,
    MutableMapping,
    Optional,
    Tuple,
    Type,
    TypeVar,
)

from .config import CacheSettings, Config
from ..features import FeatureInstallError

T = TypeVar("T")

ConfigValidator = Callable[[object], None]


_BASE_CONFIG = Config()


def _clone_cache_defaults() -> CacheSettings:
    """Return a fresh cache settings instance seeded from the global defaults."""
    cache = getattr(_BASE_CONFIG, "cache", None)
    if isinstance(cache, CacheSettings):
        return cache.clone()
    return CacheSettings()


@dataclass
class CoreConfigSlice:
    """Structured settings that are always available for every QPane instance."""

    cache: CacheSettings = field(default_factory=_clone_cache_defaults)
    tile_size: int = _BASE_CONFIG.tile_size
    tile_overlap: int = _BASE_CONFIG.tile_overlap
    min_view_size_px: int = _BASE_CONFIG.min_view_size_px
    canvas_expansion_factor: float = _BASE_CONFIG.canvas_expansion_factor
    safe_min_zoom: float = _BASE_CONFIG.safe_min_zoom
    drag_out_enabled: bool = _BASE_CONFIG.drag_out_enabled
    default_brush_size: int = _BASE_CONFIG.default_brush_size
    brush_scroll_increment: int = _BASE_CONFIG.brush_scroll_increment
    smart_select_min_size: int = _BASE_CONFIG.smart_select_min_size
    concurrency: MutableMapping[str, object] = field(
        default_factory=lambda: deepcopy(_BASE_CONFIG.concurrency)
    )


@dataclass
class MaskConfigSlice:
    """Mask editing, autosave, and diagnostics toggles."""

    mask_undo_limit: int = _BASE_CONFIG.mask_undo_limit
    mask_border_enabled: bool = _BASE_CONFIG.mask_border_enabled
    mask_prefetch_enabled: bool = _BASE_CONFIG.mask_prefetch_enabled
    mask_autosave_enabled: bool = _BASE_CONFIG.mask_autosave_enabled
    mask_autosave_on_creation: bool = _BASE_CONFIG.mask_autosave_on_creation
    mask_autosave_debounce_ms: int = _BASE_CONFIG.mask_autosave_debounce_ms
    mask_autosave_path_template: str = _BASE_CONFIG.mask_autosave_path_template


@dataclass
class DiagnosticsConfigSlice:
    """Diagnostics overlay switches and viewer debug toggles."""

    diagnostics_overlay_enabled: bool = _BASE_CONFIG.diagnostics_overlay_enabled
    diagnostics_domains_enabled: tuple[str, ...] = tuple(
        _BASE_CONFIG.diagnostics_domains_enabled
    )
    draw_tile_grid: bool = _BASE_CONFIG.draw_tile_grid


@dataclass
class SamConfigSlice:
    """SAM feature configuration for checkpoint and performance tuning.

    ``sam_prefetch_depth`` set to ``None`` inherits the cache prefetch depth.
    """

    sam_device: str = _BASE_CONFIG.sam_device
    sam_model_path: Optional[str] = _BASE_CONFIG.sam_model_path
    sam_model_url: str = _BASE_CONFIG.sam_model_url
    sam_model_hash: Optional[str] = _BASE_CONFIG.sam_model_hash
    sam_download_mode: str = _BASE_CONFIG.sam_download_mode
    sam_prefetch_depth: Optional[int] = _BASE_CONFIG.sam_prefetch_depth
    sam_cache_limit: int = _BASE_CONFIG.sam_cache_limit


@dataclass(frozen=True)
class FeatureConfigDescriptor:
    """Declarative description of a feature-owned configuration namespace."""

    namespace: str
    schema: Type[object]
    requires: Tuple[str, ...] = ()
    title: str | None = None
    description: str | None = None
    validators: Tuple[ConfigValidator, ...] = ()

    def create_defaults(self) -> object:
        """Return a fresh instance of the schema populated with defaults."""
        return self.schema()  # type: ignore[call-arg]


def _validate_core_config(slice_obj: CoreConfigSlice) -> None:
    """Enforce basic bounds for core viewer settings."""
    if slice_obj.tile_size <= 0:
        raise ValueError("tile_size must be greater than 0")
    if slice_obj.tile_overlap < 0:
        raise ValueError("tile_overlap must be non-negative")
    if slice_obj.tile_overlap >= slice_obj.tile_size:
        raise ValueError("tile_overlap must be smaller than tile_size")
    if slice_obj.min_view_size_px <= 0:
        raise ValueError("min_view_size_px must be greater than 0")
    if slice_obj.canvas_expansion_factor <= 0:
        raise ValueError("canvas_expansion_factor must be greater than 0")
    if slice_obj.safe_min_zoom <= 0:
        raise ValueError("safe_min_zoom must be greater than 0")
    if slice_obj.default_brush_size <= 0:
        raise ValueError("default_brush_size must be greater than 0")
    if slice_obj.brush_scroll_increment <= 0:
        raise ValueError("brush_scroll_increment must be greater than 0")
    if slice_obj.smart_select_min_size <= 0:
        raise ValueError("smart_select_min_size must be greater than 0")


def _validate_mask_config(slice_obj: MaskConfigSlice) -> None:
    """Ensure mask editing and autosave knobs remain in supported ranges."""
    if slice_obj.mask_undo_limit < 0:
        raise ValueError("mask_undo_limit must be non-negative")
    if slice_obj.mask_autosave_debounce_ms < 0:
        raise ValueError("mask_autosave_debounce_ms must be non-negative")
    if slice_obj.mask_autosave_enabled and not slice_obj.mask_autosave_path_template:
        raise ValueError(
            "mask_autosave_path_template must be set when autosave is enabled"
        )


def _validate_diagnostics_config(slice_obj: DiagnosticsConfigSlice) -> None:
    """Validate diagnostics toggles remain booleans and domain sets are strings."""
    if not isinstance(slice_obj.diagnostics_overlay_enabled, bool):
        raise ValueError("diagnostics_overlay_enabled must be a boolean")
    if not isinstance(slice_obj.draw_tile_grid, bool):
        raise ValueError("draw_tile_grid must be a boolean")
    domains = getattr(slice_obj, "diagnostics_domains_enabled", ())
    if not isinstance(domains, (tuple, list)):
        raise ValueError("diagnostics_domains_enabled must be a sequence of strings")
    for domain in domains:
        if not isinstance(domain, str):
            raise ValueError("diagnostics_domains_enabled must contain strings only")


def _validate_sam_config(slice_obj: SamConfigSlice) -> None:
    """Guard SAM tuning values to avoid negative limits and unavailable devices."""
    _validate_sam_download_mode(slice_obj.sam_download_mode)
    _validate_sam_model_path(slice_obj.sam_model_path)
    _validate_sam_model_url(slice_obj.sam_model_url)
    _validate_sam_model_hash(slice_obj.sam_model_hash)
    if slice_obj.sam_prefetch_depth is not None and slice_obj.sam_prefetch_depth < 0:
        raise ValueError("sam_prefetch_depth must be non-negative or None")
    if slice_obj.sam_cache_limit < 0:
        raise ValueError("sam_cache_limit must be non-negative")
    _validate_sam_device_available(slice_obj.sam_device)


_SAM_DOWNLOAD_MODES: tuple[str, ...] = ("blocking", "background", "disabled")


def _validate_sam_download_mode(mode: object) -> None:
    """Ensure SAM download mode is one of the supported options."""
    normalized = str(mode or "").strip().lower()
    if normalized not in _SAM_DOWNLOAD_MODES:
        raise ValueError(
            "sam_download_mode must be one of: blocking, background, disabled"
        )


def _validate_sam_model_path(path_value: object) -> None:
    """Reject empty checkpoint paths when explicitly provided."""
    if path_value is None:
        return
    if isinstance(path_value, os.PathLike):
        path_value = os.fspath(path_value)
    if not isinstance(path_value, str):
        raise ValueError("sam_model_path must be a string or None")
    if not path_value.strip():
        raise ValueError("sam_model_path must be a non-empty string when set")


def _validate_sam_model_url(url: object) -> None:
    """Reject empty SAM model URLs so downloaders have a usable target."""
    if not isinstance(url, str):
        raise ValueError("sam_model_url must be a non-empty string")
    if not url.strip():
        raise ValueError("sam_model_url must be a non-empty string")


def _validate_sam_model_hash(hash_value: object) -> None:
    """Ensure the SAM model hash is either None or a non-empty string."""
    if hash_value is None:
        return
    if not isinstance(hash_value, str):
        raise ValueError("sam_model_hash must be a string or None")
    if not hash_value.strip():
        raise ValueError("sam_model_hash must be a non-empty string when set")


def _import_torch():
    """Import torch lazily so validators can inspect device availability."""
    import importlib

    return importlib.import_module("torch")


def _validate_sam_device_available(device: str) -> None:
    """Validate that the requested SAM device exists on this host."""
    normalized = str(device or "").strip().lower()
    if not normalized:
        raise ValueError("sam_device must be specified")
    if normalized == "cpu":
        return
    try:
        torch = _import_torch()
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise ValueError(
            f"SAM device '{device}' requested but torch is unavailable"
        ) from exc
    if normalized.startswith("cuda"):
        cuda = getattr(torch, "cuda", None)
        if cuda is None or not callable(getattr(cuda, "is_available", None)):
            raise ValueError("torch.cuda is unavailable; cannot use CUDA device")
        if not cuda.is_available():  # type: ignore[call-arg]
            raise ValueError("CUDA device requested but torch reports no CUDA devices")
        return
    if normalized == "mps":
        mps = getattr(torch, "mps", None)
        if mps is None or not callable(getattr(mps, "is_available", None)):
            raise ValueError("torch.mps is unavailable; cannot use MPS device")
        if not mps.is_available():  # type: ignore[call-arg]
            raise ValueError("MPS device requested but torch reports no MPS devices")
        return
    raise ValueError(f"Unknown SAM device '{device}'")


class ConfigFeatureRegistry:
    """Registry ensuring configuration namespaces remain unique."""

    def __init__(self) -> None:
        """Initialize an empty descriptor registry."""
        self._descriptors: Dict[str, FeatureConfigDescriptor] = {}

    def register(self, descriptor: FeatureConfigDescriptor) -> None:
        """Add ``descriptor`` while rejecting duplicate namespaces."""
        namespace = descriptor.namespace
        if namespace in self._descriptors:
            raise ValueError(f"Duplicate config namespace: {namespace}")
        self._descriptors[namespace] = descriptor

    def get(self, namespace: str) -> FeatureConfigDescriptor:
        """Return the descriptor registered for ``namespace``."""
        return self._descriptors[namespace]

    def values(self) -> Tuple[FeatureConfigDescriptor, ...]:
        """Expose the registered descriptors in registration order."""
        return tuple(self._descriptors.values())

    def items(self) -> Tuple[Tuple[str, FeatureConfigDescriptor], ...]:
        """Return ``(namespace, descriptor)`` pairs for callers needing both."""
        return tuple(self._descriptors.items())


_registry = ConfigFeatureRegistry()

CORE_DESCRIPTOR = FeatureConfigDescriptor(
    namespace="core",
    schema=CoreConfigSlice,
    title="Viewer",
    description="Viewport, cache, and concurrency defaults shared by every qpane.",
    validators=(_validate_core_config,),
)
MASK_DESCRIPTOR = FeatureConfigDescriptor(
    namespace="mask",
    schema=MaskConfigSlice,
    requires=("mask",),
    title="Masks",
    description="Mask editing, autosave, and diagnostics knobs.",
    validators=(_validate_mask_config,),
)
DIAGNOSTICS_DESCRIPTOR = FeatureConfigDescriptor(
    namespace="diagnostics",
    schema=DiagnosticsConfigSlice,
    title="Diagnostics",
    description="Overlay controls and debug visualization switches.",
    validators=(_validate_diagnostics_config,),
)
SAM_DESCRIPTOR = FeatureConfigDescriptor(
    namespace="sam",
    schema=SamConfigSlice,
    requires=("sam",),
    title="SAM",
    description="Settings for the Smart Automatic Masking (SAM) feature.",
    validators=(_validate_sam_config,),
)

for descriptor in (
    CORE_DESCRIPTOR,
    MASK_DESCRIPTOR,
    DIAGNOSTICS_DESCRIPTOR,
    SAM_DESCRIPTOR,
):
    _registry.register(descriptor)


def iter_descriptors() -> Tuple[FeatureConfigDescriptor, ...]:
    """Return all registered descriptors in deterministic order."""
    return _registry.values()


def descriptors_by_namespace() -> Mapping[str, FeatureConfigDescriptor]:
    """Expose the descriptor registry keyed by namespace."""
    return dict(_registry.items())


def register_descriptor(descriptor: FeatureConfigDescriptor) -> None:
    """Allow feature installers to register additional descriptors."""
    _registry.register(descriptor)


def require_mask_config(source: object) -> MaskConfigSlice:
    """Return the mask config slice from ``source`` when available."""
    return _require_feature_slice("mask", MaskConfigSlice, source)


def require_sam_config(source: object) -> SamConfigSlice:
    """Return the SAM config slice from ``source`` when available."""
    return _require_feature_slice("sam", SamConfigSlice, source)


def _require_feature_slice(namespace: str, slice_type: Type[T], source: object) -> T:
    """Return the feature slice for ``namespace`` or raise if unavailable or wrong type.

    Raises:
        FeatureInstallError: If the feature namespace is inactive or missing.
        TypeError: If the resolved slice does not match ``slice_type``.
    """
    if isinstance(source, slice_type):
        return source
    for_feature = getattr(source, "for_feature", None)
    if callable(for_feature):
        slice_obj = for_feature(namespace)
        if isinstance(slice_obj, slice_type):
            return slice_obj
        raise TypeError(
            f"Feature '{namespace}' slice resolved to unexpected type: {type(slice_obj)!r}"
        )
    raise FeatureInstallError(
        f"Feature '{namespace}' configuration is unavailable; pass feature-aware settings."
    )
