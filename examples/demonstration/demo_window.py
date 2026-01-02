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


"""Demo window composition for the QPane example.

The module mirrors the intended tutorial flow: configure, instantiate QPane,
wire signals, build the surrounding UI, then layer optional extensions.
"""


from __future__ import annotations


import logging
import random
import traceback
import uuid

from dataclasses import dataclass
from functools import partial
from math import isclose
from pathlib import Path
from typing import Iterable, Mapping, Sequence


from PySide6.QtCore import QByteArray, QEvent, QPoint, QRect, Qt, QTimer, QThreadPool

from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QIcon,
    QImage,
    QKeySequence,
    QShortcut,
)

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QSplitter,
    QStatusBar,
    QSizePolicy,
    QToolButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


from examples.demo_settings import load_demo_settings, save_demo_settings

from examples.demonstration import hooks_examples, demo_text

from examples.demonstration.custom_tool import build_custom_cursor_tool

from examples.demonstration.hooks_editor import HookEditorWindow

from examples.demonstration.catalog.dock import CatalogDock

from examples.demonstration.catalog.models import CatalogSnapshot

from examples.demonstration.config.dialog import ConfigDialog

from examples.demonstration.config.spec import (
    build_sections_for_features,
    field_sets_for_sections,
)

from examples.demonstration.catalog.builders import build_catalog_snapshot

from examples.demonstration.workers import ImageLoaderWorker

from qpane import QPane, Config


_DIAGNOSTIC_DOMAIN_FIELD = "diagnostics_domains_enabled"
_SAM_CONFIG_FIELDS = ConfigDialog.SAM_FIELDS


_DETAIL_LABELS = {
    "cache": "Cache",
    "swap": "Swap",
    "mask": "Mask",
    "executor": "Executor",
    "retry": "Retry",
    "sam": "SAM",
}

_FEATURE_LABELS = {
    "core": "Core",
    "mask": "Masks",
    "masksam": "Mask+SAM",
}


_CUSTOM_TOOL_MODE = "custom"

_CUSTOM_OVERLAY_NAME = "custom_overlay"

_LENS_TOOL_MODE = "lens"

_LENS_OVERLAY_NAME = "lens_overlay"


MASK_KEY_LOOKUP = {
    Qt.Key_1: 0,
    Qt.Key_2: 1,
    Qt.Key_3: 2,
    Qt.Key_4: 3,
    Qt.Key_5: 4,
    Qt.Key_6: 5,
    Qt.Key_7: 6,
    Qt.Key_8: 7,
    Qt.Key_9: 8,
    Qt.Key_0: 9,
}


def _filter_sam_device_limits(
    device_limits: Mapping[str, Mapping[str, int]] | None,
) -> dict[str, dict[str, int]]:
    """Return only the SAM entries from a nested device limit map.

    Args:
        device_limits: Optional nested mapping of device -> category -> limit.

    Returns:
        Mapping limited to ``sam`` category entries.
    """
    if not isinstance(device_limits, Mapping):
        return {}
    filtered: dict[str, dict[str, int]] = {}
    for device, category_limits in device_limits.items():
        if not isinstance(category_limits, Mapping):
            continue
        trimmed: dict[str, int] = {}
        for category, raw_value in category_limits.items():
            if category != "sam":
                continue
            try:
                trimmed["sam"] = int(raw_value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        if trimmed:
            filtered[str(device)] = trimmed
    return filtered


logger = logging.getLogger(__name__)


@dataclass
class ExampleOptions:
    """CLI options controlling the example feature set and SAM configuration."""

    feature_set: str = "masksam"  # core, mask, masksam
    config_strict: bool = False
    log_level: str = "INFO"
    sam_download_mode: str | None = None
    sam_model_path: str | None = None
    sam_model_url: str | None = None
    sam_model_hash: str | None = None


class ExampleWindow(QMainWindow):
    """Compose the example QPane with catalog/config UI using only facade collaborators.

    Tutorial flow:
    - Configure settings and create the QPane (``_build_qpane``).
    - Wire QPane signals into UI updates (``_connect_qpane_signals``).
    - Build the layout, status bar, and catalog panel (``_build_layout``,
      ``_build_status_bar``, ``_build_catalog_panel``).
    - Create actions, menus, and toolbars (``_create_actions``,
      ``_create_menus``, ``_build_toolbars``).
    - Finalize startup state (``_finalize_startup``).

    Extension seams:
    - Add gallery/mask/SAM/diagnostics actions in the builders below; wire through
      ``qpane.linkedGroups()``, ``qpane.diagnosticsOverlayEnabled()``,
      and the ``QPane.register*`` facade helpers rather than private attributes.
    - Use ``_build_catalog_panel`` to attach custom dock widgets fed by QPane signals
      (``catalogChanged``, ``catalogSelectionChanged``) and snapshots pulled via
      ``build_catalog_snapshot(qpane)``.
    - Register overlays, cursors, and custom tools via ``QPane.registerOverlay``,
      ``QPane.registerCursorProvider``, and ``QPane.registerTool`` to keep hooks on the public
      surface.
    """

    def __init__(self, options: ExampleOptions, *, config: Config | None = None):
        """Assemble the example window in the same order a host would build a QPane UI."""
        super().__init__()
        self.options = options
        self._example_config = config if config is not None else Config()
        self._catalog_toggle_sync = False
        self._reference_dialog: QuickReferenceDialog | None = None
        self._catalog_user_override = False
        self._catalog_panel_width_hint: int | None = None
        self._catalog_contains_masks = False
        self._space_forwarded_to_qpane = False
        self._space_restore_mode: str | None = None
        self._event_filter_installed = False
        self._overlay_detail_actions: dict[str, QAction] = {}
        self._custom_tool_action: QAction | None = None
        self._custom_tool_registered = False
        self._custom_tool_enabled = False
        self._custom_tool_editor: HookEditorWindow | None = None
        self._custom_cursor_provider_registered = False
        self._custom_overlay_enabled = False
        self._custom_overlay_editor: HookEditorWindow | None = None
        self._custom_overlay_registered = False
        self._lens_tool_action: QAction | None = None
        self._lens_tool_registered = False
        self._lens_tool_enabled = False
        self._lens_editor: HookEditorWindow | None = None
        self._lens_cursor_registered = False
        self._lens_overlay_registered = False
        self._load_batch_auto_select = False
        self._tool_state: tuple[bool, bool] | None = None
        self._sam_status_label: QLabel | None = None
        self._sam_status_auto_hide = False
        self._shortcuts: list[QShortcut] = []
        self._configure_window_frame()
        self._build_qpane()
        self._configure_dialog_fields()
        self._build_layout()
        self._build_status_bar()
        self._build_catalog_panel()
        self._ensure_global_event_filter()
        self._apply_detail_preferences({})
        self._connect_qpane_signals()
        self._install_shortcuts()
        self._create_actions()
        self._create_menus()
        self._build_toolbars()
        self._finalize_startup()

    def _configure_window_frame(self) -> None:
        """Apply the window title and initial sizing."""
        feature_label = _FEATURE_LABELS.get(
            self.options.feature_set, self.options.feature_set
        )
        self.setWindowTitle(f"QPane Example ({feature_label})")
        self._apply_window_icon()
        settings = load_demo_settings()
        window_geometry = settings.get("window_geometry")
        if isinstance(window_geometry, str):
            restored = self._restore_window_geometry(window_geometry)
        else:
            restored = False
        if not restored:
            window_size = settings.get("window_size")
            window_position = settings.get("window_position")
            if isinstance(window_size, tuple):
                self.resize(*window_size)
            else:
                self.resize(1280, 900)
            if isinstance(window_position, tuple):
                self.move(*window_position)

    def _persist_window_geometry(self) -> None:
        """Save the window size and position to the demo settings file."""
        geometry = self._window_geometry_snapshot()
        size = geometry.size()
        position = geometry.topLeft()
        window_geometry = self._encode_window_geometry()
        existing = load_demo_settings()
        sam_download_mode = self._resolve_sam_download_mode(existing)
        sam_model_path = self._resolve_optional_setting(
            self.options.sam_model_path, existing.get("sam_model_path")
        )
        sam_model_url = self._resolve_optional_setting(
            self.options.sam_model_url, existing.get("sam_model_url")
        )
        sam_model_hash = self._resolve_optional_setting(
            self.options.sam_model_hash, existing.get("sam_model_hash")
        )
        save_demo_settings(
            self.options.feature_set,
            self.options.log_level,
            sam_download_mode,
            sam_model_path,
            sam_model_url,
            sam_model_hash,
            window_geometry=window_geometry,
            window_size=(size.width(), size.height()),
            window_position=(position.x(), position.y()),
        )

    def _window_geometry_snapshot(self) -> QRect:
        """Return the geometry snapshot to persist."""
        if self.isMaximized() or self.isFullScreen():
            return self.normalGeometry()
        return self.geometry()

    def _encode_window_geometry(self) -> str | None:
        """Return the current window geometry encoded as base64."""
        geometry = self.saveGeometry()
        if geometry.isEmpty():
            return None
        return bytes(geometry.toBase64()).decode("ascii")

    def _restore_window_geometry(self, encoded: str) -> bool:
        """Restore the window geometry from a base64 settings payload."""
        try:
            raw = QByteArray(encoded.encode("ascii"))
        except UnicodeEncodeError:
            return False
        restored = self.restoreGeometry(QByteArray.fromBase64(raw))
        if not restored:
            return False
        return True

    def _resolve_sam_download_mode(self, existing: dict[str, object]) -> str:
        """Select a SAM download mode for persisted demo settings."""
        if self.options.sam_download_mode:
            return self.options.sam_download_mode
        existing_mode = existing.get("sam_download_mode")
        if isinstance(existing_mode, str):
            return existing_mode
        return "background"

    @staticmethod
    def _resolve_optional_setting(
        preferred: str | None, fallback: object
    ) -> str | None:
        """Return preferred optional settings or a validated fallback."""
        if preferred is not None:
            return preferred
        if isinstance(fallback, str):
            return fallback
        return None

    def _apply_window_icon(self) -> None:
        """Set the demo window icon when the asset exists on disk."""
        icon_path = (
            Path(__file__).resolve().parents[2] / "assets" / "logos" / "icon-white.png"
        )
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

    @staticmethod
    def _feature_names(feature_set: str) -> tuple[str, ...]:
        """Return the configured feature names for the demo tier."""
        if feature_set == "masksam":
            return ("mask", "sam")
        if feature_set == "mask":
            return ("mask",)
        return tuple()

    @staticmethod
    def _placeholder_panzoom_enabled(settings: Mapping[str, object]) -> bool:
        """Return True when pan/zoom is allowed with placeholders."""
        placeholder = settings.get("placeholder")
        if not isinstance(placeholder, Mapping):
            return False
        return bool(placeholder.get("panzoom_enabled", False))

    def _mask_tools_available(self) -> bool:
        """Return True when mask tooling is available."""
        return self.qpane.maskFeatureAvailable()

    def _mask_status_enabled(self) -> bool:
        """Return True when the demo should show mask-specific status widgets."""
        return self._mask_tools_available()

    def _sam_tools_available(self) -> bool:
        """Return True when SAM tooling is available."""
        return self.qpane.samFeatureAvailable()

    def _all_images_linked(self) -> bool:
        """Return True when every loaded image participates in a single link group."""
        image_ids = self.qpane.imageIDs()
        raw_groups = [group.members for group in self.qpane.linkedGroups()]
        return (
            bool(raw_groups)
            and len(raw_groups) == 1
            and len(image_ids) >= 2
            and set(raw_groups[0]) == set(image_ids)
        )

    def _build_qpane(self) -> None:
        """Create the public QPane facade and capture feature state."""
        feature_names = self._feature_names(self.options.feature_set)
        self.qpane = QPane(
            config=self._example_config.copy(),
            features=feature_names,
            config_strict=self.options.config_strict,
        )
        self.qpane.setFocusPolicy(Qt.StrongFocus)
        self._active_features = tuple(self.qpane.installedFeatures)
        mask_enabled = self._mask_tools_available()
        sam_enabled = self._sam_tools_available()
        self._reference_hints = self._build_reference_hints(mask_enabled, sam_enabled)
        self._overlay_enabled = self.qpane.diagnosticsOverlayEnabled()

    def _configure_dialog_fields(self) -> None:
        """Prepare field metadata used by the config dialog."""
        sections = build_sections_for_features(self._active_features)
        self._dialog_all_fields, _, _ = field_sets_for_sections(sections)

    def _build_layout(self) -> None:
        """Compose the splitter and primary container widgets."""
        self._qpane_container = QWidget(self)
        self._qpane_container_layout = QVBoxLayout(self._qpane_container)
        self._qpane_container_layout.setContentsMargins(0, 0, 0, 0)
        self._qpane_container_layout.setSpacing(0)
        self._qpane_container_layout.addWidget(self.qpane)
        self._catalog_container = QWidget(self)
        self._catalog_container_default_max = self._catalog_container.maximumWidth()
        self._catalog_container_layout = QVBoxLayout(self._catalog_container)
        self._catalog_container_layout.setContentsMargins(0, 0, 0, 0)
        self._catalog_container_layout.setSpacing(0)
        self._splitter = QSplitter(Qt.Horizontal, self)
        self._splitter.addWidget(self._catalog_container)
        self._splitter.addWidget(self._qpane_container)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setCollapsible(0, True)
        self.setCentralWidget(self._splitter)
        self.catalog_dock: CatalogDock | None = None
        self._catalog_container.hide()
        self.installEventFilter(self)

    def _build_status_bar(self) -> None:
        """Assemble the status bar widgets for the demo."""
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        sam_enabled = self._sam_tools_available()
        if sam_enabled:
            self._sam_status_label = QLabel("SAM: --")
            self._sam_status_label.setObjectName("samStatusLabel")
            self._sam_status_label.setStyleSheet("padding: 0 6px;")
            self.status.addPermanentWidget(self._sam_status_label)
        if self._mask_status_enabled():
            self._mask_stack_label = QLabel("Undo: 0 / Redo: 0")
            self._mask_stack_label.setObjectName("maskStackStatusLabel")
            self._mask_stack_label.setStyleSheet("padding: 0 6px;")
            self.status.addPermanentWidget(self._mask_stack_label)
        self._image_size_label = QLabel("-- x --px")
        self._image_size_label.setObjectName("imageSizeStatusLabel")
        self._image_size_label.setStyleSheet("padding: 0 6px;")
        self.status.addPermanentWidget(self._image_size_label)
        self._zoom_toggle_container = QWidget()
        self._zoom_toggle_container.setObjectName("zoomToggleContainer")
        self._zoom_toggle_container.setStyleSheet("padding: 0 6px;")
        self._zoom_toggle_layout = QHBoxLayout(self._zoom_toggle_container)
        self._zoom_toggle_layout.setContentsMargins(0, 0, 0, 0)
        self._zoom_toggle_layout.setSpacing(4)
        self._zoom_toggle_button = QToolButton()
        self._zoom_toggle_button.setObjectName("zoomToggleButton")
        self._zoom_toggle_button.setAutoRaise(True)
        self._zoom_toggle_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._zoom_toggle_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._zoom_toggle_button.clicked.connect(self._toggle_zoom_target)
        self._zoom_toggle_layout.addWidget(self._zoom_toggle_button)
        self.status.addPermanentWidget(self._zoom_toggle_container)
        self._zoom_container = QWidget()
        self._zoom_container.setObjectName("zoomStatusContainer")
        self._zoom_container.setStyleSheet("padding: 0 6px;")
        self._zoom_container_layout = QHBoxLayout(self._zoom_container)
        self._zoom_container_layout.setContentsMargins(0, 0, 0, 0)
        self._zoom_container_layout.setSpacing(4)
        self._zoom_label = QLabel("Zoom:")
        self._zoom_label.setObjectName("zoomStatusLabel")
        self._zoom_label.setStyleSheet("padding-right: 2px;")
        self._zoom_container_layout.addWidget(self._zoom_label)
        self._zoom_input = QLineEdit()
        self._zoom_input.setObjectName("zoomPercentInput")
        self._zoom_input.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._zoom_input.setClearButtonEnabled(False)
        self._zoom_input.setReadOnly(True)
        self._zoom_input.setFrame(False)
        self._zoom_input.setFocusPolicy(Qt.ClickFocus)
        self._zoom_input.setStyleSheet(
            "QLineEdit#zoomPercentInput { background: transparent; border: none; }"
        )
        self._zoom_input.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._zoom_input.returnPressed.connect(self._apply_zoom_from_input)
        self._zoom_input.installEventFilter(self)
        self._zoom_container_layout.addWidget(self._zoom_input)
        self.status.addPermanentWidget(self._zoom_container)

    def _finalize_startup(self) -> None:
        """Complete the final step of demo initialization."""
        self._refresh_tool_enables()
        self._catalog_toggle_sync = True
        try:
            self.catalog_panel_action.setChecked(False)
        finally:
            self._catalog_toggle_sync = False
        self._set_catalog_visible(False)
        self._update_mode_checks(self.qpane.getControlMode())
        self._update_action_states(0)
        self._set_status("Right-click canvas or press Ctrl+O to load images.")
        self._prime_zoom_readout()
        if self._mask_status_enabled():
            self._prime_mask_stack_readout()
        self._update_image_size_readout()
        self._prime_sam_status()

    def _set_status(self, message: str) -> None:
        """Update the status bar with the provided message."""
        self.status.showMessage(message)

    def _install_shortcuts(self) -> None:
        """Register keyboard shortcuts that drive demo actions."""
        self._shortcuts.clear()

        def _add_shortcut(sequence: QKeySequence, handler) -> None:
            """Register a shortcut and retain it for the window lifetime."""
            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

        if self._mask_tools_available():
            _add_shortcut(QKeySequence.StandardKey.Undo, self.qpane.undoMaskEdit)
            _add_shortcut(QKeySequence.StandardKey.Redo, self.qpane.redoMaskEdit)
        _add_shortcut(
            QKeySequence(Qt.Key_A),
            partial(self._step_image, -1),
        )
        _add_shortcut(
            QKeySequence(Qt.Key_D),
            partial(self._step_image, 1),
        )
        _add_shortcut(
            QKeySequence(Qt.Key_Delete),
            self._remove_current_image,
        )
        _add_shortcut(
            QKeySequence(Qt.Key_Backspace),
            self._remove_current_image,
        )
        _add_shortcut(
            QKeySequence(Qt.Key_M),
            self._create_mask_for_current_image,
        )
        for key, index in MASK_KEY_LOOKUP.items():
            _add_shortcut(
                QKeySequence(key),
                partial(self._select_mask_by_index, index),
            )

    def _handle_image_loaded(self, path: Path | None) -> None:
        """Notify listeners when a new image has finished loading."""
        name = path.name if path else "<memory>"
        self._set_status(f"Loaded image: {name}")

    def _handle_mask_saved(self, path: str, mask_id: str) -> None:
        """Announce mask autosave completion."""
        self._set_status(f"Autosaved mask {mask_id} to {path}")

    def _set_control_mode(self, mode: str) -> None:
        """Switch control modes, enforcing mask readiness for brush-based modes."""
        if mode == QPane.CONTROL_MODE_DRAW_BRUSH:
            if not self._mask_tools_available():
                self._set_status("Brush mode unavailable in core demo.")
                return
            if not self._ensure_active_mask():
                return
        if mode == QPane.CONTROL_MODE_SMART_SELECT:
            if not (self._mask_tools_available() and self._sam_tools_available()):
                self._set_status("Smart Select requires Mask+SAM features.")
                return
            if not self._ensure_active_mask():
                return
        self.qpane.setControlMode(mode)
        self._update_mode_checks(mode)
        self._set_status(f"Mode: {self._describe_mode(mode)}")
        if self.catalog_dock is not None:
            self.catalog_dock.refresh_selection()

    def _update_image_size_readout(self) -> None:
        """Mirror the current image resolution in the status bar."""
        label = getattr(self, "_image_size_label", None)
        if label is None:
            return
        image = self.qpane.currentImage
        if image.isNull():
            label.setText("-- x --px")
            return
        size = image.size()
        label.setText(f"{size.width()} x {size.height()}px")

    def _cycle_control_mode(self) -> None:
        """Advance to the next available control mode."""
        placeholder_active = self.qpane.placeholderActive()
        settings = self.qpane.settings.as_dict()
        panzoom_allowed = (not placeholder_active) or self._placeholder_panzoom_enabled(
            settings
        )
        preferred_order: list[str] = [
            QPane.CONTROL_MODE_CURSOR,
            QPane.CONTROL_MODE_PANZOOM,
        ]
        if self._mask_tools_available():
            preferred_order.append(QPane.CONTROL_MODE_DRAW_BRUSH)
        if self._sam_tools_available():
            preferred_order.append(QPane.CONTROL_MODE_SMART_SELECT)
        seen = set(preferred_order)
        for mode in self.qpane.availableControlModes():
            if mode in seen:
                continue
            preferred_order.append(mode)
            seen.add(mode)

        def _mode_allowed(mode: str) -> bool:
            """Return True when the given mode is allowed by current state."""
            if mode == QPane.CONTROL_MODE_PANZOOM:
                return panzoom_allowed
            if mode == QPane.CONTROL_MODE_DRAW_BRUSH:
                return self._mask_tools_available() and not placeholder_active
            if mode == QPane.CONTROL_MODE_SMART_SELECT:
                return (
                    self._mask_tools_available()
                    and self._sam_tools_available()
                    and not placeholder_active
                )
            if placeholder_active and mode not in {
                QPane.CONTROL_MODE_CURSOR,
                QPane.CONTROL_MODE_PANZOOM,
            }:
                return False
            return True

        ordered_modes = [mode for mode in preferred_order if _mode_allowed(mode)]
        if not ordered_modes:
            return
        current = self.qpane.getControlMode()
        if current not in ordered_modes:
            self._set_control_mode(ordered_modes[0])
            return
        if len(ordered_modes) == 1:
            return
        next_index = (ordered_modes.index(current) + 1) % len(ordered_modes)
        self._set_control_mode(ordered_modes[next_index])

    def _ensure_active_mask(self) -> bool:
        """Ensure a mask exists and is active before enabling mask-dependent modes."""
        if not self._mask_tools_available():
            return False
        if self.qpane.currentImage.isNull():
            self._set_status("Load an image before using mask tools.")
            return False
        if self.qpane.activeMaskID() is not None:
            return True
        return self._create_mask_for_current_image(announce=True) is not None

    def _describe_mode(self, mode: str) -> str:
        """Return a human-readable label for the given control mode."""
        if mode == QPane.CONTROL_MODE_CURSOR:
            return "Cursor"
        if mode == QPane.CONTROL_MODE_PANZOOM:
            return "Pan / Zoom"
        if mode == QPane.CONTROL_MODE_DRAW_BRUSH:
            return "Brush"
        if mode == QPane.CONTROL_MODE_SMART_SELECT:
            return "Smart Select (SAM)"
        return mode

    def _catalog_prefers_mask_selection(self) -> bool:
        """Return True when the catalog should highlight the active mask."""
        return self.qpane.getControlMode() in {
            QPane.CONTROL_MODE_DRAW_BRUSH,
            QPane.CONTROL_MODE_SMART_SELECT,
        }

    def _apply_catalog_focus(self, kind: str) -> None:
        """Select a matching tool mode based on the catalog click target."""
        current_mode = self.qpane.getControlMode()
        if kind == "mask":
            if current_mode in {
                QPane.CONTROL_MODE_DRAW_BRUSH,
                QPane.CONTROL_MODE_SMART_SELECT,
            }:
                return
            self._set_control_mode(QPane.CONTROL_MODE_DRAW_BRUSH)
            return
        if kind == "image":
            if current_mode in {
                QPane.CONTROL_MODE_CURSOR,
                QPane.CONTROL_MODE_PANZOOM,
            }:
                return
            self._set_control_mode(QPane.CONTROL_MODE_PANZOOM)

    def _open_images_dialog(self) -> None:
        """Open the file picker for adding images to the gallery."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Open images",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp)",
        )
        if files:
            self._load_images(Path(f) for f in files)

    def _clear_gallery(self) -> None:
        """Clear all images from the gallery via the qpane helper."""
        if not self.qpane.hasImages():
            return
        self.qpane.clearImages()
        self._set_status("Cleared all images.")

    @staticmethod
    def _set_action_checked(action: QAction | None, checked: bool) -> None:
        """Set an action's checked state while blocking signals."""
        if action is None:
            return
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)

    def _add_mask_layer(self) -> None:
        """Delegate mask creation to the qpane."""
        self._create_mask_for_current_image()

    def _load_mask_dialog(self) -> None:
        """Prompt for a mask file and import it into the active image."""
        if not self._mask_tools_available():
            self._set_status("Mask tools disabled in this mode.")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Mask",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp)",
        )
        if not file_path:
            return
        self._import_mask_from_path(Path(file_path))

    def _save_mask_dialog(self) -> None:
        """Open the save dialog for the active mask."""
        self._save_active_mask_dialog()

    def _cycle_masks_forward(self) -> None:
        """Rotate masks forward via the qpane."""
        self.qpane.cycleMasksForward()

    def _cycle_masks_backward(self) -> None:
        """Rotate masks backward via the qpane."""
        self.qpane.cycleMasksBackward()

    def _step_image(self, delta: int) -> None:
        """Advance the current image using the QPane facade."""
        ids = self.qpane.imageIDs()
        if not ids:
            self._set_status("Load an image first.")
            return
        current_id = self.qpane.currentImageID() or ids[0]
        try:
            current_index = ids.index(current_id)
        except ValueError:
            current_index = 0
        next_index = (current_index + delta) % len(ids)
        next_id = ids[next_index]
        settings = self.qpane.settings.as_dict()
        if bool(settings.get("mask_prefetch_enabled", False)):
            self.qpane.prefetchMaskOverlays(next_id, reason="step")
        self.qpane.setCurrentImageID(next_id)
        self._set_status(f"Showing image {next_index + 1} of {len(ids)}.")

    def _save_active_mask_dialog(self) -> None:
        """Save the active mask via the QPane facade."""
        if not self._mask_tools_available():
            self._set_status("Mask tools disabled in this mode.")
            return
        mask_image = self.qpane.getActiveMaskImage()
        if mask_image is None or mask_image.isNull():
            self._set_status("No active mask to save.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save mask image",
            "mask.png",
            "PNG Images (*.png)",
        )
        if not path:
            return
        if not mask_image.save(path):
            self._set_status(f"Failed to save mask to {path}.")
            return
        self._set_status(f"Saved mask to {path}.")

    def _delete_active_mask(self) -> None:
        """Delete the active mask via the QPane facade."""
        if not self._mask_tools_available():
            self._set_status("Mask tools disabled in this mode.")
            return
        image_id = self.qpane.currentImageID()
        if image_id is None:
            self._set_status("Load an image before deleting masks.")
            return
        mask_id = self.qpane.activeMaskID()
        if mask_id is None:
            self._set_status("Select a mask to delete.")
            return
        if not self.qpane.removeMaskFromImage(image_id, mask_id):
            self._set_status("Unable to delete the selected mask.")
            return
        self._set_status("Deleted active mask layer.")

    def _load_images(self, paths: Iterable[Path]) -> None:
        """Load images asynchronously to keep the UI responsive."""
        path_list = list(paths)
        if not path_list:
            return
        self._set_status(f"Queued {len(path_list)} images for loading...")
        self._load_batch_auto_select = True
        worker = ImageLoaderWorker(path_list)
        worker.signals.image_loaded.connect(self._handle_async_image_loaded)
        worker.signals.finished.connect(self._handle_async_load_finished)
        QThreadPool.globalInstance().start(worker)

    def _handle_async_image_loaded(self, path: Path, image: QImage) -> None:
        """Append a newly loaded image to the QPane gallery."""
        relink_all = self._all_images_linked()
        existing_ids = self.qpane.imageIDs()
        existing_images = self.qpane.allImages
        existing_paths = self.qpane.allImagePaths
        new_id = uuid.uuid4()
        current_id = self.qpane.currentImageID()
        if self._load_batch_auto_select:
            current_id = new_id
            self._load_batch_auto_select = False
        elif current_id is None:
            current_id = new_id
        image_map = QPane.imageMapFromLists(
            existing_images + [image],
            existing_paths + [path],
            existing_ids + [new_id],
        )
        self.qpane.setImagesByID(image_map, current_id=current_id)
        if relink_all:
            self.qpane.setAllImagesLinked(True)
        self._set_status(f"Loaded {path.name}...")

    def _handle_async_load_finished(self, count: int) -> None:
        """Announce completion of the background loading task."""
        self._load_batch_auto_select = False
        total = len(self.qpane.imageIDs())
        self._set_status(
            f"Finished loading {count} images. Total: {total}. Use Left/Right to navigate."
        )

    def _remove_current_image(self) -> None:
        """Remove the active image and select a neighbor when available."""
        ids = self.qpane.imageIDs()
        if not ids:
            self._set_status("No images to remove.")
            return
        current_id = self.qpane.currentImageID() or ids[0]
        try:
            current_index = ids.index(current_id)
        except ValueError:
            current_index = 0
        remaining_ids = ids[:current_index] + ids[current_index + 1 :]
        self.qpane.removeImageByID(current_id)
        if not remaining_ids:
            self._set_status("Removed final image; viewer cleared.")
            return
        target_index = min(current_index, len(remaining_ids) - 1)
        target_id = remaining_ids[target_index]
        self.qpane.setCurrentImageID(target_id)
        self._set_status("Removed image; showing next entry.")

    def _create_mask_for_current_image(
        self, *, announce: bool = True
    ) -> uuid.UUID | None:
        """Create a new mask for the current image via the facade."""
        if not self._mask_tools_available():
            self._set_status("Mask tools disabled in this mode.")
            return None
        if self.qpane.currentImage.isNull():
            self._set_status("Load an image before creating masks.")
            return None
        mask_id = self.qpane.createBlankMask(self.qpane.currentImage.size())
        if mask_id is None:
            self._set_status("Unable to create a mask layer.")
            return None
        color = QColor.fromHsv(random.randint(0, 359), 200, 255)
        self.qpane.setMaskProperties(mask_id, color=color)
        self.qpane.setActiveMaskID(mask_id)
        if announce:
            self._set_status(f"Mask created (ID: {mask_id}). Brush armed.")
        return mask_id

    def _select_mask_by_index(self, index: int) -> None:
        """Activate the mask at the provided index when available."""
        if not self._mask_tools_available():
            return
        image_id = self.qpane.currentImageID()
        if image_id is None:
            self._set_status("Load an image before selecting masks.")
            return
        mask_ids = self.qpane.maskIDsForImage(image_id)
        if not mask_ids:
            self._set_status("No masks on this image.")
            return
        if index >= len(mask_ids):
            self._set_status(f"Mask slot {index + 1} is empty.")
            return
        mask_id = mask_ids[index]
        self.qpane.setActiveMaskID(mask_id)
        self._set_status(f"Selected mask #{index + 1} (ID: {mask_id}).")

    def _import_mask_from_path(self, path: Path) -> None:
        """Import a mask layer from disk and activate it."""
        if not self._mask_tools_available():
            self._set_status("Mask tools disabled in this mode.")
            return
        if self.qpane.currentImage.isNull():
            self._set_status("Load an image before importing masks.")
            return
        mask_path = Path(path)
        mask_id = self.qpane.loadMaskFromFile(str(mask_path))
        if mask_id is None:
            self._set_status(f"Failed to import mask from {mask_path.name}.")
            return
        self.qpane.setActiveMaskID(mask_id)
        self._set_status(f"Imported mask layer from {mask_path.name}.")

    def _connect_qpane_signals(self) -> None:
        """Wire qpane signals to window/UI slots."""
        self.qpane.catalogChanged.connect(self._handle_catalog_event)
        self.qpane.catalogSelectionChanged.connect(
            self._handle_catalog_selection_change
        )
        self.qpane.catalogChanged.connect(lambda _event: self._refresh_tool_enables())
        self.qpane.catalogSelectionChanged.connect(
            lambda _image_id: self._refresh_tool_enables()
        )
        self.qpane.currentImageChanged.connect(
            lambda _image_id: self._refresh_tool_enables()
        )
        self.qpane.currentImageChanged.connect(
            lambda _image_id: self._update_image_size_readout()
        )
        self.qpane.imageLoaded.connect(self._handle_image_loaded)
        self.qpane.imageLoaded.connect(lambda _path: self._refresh_tool_enables())
        self.qpane.imageLoaded.connect(lambda _path: self._update_image_size_readout())
        self.qpane.zoomChanged.connect(self._update_zoom_readout)
        self.qpane.maskSaved.connect(self._handle_mask_saved)
        if self._mask_status_enabled():
            self.qpane.maskUndoStackChanged.connect(self._update_mask_stack_readout)
        self.qpane.diagnosticsOverlayToggled.connect(self._sync_overlay_toggle)
        self.qpane.diagnosticsDomainToggled.connect(self._sync_overlay_detail_toggle)
        self.qpane.samCheckpointStatusChanged.connect(
            self._handle_sam_checkpoint_status
        )
        self.qpane.samCheckpointProgress.connect(self._handle_sam_checkpoint_progress)

    def _prime_zoom_readout(self) -> None:
        """Initialize the zoom status label with the viewport's current scale."""
        try:
            zoom = self.qpane.currentZoom()
        except Exception:  # pragma: no cover - defensive guard during init
            zoom = 1.0
        self._update_zoom_readout(zoom)
        self._set_zoom_input_width()
        self._set_zoom_toggle_width()

    @staticmethod
    def _format_zoom_percent(zoom: float) -> str:
        """Return a formatted percentage label for a zoom factor."""
        return f"{zoom * 100:.1f}%"

    def _update_zoom_readout(self, zoom: float) -> None:
        """Mirror QPane.zoomChanged by showing a one-decimal percentage."""
        input_field = getattr(self, "_zoom_input", None)
        if input_field is None:
            return
        if input_field.isReadOnly():
            input_field.setText(self._format_zoom_percent(zoom))
        self._update_zoom_toggle_label(zoom)

    def _update_zoom_toggle_label(self, zoom: float) -> None:
        """Update the zoom toggle button label based on the current zoom."""
        button = getattr(self, "_zoom_toggle_button", None)
        if button is None:
            return
        if isclose(zoom, 1.0, rel_tol=0.0, abs_tol=1e-3):
            button.setText("Set Fit")
            button.setToolTip("Fit the image to the viewport.")
        else:
            button.setText("Set 1:1")
            button.setToolTip("Snap to native 1:1 pixel scale.")

    def _set_zoom_input_width(self) -> None:
        """Set the zoom input width based on the maximum percent label."""
        input_field = getattr(self, "_zoom_input", None)
        if input_field is None:
            return
        metrics = input_field.fontMetrics()
        base_width = metrics.horizontalAdvance("1000.0%")
        padding = input_field.textMargins().left() + input_field.textMargins().right()
        input_field.setFixedWidth(base_width + padding + 12)

    def _set_zoom_toggle_width(self) -> None:
        """Set a fixed width so the toggle text does not shift layout."""
        button = getattr(self, "_zoom_toggle_button", None)
        if button is None:
            return
        metrics = button.fontMetrics()
        base_width = metrics.horizontalAdvance("Set 1:1")
        button.setFixedWidth(base_width + 16)

    def _apply_zoom_from_input(self) -> None:
        """Apply a percent zoom value from the status input field."""
        input_field = getattr(self, "_zoom_input", None)
        if input_field is None:
            return
        raw = input_field.text().strip()
        if not raw:
            self._reset_zoom_input_text()
            self._exit_zoom_edit_mode()
            return
        if raw.endswith("%"):
            raw = raw[:-1].strip()
        try:
            percent = float(raw)
        except ValueError:
            self._set_status("Enter zoom as a percent (for example: 125%).")
            self._reset_zoom_input_text()
            self._exit_zoom_edit_mode()
            return
        if percent <= 0:
            self._set_status("Zoom percent must be greater than zero.")
            self._reset_zoom_input_text()
            self._exit_zoom_edit_mode()
            return
        self.qpane.applyZoom(percent / 100.0)
        input_field.setText(self._format_zoom_percent(self.qpane.currentZoom()))
        self._exit_zoom_edit_mode()

    def _toggle_zoom_target(self) -> None:
        """Toggle between fit and 1:1 zoom presets."""
        current_zoom = self.qpane.currentZoom()
        if isclose(current_zoom, 1.0, rel_tol=0.0, abs_tol=1e-3):
            self.qpane.setZoomFit()
        else:
            self.qpane.setZoom1To1()

    def _prime_mask_stack_readout(self) -> None:
        """Seed the undo/redo readout with the active mask stack state."""
        self._update_mask_stack_readout()

    def _prime_sam_status(self) -> None:
        """Seed the SAM status label from the current manager state."""
        label = getattr(self, "_sam_status_label", None)
        if label is None:
            return
        if not self.qpane.samFeatureAvailable():
            label.setText("SAM: unavailable")
            return
        ready = self.qpane.samCheckpointReady()
        label.setText("SAM: ready" if ready else "SAM: pending")
        path = self.qpane.samCheckpointPath()
        if path is not None:
            label.setToolTip(str(path))

    @staticmethod
    def _format_bytes(value: int) -> str:
        """Return a compact, human-readable byte size."""
        size = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    def _set_sam_status_label(self, text: str, tooltip: str | None = None) -> None:
        """Update the SAM status label when it is present."""
        label = getattr(self, "_sam_status_label", None)
        if label is None:
            return
        label.setText(text)
        if tooltip is not None:
            label.setToolTip(tooltip)

    def _handle_sam_checkpoint_status(self, status: str, path: Path) -> None:
        """Mirror SAM checkpoint status changes in the status bar."""
        normalized = status.strip().lower()
        if normalized == "downloading":
            text = "SAM: downloading"
            self._sam_status_auto_hide = True
        elif normalized == "ready":
            if not self._sam_status_auto_hide:
                return
            text = "SAM: ready"
        elif normalized == "failed":
            text = "SAM: failed"
            self._sam_status_auto_hide = False
        elif normalized == "missing":
            text = "SAM: missing"
            self._sam_status_auto_hide = False
        else:
            text = f"SAM: {status}"
            self._sam_status_auto_hide = False
        self._set_sam_status_label(text, tooltip=str(path))
        if normalized == "ready" and self._sam_status_auto_hide:
            label = getattr(self, "_sam_status_label", None)
            if label is not None:
                QTimer.singleShot(10000, label.hide)

    def _handle_sam_checkpoint_progress(
        self, downloaded: int, total: int | None
    ) -> None:
        """Show SAM checkpoint download progress in the status bar."""
        self._sam_status_auto_hide = True
        if total:
            percent = max(0.0, min(100.0, (downloaded / total) * 100.0))
            text = f"SAM: downloading {percent:.0f}%"
        else:
            text = f"SAM: downloading {self._format_bytes(downloaded)}"
        self._set_sam_status_label(text)

    def _update_mask_stack_readout(self, mask_id: uuid.UUID | None = None) -> None:
        """Show undo/redo depth updates emitted via QPane.maskUndoStackChanged."""
        label = getattr(self, "_mask_stack_label", None)
        if label is None:
            return
        if not self._mask_tools_available():
            label.setText("Undo: -- / Redo: --")
            return
        active_mask_id = mask_id or self.qpane.activeMaskID()
        if active_mask_id is None:
            label.setText("Undo: 0 / Redo: 0")
            return
        state = None
        try:
            state = self.qpane.getMaskUndoState(active_mask_id)
        except Exception:  # pragma: no cover - defensive UI read
            state = None
        if state is None:
            label.setText("Undo: 0 / Redo: 0")
            return
        label.setText(f"Undo: {state.undo_depth} / Redo: {state.redo_depth}")

    def _handle_catalog_selection_change(self, image_id: uuid.UUID | None) -> None:
        """Show the active image path in the status bar when selection changes."""
        self._update_mask_stack_readout()
        if image_id is None:
            self._set_status("No image selected.")
            self._update_image_size_readout()
            return
        path = self.qpane.currentImagePath
        if path is None:
            self._set_status("Selected image has no file path.")
            self._update_image_size_readout()
            return
        self._set_status(f"Selected image path: {path}")
        self._update_image_size_readout()

    def _open_config_dialog(self) -> None:
        """Launch the config dialog and apply changes when accepted."""
        dialog = ConfigDialog(
            self._example_config, self, active_features=self._active_features
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.result()
        if not result.values:
            return
        self._apply_configuration(
            result.values,
            config_fields=result.config_fields,
            restart_fields=result.restart_fields,
        )

    def _apply_configuration(
        self,
        values: dict[str, object],
        *,
        config_fields: set[str],
        restart_fields: set[str] | None = None,
    ) -> None:
        """Apply dialog changes live and persist them into the example config."""
        previous_overlay = getattr(self, "_overlay_enabled", False)
        config_snapshot = self._example_config.as_dict()
        concurrency_changed = self._persist_concurrency_config(values)
        config_updates = {
            key: value
            for key, value in values.items()
            if key in config_fields or key in config_snapshot
        }
        restart_fields = set(restart_fields or ())
        live_updates = {
            key: value
            for key, value in config_updates.items()
            if key not in restart_fields
        }
        deferred_updates = {
            key: value for key, value in config_updates.items() if key in restart_fields
        }
        live_config = self._example_config.copy()
        if live_updates:
            collapsed_live = ConfigDialog.collapse_values(live_updates)
            live_config.configure(**collapsed_live)
            self._example_config.configure(**collapsed_live)
        if deferred_updates:
            self._example_config.configure(
                **ConfigDialog.collapse_values(deferred_updates)
            )
        self._apply_detail_preferences(values)
        target_overlay = bool(
            values.get(
                "diagnostics_overlay_enabled",
                config_snapshot.get("diagnostics_overlay_enabled", previous_overlay),
            )
        )
        self._apply_live_settings(
            live_updates,
            overlay_target=target_overlay,
            config_fields=config_fields,
            concurrency_changed=concurrency_changed,
            config_override=live_config,
            preconfigured=True,
        )
        if any(name in _SAM_CONFIG_FIELDS for name in live_updates):
            success, message = self.qpane.refreshSamFeature()
            if not success:
                self._set_status(message)
        if deferred_updates:
            self._announce_sam_restart_required(deferred_updates)
        else:
            self._set_status("Configuration applied.")

    def _apply_live_settings(
        self,
        values: dict[str, object],
        overlay_target: bool | None = None,
        *,
        config_fields: set[str],
        concurrency_changed: bool = False,
        config_override: Config | None = None,
        preconfigured: bool = False,
    ) -> None:
        """Apply config-field updates live to the QPane and overlay."""
        config_snapshot = self._example_config.as_dict()
        live_updates = {
            k: v
            for k, v in values.items()
            if k in config_fields or k in config_snapshot
        }
        if live_updates and not preconfigured:
            collapsed = ConfigDialog.collapse_values(live_updates)
            target_config = config_override or self._example_config
            target_config.configure(**collapsed)
        apply_config = config_override or self._example_config
        if live_updates or concurrency_changed:
            self.qpane.applySettings(config=apply_config)
            self._refresh_tool_enables()
        if overlay_target is None:
            overlay_target = bool(
                values.get(
                    "diagnostics_overlay_enabled",
                    config_snapshot.get("diagnostics_overlay_enabled", False),
                )
            )
        self._apply_overlay_setting(overlay_target, announce=True)

    def _announce_sam_restart_required(
        self, deferred_updates: dict[str, object]
    ) -> None:
        """Explain that blocking/disabled SAM updates need a restart."""
        sam_updates = [name for name in deferred_updates if name in _SAM_CONFIG_FIELDS]
        if not sam_updates:
            return
        self._set_status(
            "SAM checkpoint changes queued. Restart the demo to apply blocking/disabled settings."
        )

    def _persist_concurrency_config(self, values: dict[str, object]) -> bool:
        """Store concurrency dialog values into the example config snapshot."""
        config = getattr(self, "_example_config", None)
        if config is None or not values:
            return False

        def _coerce_int(raw_value: object, *, minimum: int | None = None) -> int | None:
            """Coerce config dialog numeric inputs to ints with optional minimum."""
            try:
                coerced = int(raw_value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            if minimum is not None and coerced < minimum:
                return None
            return coerced

        def _normalize_map(
            mapping: object,
            *,
            minimum: int | None = None,
        ) -> dict[str, int] | None:
            """Normalize mapping values to ints while discarding invalid entries."""
            if not isinstance(mapping, dict):
                return None
            normalized: dict[str, int] = {}
            for key, raw_value in mapping.items():
                coerced = _coerce_int(raw_value, minimum=minimum)
                if coerced is None:
                    continue
                normalized[str(key)] = coerced
            return normalized or None

        concurrency_updates: dict[str, object] = {}
        if "concurrency_max_workers" in values:
            workers = _coerce_int(values.get("concurrency_max_workers"), minimum=1)
            if workers is not None:
                concurrency_updates["max_workers"] = workers
        if "concurrency_max_pending_total" in values:
            pending_total = _coerce_int(
                values.get("concurrency_max_pending_total"), minimum=0
            )
            concurrency_updates["max_pending_total"] = (
                pending_total if pending_total and pending_total > 0 else None
            )
        map_specs = (
            ("concurrency_category_priorities_map", "category_priorities", None),
            ("concurrency_category_limits_map", "category_limits", 0),
            ("concurrency_pending_limits_map", "pending_limits", 0),
        )
        for source_key, target_key, minimum in map_specs:
            normalized = _normalize_map(values.get(source_key), minimum=minimum)
            if normalized is not None:
                concurrency_updates[target_key] = normalized
        devices_raw = values.get("concurrency_device_limits_map")
        filtered_devices = (
            _filter_sam_device_limits(devices_raw)
            if isinstance(devices_raw, dict)
            else {}
        )
        if filtered_devices:
            concurrency_updates["device_limits"] = {
                str(device): {
                    str(category): int(limit) for category, limit in limits.items()
                }
                for device, limits in filtered_devices.items()
            }
        if concurrency_updates:
            config.configure(concurrency=concurrency_updates)
            return True
        return False

    def _apply_overlay_setting(self, enabled: bool, *, announce: bool = True) -> None:
        """Toggle diagnostics overlay state and align UI controls."""
        previous = getattr(self, "_overlay_enabled", None)
        self._overlay_enabled = enabled
        if hasattr(self, "_example_config"):
            self._example_config.configure(diagnostics_overlay_enabled=enabled)
        self.qpane.setDiagnosticsOverlayEnabled(enabled)
        toggle = getattr(self, "overlay_toggle_action", None)
        self._set_action_checked(toggle, enabled)
        self._refresh_overlay_detail_enabled()
        if announce and previous is not None and previous != enabled:
            message = (
                "Diagnostics overlay enabled."
                if enabled
                else "Diagnostics overlay hidden."
            )
            self._set_status(message)

    def _sync_overlay_toggle(self, enabled: bool) -> None:
        """Align overlay UI state when QPane signals a change."""
        previous = getattr(self, "_overlay_enabled", None)
        if previous == enabled:
            return
        self._overlay_enabled = enabled
        if hasattr(self, "_example_config"):
            self._example_config.configure(diagnostics_overlay_enabled=enabled)
        toggle = getattr(self, "overlay_toggle_action", None)
        self._set_action_checked(toggle, enabled)
        self._refresh_overlay_detail_enabled()
        if previous is not None:
            message = (
                "Diagnostics overlay enabled."
                if enabled
                else "Diagnostics overlay hidden."
            )
            self._set_status(message)

    def _apply_detail_preferences(self, values: dict[str, object]) -> None:
        """Update overlay detail preferences and synchronize action state."""
        if _DIAGNOSTIC_DOMAIN_FIELD not in self._dialog_all_fields:
            self._overlay_detail_actions.clear()
            self._refresh_overlay_detail_enabled()
            return
        available_domains = set(self.qpane.diagnosticsDomains())
        config_snapshot = self._example_config.as_dict()
        configured = values.get(
            _DIAGNOSTIC_DOMAIN_FIELD,
            config_snapshot.get(_DIAGNOSTIC_DOMAIN_FIELD, ()),
        )
        target_domains = tuple(
            domain
            for domain in self._ordered_domains(configured)
            if domain in available_domains
        )
        for domain in available_domains:
            self.qpane.setDiagnosticsDomainEnabled(domain, domain in target_domains)
        self._sync_overlay_detail_actions()

    def _handle_overlay_toggled(self, checked: bool) -> None:
        """Handle overlay toggle action changes."""
        self._apply_overlay_setting(checked)

    def _handle_overlay_detail_toggled(self, domain: str, checked: bool) -> None:
        """Persist detail toggles and refresh overlay preferences."""
        config_snapshot = self._example_config.as_dict()
        configured = set(config_snapshot.get(_DIAGNOSTIC_DOMAIN_FIELD, ()) or ())
        if checked:
            configured.add(domain)
        else:
            configured.discard(domain)
        ordered = self._ordered_domains(tuple(configured))
        self._example_config.configure(**{_DIAGNOSTIC_DOMAIN_FIELD: ordered})
        self._apply_detail_preferences({_DIAGNOSTIC_DOMAIN_FIELD: ordered})

    def _sync_overlay_detail_toggle(self, domain: str, enabled: bool) -> None:
        """Align detail toggle UI when QPane signals a change."""
        if _DIAGNOSTIC_DOMAIN_FIELD not in self._dialog_all_fields:
            return
        config_snapshot = self._example_config.as_dict()
        configured = list(config_snapshot.get(_DIAGNOSTIC_DOMAIN_FIELD, ()) or ())
        if enabled and domain not in configured:
            configured.append(domain)
        if not enabled and domain in configured:
            configured = [item for item in configured if item != domain]
        ordered = self._ordered_domains(tuple(configured))
        if tuple(config_snapshot.get(_DIAGNOSTIC_DOMAIN_FIELD, ())) != ordered:
            self._example_config.configure(**{_DIAGNOSTIC_DOMAIN_FIELD: ordered})
        action = getattr(self, "_overlay_detail_actions", {}).get(domain)
        self._set_action_checked(action, enabled)
        self._refresh_overlay_detail_enabled()

    def _ordered_domains(self, domains: Sequence[str]) -> tuple[str, ...]:
        """Return configured domains ordered by the QPane's advertised list."""
        available_order = self.qpane.diagnosticsDomains()
        domain_set = set(domains)
        return tuple(domain for domain in available_order if domain in domain_set)

    def _refresh_overlay_detail_enabled(self) -> None:
        """Enable or disable detail actions based on overlay visibility."""
        enabled = getattr(self, "_overlay_enabled", False)
        for action in self._overlay_detail_actions.values():
            action.setEnabled(enabled)

    def _sync_overlay_detail_actions(self) -> None:
        """Align overlay detail actions with the stored configuration."""
        if not hasattr(self, "_overlay_detail_actions"):
            return
        if _DIAGNOSTIC_DOMAIN_FIELD not in self._dialog_all_fields:
            self._overlay_detail_actions.clear()
            self._refresh_overlay_detail_enabled()
            return
        config_snapshot = self._example_config.as_dict()
        configured = set(config_snapshot.get(_DIAGNOSTIC_DOMAIN_FIELD, ()) or ())
        for domain, action in self._overlay_detail_actions.items():
            target = (
                self.qpane.diagnosticsDomainEnabled(domain)
                if hasattr(self, "qpane")
                else domain in configured
            )
            self._set_action_checked(action, target)
        self._refresh_overlay_detail_enabled()

    def _collect_image_state(self):
        """Capture catalog contents needed to rebuild the qpane."""
        image_ids = self.qpane.imageIDs()
        if not image_ids:
            return None
        return {
            "ids": image_ids,
            "images": self.qpane.allImages,
            "paths": self.qpane.allImagePaths,
            "current_id": self.qpane.currentImageID(),
        }

    def _restore_image_state(self, state) -> None:
        """Restore catalog contents after rebuilding the qpane."""
        image_map = QPane.imageMapFromLists(
            state["images"], state["paths"], state["ids"]
        )
        current_id = state["current_id"] or state["ids"][-1]
        self.qpane.setImagesByID(image_map, current_id)

    def _ensure_global_event_filter(self) -> None:
        """Install a global event filter once so we can observe dock key events."""
        if self._event_filter_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._event_filter_installed = True

    def eventFilter(self, watched, event):  # type: ignore[override]
        """Route global demo shortcuts like spacebar pan and right-click open."""
        try:
            if self._handle_zoom_editor_events(watched, event):
                return True
            if self._handle_open_image_event(watched, event):
                return True
            if self._handle_spacebar_event(watched, event):
                return True
        except Exception:
            logger.exception(
                "Global event filter failed (type=%s, watched=%s)",
                getattr(event, "type", lambda: "<unknown>")(),
                type(watched).__name__,
            )
            return False
        return super().eventFilter(watched, event)

    def _handle_zoom_editor_events(self, watched, event) -> bool:
        """Toggle the zoom field between display and edit states."""
        input_field = getattr(self, "_zoom_input", None)
        if input_field is None or watched is not input_field:
            return False
        if event.type() == QEvent.MouseButtonDblClick:
            self._enter_zoom_edit_mode()
            return True
        if event.type() == QEvent.FocusOut and not input_field.isReadOnly():
            self._apply_zoom_from_input()
            return False
        if event.type() == QEvent.FontChange:
            self._set_zoom_input_width()
            self._set_zoom_toggle_width()
        return False

    def _enter_zoom_edit_mode(self) -> None:
        """Switch the zoom input into editable mode."""
        input_field = getattr(self, "_zoom_input", None)
        if input_field is None:
            return
        input_field.setReadOnly(False)
        input_field.setFrame(True)
        input_field.setStyleSheet("")
        input_field.setCursor(Qt.IBeamCursor)
        input_field.setFocus(Qt.MouseFocusReason)
        input_field.selectAll()

    def _exit_zoom_edit_mode(self) -> None:
        """Return the zoom input to its display-only state."""
        input_field = getattr(self, "_zoom_input", None)
        if input_field is None:
            return
        input_field.setReadOnly(True)
        input_field.setFrame(False)
        input_field.setStyleSheet(
            "QLineEdit#zoomPercentInput { background: transparent; border: none; }"
        )
        input_field.setCursor(Qt.ArrowCursor)

    def _reset_zoom_input_text(self) -> None:
        """Restore the zoom input text to the current zoom value."""
        input_field = getattr(self, "_zoom_input", None)
        if input_field is None:
            return
        input_field.setText(self._format_zoom_percent(self.qpane.currentZoom()))

    def _handle_open_image_event(self, watched, event) -> bool:
        """Open the image dialog when right-clicking within the qpane."""
        qpane_widget = getattr(self, "qpane", None)
        if qpane_widget is None:
            return False
        if event.type() != QEvent.MouseButtonPress:
            return False
        if event.button() != Qt.MouseButton.RightButton:
            return False
        if watched is qpane_widget:
            self._open_images_dialog()
            event.accept()
            return True
        if isinstance(watched, QWidget):
            try:
                if qpane_widget.isAncestorOf(watched):
                    self._open_images_dialog()
                    event.accept()
                    return True
            except RuntimeError:
                return False
        return False

    def _handle_spacebar_event(self, watched, event) -> bool:
        """Decide whether to forward spacebar presses to the qpane for panning."""
        qpane_widget = getattr(self, "qpane", None)
        if qpane_widget is None:
            return False
        event_type = event.type()
        if event_type not in (
            QEvent.KeyPress,
            QEvent.KeyRelease,
            QEvent.ShortcutOverride,
        ):
            return False
        if event.key() != Qt.Key_Space:
            return False
        if event_type in (QEvent.KeyPress, QEvent.ShortcutOverride):
            if self._should_forward_space_event(qpane_widget, watched, event):
                if not self._space_forwarded_to_qpane and not event.isAutoRepeat():
                    self._space_restore_mode = qpane_widget.getControlMode()
                    if self._space_restore_mode != QPane.CONTROL_MODE_PANZOOM:
                        qpane_widget.setControlMode(QPane.CONTROL_MODE_PANZOOM)
                event.accept()
                self._space_forwarded_to_qpane = True
                return True
        if event_type == QEvent.KeyRelease and self._space_forwarded_to_qpane:
            event.accept()
            if event.isAutoRepeat():
                return True
            restore_mode = getattr(self, "_space_restore_mode", None)
            if restore_mode is not None:
                qpane_widget.setControlMode(restore_mode)
            self._space_restore_mode = None
            self._space_forwarded_to_qpane = False
            return True
        return False

    def _should_forward_space_event(self, qpane_widget: QPane, watched, event) -> bool:
        """Return True when a space press should be forwarded to the qpane."""
        if event.isAutoRepeat():
            return False
        if not self._qpane_under_cursor(qpane_widget):
            return False
        if watched is qpane_widget:
            return False
        if isinstance(watched, QWidget):
            try:
                if watched is qpane_widget or qpane_widget.isAncestorOf(watched):
                    return False
            except RuntimeError:
                return False
        return True

    def _qpane_under_cursor(self, qpane_widget: QPane) -> bool:
        """Return True when the cursor currently hovers the qpane or its children."""
        cursor_pos = QCursor.pos()
        widget = QApplication.widgetAt(cursor_pos)
        if widget is not None:
            try:
                if widget is qpane_widget or qpane_widget.isAncestorOf(widget):
                    return True
            except RuntimeError:
                return False
        qpane_top_left = qpane_widget.mapToGlobal(QPoint(0, 0))
        qpane_rect = QRect(qpane_top_left, qpane_widget.size())
        return qpane_rect.contains(cursor_pos)

    def _create_actions(self) -> None:
        """Instantiate example actions grouped by gallery, masks/SAM, diagnostics, and config."""
        mask_enabled = self._mask_tools_available()
        sam_enabled = self._sam_tools_available()
        self.open_images_action = QAction("Open Images...", self)
        self.open_images_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_images_action.triggered.connect(self._open_images_dialog)
        self.clear_action = QAction("Clear", self)
        self.clear_action.triggered.connect(self._clear_gallery)
        self.prev_image_action = QAction("◀ Prev", self)
        self.prev_image_action.setShortcut(Qt.Key_Left)
        self.prev_image_action.triggered.connect(lambda: self._step_image(-1))
        self.next_image_action = QAction("Next ▶", self)
        self.next_image_action.setShortcut(Qt.Key_Right)
        self.next_image_action.triggered.connect(lambda: self._step_image(1))
        self.mode_pan_action = QAction("Pan/Zoom", self, checkable=True)
        self.mode_pan_action.triggered.connect(
            lambda: self._set_control_mode(QPane.CONTROL_MODE_PANZOOM)
        )
        self.mode_cursor_action = QAction("Cursor", self, checkable=True)
        self.mode_cursor_action.triggered.connect(
            lambda: self._set_control_mode(QPane.CONTROL_MODE_CURSOR)
        )
        self.mode_brush_action: QAction | None = None
        self.mode_smart_action: QAction | None = None
        self.cycle_mode_action: QAction | None = None
        self.add_mask_action: QAction | None = None
        self.delete_mask_action: QAction | None = None
        self.load_mask_action: QAction | None = None
        self.save_mask_action: QAction | None = None
        self.cycle_masks_backward_action: QAction | None = None
        self.cycle_masks_forward_action: QAction | None = None
        mask_actions: list[QAction] = []
        if mask_enabled:
            self.add_mask_action = QAction("Add Mask", self)
            self.add_mask_action.triggered.connect(self._add_mask_layer)
            mask_actions.append(self.add_mask_action)
            self.delete_mask_action = QAction("Delete Mask", self)
            self.delete_mask_action.triggered.connect(self._delete_active_mask)
            self.delete_mask_action.setEnabled(False)
            mask_actions.append(self.delete_mask_action)
            self.load_mask_action = QAction("Load Mask...", self)
            self.load_mask_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
            self.load_mask_action.triggered.connect(self._load_mask_dialog)
            mask_actions.append(self.load_mask_action)
            self.save_mask_action = QAction("Save Mask...", self)
            self.save_mask_action.setShortcut(QKeySequence.StandardKey.Save)
            self.save_mask_action.triggered.connect(self._save_mask_dialog)
            mask_actions.append(self.save_mask_action)
            self.cycle_masks_forward_action = QAction("Mask Up", self)
            self.cycle_masks_forward_action.triggered.connect(self._cycle_masks_forward)
            mask_actions.append(self.cycle_masks_forward_action)
            self.cycle_masks_backward_action = QAction("Mask Down", self)
            self.cycle_masks_backward_action.triggered.connect(
                self._cycle_masks_backward
            )
            mask_actions.append(self.cycle_masks_backward_action)
            self.mode_brush_action = QAction("Brush", self, checkable=True)
            self.mode_brush_action.triggered.connect(
                lambda: self._set_control_mode(QPane.CONTROL_MODE_DRAW_BRUSH)
            )
            if sam_enabled:
                self.mode_smart_action = QAction("Smart Select", self, checkable=True)
                self.mode_smart_action.triggered.connect(
                    lambda: self._set_control_mode(QPane.CONTROL_MODE_SMART_SELECT)
                )
        # Only expose cycling when more than one mode is available.
        available_modes = [self.mode_pan_action, self.mode_cursor_action]
        if self.mode_brush_action:
            available_modes.append(self.mode_brush_action)
        if self.mode_smart_action:
            available_modes.append(self.mode_smart_action)
        if len(available_modes) > 1:
            self.cycle_mode_action = QAction("Cycle Mode", self)
            self.cycle_mode_action.setShortcut(Qt.Key_B)
            self.cycle_mode_action.triggered.connect(self._cycle_control_mode)
        self.config_action = QAction("Config", self)
        self.config_action.triggered.connect(self._open_config_dialog)
        self.catalog_panel_action = QAction("Catalog", self, checkable=True)
        self.catalog_panel_action.setChecked(False)
        self.catalog_panel_action.toggled.connect(self._handle_catalog_toggled)
        self.quick_reference_action = QAction("Quick Reference", self)
        self.quick_reference_action.triggered.connect(self._show_reference_popover)
        self.overlay_hook_action = QAction(
            "Custom Overlay (Hook)", self, checkable=True
        )
        self.overlay_hook_action.toggled.connect(self._handle_custom_overlay_toggled)
        self.cursor_hook_action = QAction("Custom Cursor Tool", self, checkable=True)
        self.cursor_hook_action.toggled.connect(self._handle_custom_tool_toggled)
        self.lens_hook_action = QAction("Custom Cursor + Overlay", self, checkable=True)
        self.lens_hook_action.toggled.connect(self._handle_lens_demo_toggled)
        self._gallery_actions = [
            (self.clear_action, True),
            (self.prev_image_action, True),
            (self.next_image_action, True),
        ]
        for action in mask_actions:
            self._gallery_actions.append((action, True))

    def _create_menus(self) -> None:
        """Construct the menubar to illustrate how facade-backed actions are organized."""
        menu_bar = self.menuBar()
        menu_bar.clear()
        file_menu = menu_bar.addMenu("&File")
        file_menu.addAction(self.open_images_action)
        file_menu.addAction(self.clear_action)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("Exit")
        exit_action.triggered.connect(self.close)
        view_menu = menu_bar.addMenu("&View")
        view_menu.addAction(self.catalog_panel_action)
        view_menu.addAction(self.quick_reference_action)
        view_menu.addSeparator()
        diagnostics_menu = view_menu.addMenu("Diagnostics Overlay")
        self._build_diagnostics_menu(diagnostics_menu)
        hooks_menu = menu_bar.addMenu("Hooks")
        hooks_menu.addAction(self.overlay_hook_action)
        hooks_menu.addAction(self.cursor_hook_action)
        hooks_menu.addAction(self.lens_hook_action)
        menu_bar.addAction(self.config_action)

    def _build_diagnostics_menu(self, menu: QMenu) -> None:
        """Populate the diagnostics overlay submenu with toggle actions."""
        menu.clear()
        self.overlay_toggle_action = QAction("Enable Overlay", self, checkable=True)
        self.overlay_toggle_action.setChecked(self._overlay_enabled)
        self.overlay_toggle_action.toggled.connect(self._handle_overlay_toggled)
        menu.addAction(self.overlay_toggle_action)
        menu.addSeparator()
        self._overlay_detail_actions.clear()
        detail_allowed = _DIAGNOSTIC_DOMAIN_FIELD in self._dialog_all_fields
        available_domains = tuple(self.qpane.diagnosticsDomains())
        config_snapshot = self._example_config.as_dict()
        configured = config_snapshot.get(_DIAGNOSTIC_DOMAIN_FIELD, ())
        for domain in available_domains:
            if not detail_allowed:
                continue
            label = _DETAIL_LABELS.get(domain, domain.title())
            action = QAction(label, self, checkable=True)
            action.setChecked(
                self.qpane.diagnosticsDomainEnabled(domain)
                if hasattr(self, "qpane")
                else domain in configured
            )
            action.toggled.connect(partial(self._handle_overlay_detail_toggled, domain))
            menu.addAction(action)
            self._overlay_detail_actions[domain] = action
        self._refresh_overlay_detail_enabled()

    def _build_toolbars(self) -> None:
        """Create the vertical tools toolbar grouping navigation, modes, masks, and diagnostics."""
        if hasattr(self, "_tools_toolbar") and self._tools_toolbar is not None:
            self.removeToolBar(self._tools_toolbar)
        self._tools_toolbar = QToolBar("Tools", self)
        self._tools_toolbar.setMovable(False)
        self._tools_toolbar.setOrientation(Qt.Vertical)
        self._tools_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.LeftToolBarArea, self._tools_toolbar)

        def _add_group(actions: list[QAction | None]) -> bool:
            """Add the provided actions to the tools toolbar, returning True if any were added."""
            added = False
            for action in actions:
                if action is None:
                    continue
                self._tools_toolbar.addAction(action)
                added = True
            return added

        navigation_added = _add_group([self.prev_image_action, self.next_image_action])
        mode_actions = [
            self.mode_cursor_action,
            self.mode_pan_action,
            self.mode_brush_action,
            self.mode_smart_action,
        ]
        if self._custom_tool_action is not None:
            mode_actions.append(self._custom_tool_action)
        if self._lens_tool_action is not None:
            mode_actions.append(self._lens_tool_action)
        has_modes = any(action is not None for action in mode_actions) or bool(
            self.cycle_mode_action
        )
        if navigation_added and has_modes:
            self._tools_toolbar.addSeparator()
        modes_added = _add_group(mode_actions)
        if self.cycle_mode_action is not None:
            self._tools_toolbar.addAction(self.cycle_mode_action)
            modes_added = True
        mask_stack = [
            self.add_mask_action,
            self.delete_mask_action,
            self.load_mask_action,
            self.save_mask_action,
            self.cycle_masks_backward_action,
            self.cycle_masks_forward_action,
        ]
        has_masks = any(action is not None for action in mask_stack)
        if modes_added and has_masks:
            self._tools_toolbar.addSeparator()
        _add_group(mask_stack)

    def _build_catalog_panel(self) -> None:
        """Create or refresh the catalog panel embedded in the splitter.

        Extend by swapping the dock implementation or layering widgets that consume
        ``CatalogSnapshot`` from QPane signals (``catalogChanged``, ``catalogSelectionChanged``)
        and link toggles via
        ``qpane.setLinkedGroups()``.
        """
        if self.catalog_dock is not None:
            try:
                self.catalog_dock.visibilityChanged.disconnect(
                    self._sync_catalog_toggle
                )
            except (TypeError, RuntimeError):
                pass
            self._catalog_container_layout.removeWidget(self.catalog_dock)
            self.catalog_dock.setParent(None)
            self.catalog_dock.deleteLater()
            self.catalog_dock = None
        self.catalog_dock = CatalogDock(
            self.qpane,
            show_mask_selection=self._catalog_prefers_mask_selection,
            on_focus_requested=self._apply_catalog_focus,
            set_status=self._set_status,
            parent=self._catalog_container,
        )
        self.catalog_dock.visibilityChanged.connect(self._sync_catalog_toggle)
        width_hint = max(self.catalog_dock.panelWidthHint(), 1)
        self._catalog_panel_width_hint = width_hint
        self._catalog_container_layout.addWidget(self.catalog_dock)
        self.catalog_dock.hide()
        self._apply_catalog_width_constraints(False)
        self._handle_catalog_event(None)

    def _apply_catalog_width_constraints(self, visible: bool) -> None:
        """Apply catalog sizing defaults while allowing user expansion."""
        if self._catalog_panel_width_hint is None:
            return
        min_width = self._catalog_panel_width_hint if visible else 0
        self._catalog_container.setMinimumWidth(min_width)
        max_width = (
            getattr(
                self, "_catalog_container_default_max", self._catalog_panel_width_hint
            )
            if visible
            else self._catalog_panel_width_hint
        )
        self._catalog_container.setMaximumWidth(max_width)

    def _sync_splitter_for_catalog_width(self) -> None:
        """Force the splitter to honor the fixed catalog width when visible."""
        width = self._catalog_panel_width_hint
        if width is None:
            return
        sizes = self._splitter.sizes()
        total = sum(sizes)
        if total <= 0:
            total = max(self.width(), width * 2)
        total = max(total, width + 1)
        secondary = max(total - width, 1)
        self._splitter.setSizes([width, secondary])

    def _set_catalog_visible(self, visible: bool) -> None:
        """Show or hide the catalog dock while maintaining width constraints."""
        self._apply_catalog_visibility(visible, user_initiated=False)

    def _apply_catalog_visibility(self, visible: bool, *, user_initiated: bool) -> None:
        """Toggle catalog visibility while preserving splitter constraints and user intent."""
        if self.catalog_dock is None:
            return
        if user_initiated:
            self._catalog_user_override = True
        self._catalog_toggle_sync = True
        try:
            self._set_action_checked(
                getattr(self, "catalog_panel_action", None), visible
            )
            self._apply_catalog_width_constraints(visible)
            self.catalog_dock.setVisible(visible)
            self._catalog_container.setVisible(visible)
            if visible:
                self._sync_splitter_for_catalog_width()
        finally:
            self._catalog_toggle_sync = False

    def _handle_catalog_toggled(self, expanded: bool) -> None:
        """Respond to catalog visibility toggles from the dock widget."""
        if self._catalog_toggle_sync:
            return
        self._apply_catalog_visibility(expanded, user_initiated=True)
        message = "Catalog shown." if expanded else "Catalog hidden."
        self._set_status(message)

    def _sync_catalog_toggle(self, visible: bool) -> None:
        """Synchronize the toggle action with the current catalog visibility."""
        if self._catalog_toggle_sync:
            return
        self._apply_catalog_visibility(visible, user_initiated=False)

    def _maybe_auto_show_catalog(self, count: int, *, force: bool = False) -> None:
        """Automatically reveal the catalog when enough images are loaded."""
        if self.catalog_panel_action is None:
            return
        if (not force and count < 2) or self.catalog_panel_action.isChecked():
            return
        if self._catalog_user_override:
            return
        self._apply_catalog_visibility(True, user_initiated=False)

    def _build_reference_hints(
        self, mask_enabled: bool, sam_enabled: bool
    ) -> list[str]:
        """Return the shortcut hints displayed in the quick-reference dialog."""
        return demo_text.reference_hints(mask_enabled, sam_enabled)

    def _show_reference_popover(self) -> None:
        """Display or refocus the quick-reference dialog."""
        if self._reference_dialog is None:
            dialog = QuickReferenceDialog(self._reference_hints, self)
            dialog.finished.connect(self._handle_reference_closed)
            self._reference_dialog = dialog
        self._reference_dialog.show()
        self._reference_dialog.raise_()
        self._reference_dialog.activateWindow()

    def _handle_reference_closed(self, _: int) -> None:
        """Clear the reference dialog pointer after it closes."""
        self._reference_dialog = None

    def _update_mode_checks(self, mode: str) -> None:
        """Update mode toggle actions to reflect the current control mode."""
        self.mode_cursor_action.setChecked(mode == QPane.CONTROL_MODE_CURSOR)
        self.mode_pan_action.setChecked(mode == QPane.CONTROL_MODE_PANZOOM)
        if self.mode_brush_action:
            self.mode_brush_action.setChecked(mode == QPane.CONTROL_MODE_DRAW_BRUSH)
        if self.mode_smart_action:
            self.mode_smart_action.setChecked(mode == QPane.CONTROL_MODE_SMART_SELECT)
        if self._custom_tool_action is not None:
            self._custom_tool_action.setChecked(mode == _CUSTOM_TOOL_MODE)
        if self._lens_tool_action is not None:
            self._lens_tool_action.setChecked(mode == _LENS_TOOL_MODE)

    def _update_action_states(self, count: int) -> None:
        """Enable or disable actions based on catalog size and feature availability."""
        if not hasattr(self, "_gallery_actions"):
            return
        has_images = count > 0
        for action, base_enabled in self._gallery_actions:
            action.setEnabled(base_enabled and has_images)
        if count < 2 and self._all_images_linked():
            self.qpane.setAllImagesLinked(False)
            self._set_status("Pan/zoom linking disabled.")
        self._maybe_auto_show_catalog(count)

    def _refresh_tool_enables(self) -> None:
        """Toggle tool availability based on placeholder state and config."""
        if not hasattr(self, "mode_cursor_action"):
            return
        placeholder_active = self.qpane.placeholderActive()
        settings = self.qpane.settings.as_dict()
        panzoom_allowed = (not placeholder_active) or self._placeholder_panzoom_enabled(
            settings
        )
        mask_available = self._mask_tools_available()
        sam_available = self._sam_tools_available()
        mask_enabled = mask_available and not placeholder_active
        sam_enabled = sam_available and not placeholder_active
        panzoom_enabled = panzoom_allowed

        def _enable(action: QAction | None, enabled: bool) -> None:
            """Enable or disable the action and update its checked state if disabled."""
            if action is None:
                return
            action.setEnabled(enabled)
            if not enabled:
                self._set_action_checked(action, False)

        _enable(self.mode_pan_action, panzoom_enabled)
        self.mode_cursor_action.setEnabled(True)
        _enable(self.mode_brush_action, mask_enabled)
        _enable(self.mode_smart_action, sam_enabled)
        mask_actions = (
            self.add_mask_action,
            self.delete_mask_action,
            self.load_mask_action,
            self.save_mask_action,
            self.cycle_masks_backward_action,
            self.cycle_masks_forward_action,
        )
        for action in mask_actions:
            _enable(action, mask_enabled)
        custom_mode_available = (
            self._custom_tool_action is not None and not placeholder_active
        )
        lens_mode_available = (
            self._lens_tool_action is not None and not placeholder_active
        )
        _enable(self._custom_tool_action, custom_mode_available)
        _enable(self._lens_tool_action, lens_mode_available)
        allowed_modes = {QPane.CONTROL_MODE_CURSOR}
        available_count = 1  # cursor
        if panzoom_enabled:
            allowed_modes.add(QPane.CONTROL_MODE_PANZOOM)
            available_count += 1
        if mask_enabled and self.mode_brush_action is not None:
            allowed_modes.add(QPane.CONTROL_MODE_DRAW_BRUSH)
            available_count += 1
        if sam_enabled and self.mode_smart_action is not None:
            allowed_modes.add(QPane.CONTROL_MODE_SMART_SELECT)
            available_count += 1
        if custom_mode_available:
            allowed_modes.add(_CUSTOM_TOOL_MODE)
            available_count += 1
        if lens_mode_available:
            allowed_modes.add(_LENS_TOOL_MODE)
            available_count += 1
        if self.cycle_mode_action is not None:
            self.cycle_mode_action.setEnabled(available_count > 1)
        current_mode = self.qpane.getControlMode()
        if current_mode not in allowed_modes:
            fallback_mode = (
                QPane.CONTROL_MODE_PANZOOM
                if panzoom_enabled
                else QPane.CONTROL_MODE_CURSOR
            )
            self._set_control_mode(fallback_mode)
            current_mode = fallback_mode
        self._update_mode_checks(current_mode)
        previous_state = getattr(self, "_tool_state", None)
        self._tool_state = (placeholder_active, panzoom_enabled)
        if placeholder_active and previous_state != self._tool_state:
            disabled_bits: list[str] = []
            if mask_available:
                disabled_bits.append("mask tools")
            if sam_available and self.mode_smart_action is not None:
                disabled_bits.append("smart select")
            if not panzoom_allowed:
                disabled_bits.append("pan/zoom")
            if disabled_bits:
                message = "Placeholder showing: "
                message += ", ".join(disabled_bits)
                message += " are unavailable until you load an image."
            else:
                message = (
                    "Placeholder showing. Pan/zoom follows your placeholder settings."
                )
            if not panzoom_allowed:
                message += " Enable pan/zoom for placeholders in Config > Placeholder if you need to inspect it."
            self._set_status(message)
        elif previous_state and previous_state[0] and not placeholder_active:
            self._set_status("Image selected; tools are available again.")
        self._update_mask_stack_readout()

    def _handle_catalog_event(self, _event) -> None:
        """Refresh action states and mask UI from the latest catalog snapshot."""
        snapshot = build_catalog_snapshot(self.qpane)
        self._update_action_states(snapshot.image_count)
        self._handle_catalog_snapshot(snapshot)
        self._refresh_tool_enables()

    def _handle_catalog_snapshot(self, snapshot: CatalogSnapshot) -> None:
        """Track mask presence and auto-show the catalog once masks exist."""
        has_masks = any(
            image.masks for group in snapshot.groups for image in group.images
        )
        if has_masks and not self._catalog_contains_masks:
            self._maybe_auto_show_catalog(snapshot.image_count, force=True)
        delete_action = getattr(self, "delete_mask_action", None)
        if delete_action is not None:
            delete_action.setEnabled(has_masks)
        self._catalog_contains_masks = has_masks

    def _handle_custom_tool_toggled(self, enabled: bool) -> None:
        """Toggle the custom tool demo that pairs a cursor provider with an inert tool."""
        if enabled:
            self._enable_custom_tool_demo()
        else:
            self._disable_custom_tool_demo()
        self._refresh_tool_enables()

    def _enable_custom_tool_demo(self) -> None:
        """Register the custom tool, cursor provider, and open the live editor."""
        self._ensure_custom_tool_registered()
        code, error = hooks_examples.load_custom_cursor_example()
        self._ensure_custom_tool_editor(code)
        self._custom_tool_editor.set_code(code)
        self._custom_tool_editor.show()
        self._custom_tool_editor.raise_()
        self._custom_tool_editor.activateWindow()
        if error:
            self._set_status(error)
            self._custom_tool_enabled = True
            return
        success, message = self._apply_custom_cursor_code(code)
        if success:
            self._set_control_mode(_CUSTOM_TOOL_MODE)
            self._set_status(demo_text.CUSTOM_TOOL_ENABLED)
        else:
            self._set_status(message)
        self._custom_tool_enabled = True

    def _disable_custom_tool_demo(self) -> None:
        """Remove the custom tool hooks and close the editor window."""
        if self.qpane.getControlMode() == _CUSTOM_TOOL_MODE:
            self._set_control_mode(QPane.CONTROL_MODE_CURSOR)
        if self._custom_cursor_provider_registered:
            self.qpane.unregisterCursorProvider(_CUSTOM_TOOL_MODE)
            self._custom_cursor_provider_registered = False
        if self._custom_tool_registered:
            try:
                self.qpane.unregisterTool(_CUSTOM_TOOL_MODE)
                self._custom_tool_registered = False
            except RuntimeError:
                logger.exception("Custom tool unregistration failed")
        self._custom_tool_enabled = False
        self._custom_tool_action = None
        self._build_toolbars()
        if self._custom_tool_editor is not None:
            self._custom_tool_editor.close()
            self._custom_tool_editor = None
        self._set_status(demo_text.CUSTOM_TOOL_DISABLED)

    def _apply_custom_cursor_code(self, code: str) -> tuple[bool, str]:
        """Compile and register the custom cursor provider hook."""
        self._ensure_custom_tool_registered()
        sandbox: dict[str, object] = {"__builtins__": __builtins__}
        try:
            from PySide6.QtCore import Qt, QPoint, QRectF, QSize
            from PySide6.QtGui import (
                QBitmap,
                QColor,
                QCursor,
                QFont,
                QFontMetricsF,
                QImage,
                QPainter,
                QPen,
                QPixmap,
            )

            sandbox.update(
                {
                    "Qt": Qt,
                    "QPoint": QPoint,
                    "QRectF": QRectF,
                    "QSize": QSize,
                    "QBitmap": QBitmap,
                    "QColor": QColor,
                    "QCursor": QCursor,
                    "QFont": QFont,
                    "QFontMetricsF": QFontMetricsF,
                    "QImage": QImage,
                    "QPainter": QPainter,
                    "QPen": QPen,
                    "QPixmap": QPixmap,
                    "CUSTOM_MODE": _CUSTOM_TOOL_MODE,
                }
            )
        except Exception:
            logger.exception("Failed to import PySide6 cursor dependencies")
        try:
            exec(code, sandbox)
        except Exception:
            logger.exception("Custom cursor code failed to execute")
            return (
                False,
                f"Error applying cursor code:\n{traceback.format_exc(limit=1)}",
            )
        provider = sandbox.get("cursor")
        if not callable(provider):
            return (
                False,
                "Define a function named 'cursor(qpane)' that returns a QCursor.",
            )
        self.qpane.unregisterCursorProvider(_CUSTOM_TOOL_MODE)
        self.qpane.registerCursorProvider(_CUSTOM_TOOL_MODE, provider)  # type: ignore[arg-type]
        self._custom_cursor_provider_registered = True
        return True, demo_text.CUSTOM_TOOL_APPLIED

    def _ensure_custom_tool_registered(self) -> None:
        """Register the custom tool and toolbar action if missing."""
        if not self._custom_tool_registered:
            try:
                self.qpane.registerTool(
                    _CUSTOM_TOOL_MODE,
                    build_custom_cursor_tool(self.qpane),
                )
                self._custom_tool_registered = True
            except ValueError:
                logger.info("Custom tool already registered; continuing")
                self._custom_tool_registered = True
        if self._custom_tool_action is None:
            action = QAction("Custom", self, checkable=True)
            action.triggered.connect(lambda: self._set_control_mode(_CUSTOM_TOOL_MODE))
            self._custom_tool_action = action
            self._build_toolbars()

    def _ensure_custom_tool_editor(self, seed_code: str) -> None:
        """Instantiate the custom tool editor window on demand."""
        if self._custom_tool_editor is None:
            self._custom_tool_editor = HookEditorWindow(
                "Custom Cursor Editor",
                demo_text.CUSTOM_CURSOR_EDITOR_HINT,
                seed_code,
                self._apply_custom_cursor_code,
                parent=self,
            )

    def _handle_custom_overlay_toggled(self, enabled: bool) -> None:
        """Toggle the custom overlay demo window and hook registration."""
        if enabled:
            self._enable_custom_overlay_demo()
            return
        self._disable_custom_overlay_demo()

    def _enable_custom_overlay_demo(self) -> None:
        """Register the custom overlay and open the live editor."""
        code, error = hooks_examples.load_custom_overlay_example()
        self._ensure_custom_overlay_editor(code)
        self._custom_overlay_editor.set_code(code)
        self._custom_overlay_editor.show()
        self._custom_overlay_editor.raise_()
        self._custom_overlay_editor.activateWindow()
        if error:
            self._set_status(error)
            self._custom_overlay_enabled = True
            return
        success, message = self._apply_custom_overlay_code(code)
        self._custom_overlay_enabled = True
        if success:
            self._set_status(demo_text.CUSTOM_OVERLAY_ENABLED)
        else:
            self._set_status(message)

    def _disable_custom_overlay_demo(self) -> None:
        """Remove the custom overlay hook and close the editor window."""
        if self._custom_overlay_registered:
            self.qpane.unregisterOverlay(_CUSTOM_OVERLAY_NAME)
            self._custom_overlay_registered = False
        self._custom_overlay_enabled = False
        if self._custom_overlay_editor is not None:
            self._custom_overlay_editor.close()
            self._custom_overlay_editor = None
        self._set_status(demo_text.CUSTOM_OVERLAY_DISABLED)

    def _apply_custom_overlay_code(self, code: str) -> tuple[bool, str]:
        """Compile and register the custom overlay draw hook."""
        sandbox: dict[str, object] = {"__builtins__": __builtins__}
        try:
            from PySide6.QtCore import Qt, QRect
            from PySide6.QtGui import QColor, QFont, QLinearGradient

            sandbox.update(
                {
                    "Qt": Qt,
                    "QRect": QRect,
                    "QColor": QColor,
                    "QFont": QFont,
                    "QLinearGradient": QLinearGradient,
                }
            )
        except Exception:
            logger.exception("Failed to import PySide6 overlay dependencies")
        try:
            exec(code, sandbox)
        except Exception:
            logger.exception("Custom overlay code failed to execute")
            return (
                False,
                f"Error applying overlay code:\n{traceback.format_exc(limit=1)}",
            )
        draw_fn = sandbox.get("draw_overlay")
        if not callable(draw_fn):
            return False, "Define a function named 'draw_overlay(painter, state)'."
        self.qpane.unregisterOverlay(_CUSTOM_OVERLAY_NAME)
        self.qpane.registerOverlay(_CUSTOM_OVERLAY_NAME, draw_fn)  # type: ignore[arg-type]
        self._custom_overlay_registered = True
        self.qpane.update()
        return True, demo_text.CUSTOM_OVERLAY_APPLIED

    def _ensure_custom_overlay_editor(self, seed_code: str) -> None:
        """Instantiate the custom overlay editor window on demand."""
        if self._custom_overlay_editor is None:
            self._custom_overlay_editor = HookEditorWindow(
                "Custom Overlay Editor",
                demo_text.CUSTOM_OVERLAY_EDITOR_HINT,
                seed_code,
                self._apply_custom_overlay_code,
                parent=self,
            )

    def _handle_lens_demo_toggled(self, enabled: bool) -> None:
        """Toggle the combined cursor+overlay lens demo."""
        if enabled:
            self._enable_lens_demo()
        else:
            self._disable_lens_demo()
        self._refresh_tool_enables()

    def _enable_lens_demo(self) -> None:
        """Register lens tool, cursor, and overlay; open the combined editor."""
        self._ensure_lens_tool_registered()
        code, error = hooks_examples.load_lens_example()
        self._ensure_lens_editor(code)
        self._lens_editor.set_code(code)
        self._lens_editor.show()
        self._lens_editor.raise_()
        self._lens_editor.activateWindow()
        if error:
            self._set_status(error)
            self._lens_tool_enabled = True
            return
        success, message = self._apply_lens_code(code)
        if success:
            self._set_control_mode(_LENS_TOOL_MODE)
            self._set_status(demo_text.LENS_DEMO_ENABLED)
        else:
            self._set_status(message)
        self._lens_tool_enabled = True

    def _disable_lens_demo(self) -> None:
        """Unregister lens hooks and close the editor window."""
        if self.qpane.getControlMode() == _LENS_TOOL_MODE:
            self._set_control_mode(QPane.CONTROL_MODE_CURSOR)
        if self._lens_cursor_registered:
            self.qpane.unregisterCursorProvider(_LENS_TOOL_MODE)
            self._lens_cursor_registered = False
        if self._lens_overlay_registered:
            self.qpane.unregisterOverlay(_LENS_OVERLAY_NAME)
            self._lens_overlay_registered = False
        if self._lens_tool_registered:
            try:
                self.qpane.unregisterTool(_LENS_TOOL_MODE)
                self._lens_tool_registered = False
            except RuntimeError:
                logger.exception("Lens tool unregistration failed")
        self._lens_tool_enabled = False
        self._lens_tool_action = None
        self._build_toolbars()
        if self._lens_editor is not None:
            self._lens_editor.close()
            self._lens_editor = None
        self.qpane.update()
        self._set_status(demo_text.LENS_DEMO_DISABLED)

    def _apply_lens_code(self, code: str) -> tuple[bool, str]:
        """Compile and register the combined lens cursor and overlay hooks."""
        self._ensure_lens_tool_registered()
        sandbox: dict[str, object] = {"__builtins__": __builtins__}
        try:
            from PySide6.QtCore import Qt, QPoint, QRect
            from PySide6.QtCore import QSize
            from PySide6.QtGui import (
                QColor,
                QCursor,
                QFont,
                QImage,
                QPainter,
                QPainterPath,
                QPen,
                QPixmap,
            )

            sandbox.update(
                {
                    "Qt": Qt,
                    "QPoint": QPoint,
                    "QRect": QRect,
                    "QColor": QColor,
                    "QCursor": QCursor,
                    "QFont": QFont,
                    "QImage": QImage,
                    "QPainter": QPainter,
                    "QPainterPath": QPainterPath,
                    "QPen": QPen,
                    "QPixmap": QPixmap,
                    "QSize": QSize,
                }
            )
        except Exception:
            logger.exception("Failed to import PySide6 lens dependencies")
        sandbox.update({"CUSTOM_MODE": _LENS_TOOL_MODE, "qpane": self.qpane})
        try:
            exec(code, sandbox)
        except Exception:
            logger.exception("Lens code failed to execute")
            return (
                False,
                f"Error applying lens code:\n{traceback.format_exc(limit=1)}",
            )
        cursor_provider = sandbox.get("cursor")
        overlay_fn = sandbox.get("draw_overlay")
        if not callable(cursor_provider):
            return (
                False,
                "Define a function named 'cursor(qpane)' that returns a QCursor.",
            )
        if not callable(overlay_fn):
            return False, "Define a function named 'draw_overlay(painter, state)'."
        self.qpane.unregisterCursorProvider(_LENS_TOOL_MODE)
        self.qpane.registerCursorProvider(_LENS_TOOL_MODE, cursor_provider)  # type: ignore[arg-type]
        self._lens_cursor_registered = True
        self.qpane.unregisterOverlay(_LENS_OVERLAY_NAME)
        self.qpane.registerOverlay(_LENS_OVERLAY_NAME, overlay_fn)  # type: ignore[arg-type]
        self._lens_overlay_registered = True
        self.qpane.update()
        return True, demo_text.LENS_DEMO_APPLIED

    def _ensure_lens_tool_registered(self) -> None:
        """Register the lens tool and toolbar action if missing."""
        if not self._lens_tool_registered:
            try:
                self.qpane.registerTool(
                    _LENS_TOOL_MODE,
                    build_custom_cursor_tool(self.qpane),
                )
                self._lens_tool_registered = True
            except ValueError:
                logger.info("Lens tool already registered; continuing")
                self._lens_tool_registered = True
        if self._lens_tool_action is None:
            action = QAction("Lens", self, checkable=True)
            action.triggered.connect(lambda: self._set_control_mode(_LENS_TOOL_MODE))
            self._lens_tool_action = action
            self._build_toolbars()

    def _ensure_lens_editor(self, seed_code: str) -> None:
        """Instantiate the combined lens editor window on demand."""
        if self._lens_editor is None:
            self._lens_editor = HookEditorWindow(
                "Cursor + Overlay Editor",
                demo_text.LENS_EDITOR_HINT,
                seed_code,
                self._apply_lens_code,
                parent=self,
            )

    def closeEvent(self, event: QEvent) -> None:
        """Close helper dialogs and emit a farewell message on exit."""
        if self._reference_dialog is not None:
            self._reference_dialog.close()
        if self._custom_tool_enabled:
            self._disable_custom_tool_demo()
        if self._custom_overlay_enabled:
            self._disable_custom_overlay_demo()
        if self._lens_tool_enabled:
            self._disable_lens_demo()
        self._set_status(demo_text.EXIT_MESSAGE)
        super().closeEvent(event)
        if event.isAccepted():
            self._persist_window_geometry()


class QuickReferenceDialog(QDialog):
    """Floating helper that summarizes the primary demo shortcuts."""

    def __init__(self, hints: list[str], parent: QWidget | None = None) -> None:
        """Render the hint list in a lightweight, non-modal popup."""
        super().__init__(parent)
        self.setWindowTitle("Quick Reference")
        self.setModal(False)
        self.setWindowFlag(Qt.Tool)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        label = QLabel("\n".join(f"- {hint}" for hint in hints), self)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
