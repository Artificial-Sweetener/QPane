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

"""Mask feature installer entry point and helper wiring."""

from __future__ import annotations


from typing import TYPE_CHECKING


from qpane.core.config_features import require_mask_config

from qpane.features import FeatureInstallError

from .mask import MaskManager, _require_cv2

from .mask_controller import MaskController

from .mask_diagnostics import MaskStrokeDiagnostics

from .mask_service import MaskService, should_enable_mask_autosave

from .tools import BrushTool, connect_brush_signals, disconnect_brush_signals


if TYPE_CHECKING:
    from qpane import QPane
__all__ = [
    "install_mask_feature",
    "should_enable_mask_autosave",
]


def install_mask_feature(qpane: "QPane") -> None:
    """Install mask management subsystems and tool wiring for a QPane."""
    hooks = qpane.hooks
    try:
        _require_cv2()
    except RuntimeError as exc:  # pragma: no cover - optional dependency path
        raise FeatureInstallError(
            "Mask feature requires OpenCV.",
            hint="Install the mask extras via 'pip install qpane[mask]' to enable it.",
        ) from exc
    mask_config = require_mask_config(qpane.settings)
    catalog_facade = qpane.catalog()
    diagnostics_manager = qpane.diagnostics()
    mask_manager = catalog_facade.maskManager()
    if mask_manager is None:
        mask_manager = MaskManager(undo_limit=mask_config.mask_undo_limit)
        catalog_facade.setMaskManager(mask_manager)
    else:
        mask_manager.set_undo_limit(mask_config.mask_undo_limit)
    try:
        hooks.registerTool(
            qpane.CONTROL_MODE_DRAW_BRUSH,
            BrushTool,
            on_connect=connect_brush_signals,
            on_disconnect=disconnect_brush_signals,
        )
    except ValueError:
        pass
    diagnostics_tracker = MaskStrokeDiagnostics(
        enabled=False,
        dirty_callback=lambda domain="mask": diagnostics_manager.set_dirty(domain),
    )
    mask_controller = MaskController(
        mask_manager,
        image_to_panel_point=qpane.view().viewport.content_to_panel_point,
        config=qpane.settings,
        mask_config=mask_config,
        stroke_diagnostics=diagnostics_tracker,
    )
    service = MaskService(
        qpane=qpane,
        mask_manager=mask_manager,
        mask_controller=mask_controller,
        config=qpane.settings,
        mask_config=mask_config,
        executor=qpane.executor,
        stroke_diagnostics=diagnostics_tracker,
    )
    qpane.attachMaskService(service)
    service.configureStrokeDiagnostics(qpane.settings)
    controller = service.controller

    def _handle_mask_updated(mask_id, rect=None):
        """Mark the QPane dirty when mask scene content changes."""
        if rect is None or rect.isNull() or rect.isEmpty():
            qpane.markDirty()
        else:
            qpane.markDirty(dirty_rect=rect)
        qpane.update()

    tm_signals = qpane._tools_manager.signals
    tm_signals.stroke_applied.connect(service.applyStrokeSegment)
    tm_signals.stroke_completed.connect(service.commitStroke)
    tm_signals.brush_size_changed.connect(qpane.setBrushSize)
    tm_signals.undo_state_push_requested.connect(service.pushActiveMaskState)
    controller.mask_updated.connect(_handle_mask_updated)
    controller.active_mask_properties_changed.connect(qpane.refreshCursor)
