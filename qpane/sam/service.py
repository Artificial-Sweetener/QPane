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

"""Load MobileSAM dependencies and helpers for predictor-based masking."""

from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable
import sys
import urllib.request
import hashlib

import numpy as np
from PySide6.QtCore import QStandardPaths

if TYPE_CHECKING:
    import torch
    from mobile_sam import SamPredictor


class SamDependencyError(RuntimeError):
    """Raised when MobileSAM dependencies or checkpoints are unavailable."""


def ensure_dependencies() -> None:
    """Ensure MobileSAM dependencies import successfully.

    Raises:
        SamDependencyError: When torch or mobilesam-mirror cannot be imported.
    """
    _import_dependencies()


def resolve_checkpoint_path(checkpoint_path: str | Path | None) -> Path:
    """Resolve the MobileSAM checkpoint path, defaulting to app data."""
    if checkpoint_path is None:
        app_data = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        if not app_data:
            raise SamDependencyError(
                "SAM app data location is unavailable. Configure sam_model_path or sam_model_url."
            )
        return (Path(app_data) / "mobile_sam.pt").resolve()
    try:
        path = Path(checkpoint_path).expanduser()
    except TypeError as exc:
        raise SamDependencyError(
            "SAM checkpoint path is not set. Configure sam_model_path or sam_model_url."
        ) from exc
    if str(path).strip() == "":
        raise SamDependencyError(
            "SAM checkpoint path is not set. Configure sam_model_path or sam_model_url."
        )
    return path.resolve()


def ensure_checkpoint(
    checkpoint_path: str | Path | None,
    *,
    download_mode: str,
    model_url: str,
    expected_hash: str | None = None,
    progress_callback: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Ensure the SAM checkpoint exists, downloading when permitted.

    Args:
        checkpoint_path: Optional checkpoint override path.
        download_mode: One of "blocking", "background", or "disabled".
        model_url: Download URL to use when the checkpoint is missing.
        expected_hash: Optional SHA-256 hex digest used to verify the checkpoint.
        progress_callback: Optional callback invoked with download progress.

    Returns:
        The resolved checkpoint path.

    Raises:
        SamDependencyError: When the checkpoint is missing or cannot be downloaded.

    Side effects:
        Creates directories and writes the checkpoint file to disk.
    """
    resolved = resolve_checkpoint_path(checkpoint_path)
    if resolved.exists():
        return resolved
    if download_mode not in {"blocking", "background", "disabled"}:
        raise SamDependencyError(
            "SAM download mode is invalid; expected blocking, background, or disabled."
        )
    if download_mode == "disabled":
        raise SamDependencyError(
            "SAM checkpoint not found at '"
            f"{resolved}'. Downloading is disabled; set sam_model_path or sam_model_url."
        )
    if not isinstance(model_url, str) or not model_url.strip():
        raise SamDependencyError(
            "SAM model URL is not set. Configure sam_model_url or provide sam_model_path."
        )
    normalized_hash = _normalize_expected_hash(expected_hash)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    _download_checkpoint(model_url, resolved, progress_callback=progress_callback)
    if not resolved.exists():
        raise SamDependencyError(
            "SAM checkpoint download did not produce the expected file at "
            f"'{resolved}'."
        )
    if normalized_hash is not None:
        try:
            _verify_checkpoint_hash(resolved, normalized_hash)
        except SamDependencyError:
            try:
                resolved.unlink()
            except OSError:
                pass
            raise
    return resolved


def load_predictor(checkpoint_path: Path, *, device: str = "cpu") -> "SamPredictor":
    """Return a SAM predictor initialised on device using a cached model.

    Args:
        checkpoint_path: Resolved filesystem path to the SAM checkpoint file.
        device: Compute target passed to the MobileSAM model.

    Raises:
        SamDependencyError: When dependencies or the checkpoint are missing.
    """
    if not isinstance(checkpoint_path, Path):
        raise SamDependencyError(
            "SAM checkpoint path must be resolved before loading. "
            "Use resolve_checkpoint_path or ensure_checkpoint."
        )
    _, SamPredictor, _ = _import_dependencies()
    model = _load_model(device=device, checkpoint_path=checkpoint_path)
    return SamPredictor(model)


def predict_mask_from_box(
    predictor: "SamPredictor", bbox: np.ndarray
) -> np.ndarray | None:
    """Predict a mask for bbox using the provided predictor.

    Args:
        predictor: MobileSAM predictor prepared with an image.
        bbox: Bounding box array in (x0, y0, x1, y1) order.

    Returns:
        The first mask array when available, otherwise None.

    Raises:
        ValueError: If bbox does not have shape (4,) or (1, 4).
    """
    normalized_bbox = _normalize_bbox(bbox)
    masks, scores, logits = predictor.predict(
        box=normalized_bbox, multimask_output=False
    )
    if masks.size == 0:
        return None
    return masks[0]


def _normalize_bbox(bbox: np.ndarray) -> np.ndarray:
    """Validate bbox shape and return it as a float32 array shaped (1, 4).

    Raises:
        ValueError: When bbox is not length 4 or shape (1, 4).
    """
    bbox_array = np.asarray(bbox)
    if bbox_array.ndim == 1:
        if bbox_array.size != 4:
            raise ValueError(
                "Expected a bounding box of four coordinates; received "
                f"shape {bbox_array.shape}."
            )
        bbox_array = bbox_array.reshape(1, 4)
    elif bbox_array.shape != (1, 4):
        raise ValueError(
            "Bounding box must have shape (4,) or (1, 4); received "
            f"{bbox_array.shape}."
        )
    return bbox_array.astype(np.float32, copy=False)


@lru_cache(maxsize=1)
def _import_dependencies() -> tuple[ModuleType, type["SamPredictor"], Any]:
    """Import torch/mobilesam-mirror once with warnings suppressed.

    Raises:
        SamDependencyError: When torch or mobilesam-mirror cannot be imported or initialised.
    """
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Importing from timm.models.layers is deprecated",
                category=FutureWarning,
                module=r"timm\.models\.layers",
            )
            warnings.filterwarnings(
                "ignore",
                message="Importing from timm.models.registry is deprecated",
                category=FutureWarning,
                module=r"timm\.models\.registry",
            )
            warnings.filterwarnings(
                "ignore",
                message="Overwriting tiny_vit_",
                category=UserWarning,
                module=r"mobile_sam\.modeling\.tiny_vit_sam",
            )
            import torch  # type: ignore[import]
            from mobile_sam import (  # type: ignore[import]
                SamPredictor,
                sam_model_registry,
            )
    # pragma: no cover - optional dependency path
    except ModuleNotFoundError as exc:
        raise SamDependencyError(
            "SAM feature requires torch and mobilesam-mirror. Install via "
            "'pip install qpane[sam]'."
        ) from exc
    # pragma: no cover - optional dependency path
    except RuntimeError as exc:
        raise SamDependencyError(
            "SAM feature failed to initialise torch/mobilesam-mirror. Check your "
            "install and GPU drivers."
        ) from exc
    return torch, SamPredictor, sam_model_registry


def _download_checkpoint(
    url: str,
    destination: Path,
    *,
    progress_callback: Callable[[int, int | None], None] | None = None,
) -> None:
    """Download a checkpoint file and optionally forward progress updates."""
    temp_path = destination.with_suffix(f"{destination.suffix}.part")
    if temp_path.exists():
        temp_path.unlink()
    try:
        with urllib.request.urlopen(url) as response:
            total_bytes = _content_length(response)
            downloaded = 0
            with temp_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total_bytes)
                    else:
                        _emit_download_progress(downloaded, total_bytes)
        if progress_callback is None:
            _finish_download_progress(total_bytes)
        temp_path.replace(destination)
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink()
        raise SamDependencyError(
            "Failed to download SAM checkpoint from " f"'{url}' to '{destination}'."
        ) from exc


def _content_length(response: Any) -> int | None:
    """Return the content length of a response when present."""
    length = None
    if hasattr(response, "getheader"):
        length = response.getheader("Content-Length")
    if length is None and hasattr(response, "headers"):
        length = response.headers.get("Content-Length")
    if length is None:
        return None
    try:
        value = int(length)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _emit_download_progress(downloaded: int, total: int | None) -> None:
    """Emit a single-line download progress update to stdout."""
    if total is None:
        message = f"\rDownloading SAM checkpoint: {_format_bytes(downloaded)}"
    else:
        percent = min(100, int(downloaded / total * 100))
        message = (
            "\rDownloading SAM checkpoint: "
            f"{percent}% ({_format_bytes(downloaded)} of {_format_bytes(total)})"
        )
    sys.stdout.write(message)
    sys.stdout.flush()


def _finish_download_progress(total: int | None) -> None:
    """Finish the CLI progress line with a newline."""
    if total is not None:
        sys.stdout.write("\rDownloading SAM checkpoint: 100%\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()


def _format_bytes(value: int) -> str:
    """Return a human-friendly byte size string."""
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(max(0, value))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{int(size)} B"


@lru_cache(maxsize=None)
def _load_model(device: str, checkpoint_path: Path) -> "torch.nn.Module":
    """Load and cache the MobileSAM model on the target device.

    Args:
        device: Device passed through to torch for model placement.
        checkpoint_path: Filesystem path to the SAM checkpoint.

    Raises:
        SamDependencyError: When the checkpoint is missing or dependencies are unavailable.
    """
    checkpoint = checkpoint_path
    torch, _, sam_model_registry = _import_dependencies()
    if not checkpoint.exists():
        raise SamDependencyError(
            "SAM checkpoint not found at '"
            f"{checkpoint}'. Set sam_model_path or sam_model_url to provide it."
        )
    model = sam_model_registry["vit_t"](checkpoint=checkpoint)
    model.to(device=device)
    model.eval()
    return model


def _normalize_expected_hash(expected_hash: str | None) -> str | None:
    """Return a normalized lowercase hash value or ``None`` when unset."""
    if expected_hash is None:
        return None
    normalized = expected_hash.strip().lower()
    if not normalized:
        raise SamDependencyError(
            "SAM model hash must be a non-empty string when provided."
        )
    return normalized


def _verify_checkpoint_hash(checkpoint_path: Path, expected_hash: str) -> None:
    """Verify the checkpoint file hash matches the expected digest."""
    if not checkpoint_path.exists():
        raise SamDependencyError(f"SAM checkpoint not found at '{checkpoint_path}'.")
    actual = _hash_file(checkpoint_path)
    if actual != expected_hash:
        raise SamDependencyError(
            "SAM checkpoint hash mismatch. Expected "
            f"'{expected_hash}' but found '{actual}'."
        )


def _hash_file(path: Path) -> str:
    """Return the SHA-256 digest for the provided file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
