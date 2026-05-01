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


"""Catalog dock presenting compositions, images, link groups, and masks."""


from __future__ import annotations

import uuid

from collections.abc import Callable
from typing import Optional

from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QPoint, Qt, Signal

from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPixmap

from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qpane import CompositionEntry
from qpane import CompositionSnapshot as QPaneCompositionSnapshot
from qpane import LinkedGroup, QPane

from examples.demonstration import demo_text

from examples.demonstration.catalog.builders import build_catalog_snapshot
from examples.demonstration.catalog.models import (
    CatalogImage,
    CatalogMask,
    CatalogSnapshot,
)


FocusPolicy = Callable[[str], None]
SelectionPolicy = Callable[[], bool]
StatusSink = Callable[[str], None]
_IMAGE_ROLE = Qt.UserRole
_BROWSER_COMPOSITIONS = "compositions"
_BROWSER_CATALOG = "catalog"


class CatalogDock(QWidget):
    """Panel that mirrors QPane snapshots and exposes catalog actions."""

    visibilityChanged = Signal(bool)

    def __init__(
        self,
        qpane: QPane,
        *,
        show_mask_selection: SelectionPolicy,
        on_focus_requested: FocusPolicy,
        set_status: StatusSink,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the dock with catalog actions and the tree view."""
        super().__init__(parent)
        self._qpane = qpane
        self._show_mask_selection = show_mask_selection
        self._on_focus_requested = on_focus_requested
        self._set_status = set_status
        self._snapshot: CatalogSnapshot | None = None
        self._composition_snapshot: QPaneCompositionSnapshot | None = None
        self._browser_mode = _BROWSER_COMPOSITIONS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self._toolbar = QToolBar(self)
        self._toolbar.setMovable(False)
        self._toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._toolbar.setStyleSheet("QToolBar { border: 0; }")
        layout.addWidget(self._toolbar)
        self._browser_mode_group = QActionGroup(self)
        self._browser_mode_group.setExclusive(True)
        self._compositions_action = self._toolbar.addAction("Compositions")
        self._compositions_action.setCheckable(True)
        self._compositions_action.setChecked(True)
        self._catalog_action = self._toolbar.addAction("Catalog")
        self._catalog_action.setCheckable(True)
        self._browser_mode_group.addAction(self._compositions_action)
        self._browser_mode_group.addAction(self._catalog_action)
        self._compositions_action.toggled.connect(
            lambda checked: (
                self._set_browser_mode(_BROWSER_COMPOSITIONS) if checked else None
            )
        )
        self._catalog_action.toggled.connect(
            lambda checked: (
                self._set_browser_mode(_BROWSER_CATALOG) if checked else None
            )
        )
        self._toolbar.addSeparator()
        self._link_action = self._toolbar.addAction("Link", self._handle_link_selected)
        self._unlink_action = self._toolbar.addAction(
            "Unlink", self._handle_unlink_selected
        )
        self._clear_action = self._toolbar.addAction(
            "Clear Links", self._handle_clear_links
        )
        self._remove_action = self._toolbar.addAction(
            "Remove Image", self._handle_remove_images
        )
        self._toolbar.addSeparator()
        self._toggle_hints_action = self._toolbar.addAction("Tips")
        self._toggle_hints_action.setCheckable(True)
        self._toggle_hints_action.toggled.connect(self._toggle_hints)
        self._hint_label = QLabel(demo_text.CATALOG_HINT, self)
        self._hint_label.setWordWrap(True)
        self._hint_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._hint_label.setObjectName("catalogHintLabel")
        self._hint_label.setVisible(False)
        self._hint_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self._hint_label)
        self._tree = CatalogTree(self)
        layout.addWidget(self._tree)
        self._tree.itemEntered.connect(self._handle_tree_item_entered)
        self._tree.itemClicked.connect(self._handle_item_clicked)
        self._tree.itemSelectionChanged.connect(self._update_button_states)
        self._tree.customContextMenuRequested.connect(self._handle_context_menu)
        qpane.catalogChanged.connect(self._on_catalog_changed)
        qpane.catalogSelectionChanged.connect(self._handle_selection_changed)
        qpane.compositionChanged.connect(
            lambda _snapshot: self._on_catalog_changed(None)
        )
        qpane.compositionSelectionChanged.connect(
            self._handle_composition_selection_changed
        )
        qpane.linkGroupsChanged.connect(lambda: self._on_catalog_changed(None))
        self._on_catalog_changed(None)

    def panelWidthHint(self) -> int:
        """Return the width required to align the panel with its toolbar."""
        toolbar_width = self._toolbar.sizeHint().width()
        layout = self.layout()
        margins = layout.contentsMargins() if layout else None
        if margins is None:
            return toolbar_width
        return toolbar_width + margins.left() + margins.right()

    def _toggle_hints(self, checked: bool) -> None:
        """Show or hide the inline usage tips banner."""
        self._hint_label.setVisible(checked)

    def _set_browser_mode(self, mode: str) -> None:
        """Switch the browser between composition and catalog views."""
        if self._browser_mode == mode:
            return
        self._browser_mode = mode
        self._on_catalog_changed(None)
        label = (
            "Composition browser"
            if mode == _BROWSER_COMPOSITIONS
            else "Catalog browser"
        )
        self._set_status(f"{label} active.")

    def _handle_selection_changed(self, image_id: uuid.UUID | None) -> None:
        """Sync tree selection when the QPane selection changes."""
        if self._browser_mode == _BROWSER_COMPOSITIONS:
            self._tree.sync_composition_selection(self._qpane.currentCompositionID())
            return
        if (
            image_id is None
            or not self._show_mask_selection()
            or not self._qpane.maskFeatureAvailable()
        ):
            mask_id = None
        elif self._qpane.currentImageID() != image_id:
            mask_id = None
        else:
            mask_id = self._qpane.activeMaskID()
        self._tree.sync_selection(image_id, mask_id)

    def refresh_selection(self) -> None:
        """Resync the tree selection using the current qpane state."""
        self._handle_selection_changed(self._qpane.currentImageID())

    def _handle_composition_selection_changed(
        self, composition_id: uuid.UUID | None
    ) -> None:
        """Sync tree selection when the active composition changes."""
        if self._browser_mode == _BROWSER_COMPOSITIONS:
            self._tree.sync_composition_selection(composition_id)

    def showEvent(self, event) -> None:  # type: ignore[override]
        """Notify listeners that the dock has become visible."""
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        """Notify listeners that the dock has been hidden."""
        super().hideEvent(event)
        self.visibilityChanged.emit(False)

    def _on_catalog_changed(self, _) -> None:
        """Update the tree contents from the latest catalog snapshot."""
        snapshot = build_catalog_snapshot(self._qpane)
        self._snapshot = snapshot
        self._composition_snapshot = self._qpane.getCompositionSnapshot()
        if self._browser_mode == _BROWSER_COMPOSITIONS:
            self._tree.update_composition_snapshot(self._composition_snapshot)
            self._tree.sync_composition_selection(self._qpane.currentCompositionID())
        else:
            self._tree.update_snapshot(snapshot)
            self._handle_selection_changed(self._qpane.currentImageID())
        self._update_button_states()

    def _selected_image_ids(self) -> list[uuid.UUID]:
        """Return all image UUIDs currently selected in the tree."""
        return self._tree.selected_image_ids()

    def _has_linked_selection(self) -> bool:
        """Return True when any selected image currently participates in a link."""
        if self._snapshot is None:
            return False
        linked_ids = {
            image.image_id
            for group in self._snapshot.groups
            if group.is_link_group
            for image in group.images
        }
        return any(image_id in linked_ids for image_id in self._selected_image_ids())

    def _update_button_states(self) -> None:
        """Enable or disable command buttons based on the current selection."""
        if self._browser_mode == _BROWSER_COMPOSITIONS:
            self._link_action.setEnabled(False)
            self._unlink_action.setEnabled(False)
            self._clear_action.setEnabled(False)
            self._remove_action.setEnabled(False)
            return
        selected_images = self._selected_image_ids()
        selection_count = len(selected_images)
        has_links = self._snapshot is not None and any(
            group.is_link_group
            for group in (self._snapshot.groups if self._snapshot else [])
        )
        can_link = selection_count >= 2
        can_unlink = selection_count >= 1 and self._has_linked_selection()
        can_remove = selection_count >= 1
        self._link_action.setEnabled(can_link)
        self._unlink_action.setEnabled(can_unlink)
        self._clear_action.setEnabled(has_links)
        self._remove_action.setEnabled(can_remove)

    def _handle_link_selected(self) -> None:
        """Create a new link group from the currently selected images."""
        image_ids = tuple(self._selected_image_ids())
        if len(image_ids) < 2:
            return
        cleaned: list[LinkedGroup] = []
        for group in self._qpane.linkedGroups():
            remaining = tuple(mid for mid in group.members if mid not in image_ids)
            if len(remaining) >= 2:
                cleaned.append(LinkedGroup(group_id=group.group_id, members=remaining))
        cleaned.append(LinkedGroup(group_id=uuid.uuid4(), members=image_ids))
        self._qpane.setLinkedGroups(tuple(cleaned))

    def _handle_unlink_selected(self) -> None:
        """Remove any selected images from their current link groups."""
        image_ids = self._selected_image_ids()
        if not image_ids:
            return
        targets = {image_id for image_id in image_ids}
        if not targets:
            return
        remaining: list[LinkedGroup] = []
        for group in self._qpane.linkedGroups():
            filtered = tuple(mid for mid in group.members if mid not in targets)
            if len(filtered) >= 2:
                remaining.append(LinkedGroup(group_id=group.group_id, members=filtered))
        self._qpane.setLinkedGroups(tuple(remaining))

    def _handle_clear_links(self) -> None:
        """Clear every configured link group."""
        self._qpane.setLinkedGroups(tuple())

    def _handle_remove_images(self) -> None:
        """Delete the currently selected images from the qpane catalog."""
        image_ids = self._selected_image_ids()
        if not image_ids:
            return
        self._qpane.removeImagesByID(image_ids)

    def _handle_deselect(self) -> None:
        """Clear the active view via the facade, leaving catalog data intact.

        Demonstrates using ``setCurrentImageID(None)`` to revert to the placeholder
        or blank state without modifying the underlying image list.
        """
        self._qpane.setCurrentImageID(None)

    def _handle_tree_item_entered(self, item: QTreeWidgetItem, column: int) -> None:
        """Trigger mask prefetch when hovering catalog entries."""
        payload = item.data(0, _IMAGE_ROLE)
        if not payload:
            return
        kind = payload[0]
        if kind == "composition":
            image_id = payload[2]
            if image_id is not None:
                self._qpane.prefetchMaskOverlays(image_id, reason="composition-hover")
        elif kind == "image":
            self._qpane.prefetchMaskOverlays(payload[1], reason="catalog-hover")
        elif kind == "mask":
            image_id, _ = payload[1]
            self._qpane.prefetchMaskOverlays(image_id, reason="catalog-hover")

    def _handle_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Focus images or masks when the corresponding tree rows are clicked."""
        payload = item.data(0, _IMAGE_ROLE)
        if not payload:
            return
        kind = payload[0]
        if kind == "composition":
            composition_id = payload[1]
            self._qpane.openComposition(composition_id)
            image_id = payload[2]
            if image_id is not None:
                self._qpane.prefetchMaskOverlays(image_id, reason="composition-click")
            self._on_focus_requested("image")
        elif kind == "image":
            image_id = payload[1]
            self._qpane.prefetchMaskOverlays(image_id, reason="catalog-click")
            self._qpane.setCurrentImageID(image_id)
            self._on_focus_requested("image")
        elif kind == "mask":
            image_id, mask_id = payload[1]
            self._qpane.prefetchMaskOverlays(image_id, reason="catalog-click")
            self._qpane.setCurrentImageID(image_id)
            self._qpane.setActiveMaskID(mask_id)
            self._on_focus_requested("mask")
            # Refresh snapshot to reflect any stack reordering and keep selection aligned.
            self._on_catalog_changed(None)
            self._tree.sync_selection(image_id, mask_id)

    def _handle_context_menu(self, position: QPoint) -> None:
        """Construct and display a context menu for the item under the cursor."""
        item = self._tree.itemAt(position)
        if not item:
            return
        payload = item.data(0, _IMAGE_ROLE)
        menu = QMenu(self)
        link_action = menu.addAction("Link Selected")
        link_action.setEnabled(self._link_action.isEnabled())
        unlink_action = menu.addAction("Unlink Selected")
        unlink_action.setEnabled(self._unlink_action.isEnabled())
        menu.addSeparator()
        deselect_action = menu.addAction("Deselect (Clear View)")
        deselect_action.setEnabled(self._qpane.currentImageID() is not None)
        remove_action = menu.addAction("Remove Images")
        remove_action.setEnabled(self._remove_action.isEnabled())
        menu.addSeparator()
        mask_actions: tuple[QAction, QAction] | None = None
        reveal_action: QAction | None = None
        reveal_path: Path | None = None
        if payload and payload[0] == "mask":
            image_id, mask_id = payload[1]
            mask = self._tree.mask_details(image_id, mask_id)
            if mask is not None:
                change_action = menu.addAction("Change Mask Color…")
                delete_action = menu.addAction("Delete Mask")
                mask_actions = (change_action, delete_action)
            reveal_path = self._resolve_image_path(image_id)
        elif payload and payload[0] == "image":
            image_id = payload[1]
            reveal_path = self._resolve_image_path(image_id)
        if reveal_path is not None or (payload and payload[0] in {"image", "mask"}):
            reveal_action = menu.addAction("Show in File Browser")
            reveal_action.setEnabled(reveal_path is not None)
        chosen = menu.exec(self._tree.viewport().mapToGlobal(position))
        if chosen is None:
            return
        if chosen is link_action:
            self._handle_link_selected()
            return
        if chosen is unlink_action:
            self._handle_unlink_selected()
            return
        if chosen is deselect_action:
            self._handle_deselect()
            return
        if chosen is remove_action:
            self._handle_remove_images()
            return
        if mask_actions and chosen is mask_actions[0]:
            image_id, mask_id = payload[1]
            mask = self._tree.mask_details(image_id, mask_id)
            if mask is not None:
                self._handle_change_mask_color(mask)
            return
        if mask_actions and chosen is mask_actions[1]:
            image_id, mask_id = payload[1]
            self._qpane.removeMaskFromImage(image_id, mask_id)
            return
        if reveal_action is not None and chosen is reveal_action:
            self._handle_reveal_in_file_browser(payload)
            return

    def _handle_change_mask_color(self, mask_entry: "MaskEntry") -> None:
        """Prompt for a new color and forward the update to the qpane."""
        current_color = mask_entry.color
        color = QColorDialog.getColor(current_color, self, "Select Mask Color")
        if not color.isValid() or color == current_color:
            return
        self._qpane.setMaskProperties(mask_entry.mask_id, color=color)

    def _handle_reveal_in_file_browser(self, payload) -> None:
        """Open the file browser at the image path for the clicked item."""
        kind = payload[0] if payload else None
        if kind not in {"image", "mask"}:
            return
        if kind == "image":
            image_id = payload[1]
        else:
            image_id, _mask_id = payload[1]
        path = self._resolve_image_path(image_id)
        if path is None:
            self._set_status("No file path available for this item.")
            return
        success = self._open_file_browser(path)
        if success:
            self._set_status(f"Opened file location: {path}")
        else:
            self._set_status(f"Unable to open file location: {path}")

    def _resolve_image_path(self, image_id: uuid.UUID | None) -> Path | None:
        """Return the filesystem path for the given image_id when available."""
        if image_id is None:
            return None
        current_id = self._qpane.currentImageID()
        if current_id is not None and image_id == current_id:
            return self._qpane.currentImagePath
        return self._qpane.imagePath(image_id)

    def _open_file_browser(self, path: Path) -> bool:
        """Best-effort attempt to reveal ``path`` in the OS file browser."""
        import subprocess
        import sys

        resolved = path.resolve()
        if not resolved.exists():
            return False
        try:
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["explorer", "/select,", str(resolved)], check=True, shell=False
                )
                return True
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(resolved)], check=True, shell=False)
                return True
            subprocess.run(["xdg-open", str(resolved.parent)], check=True, shell=False)
            return True
        except Exception:
            return False


class MaskEntry:
    """Value object storing mask metadata for quick lookups."""

    __slots__ = ("image_id", "mask_id", "color")

    def __init__(self, image_id: uuid.UUID, mask: CatalogMask) -> None:
        """Store the immutable mask metadata required for context actions."""
        self.image_id = image_id
        self.mask_id = mask.mask_id
        self.color = mask.color


class CatalogTree(QTreeWidget):
    """Tree widget mirroring the catalog snapshot with headers, images, and masks."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Configure the tree widget for grouped catalog presentation."""
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.setExpandsOnDoubleClick(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self._image_items: dict[uuid.UUID, QTreeWidgetItem] = {}
        self._composition_items: dict[uuid.UUID, QTreeWidgetItem] = {}
        self._mask_items: dict[tuple[uuid.UUID, uuid.UUID], QTreeWidgetItem] = {}
        self._mask_lookup: dict[tuple[uuid.UUID, uuid.UUID], MaskEntry] = {}
        self._syncing_selection = False
        self._preserve_multi_selection = False
        self._icon_cache: dict[int, QIcon] = {}

    def update_composition_snapshot(self, snapshot: QPaneCompositionSnapshot) -> None:
        """Rebuild tree contents from a public QPane composition snapshot."""
        selected_payloads = [item.data(0, _IMAGE_ROLE) for item in self.selectedItems()]
        self._syncing_selection = True
        self.clear()
        self._image_items.clear()
        self._composition_items.clear()
        self._mask_items.clear()
        self._mask_lookup.clear()
        root = QTreeWidgetItem(["Compositions"])
        root.setFlags(Qt.ItemIsEnabled)
        self.addTopLevelItem(root)
        for index, composition_id in enumerate(snapshot.order):
            entry = snapshot.compositions.get(composition_id)
            if entry is None:
                continue
            item = QTreeWidgetItem(root, [self._format_composition_label(index, entry)])
            item.setData(
                0,
                _IMAGE_ROLE,
                ("composition", composition_id, entry.current_image_id),
            )
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.setToolTip(0, self._format_composition_tooltip(entry))
            self._composition_items[composition_id] = item
        self.expandAll()
        self._restore_selection(selected_payloads)
        self._syncing_selection = False

    def update_snapshot(self, snapshot: CatalogSnapshot) -> None:
        """Rebuild tree contents from ``snapshot`` while preserving selection."""
        selected_payloads = [item.data(0, _IMAGE_ROLE) for item in self.selectedItems()]
        self._syncing_selection = True
        self.clear()
        self._image_items.clear()
        self._composition_items.clear()
        self._mask_items.clear()
        self._mask_lookup.clear()
        for group in snapshot.groups:
            group_item = QTreeWidgetItem([group.title])
            group_item.setFlags(Qt.ItemIsEnabled)
            if group.is_link_group:
                font = group_item.font(0)
                font.setBold(True)
                group_item.setFont(0, font)
            self.addTopLevelItem(group_item)
            for image in group.images:
                image_item = QTreeWidgetItem(
                    group_item, [self._format_image_label(image)]
                )
                image_item.setData(0, _IMAGE_ROLE, ("image", image.image_id))
                image_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self._image_items[image.image_id] = image_item
                tooltip = str(image.path) if image.path else image.label
                image_item.setToolTip(0, tooltip)
                for mask in image.masks:
                    mask_item = QTreeWidgetItem(image_item, [mask.label])
                    mask_item.setData(
                        0, _IMAGE_ROLE, ("mask", (image.image_id, mask.mask_id))
                    )
                    mask_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    mask_item.setIcon(0, self._color_icon(mask.color))
                    self._mask_items[(image.image_id, mask.mask_id)] = mask_item
                    self._mask_lookup[(image.image_id, mask.mask_id)] = MaskEntry(
                        image.image_id, mask
                    )
        self.expandAll()
        self._restore_selection(selected_payloads)
        self._syncing_selection = False

    def sync_selection(
        self, image_id: uuid.UUID | None, mask_id: uuid.UUID | None
    ) -> None:
        """Align tree selection with the qpane's active image and mask."""
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            if image_id is None:
                self.clearSelection()
                return
            image_item = self._image_items.get(image_id)
            if image_item is None:
                self.clearSelection()
                return
            # Preserve existing multi-selection when the user is holding a modifier;
            # only override to a single selection when no modifiers are active.
            modifiers = QApplication.keyboardModifiers()
            preserve_multi = modifiers & (Qt.ControlModifier | Qt.ShiftModifier)
            if not preserve_multi:
                self.clearSelection()
            target_item = image_item
            if mask_id is not None:
                mask_item = self._mask_items.get((image_id, mask_id))
                if mask_item is not None:
                    target_item = mask_item
                    mask_item.setSelected(True)
            target_item.setSelected(True)
            self.setCurrentItem(
                target_item, 0, QItemSelectionModel.Select | QItemSelectionModel.Current
            )
        finally:
            self._preserve_multi_selection = False
            self._syncing_selection = False

    def sync_composition_selection(self, composition_id: uuid.UUID | None) -> None:
        """Align tree selection with the active composition."""
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            if composition_id is None:
                self.clearSelection()
                return
            item = self._composition_items.get(composition_id)
            if item is None:
                self.clearSelection()
                return
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(
                item, 0, QItemSelectionModel.Select | QItemSelectionModel.Current
            )
        finally:
            self._syncing_selection = False

    def selected_image_ids(self) -> list[uuid.UUID]:
        """Return the UUIDs for all selected image nodes."""
        ids: list[uuid.UUID] = []
        for item in self.selectedItems():
            payload = item.data(0, _IMAGE_ROLE)
            if not payload:
                continue
            if payload[0] == "image":
                ids.append(payload[1])
            elif payload[0] == "mask":
                image_id, _ = payload[1]
                if image_id not in ids:
                    ids.append(image_id)
        return ids

    def mask_details(
        self, image_id: uuid.UUID, mask_id: uuid.UUID
    ) -> Optional[MaskEntry]:
        """Return cached mask metadata for ``(image_id, mask_id)`` when available."""
        return self._mask_lookup.get((image_id, mask_id))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """Track modifier usage so multi-select initiated by the user persists."""
        self._preserve_multi_selection = bool(
            event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
        )
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        """Preserve multi-selection when keyboard modifiers extend selection."""
        if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            self._preserve_multi_selection = True
        super().keyPressEvent(event)

    def _format_image_label(self, image: CatalogImage) -> str:
        """Return the label shown for ``image`` including its ordinal."""
        index = image.index + 1
        if image.label:
            return f"{index}. {image.label}"
        return f"Image {index}"

    def _format_composition_label(self, index: int, entry: CompositionEntry) -> str:
        """Return the label shown for a composition row."""
        prefix = index + 1
        if entry.kind == "layered-scene":
            return f"{prefix}. {entry.title} ({entry.scene_layer_count} layers)"
        compare_suffix = " + compare" if entry.comparison.enabled else ""
        return f"{prefix}. {entry.title}{compare_suffix}"

    def _format_composition_tooltip(self, entry: CompositionEntry) -> str:
        """Return a concise composition source summary."""
        count = len(entry.source_image_ids)
        noun = "image" if count == 1 else "images"
        if entry.kind == "layered-scene" and entry.scene_bounds is not None:
            bounds = entry.scene_bounds
            return (
                f"Scene; {entry.scene_layer_count} layers; "
                f"{count} source {noun}; "
                f"{bounds.width():.0f} x {bounds.height():.0f}"
            )
        if entry.kind == "default-image":
            return f"Default image view; {count} source {noun}"
        if entry.kind == "explicit":
            return f"Saved image composition; {count} source {noun}"
        return f"{entry.kind}; {count} source {noun}"

    def _color_icon(self, color: QColor) -> QIcon:
        """Return a cached square icon representing ``color``."""
        key = (color.red() << 16) | (color.green() << 8) | color.blue()
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(12, 12)
        pixmap.fill(color)
        icon = QIcon(pixmap)
        self._icon_cache[key] = icon
        return icon

    def _restore_selection(self, payloads: list) -> None:
        """Reapply the previous selection after rebuilding tree items."""
        for payload in payloads:
            if not payload:
                continue
            kind = payload[0]
            if kind == "image":
                image_id = payload[1]
                item = self._image_items.get(image_id)
                if item is not None:
                    item.setSelected(True)
            elif kind == "composition":
                composition_id = payload[1]
                item = self._composition_items.get(composition_id)
                if item is not None:
                    item.setSelected(True)
            elif kind == "mask":
                key = payload[1]
                if isinstance(key, list):
                    key = tuple(key)
                if not isinstance(key, tuple):
                    continue
                item = self._mask_items.get(key)
                if item is not None:
                    item.setSelected(True)
