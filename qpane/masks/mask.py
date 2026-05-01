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

"""Mask domain primitives, surfaces, and undo helpers used by the workflow."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence, Set

import numpy as np
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QColor, QImage

from ..catalog.image_utils import (
    qimage_to_numpy_view_grayscale8,
)
from .mask_undo import (
    MaskHistoryChange,
    MaskImageCommand,
    MaskLayerUndoProvider,
    MaskPatch,
    MaskPatchCommand,
    MaskUndoCommand,
    MaskUndoProvider,
    MaskUndoState,
)

try:
    import cv2  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore[assignment]
logger = logging.getLogger(__name__)


def _require_cv2() -> Any:
    """Return the OpenCV module or raise a helpful error if unavailable."""
    if cv2 is None:
        raise RuntimeError(
            "OpenCV is not available. Install the mask extra via 'pip install qpane[mask]' "
            "to enable mask editing features."
        )
    return cv2  # type: ignore[return-value]


def _warn_missing_mask(mask_id: uuid.UUID, *, action: str) -> None:
    """Log a warning for missing mask identifiers."""
    logger.warning(
        "Mask %s not found while attempting to %s; skipping.",
        mask_id,
        action,
    )


def _normalize_mask_array(array: np.ndarray | None) -> np.ndarray:
    """Return a contiguous uint8 array using 0/255 semantics for masks."""
    if array is None:
        return np.zeros((0, 0), dtype=np.uint8)
    mask_array = np.asarray(array)
    if mask_array.ndim != 2:
        raise ValueError("Mask arrays must be two-dimensional (H, W).")
    if mask_array.size == 0:
        return np.zeros(mask_array.shape, dtype=np.uint8)
    if mask_array.dtype == np.bool_:
        mask_array = mask_array.astype(np.uint8) * 255
    elif np.issubdtype(mask_array.dtype, np.floating):
        safe = np.nan_to_num(mask_array, nan=0.0, posinf=255.0, neginf=0.0)
        max_value = float(safe.max()) if safe.size else 0.0
        if max_value <= 1.0:
            safe = np.clip(safe, 0.0, 1.0) * 255.0
        else:
            safe = np.clip(safe, 0.0, 255.0)
        mask_array = safe.astype(np.uint8)
    elif mask_array.dtype != np.uint8:
        mask_array = np.clip(mask_array, 0, 255).astype(np.uint8)
    result = np.empty(mask_array.shape, dtype=np.uint8, order="C")
    np.copyto(result, mask_array)
    return result


class MaskSurface:
    """Thread-safe grayscale mask storage backed by a NumPy buffer."""

    def __init__(self, buffer: np.ndarray | None = None) -> None:
        """Initialize mask storage, coercing the buffer and preparing snapshots."""
        self._lock = threading.RLock()
        self._buffer = self._coerce_array(buffer)
        self._image = self._wrap_buffer(self._buffer)
        self._snapshot_cache: QImage | None = None
        self._snapshot_generation: int = -1
        self.generation: int = 0

    @staticmethod
    def _coerce_array(buffer: np.ndarray | None) -> np.ndarray:
        """Normalize provided mask data into a contiguous uint8 array."""
        return _normalize_mask_array(buffer)

    @classmethod
    def from_qimage(cls, image: QImage) -> "MaskSurface":
        """Build a surface from an existing ``QImage`` snapshot."""
        if image.isNull():
            return cls(np.zeros((0, 0), dtype=np.uint8))
        if image.format() != QImage.Format_Grayscale8:
            image = image.convertToFormat(QImage.Format_Grayscale8)
        view, _ = qimage_to_numpy_view_grayscale8(image)
        buffer = np.ascontiguousarray(view, dtype=np.uint8)
        return cls(buffer)

    @classmethod
    def blank(cls, size: QSize) -> "MaskSurface":
        """Create an empty surface matching ``size``."""
        if not size.isValid():
            return cls(np.zeros((0, 0), dtype=np.uint8))
        buffer = np.zeros((size.height(), size.width()), dtype=np.uint8)
        return cls(buffer)

    def _wrap_buffer(self, buffer: np.ndarray) -> QImage:
        """Expose ``buffer`` as a grayscale ``QImage`` without copying."""
        if buffer.size == 0:
            return QImage()
        height, width = buffer.shape
        bytes_per_line = int(buffer.strides[0])
        image = QImage(
            buffer.data, width, height, bytes_per_line, QImage.Format_Grayscale8
        )
        if image.isNull():
            raise RuntimeError("Failed to wrap mask buffer into QImage.")
        return image

    def is_null(self) -> bool:
        """Return True when the surface has no pixel data."""
        return self._buffer.size == 0

    def borrow_qimage_view(self) -> QImage:
        """Expose the live ``QImage`` view without copying."""
        return self._image

    def borrow_buffer(self) -> np.ndarray:
        """Return the backing NumPy buffer without copying."""
        return self._buffer

    def snapshot_qimage(self) -> QImage:
        """Return a detached ``QImage`` snapshot for thread-safe usage."""
        with self._lock:
            if self.is_null():
                return QImage()
            if (
                self._snapshot_cache is None
                or self._snapshot_generation != self.generation
            ):
                self._snapshot_cache = self._image.copy()
                self._snapshot_generation = self.generation
            return self._snapshot_cache.copy()

    def snapshot_array(self) -> np.ndarray:
        """Return a deep copy of the backing NumPy buffer."""
        with self._lock:
            return np.array(self._buffer, copy=True)

    def replace_with_array(self, array: np.ndarray) -> None:
        """Swap the surface contents for ``array`` and reset caches."""
        with self._lock:
            coerced = self._coerce_array(array)
            self._buffer = coerced
            self._image = self._wrap_buffer(self._buffer)
            self._snapshot_cache = None
            self._snapshot_generation = -1
            self.generation += 1

    def replace_with_qimage(self, image: QImage) -> None:
        """Replace the surface contents with ``image``."""
        if image.isNull():
            self.replace_with_array(np.zeros((0, 0), dtype=np.uint8))
            return
        if image.format() != QImage.Format_Grayscale8:
            image = image.convertToFormat(QImage.Format_Grayscale8)
        view, _ = qimage_to_numpy_view_grayscale8(image)
        self.replace_with_array(view)

    def mutate_with_view(self, mutator: Callable[[np.ndarray, QImage], None]) -> None:
        """Run ``mutator`` with both views and flag the surface as dirty."""
        with self._lock:
            mutator(self._buffer, self._image)
            self._snapshot_cache = None
            self._snapshot_generation = -1
            self.generation += 1

    def mark_dirty(self) -> None:
        """Invalidate cached snapshots after in-place mutations."""
        with self._lock:
            self._snapshot_cache = None
            self._snapshot_generation = -1
            self.generation += 1

    @property
    def lock(self) -> threading.RLock:
        """Expose the re-entrant lock guarding buffer mutations."""
        return self._lock


@dataclass
class MaskLayer:
    """Represents a single mask layer and its rendering metadata."""

    surface: MaskSurface
    color: QColor = field(default_factory=lambda: QColor(255, 0, 0))
    opacity: float = 0.5

    def __post_init__(self) -> None:
        """Validate that the layer always wraps a real MaskSurface."""
        if not isinstance(self.surface, MaskSurface):
            raise TypeError("MaskLayer requires a MaskSurface instance.")

    @property
    def mask_image(self) -> QImage:
        """Return the live QImage view of the mask surface."""
        return self.surface.borrow_qimage_view()

    @mask_image.setter
    def mask_image(self, image: QImage) -> None:
        """Replace the backing surface with ``image``."""
        self.surface.replace_with_qimage(image)


@dataclass(frozen=True)
class MaskCombineResult:
    """Outcome data for mask combination operations."""

    image: QImage | None
    changed: bool


class MaskManager:
    """Manage mask layers, associations, and undo state wiring."""

    def __init__(
        self,
        *,
        undo_limit: int = 20,
        undo_provider: MaskUndoProvider | None = None,
    ) -> None:
        """Initialize mask registries and undo provider with the configured limit."""
        self._undo_limit = max(1, int(undo_limit))
        self._masks: Dict[uuid.UUID, MaskLayer] = {}
        self._image_mask_order: Dict[uuid.UUID, List[uuid.UUID]] = {}
        self._mask_to_images: Dict[uuid.UUID, Set[uuid.UUID]] = {}
        self._undo_provider: MaskUndoProvider = undo_provider or MaskLayerUndoProvider()

    def get_masks_for_image(self, image_id: uuid.UUID) -> List[MaskLayer]:
        """Retrieve all mask layers for ``image_id`` in draw order (bottom -> top)."""
        mask_ids = self._image_mask_order.get(image_id, [])
        return [self._masks[mid] for mid in mask_ids if mid in self._masks]

    def get_mask_ids_for_image(self, image_id: uuid.UUID) -> List[uuid.UUID]:
        """Return mask identifiers for ``image_id`` mirroring render order."""
        order = self._image_mask_order.get(image_id)
        return list(order) if order else []

    def get_images_for_mask(self, mask_id: uuid.UUID) -> List[uuid.UUID]:
        """Return the image identifiers currently associated with ``mask_id``."""
        images = self._mask_to_images.get(mask_id)
        return list(images) if images else []

    def get_layer(self, mask_id: uuid.UUID) -> MaskLayer | None:
        """Return the ``MaskLayer`` for ``mask_id`` when it exists."""
        return self._masks.get(mask_id)

    def get_surface(self, mask_id: uuid.UUID) -> MaskSurface | None:
        """Return the underlying MaskSurface for ``mask_id`` when present."""
        layer = self._masks.get(mask_id)
        return layer.surface if layer is not None else None

    def find_mask_id_for_layer(self, layer: MaskLayer | None) -> uuid.UUID | None:
        """Return the mask identifier associated with `layer` when available."""
        if layer is None:
            return None
        for mask_id, candidate in self._masks.items():
            if candidate is layer:
                return mask_id
        return None

    def reorder_mask(
        self, image_id: uuid.UUID, mask_id: uuid.UUID, target_index: int
    ) -> bool:
        """Move ``mask_id`` to ``target_index`` in ``image_id``'s render order."""
        mask_ids = self._image_mask_order.get(image_id)
        if not mask_ids or mask_id not in mask_ids:
            return False
        if target_index < 0 or target_index >= len(mask_ids):
            return False
        current_index = mask_ids.index(mask_id)
        if current_index == target_index:
            return False
        mask_ids.pop(current_index)
        mask_ids.insert(target_index, mask_id)
        return True

    @property
    def undo_limit(self) -> int:
        """Expose the configured undo stack depth for new layers."""
        return self._undo_limit

    def set_undo_limit(self, undo_limit: int) -> None:
        """Update the undo depth and trim existing stacks as needed."""
        self._undo_limit = max(1, int(undo_limit))
        for mask_id in self._masks.keys():
            self._undo_provider.set_limit(mask_id, self._undo_limit)

    @property
    def undo_provider(self) -> MaskUndoProvider:
        """Return the undo provider responsible for mask history."""
        return self._undo_provider

    def set_undo_provider(self, undo_provider: MaskUndoProvider | None) -> None:
        """Swap the undo provider and re-register all known masks."""
        old_provider = getattr(self, "_undo_provider", None)
        if old_provider is not None:
            for mask_id in list(self._masks.keys()):
                old_provider.dispose_mask(mask_id)
        self._undo_provider = undo_provider or MaskLayerUndoProvider()
        for mask_id, layer in self._masks.items():
            self._configure_provider_for_mask(mask_id, layer)

    def _configure_provider_for_mask(
        self, mask_id: uuid.UUID, layer: MaskLayer
    ) -> None:
        """Register `mask_id` with the undo provider and apply limits."""
        self._undo_provider.initialize_mask(mask_id, layer)
        self._undo_provider.set_limit(mask_id, self._undo_limit)
        capture = getattr(self._undo_provider, "capture_snapshot", None)
        if capture is not None:
            capture(mask_id, layer.surface.snapshot_qimage())

    def create_mask(self, image: QImage) -> uuid.UUID:
        """Create a blank mask sized to ``image`` and register it with history."""
        mask_id = uuid.uuid4()
        surface = MaskSurface.blank(image.size())
        new_layer = MaskLayer(surface=surface)
        self._masks[mask_id] = new_layer
        self._mask_to_images[mask_id] = set()
        self._configure_provider_for_mask(mask_id, new_layer)
        return mask_id

    def remove_mask_from_image(self, image_id: uuid.UUID, mask_id: uuid.UUID) -> bool:
        """Detach ``mask_id`` from ``image_id`` and prune orphaned layers."""
        mask_ids = self._image_mask_order.get(image_id)
        if not mask_ids or mask_id not in mask_ids:
            return False
        mask_ids = [mid for mid in mask_ids if mid != mask_id]
        if mask_ids:
            self._image_mask_order[image_id] = mask_ids
        else:
            self._image_mask_order.pop(image_id, None)
        self._detach_mask_reference(mask_id, image_id)
        return True

    def associate_mask_with_image(
        self, mask_id: uuid.UUID, image_id: uuid.UUID
    ) -> None:
        """Associate an existing mask with ``image_id`` maintaining draw order."""
        order = self._image_mask_order.setdefault(image_id, [])
        if mask_id in order:
            return
        order.append(mask_id)
        images = self._mask_to_images.setdefault(mask_id, set())
        images.add(image_id)

    def set_mask_properties(
        self,
        mask_id: uuid.UUID,
        color: QColor | None = None,
        opacity: float | None = None,
    ) -> None:
        """Update presentation details for ``mask_id`` when present."""
        layer = self._masks.get(mask_id)
        if layer is None:
            return
        if color is not None:
            layer.color = color
        if opacity is not None:
            layer.opacity = opacity

    def submit_undo_command(self, mask_id: uuid.UUID, command: MaskUndoCommand) -> None:
        """Record ``command`` with the current undo provider."""
        if mask_id not in self._masks:
            _warn_missing_mask(mask_id, action="register undo command")
            return
        self._undo_provider.submit(mask_id, command, self._undo_limit)

    def build_mask_patch_command(
        self,
        mask_id: uuid.UUID,
        patches: Sequence[MaskPatch],
        *,
        notify: Callable[[uuid.UUID], None] | None = None,
    ) -> MaskUndoCommand | None:
        """Create a command that reapplies the provided mask patches."""
        layer = self._masks.get(mask_id)
        if layer is None:
            _warn_missing_mask(mask_id, action="build undo patch command")
            return None
        if not patches:
            return None
        normalized: list[MaskPatch] = []
        for patch in patches:
            rect = patch.rect.normalized()
            before = patch.before.copy()
            after = patch.after.copy()
            mask = np.array(patch.mask, copy=True, dtype=bool)
            normalized_patch = MaskPatch(
                rect=rect,
                before=before,
                after=after,
                mask=mask,
            )
            normalized.append(normalized_patch)
        return MaskPatchCommand(
            mask_id=mask_id,
            patches=tuple(normalized),
            apply=self._apply_mask_patches,
            notify=notify,
        )

    def commit_mask_patches(
        self,
        mask_id: uuid.UUID,
        patches: Sequence[MaskPatch],
        *,
        notify: Callable[[uuid.UUID], None] | None = None,
    ) -> bool:
        """Submit an undoable command composed of patch updates."""
        command = self.build_mask_patch_command(
            mask_id,
            patches,
            notify=notify,
        )
        if command is None:
            return False
        self.submit_undo_command(mask_id, command)
        return True

    def build_mask_image_command(
        self,
        mask_id: uuid.UUID,
        new_image: QImage,
        *,
        before_image: QImage | None = None,
        notify: Callable[[uuid.UUID], None] | None = None,
    ) -> MaskUndoCommand | None:
        """Create a command that swaps the mask image for ``mask_id``."""
        layer = self._masks.get(mask_id)
        if layer is None:
            _warn_missing_mask(mask_id, action="build undo command")
            return None
        before = (
            before_image.copy()
            if before_image is not None
            else self.get_mask_image_copy(mask_id)
        )
        if before is None:
            before = QImage()
        after = new_image.copy() if not new_image.isNull() else QImage()
        return MaskImageCommand(
            mask_id=mask_id,
            before=before,
            after=after,
            apply=self._apply_mask_image,
            notify=notify,
        )

    def commit_mask_image(
        self,
        mask_id: uuid.UUID,
        new_image: QImage,
        *,
        before_image: QImage | None = None,
        notify: Callable[[uuid.UUID], None] | None = None,
    ) -> bool:
        """Convenience helper to build and submit a mask image command."""
        command = self.build_mask_image_command(
            mask_id,
            new_image,
            before_image=before_image,
            notify=notify,
        )
        if command is None:
            return False
        self.submit_undo_command(mask_id, command)
        return True

    def undo_mask(self, mask_id: uuid.UUID) -> MaskHistoryChange | None:
        """Undo the most recent command for ``mask_id`` when available."""
        return self._undo_provider.undo(mask_id)

    def redo_mask(self, mask_id: uuid.UUID) -> MaskHistoryChange | None:
        """Redo the previously undone command for ``mask_id`` when available."""
        return self._undo_provider.redo(mask_id)

    def get_undo_state(self, mask_id: uuid.UUID) -> MaskUndoState | None:
        """Return the undo/redo stack depth for ``mask_id`` when available."""
        if mask_id not in self._masks:
            return None
        try:
            return self._undo_provider.get_state(mask_id)
        except AttributeError:
            return None

    def _apply_mask_patches(
        self,
        mask_id: uuid.UUID,
        patches: Sequence[MaskPatch],
        use_after: bool,
    ) -> None:
        """Apply the provided patches to the stored mask image."""
        layer = self._masks.get(mask_id)
        if layer is None:
            _warn_missing_mask(mask_id, action="apply patch command")
            return
        surface = layer.surface
        if surface.is_null():
            _warn_missing_mask(mask_id, action="apply patch command to null surface")
            return
        sequence: Sequence[MaskPatch] = patches
        if not use_after:
            sequence = tuple(reversed(patches))

        def _mutator(dest_view: np.ndarray, _: QImage) -> None:
            """Apply each patch onto the destination mask slice respecting masks."""
            for patch in sequence:
                y0 = patch.rect.top()
                x0 = patch.rect.left()
                y1 = y0 + patch.rect.height()
                x1 = x0 + patch.rect.width()
                dest_slice = dest_view[y0:y1, x0:x1]
                src_image = patch.after if use_after else patch.before
                src_view, _ = qimage_to_numpy_view_grayscale8(src_image)
                mask = patch.mask
                np.copyto(dest_slice, src_view, where=mask)

        surface.mutate_with_view(_mutator)

    def _apply_mask_image(self, mask_id: uuid.UUID, image: QImage) -> None:
        """Replace the stored mask image for ``mask_id`` with ``image``."""
        self.set_mask_image(mask_id, image)

    def _detach_mask_reference(self, mask_id: uuid.UUID, image_id: uuid.UUID) -> None:
        """Drop the image reference for ``mask_id`` and dispose when unused."""
        images = self._mask_to_images.get(mask_id)
        if images is None:
            return
        images.discard(image_id)
        if not images:
            self._mask_to_images.pop(mask_id, None)
            self._undo_provider.dispose_mask(mask_id)
            self._masks.pop(mask_id, None)

    def set_mask_image(self, mask_id: uuid.UUID, image: QImage) -> None:
        """Replace the QImage backing ``mask_id`` and normalise its format."""
        layer = self._masks.get(mask_id)
        if layer is None:
            _warn_missing_mask(mask_id, action="set mask image")
            return
        layer.surface.replace_with_qimage(image)

    def get_mask_image_copy(self, mask_id: uuid.UUID) -> QImage | None:
        """Return a deep copy of the mask image for ``mask_id`` when present."""
        layer = self._masks.get(mask_id)
        if layer is None:
            return None
        snapshot = layer.surface.snapshot_qimage()
        return None if snapshot.isNull() else snapshot

    def get_mask_image_as_numpy(self, mask_id: uuid.UUID) -> np.ndarray | None:
        """Return the mask image as a NumPy array when available."""
        layer = self._masks.get(mask_id)
        if layer is None:
            return None
        snapshot = layer.surface.snapshot_array()
        if snapshot.size == 0:
            return None
        return snapshot

    def _numpy_to_qimage(self, array: np.ndarray) -> QImage:
        """Convert a 2-D uint8 NumPy array into a grayscale ``QImage`` copy."""
        if array.ndim != 2:
            raise ValueError("NumPy array must be in (H, W) format for grayscale.")
        if array.dtype != np.uint8:
            raise ValueError("NumPy array must have dtype uint8 for grayscale masks.")
        if not array.flags.c_contiguous:
            array = np.ascontiguousarray(array)
        height, width = array.shape
        bytes_per_line = int(array.strides[0])
        return QImage(
            array.data, width, height, bytes_per_line, QImage.Format_Grayscale8
        ).copy()

    def _coerce_mask_array(self, array: np.ndarray) -> np.ndarray:
        """Return a contiguous uint8 mask array using 0/255 semantics."""
        return _normalize_mask_array(array)

    def adjust_component_at_point(
        self,
        mask_id: uuid.UUID,
        point: "QPoint",
        grow: bool,
    ) -> QImage | None:
        """Adjust the connected component under ``point`` by growing or shrinking it."""
        active_mask_layer = self._masks.get(mask_id)
        if active_mask_layer is None:
            _warn_missing_mask(mask_id, action="adjust component")
            return None
        if active_mask_layer.surface.is_null():
            logger.warning(
                "Cannot adjust mask %s: backing image is null.",
                mask_id,
            )
            return None
        current_mask_np = self.get_mask_image_as_numpy(mask_id)
        if current_mask_np is None or current_mask_np.size == 0:
            logger.warning("Cannot adjust mask %s: no mask data available.", mask_id)
            return None
        height, width = current_mask_np.shape
        x = int(point.x())
        y = int(point.y())
        if x < 0 or y < 0 or x >= width or y >= height:
            logger.warning(
                "Ignoring component adjustment at (%s, %s): outside mask bounds %sx%s for mask %s.",
                x,
                y,
                width,
                height,
                mask_id,
            )
            return None
        cv2_mod = _require_cv2()
        num_labels, labels, stats, centroids = cv2_mod.connectedComponentsWithStats(
            current_mask_np
        )
        target_label = labels[y, x]
        if target_label == 0:
            return None
        component_mask = (labels == target_label).astype(np.uint8) * 255
        kernel = cv2_mod.getStructuringElement(cv2_mod.MORPH_ELLIPSE, (3, 3))
        if grow:
            modified_component = cv2_mod.dilate(component_mask, kernel, iterations=1)
        else:
            modified_component = cv2_mod.erode(component_mask, kernel, iterations=1)
        current_mask_np[labels == target_label] = 0
        final_mask_np = cv2_mod.bitwise_or(current_mask_np, modified_component)
        return self._numpy_to_qimage(final_mask_np)

    def combine_with_numpy_mask(
        self,
        mask_id: uuid.UUID,
        new_mask_np: np.ndarray,
        erase_mode: bool = False,
    ) -> MaskCombineResult:
        """Merge `new_mask_np` into `mask_id` and report whether pixels changed.

        The incoming array is normalised to uint8 0/255 semantics. In erase mode the
        white pixels in `new_mask_np` clear the stored mask, yielding an explicit
        blank image even when the layer previously held a null `QImage`. Shape
        adjustments rely on `_require_cv2()` so callers keep the shared OpenCV
        guidance.
        """
        layer = self._masks.get(mask_id)
        if layer is None:
            _warn_missing_mask(mask_id, action="combine numpy mask")
            return MaskCombineResult(image=None, changed=False)
        normalized_new_mask = self._coerce_mask_array(new_mask_np)
        existing_mask_np = self.get_mask_image_as_numpy(mask_id)
        was_null_image = layer.surface.is_null()
        if existing_mask_np is None:
            existing_mask_np = np.zeros(normalized_new_mask.shape, dtype=np.uint8)
        elif existing_mask_np.shape != normalized_new_mask.shape:
            cv2_mod = _require_cv2()
            normalized_new_mask = cv2_mod.resize(
                normalized_new_mask,
                (existing_mask_np.shape[1], existing_mask_np.shape[0]),
                interpolation=cv2_mod.INTER_NEAREST,
            )
            normalized_new_mask = np.ascontiguousarray(
                normalized_new_mask, dtype=np.uint8
            )
        baseline_mask_np = existing_mask_np
        if erase_mode:
            combined_mask_np = np.bitwise_and(
                baseline_mask_np, np.bitwise_not(normalized_new_mask)
            )
        else:
            combined_mask_np = np.bitwise_or(baseline_mask_np, normalized_new_mask)
        changed = was_null_image or not np.array_equal(
            baseline_mask_np, combined_mask_np
        )
        if not changed:
            return MaskCombineResult(image=None, changed=False)
        combined_mask_np = np.ascontiguousarray(combined_mask_np, dtype=np.uint8)
        combined_image = self._numpy_to_qimage(combined_mask_np)
        return MaskCombineResult(image=combined_image, changed=True)

    def handle_image_removal(self, image_id: uuid.UUID) -> None:
        """Clear associations for ``image_id`` and prune orphaned masks."""
        mask_ids = self._image_mask_order.pop(image_id, [])
        if not mask_ids:
            return
        for mask_id in mask_ids:
            self._detach_mask_reference(mask_id, image_id)

    def clear_all(self) -> None:
        """Remove all masks, associations, and provider state."""
        for mask_id in list(self._masks.keys()):
            self._undo_provider.dispose_mask(mask_id)
        self._masks.clear()
        self._image_mask_order.clear()
        self._mask_to_images.clear()
