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

"""SAM feature installer and diagnostics wiring under qpane.masks."""

from __future__ import annotations


import logging

from pathlib import Path
from typing import TYPE_CHECKING, Callable


import numpy as np

from PySide6.QtCore import QObject, QPoint, QRunnable, Signal


from qpane.types import DiagnosticRecord

from qpane.core.config import SAM_DEFAULT_MODEL_HASH, SAM_DEFAULT_MODEL_URL
from qpane.core.config_features import require_sam_config
from qpane.concurrency import BaseWorker, TaskRejected

from .tools import (
    SmartSelectTool,
    connect_smart_select_signals,
    disconnect_smart_select_signals,
    smart_select_cursor_provider,
)


from qpane.features import FeatureInstallError


logger = logging.getLogger(__name__)

_SAM_HASH_WARNING_EMITTED = False


if TYPE_CHECKING:
    from qpane import QPane


class _CheckpointDownloadSignals(QObject):
    """Signals emitted during SAM checkpoint downloads."""

    finished = Signal(Path)
    error = Signal(str)


class _CheckpointDownloadWorker(QRunnable, BaseWorker):
    """Download the SAM checkpoint on a background executor."""

    def __init__(
        self,
        checkpoint_path: Path,
        *,
        download_mode: str,
        model_url: str,
        progress_callback: Callable[[int, int | None], None],
        expected_hash: str | None,
    ) -> None:
        """Store checkpoint inputs and the progress callback."""
        super().__init__()
        BaseWorker.__init__(self)
        self._checkpoint_path = checkpoint_path
        self._download_mode = download_mode
        self._model_url = model_url
        self._progress_callback = progress_callback
        self._expected_hash = expected_hash
        self.signals = _CheckpointDownloadSignals()

    def run(self) -> None:
        """Download the checkpoint and emit completion signals."""
        try:
            from qpane.sam.service import ensure_checkpoint

            ensure_checkpoint(
                self._checkpoint_path,
                download_mode=self._download_mode,
                model_url=self._model_url,
                expected_hash=self._expected_hash,
                progress_callback=self._progress_callback,
            )
            self.emit_finished(True, payload=self._checkpoint_path)
        except Exception as exc:
            self.emit_finished(False, payload=str(exc), error=exc)


def install_sam_feature(qpane: "QPane", device: str | None = None) -> None:
    """Install SAM support onto the qpane instance for the requested SAM device."""
    hooks = qpane.hooks
    masks = qpane._masks_controller
    if not masks.mask_feature_available():
        raise RuntimeError("Mask feature must be installed before the SAM feature")
    try:
        from qpane.sam.service import (
            SamDependencyError,
            ensure_checkpoint,
            ensure_dependencies,
            resolve_checkpoint_path,
        )
    except ImportError as exc:
        raise FeatureInstallError(
            "Failed to import SAM services.",
            hint="Install the SAM extras via 'pip install qpane[sam]' and verify GPU tooling.",
        ) from exc
    try:
        ensure_dependencies()
    except SamDependencyError as exc:
        raise FeatureInstallError(
            str(exc),
            hint="Install the SAM extras via 'pip install qpane[sam]' and verify GPU tooling.",
        ) from exc
    try:
        hooks.registerTool(
            qpane.CONTROL_MODE_SMART_SELECT,
            SmartSelectTool,
            on_connect=connect_smart_select_signals,
            on_disconnect=disconnect_smart_select_signals,
        )
    except ValueError:
        pass
    from qpane.sam.manager import SamManager

    sam_config = require_sam_config(qpane.settings)
    sam_device = sam_config.sam_device if device is None else device
    download_mode = str(sam_config.sam_download_mode or "").strip().lower()
    model_url = sam_config.sam_model_url
    expected_hash = _resolve_expected_hash(
        sam_config.sam_model_hash,
        sam_model_path=sam_config.sam_model_path,
        model_url=model_url,
    )
    _warn_on_unverified_custom_url(model_url, expected_hash)
    try:
        checkpoint_path = resolve_checkpoint_path(sam_config.sam_model_path)
    except SamDependencyError as exc:
        raise FeatureInstallError(
            str(exc),
            hint="Install the SAM extras via 'pip install qpane[sam]' and verify GPU tooling.",
        ) from exc

    def _emit_checkpoint_status(status: str) -> None:
        """Emit a SAM checkpoint status update via the QPane signal."""
        qpane.samCheckpointStatusChanged.emit(status, checkpoint_path)

    def _emit_checkpoint_progress(downloaded: int, total: int | None) -> None:
        """Emit a SAM checkpoint progress update via the QPane signal."""
        qpane.samCheckpointProgress.emit(
            int(downloaded), None if total is None else int(total)
        )

    if download_mode == "blocking":
        try:
            if not checkpoint_path.exists():
                _emit_checkpoint_status("downloading")
            checkpoint_path = ensure_checkpoint(
                checkpoint_path,
                download_mode=download_mode,
                model_url=model_url,
                expected_hash=expected_hash,
                progress_callback=_emit_checkpoint_progress,
            )
            _emit_checkpoint_status("ready")
        except SamDependencyError as exc:
            raise FeatureInstallError(
                str(exc),
                hint=(
                    "Set sam_model_path or sam_model_url, or disable downloads "
                    "once the checkpoint is provisioned."
                ),
            ) from exc
    elif download_mode == "background":
        if checkpoint_path.exists():
            _emit_checkpoint_status("ready")
        else:
            _emit_checkpoint_status("downloading")
            worker = _CheckpointDownloadWorker(
                checkpoint_path,
                download_mode=download_mode,
                model_url=model_url,
                progress_callback=_emit_checkpoint_progress,
                expected_hash=expected_hash,
            )

            def _handle_download_finished(path: Path) -> None:
                """Mark the checkpoint as ready after download."""
                _emit_checkpoint_status("ready")

            def _handle_download_error(message: str) -> None:
                """Log background download failures and notify listeners."""
                logger.error(
                    "SAM checkpoint download failed for %s: %s",
                    checkpoint_path,
                    message,
                )
                _emit_checkpoint_status("failed")

            BaseWorker.connect_queued(
                worker.signals.finished, _handle_download_finished
            )
            BaseWorker.connect_queued(worker.signals.error, _handle_download_error)
            try:
                qpane.executor.submit(worker, category="sam", device=sam_device)
            except TaskRejected as exc:
                logger.error("SAM checkpoint download rejected by executor: %s", exc)
                _emit_checkpoint_status("failed")
    else:
        try:
            checkpoint_path = ensure_checkpoint(
                checkpoint_path,
                download_mode="disabled",
                model_url=model_url,
                expected_hash=expected_hash,
            )
            _emit_checkpoint_status("ready")
        except SamDependencyError as exc:
            _emit_checkpoint_status("missing")
            raise FeatureInstallError(
                str(exc),
                hint=(
                    "Provide a checkpoint at sam_model_path or switch "
                    "sam_download_mode to blocking/background."
                ),
            ) from exc
    sam_manager = SamManager(
        parent=qpane,
        device=sam_device,
        executor=qpane.executor,
        cache_limit=sam_config.sam_cache_limit,
        checkpoint_path=checkpoint_path,
    )
    qpane.attachSamManager(sam_manager)
    hooks.registerCursorProvider(
        qpane.CONTROL_MODE_SMART_SELECT, smart_select_cursor_provider
    )
    hooks.register_diagnostics_provider(
        _sam_detail_diagnostics_provider,
        domain="sam",
        tier="detail",
    )
    hooks.register_diagnostics_provider(
        _sam_summary_diagnostics_provider,
        domain="sam",
        tier="detail",
    )
    tm_signals = qpane._tools_manager.signals

    def _handle_region_selected(bbox, is_erase: bool):
        """Forward a bounding box selection to SAM when valid."""
        manager = qpane.samManager()
        if manager is None:
            return
        if qpane.currentImagePath is None:
            logger.warning("Ignoring smart-select request: no image is active")
            return
        if bbox is None:
            logger.warning("Ignoring smart-select request: bounding box missing")
            return
        bbox_array = np.asarray(bbox)
        if bbox_array.size < 4:
            logger.warning(
                "Ignoring smart-select request: bounding box invalid (size=%s)",
                bbox_array.size,
            )
            return
        manager.generateMaskFromBox(
            qpane.currentImagePath, bbox_array, erase_mode=is_erase
        )

    def _handle_component_adjustment(image_point: QPoint, grow: bool):
        """Adjust components on the active mask at the provided image point."""
        service = getattr(qpane, "mask_service", None)
        if service is None:
            return
        active_mask_id = service.getActiveMaskId()
        if image_point is None or active_mask_id is None:
            return
        mask_manager = service.manager
        new_image = mask_manager.adjust_component_at_point(
            active_mask_id, image_point, grow
        )
        if new_image is None:
            return
        if not service.controller.apply_mask_image(active_mask_id, new_image):
            return
        qpane.markDirty()
        qpane.update()

    tm_signals.region_selected_for_masking.connect(_handle_region_selected)
    tm_signals.mask_component_adjustment_requested.connect(_handle_component_adjustment)


def _resolve_expected_hash(
    raw_hash: object,
    *,
    sam_model_path: str | None,
    model_url: str,
) -> str | None:
    """Return the hash to verify for the configured SAM checkpoint settings."""
    normalized = None
    if isinstance(raw_hash, str):
        candidate = raw_hash.strip()
        if candidate:
            if candidate.lower() == "default":
                normalized = SAM_DEFAULT_MODEL_HASH
            else:
                normalized = candidate
    if (
        normalized is None
        and sam_model_path is None
        and model_url == SAM_DEFAULT_MODEL_URL
    ):
        normalized = SAM_DEFAULT_MODEL_HASH
    return normalized


def _warn_on_unverified_custom_url(
    model_url: str,
    expected_hash: str | None,
) -> None:
    """Warn once when a custom model URL is used without hash verification."""
    global _SAM_HASH_WARNING_EMITTED
    if _SAM_HASH_WARNING_EMITTED:
        return
    if expected_hash is not None:
        return
    if model_url == SAM_DEFAULT_MODEL_URL:
        return
    logger.warning(
        "SAM model URL is custom and sam_model_hash is unset; "
        "checkpoint downloads will not be integrity-checked."
    )
    _SAM_HASH_WARNING_EMITTED = True


def _sam_summary_diagnostics_provider(qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
    """Expose SAM cache usage and readiness for the SAM detail overlay tier."""
    accessor = getattr(qpane, "samManager", None)
    manager = accessor() if callable(accessor) else None
    if manager is None:
        return tuple()
    records: list[DiagnosticRecord] = [
        DiagnosticRecord("SAM|Cache", str(manager.getCachedPredictorCount()))
    ]
    delegate = None
    workflow = None
    accessor = getattr(qpane, "masks", None)
    if callable(accessor):
        try:
            workflow = accessor()
        except Exception:
            workflow = None
    if workflow is not None:
        try:
            delegate = workflow.sam_delegate()
        except Exception:
            delegate = None
    active_predictor = (
        getattr(delegate, "activePredictor", None) if delegate is not None else None
    )
    state = "Ready" if active_predictor is not None else "Idle"
    records.append(DiagnosticRecord("SAM|State", state))
    return tuple(records)


def _sam_detail_diagnostics_provider(qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
    """Return the worker-pool diagnostics rows for the SAM detail tier."""
    accessor = getattr(qpane, "samManager", None)
    manager = accessor() if callable(accessor) else None
    if manager is None:
        return tuple()
    records: list[DiagnosticRecord] = []
    thread_pool = getattr(manager, "thread_pool", None)
    if thread_pool is not None:
        try:
            records.append(
                DiagnosticRecord(
                    "SAM|Active Jobs", str(thread_pool.activeThreadCount())
                )
            )
        except Exception:
            pass
        try:
            records.append(
                DiagnosticRecord("SAM|Max Threads", str(thread_pool.maxThreadCount()))
            )
        except Exception:
            pass
    return tuple(records)
