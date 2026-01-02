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


"""Narrative copy and quick-reference hints for the QPane example demo."""


from __future__ import annotations


CATALOG_HINT = "Use Ctrl or Shift to multi-select images. Clicking a mask highlights its parent image."

EXIT_MESSAGE = "Thanks for trying the QPane example."

CORE_CHAPTER = (
    "Core: launch QPane(config=...) with no optional features by default (add features=('mask', 'sam') "
    "as needed), load images, navigate via facade helpers like imageIDs()/currentImageID(), and link panes "
    "with setLinkedGroups() without touching private state. The demo occasionally reads Config.as_dict for "
    "small UI toggles; most hosts should treat config as set-and-forget. The status bar's zoom % label is "
    "wired directly to QPane.zoomChanged so you can copy that pattern into your own hosts."
)

MASK_CHAPTER = (
    "Mask: when features include 'mask', create/import/export masks, rotate stacks, "
    "and observe mask-driven catalog rows via qpane mask signals. The status bar's "
    "undo/redo counter listens to QPane.maskUndoStackChanged so you can mirror stack depth "
    "affordances in your own hosts."
)

SAM_CHAPTER = (
    "SAM: when features include 'sam', trigger smart select with drag, tune predictor/cache controls, "
    "and surface installer guidance if extras are missing. When downloads are enabled, "
    "QPane preflights the checkpoint and fetches it on demand; connect to "
    "samCheckpointStatusChanged/samCheckpointProgress to mirror readiness and progress in host UI. "
    "Use sam_download_mode to pick blocking/background/disabled behavior, sam_model_path to point at "
    "a local checkpoint, sam_model_url to change the download source, and sam_model_hash to verify "
    "checkpoint integrity (use 'default' for the built-in MobileSAM hash). The launcher lets you "
    "override all three so you can simulate host-provided checkpoints. The status bar mirrors checkpoint "
    "readiness and download progress. Predictor caches are keyed "
    "by device and checkpoint path, and QPane.samCheckpointReady() can gate predictor requests. "
    "The demo launcher exposes the same download-mode switch so you can feel the trade-offs live. "
    "The config dialog has a SAM tab; background updates apply live while blocking/disabled "
    "changes require a restart. "
    "The default path is "
    "QStandardPaths.AppDataLocation/mobile_sam.pt unless sam_model_path is set."
)

DIAGNOSTICS_CHAPTER = (
    "Diagnostics/Config: toggle diagnosticsOverlayEnabled()/setDiagnosticsDomainEnabled, apply settings via QPane.applySettings, "
    "pick cache/mask/executor domains in the dialog, and adjust concurrency/cache spec fields grouped by domain."
)

OVERLAY_HOOK_CHAPTER = "Hooks: register overlays/cursors/tools via the QPane.register* facade to extend visuals safely."

CUSTOM_TOOL_ENABLED = (
    "Custom tool enabled via QPane.registerTool and registerCursorProvider."
)

CUSTOM_TOOL_DISABLED = "Custom tool removed; toolbar restored."

CUSTOM_TOOL_APPLIED = "Custom cursor provider applied."

CUSTOM_CURSOR_EDITOR_HINT = (
    "This editor shows how to build a cursor provider for a custom tool mode. "
    "The demo host injects qpane and CUSTOM_MODE. "
    "Define cursor(qpane) -> QCursor|None and click Apply to refresh the tool."
)

CUSTOM_OVERLAY_ENABLED = (
    "Custom overlay enabled via QPane.registerOverlay; tweak the code and click Apply."
)

CUSTOM_OVERLAY_DISABLED = "Custom overlay removed."

CUSTOM_OVERLAY_APPLIED = "Custom overlay applied and repainted."

CUSTOM_OVERLAY_EDITOR_HINT = (
    "This editor demonstrates an OverlayState-aware overlay hook. "
    "Define draw_overlay(painter, state) and click Apply to repaint the qpane."
)

LENS_DEMO_ENABLED = "Paired cursor/overlay hooks enabled."

LENS_DEMO_DISABLED = "Paired hooks removed; toolbar restored."

LENS_DEMO_APPLIED = "Paired cursor/overlay hook applied."

LENS_EDITOR_HINT = (
    "This combined editor shows how cursor and overlay hooks can collaborate. "
    "The demo host injects qpane and CUSTOM_MODE. "
    "Define cursor(qpane) and draw_overlay(painter, state), then click Apply to experiment."
)

EXTENSION_CHECKLIST = (
    "Extend actions via demo_window builders; snapshots via catalog/models; "
    "dock rendering via catalog/dock; config via config/spec+dialog with feature_namespace gating; "
    "hooks via QPane.registerTool/registerOverlay/registerCursorProvider."
)

PARITY_MAP = (
    "Renamed: DemoWindow->ExampleWindow, "
    "config_dialog_spec.py->demonstration/config/spec.py, "
    "config_dialog.py->demonstration/config/dialog.py, "
    "catalog_models.py->demonstration/catalog/models.py, "
    "catalog_dock.py->demonstration/catalog/dock.py. "
    "The demo now uses QPane directly without helper subclasses. "
    "Launcher remains examples/demo.py and launch_*.bat scripts."
)


def reference_hints(mask_enabled: bool, sam_enabled: bool) -> list[str]:
    """Return the shortcut hints displayed in the quick-reference dialog."""
    hints = [
        "Ctrl+O or right-click: load images",
        "File -> Set Placeholder: pick the fallback image shown when the gallery is empty",
        "Left/Right, A/D, or arrow toolbar buttons: switch images",
        "Delete: remove current image",
        "Zoom field: double-click to edit, enter a percent (for example, 125%), press Enter",
        "Zoom toggle: click Set Fit / Set 1:1 to switch zoom presets",
        "Catalog panel: Ctrl/Shift-click to manage linked views and masks",
    ]
    if mask_enabled:
        hints.extend(
            [
                "M key: create a mask for the current image",
                "Load Mask: import layers from external files",
                "Digits 1-0: activate mask slots",
                "Mask Up/Down: rotate the mask stack",
                "Catalog mask rows: right-click to recolor or delete",
            ]
        )
    if mask_enabled and sam_enabled:
        hints.append("Drag a box in Smart Select mode to run SAM")
    return hints
