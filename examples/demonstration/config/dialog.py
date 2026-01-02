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

"""Config dialog used by the example to edit QPane settings with feature-gated controls.

The dialog mirrors the ``Config`` tree, showing how to add controls per domain and apply
changes back through ``QPane.applySettings`` without touching private state.
"""

from __future__ import annotations
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Sequence
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from examples.demonstration.config.spec import (
    _ALL_FIELDS,
    _CONFIG_FIELDS,
    DIAGNOSTIC_DOMAIN_OPTIONS,
    FieldGroupSpec,
    FieldSpec,
    SectionSpec,
    active_namespaces_for_features,
    build_sections_for_features,
    field_sets_for_sections,
)
from qpane import Config

DEFAULT_CONCURRENCY_CATEGORIES: tuple[str, ...] = (
    "tiles",
    "pyramid",
    "io",
    "sam",
    "maintenance",
)
DEFAULT_CONCURRENCY_DEVICES: tuple[str, ...] = ("cpu", "cuda")
DEVICE_LIMIT_CATEGORIES: tuple[str, ...] = ("sam",)
_SAM_CONFIG_FIELDS: tuple[str, ...] = (
    "sam_download_mode",
    "sam_model_path",
    "sam_model_url",
    "sam_model_hash",
)


def _filter_device_limits(
    device_limits: Mapping[str, Mapping[str, int]] | None,
) -> dict[str, dict[str, int]]:
    """Restrict device limit mappings to supported SAM categories."""
    if not isinstance(device_limits, Mapping):
        return {}
    filtered: dict[str, dict[str, int]] = {}
    for device, categories in device_limits.items():
        if not isinstance(categories, Mapping):
            continue
        filtered[str(device)] = {
            category: int(categories.get(category, 0) or 0)
            for category in DEVICE_LIMIT_CATEGORIES
        }
    return filtered


class ConcurrencyAdvancedWidget(QWidget):
    """Composite editor for priorities and limits without JSON inputs."""

    valueChanged = Signal()

    def __init__(
        self,
        *,
        priorities: dict[str, int],
        category_limits: dict[str, int],
        pending_limits: dict[str, int],
        device_limits: dict[str, dict[str, int]],
        parent: QWidget | None = None,
        active_features: Sequence[str] | None = None,
    ) -> None:
        """Build the advanced concurrency editor with optional feature gating."""
        super().__init__(parent)
        device_limits = _filter_device_limits(device_limits)
        self._active_features = (
            tuple(active_features) if active_features is not None else None
        )
        self._prio_widgets: dict[str, QSpinBox] = {}
        self._cat_limit_widgets: dict[str, QSpinBox] = {}
        self._pending_widgets: dict[str, QSpinBox] = {}
        self._device_widgets: dict[tuple[str, str], QSpinBox] = {}
        cats = set(DEFAULT_CONCURRENCY_CATEGORIES)
        cats.update(priorities.keys())
        cats.update(category_limits.keys())
        cats.update(pending_limits.keys())
        if not self._sam_enabled():
            cats.discard("sam")
        self._categories = sorted(cats)
        self._device_categories = [
            category
            for category in DEVICE_LIMIT_CATEGORIES
            if category in self._categories
        ]
        devs = set(DEFAULT_CONCURRENCY_DEVICES)
        devs.update(device_limits.keys())
        if not self._cuda_enabled():
            self._devices = []
        else:
            self._devices = sorted(devs)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        prio_group = QGroupBox("Category Priorities", self)
        prio_form = QFormLayout(prio_group)
        for cat in self._categories:
            sb = QSpinBox(prio_group)
            sb.setRange(-100, 100)
            sb.setSingleStep(1)
            sb.setValue(int(priorities.get(cat, 0)))
            self._prio_widgets[cat] = sb
            sb.valueChanged.connect(self.valueChanged)
            prio_form.addRow(cat, sb)
        root.addWidget(prio_group)
        cat_group = QGroupBox("Category Limits (0 = unbounded)", self)
        cat_form = QFormLayout(cat_group)
        for cat in self._categories:
            sb = QSpinBox(cat_group)
            sb.setRange(0, 128)
            sb.setSingleStep(1)
            sb.setValue(int(category_limits.get(cat, 0) or 0))
            self._cat_limit_widgets[cat] = sb
            sb.valueChanged.connect(self.valueChanged)
            cat_form.addRow(cat, sb)
        root.addWidget(cat_group)
        pend_group = QGroupBox("Pending Limits (0 = unbounded)", self)
        pend_form = QFormLayout(pend_group)
        for cat in self._categories:
            sb = QSpinBox(pend_group)
            sb.setRange(0, 10000)
            sb.setSingleStep(1)
            sb.setValue(int(pending_limits.get(cat, 0) or 0))
            self._pending_widgets[cat] = sb
            sb.valueChanged.connect(self.valueChanged)
            pend_form.addRow(cat, sb)
        root.addWidget(pend_group)
        if self._devices and self._device_categories:
            dev_group = QGroupBox("Device Limits (0 = unbounded)", self)
            grid = QGridLayout(dev_group)
            grid.addWidget(QLabel(""), 0, 0)
            for j, cat in enumerate(self._device_categories, start=1):
                grid.addWidget(QLabel(cat), 0, j)
            for i, dev in enumerate(self._devices, start=1):
                grid.addWidget(QLabel(dev), i, 0)
                for j, cat in enumerate(self._device_categories, start=1):
                    sb = QSpinBox(dev_group)
                    sb.setRange(0, 128)
                    sb.setSingleStep(1)
                    current = (device_limits.get(dev) or {}).get(cat, 0) or 0
                    sb.setValue(int(current))
                    self._device_widgets[(dev, cat)] = sb
                    sb.valueChanged.connect(self.valueChanged)
                    grid.addWidget(sb, i, j)
            root.addWidget(dev_group)

    def _sam_enabled(self) -> bool:
        """Return True when SAM-specific controls should be visible."""
        if self._active_features is None:
            return True
        return "sam" in self._active_features

    def _cuda_enabled(self) -> bool:
        """Return True when CUDA entries should be exposed for SAM devices."""
        return self._sam_enabled()

    @staticmethod
    def _spin_values(pool: dict[str, QSpinBox]) -> dict[str, int]:
        """Return current spin-box values for a given mapping."""
        return {name: widget.value() for name, widget in pool.items()}

    def value_maps(
        self,
    ) -> tuple[
        dict[str, int],
        dict[str, int],
        dict[str, int],
        dict[str, dict[str, int]],
    ]:
        """Expose the current priority and limit mappings entered by the user."""
        prios = self._spin_values(self._prio_widgets)
        cat_limits = self._spin_values(self._cat_limit_widgets)
        pend_limits = self._spin_values(self._pending_widgets)
        dev_limits: dict[str, dict[str, int]] = {}
        for (dev, cat), sb in self._device_widgets.items():
            dev_limits.setdefault(dev, {})[cat] = sb.value()
        return prios, cat_limits, pend_limits, dev_limits


class LockedSizeWidget(QWidget):
    """Editor for width/height pairs used by placeholder locked_size."""

    valueChanged = Signal()

    def __init__(
        self,
        *,
        minimum: int,
        maximum: int,
        step: int,
        initial: tuple[int, int] | None,
        parent: QWidget | None = None,
    ) -> None:
        """Construct paired spin boxes with shared validation."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._width = QSpinBox(self)
        self._height = QSpinBox(self)
        for spin in (self._width, self._height):
            spin.setRange(int(minimum), int(maximum))
            spin.setSingleStep(int(step))
            spin.valueChanged.connect(self.valueChanged)
        initial_w, initial_h = self._coerce_size(initial, fallback=int(minimum))
        self._width.setValue(initial_w)
        self._height.setValue(initial_h)
        layout.addWidget(self._width)
        layout.addWidget(QLabel("x", self))
        layout.addWidget(self._height)

    def value(self) -> tuple[int, int]:
        """Return the current size tuple."""
        return (int(self._width.value()), int(self._height.value()))

    @staticmethod
    def _coerce_size(
        value: tuple[int, int] | None, *, fallback: int
    ) -> tuple[int, int]:
        """Sanitize a width/height pair, falling back when invalid."""
        if (
            isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(v, (int, float)) for v in value)
        ):
            try:
                w = int(value[0])
                h = int(value[1])
            except Exception:  # pragma: no cover - defensive
                return fallback, fallback
            if w > 0 and h > 0:
                return w, h
        return fallback, fallback


@dataclass(frozen=True)
class ConfigResult:
    """Diff from the dialog along with context needed for application."""

    values: Dict[str, object]
    config_fields: set[str]
    all_fields: set[str]
    restart_fields: set[str]


class DomainCheckboxGroup(QWidget):
    """Grouped checkboxes that expose the selected diagnostics detail domains."""

    def __init__(
        self,
        *,
        domains: Iterable[str],
        selected: Iterable[str] = (),
        labels: Mapping[str, str] | None = None,
        tooltips: Mapping[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the checkbox group with the specified domains and selection."""
        super().__init__(parent)
        self._domains = tuple(domains)
        selected_set = set(selected)
        self._checkboxes: Dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for domain in self._domains:
            label = (labels or {}).get(domain, domain.title())
            box = QCheckBox(label, self)
            box.setChecked(domain in selected_set)
            tooltip = (tooltips or {}).get(domain)
            if tooltip:
                box.setToolTip(tooltip)
            layout.addWidget(box)
            self._checkboxes[domain] = box

    def domains(self) -> tuple[str, ...]:
        """Return domains in display order."""
        return self._domains

    def selected_domains(self) -> tuple[str, ...]:
        """Return enabled domains in display order."""
        return tuple(
            domain for domain in self._domains if self._checkboxes[domain].isChecked()
        )

    def checkboxes(self) -> tuple[QCheckBox, ...]:
        """Expose the underlying checkboxes for signal wiring."""
        return tuple(self._checkboxes.values())


class FilterStatusLabel(QLabel):
    """QLabel that reports the requested visibility state for tests."""

    def __init__(self, *args, **kwargs) -> None:
        """Track the last requested visibility for diagnostics/tests."""
        super().__init__(*args, **kwargs)
        self._explicit_visible = False

    def setVisible(self, visible: bool) -> None:  # type: ignore[override]
        """Record visibility requests before forwarding to QLabel."""
        self._explicit_visible = visible
        super().setVisible(visible)

    def isVisible(self) -> bool:  # type: ignore[override]
        """Expose the last requested visibility instead of QWidget state."""
        return self._explicit_visible


class ConfigDialog(QDialog):
    """Dialog that edits QPane configuration settings for the demo with feature-gated controls.

    Args:
        config: Starting Config snapshot to edit.
        parent: Optional parent widget.
        active_features: Installed feature names used to hide gated controls.
    Extension seams:
    - Add new FieldSpecs in ``examples.demonstration.config.spec`` grouped by domain.
    - Wire new editors in this dialog and apply through ``QPane.applySettings`` (no private state).
    - Use ``feature_namespace`` to hide fields when features are inactive.
    """

    ALL_FIELDS: set[str] = _ALL_FIELDS
    CONFIG_FIELDS: set[str] = _CONFIG_FIELDS
    SAM_FIELDS: set[str] = set(_SAM_CONFIG_FIELDS)

    def __init__(
        self,
        config: Config,
        parent: QWidget | None = None,
        *,
        baseline: Config | None = None,
        active_features: Sequence[str] | None = None,
    ) -> None:
        """Initialize the configuration dialog with a snapshot of the current settings."""
        super().__init__(parent)
        self.setWindowTitle("QPane Configuration")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._original = config.copy()
        self._baseline = baseline.copy() if baseline is not None else Config()
        self._original_snapshot = self._original.as_dict()
        self._baseline_snapshot = self._baseline.as_dict()
        self._active_features = (
            tuple(active_features) if active_features is not None else None
        )
        self._active_namespaces = active_namespaces_for_features(self._active_features)
        self._diagnostic_domain_labels = {
            domain: label for domain, label, _tooltip, _ns in DIAGNOSTIC_DOMAIN_OPTIONS
        }
        self._diagnostic_domain_tooltips = {
            domain: tooltip
            for domain, _label, tooltip, _ns in DIAGNOSTIC_DOMAIN_OPTIONS
        }
        self._sections: tuple[SectionSpec, ...] = build_sections_for_features(
            self._active_features
        )
        (
            self._all_fields,
            self._config_fields,
            self._field_specs,
        ) = field_sets_for_sections(self._sections)
        self._widgets: Dict[str, QWidget] = {}
        self._field_containers: Dict[str, QWidget] = {}
        self._field_labels: Dict[str, QWidget] = {}
        self._concurrency_adv: ConcurrencyAdvancedWidget | None = None
        self._concurrency_initial: Dict[str, object] = {}
        self._concurrency_defaults: Dict[str, object] = {}
        self._section_items: Dict[str, QWidget] = {}
        self._section_terms: Dict[str, set[str]] = {}
        self._tab_indices: Dict[str, int] = {}
        self._cache_mode: QComboBox | None = None
        self._cache_headroom_percent: QWidget | None = None
        self._cache_headroom_cap: QWidget | None = None
        self._cache_budget: QWidget | None = None
        self._sam_download_mode: QComboBox | None = None
        self._placeholder_zoom_mode: QComboBox | None = None
        self._placeholder_locked_zoom: QWidget | None = None
        self._placeholder_locked_size: QWidget | None = None
        self._placeholder_scale_mode: QComboBox | None = None
        self._placeholder_scale_factor: QDoubleSpinBox | None = None
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(16)
        filter_row = QHBoxLayout()
        filter_label = QLabel("Filter", self)
        self._filter_input = QLineEdit(self)
        self._filter_input.setPlaceholderText("Search settings")
        self._filter_input.textChanged.connect(self._apply_filter)
        self._no_matches_label = FilterStatusLabel("No matching sections", self)
        self._no_matches_label.setVisible(False)
        filter_row.addWidget(filter_label)
        filter_row.addWidget(self._filter_input, 1)
        filter_row.addWidget(self._no_matches_label)
        root_layout.addLayout(filter_row)
        tabs = QTabWidget(self)
        self._tabs = tabs
        self._tab_layouts: Dict[str, QVBoxLayout] = {}
        self._build_sections(tabs)
        root_layout.addWidget(tabs)
        preview_box = QGroupBox("Config Preview", self)
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(12, 8, 12, 12)
        preview_layout.setSpacing(8)
        self._preview_status_label = QLabel("No changes yet", preview_box)
        self._preview_text = QPlainTextEdit(preview_box)
        self._preview_text.setReadOnly(True)
        preview_layout.addWidget(self._preview_status_label)
        preview_layout.addWidget(self._preview_text)
        root_layout.addWidget(preview_box)
        ok_button = QDialogButtonBox.StandardButton.Ok
        cancel_button = QDialogButtonBox.StandardButton.Cancel
        button_box = QDialogButtonBox(ok_button | cancel_button, parent=self)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(
            button_box,
            alignment=Qt.AlignmentFlag.AlignRight,
        )
        self._sync_placeholder_mode_fields()
        self._apply_filter(self._filter_input.text())
        self._update_preview()

    def _build_sections(self, tabs: QTabWidget) -> None:
        """Create tabbed form controls for each configurable setting."""
        for section in self._sections:
            tab_title = section.title
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            page = QWidget(scroll)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            page_layout.setSpacing(12)
            for group in section.groups:
                group_box = QGroupBox(group.title, page)
                group_layout = QFormLayout(group_box)
                group_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
                group_layout.setLabelAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                group_layout.setHorizontalSpacing(12)
                group_layout.setVerticalSpacing(6)
                for spec in group.fields:
                    widget = self._create_widget(spec)
                    value_widget = getattr(widget, "_value_widget", widget)
                    self._widgets[spec.path] = value_widget
                    self._field_containers[spec.path] = widget
                    label_text = self._label_for(spec.path, spec.label)
                    group_layout.addRow(label_text, widget)
                    label_widget = group_layout.labelForField(widget)
                    if label_widget is not None:
                        self._field_labels[spec.path] = label_widget
                page_layout.addWidget(group_box)
            page_layout.addStretch()
            scroll.setWidget(page)
            index = tabs.addTab(scroll, tab_title)
            self._tab_layouts[tab_title] = page_layout
            self._section_items[tab_title] = scroll
            self._section_terms[tab_title] = self._collect_section_terms(
                tab_title, section.groups
            )
            self._tab_indices[tab_title] = index
        self._add_concurrency_advanced_section()
        self._sync_cache_mode_fields()

    def _allowed_diagnostic_domains(self, requested: Sequence[str]) -> tuple[str, ...]:
        """Filter diagnostic domains based on active feature namespaces."""
        namespace_lookup = {
            domain: namespace for domain, _, _, namespace in DIAGNOSTIC_DOMAIN_OPTIONS
        }
        return tuple(
            domain
            for domain in requested
            if namespace_lookup.get(domain) is None
            or namespace_lookup.get(domain) in self._active_namespaces
        )

    def _config_value(
        self, name: str, *, source: Mapping[str, object] | None = None
    ) -> object:
        """Return the current value for a dotted config path."""
        config_source = source if source is not None else self._original_snapshot
        parts = name.split(".")
        if parts and parts[0] == "cache":
            cache_settings = (
                config_source.get("cache") if isinstance(config_source, Mapping) else {}
            )
            if not isinstance(cache_settings, Mapping):
                return None
            _, *tail = parts
            if not tail:
                return cache_settings
            head = tail[0]
            if head in {"mode", "headroom_percent", "headroom_cap_mb", "budget_mb"}:
                return cache_settings.get(head)
            if head == "weights" and len(tail) == 2:
                ratios = cache_settings.get("weights", {})
                if isinstance(ratios, Mapping):
                    return ratios.get(tail[1])
                return None
            if head in {"tiles", "pyramids", "masks", "predictors"}:
                bucket = cache_settings.get(head)
                if len(tail) == 2 and tail[1] == "mb" and isinstance(bucket, Mapping):
                    override = bucket.get("mb")
                    return -1 if override is None else override
            if head == "prefetch" and len(tail) == 2:
                prefetch = cache_settings.get("prefetch")
                if isinstance(prefetch, Mapping):
                    return prefetch.get(tail[1])
            return None
        if name == "concurrency_max_workers":
            concurrency = (
                config_source.get("concurrency")
                if isinstance(config_source, Mapping)
                else {}
            ) or {}
            try:
                return int(concurrency.get("max_workers", 8))
            except Exception:  # pragma: no cover - defensive
                return 8
        if name == "concurrency_max_pending_total":
            concurrency = (
                config_source.get("concurrency")
                if isinstance(config_source, Mapping)
                else {}
            ) or {}
            pending = concurrency.get("max_pending_total")
            return self._positive_int_or_zero(pending)
        value: object = config_source
        for part in parts:
            if value is None:
                return None
            if isinstance(value, Mapping):
                value = value.get(part)
                continue
            try:
                value = getattr(value, part)
            except AttributeError:
                return None
        return value

    def _initial_value(self, spec: FieldSpec) -> object:
        """Return a safe initial value for the provided field spec."""
        value = self._config_value(spec.path, source=self._original_snapshot)
        if value is not None:
            return value
        if spec.kind == "spin":
            if spec.minimum is not None:
                return int(spec.minimum)
            return 0
        if spec.kind == "double":
            if spec.minimum is not None:
                return float(spec.minimum)
            return 0.0
        if spec.kind == "checkbox":
            return False
        if spec.kind in ("line", "path"):
            return ""
        if spec.kind == "combo":
            return spec.options[0] if spec.options else ""
        if spec.kind == "size":
            minimum = int(spec.minimum) if spec.minimum is not None else 1
            return (minimum, minimum)
        if spec.kind == "multicheck":
            if isinstance(value, (list, tuple, set)):
                return tuple(value)
            return ()
        return None

    def _create_widget(self, spec: FieldSpec) -> QWidget:
        """Instantiate a control for the provided field specification."""
        current_value = self._initial_value(spec)
        if spec.kind == "combo":
            widget = QComboBox(self)
            for option in spec.options or ():
                widget.addItem(option)
            if isinstance(current_value, str):
                index = widget.findText(current_value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            if spec.path == "cache.mode":
                widget.currentTextChanged.connect(self._handle_cache_mode_changed)
                self._cache_mode = widget
            if spec.path == "placeholder.zoom_mode":
                widget.currentTextChanged.connect(
                    self._handle_placeholder_zoom_mode_changed
                )
                self._placeholder_zoom_mode = widget
            if spec.path == "placeholder.scale_mode":
                widget.currentTextChanged.connect(
                    self._handle_placeholder_scale_mode_changed
                )
                self._placeholder_scale_mode = widget
            if spec.path == "sam_download_mode":
                self._sam_download_mode = widget
            self._wire_widget_signals(widget)
            return widget
        if spec.kind == "spin":
            if spec.minimum is None or spec.maximum is None:
                raise ValueError(f"Spin fields require bounds: {spec.path}")
            widget = QSpinBox(self)
            widget.setRange(int(spec.minimum), int(spec.maximum))
            widget.setSingleStep(int(spec.step or 1))
            widget.setValue(int(current_value))
            if spec.special_value_text:
                widget.setSpecialValueText(spec.special_value_text)
            if spec.path == "cache.headroom_cap_mb":
                self._cache_headroom_cap = widget
            if spec.path == "cache.budget_mb":
                self._cache_budget = widget
        elif spec.kind == "double":
            if spec.minimum is None or spec.maximum is None:
                raise ValueError(f"Double fields require bounds: {spec.path}")
            widget = QDoubleSpinBox(self)
            widget.setDecimals(spec.decimals or 2)
            widget.setRange(float(spec.minimum), float(spec.maximum))
            widget.setSingleStep(float(spec.step or 0.1))
            widget.setValue(float(current_value))
            if spec.special_value_text:
                widget.setSpecialValueText(spec.special_value_text)
            if spec.path == "cache.headroom_percent":
                self._cache_headroom_percent = widget
            if spec.path == "placeholder.scale_factor":
                self._placeholder_scale_factor = widget
        elif spec.kind == "size":
            if spec.minimum is None or spec.maximum is None:
                raise ValueError(f"Size fields require bounds: {spec.path}")
            initial_size = self._normalize_size_value(current_value)
            widget = LockedSizeWidget(
                minimum=int(spec.minimum),
                maximum=int(spec.maximum),
                step=int(spec.step or 1),
                initial=initial_size,
                parent=self,
            )
        elif spec.kind == "checkbox":
            widget = QCheckBox(self)
            widget.setChecked(bool(current_value))
        elif spec.kind == "line":
            widget = QLineEdit(self)
            widget.setText(str(current_value))
            widget.setClearButtonEnabled(True)
            if spec.placeholder:
                widget.setPlaceholderText(spec.placeholder)
        elif spec.kind == "path":
            line_edit = QLineEdit(self)
            line_edit.setText(str(current_value))
            line_edit.setClearButtonEnabled(True)
            if spec.placeholder:
                line_edit.setPlaceholderText(spec.placeholder)
            browse = QPushButton("Browse", self)
            browse.clicked.connect(lambda *_: self._browse_for_path(line_edit))
            container = QWidget(self)
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            layout.addWidget(line_edit, 1)
            layout.addWidget(browse, 0)
            self._wire_widget_signals(line_edit)
            container._value_widget = line_edit  # type: ignore[attr-defined]
            layout.setStretch(0, 1)
            return container
        elif spec.kind == "multicheck":
            available_domains = self._allowed_diagnostic_domains(spec.options or ())
            selected = (
                tuple(current_value)
                if isinstance(current_value, (list, tuple, set))
                else tuple()
            )
            widget = DomainCheckboxGroup(
                domains=available_domains,
                selected=selected,
                labels=self._diagnostic_domain_labels,
                tooltips=self._diagnostic_domain_tooltips,
                parent=self,
            )
        else:
            raise ValueError(f"Unsupported field kind: {spec.kind}")
        if spec.suffix and isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setSuffix(spec.suffix)
        if spec.tooltip:
            widget.setToolTip(spec.tooltip)
        if spec.path == "placeholder.locked_zoom":
            self._placeholder_locked_zoom = widget
        if spec.path == "placeholder.locked_size":
            self._placeholder_locked_size = widget
        self._wire_widget_signals(widget)
        return widget

    def _browse_for_path(self, line_edit: QLineEdit) -> None:
        """Open a file picker and populate ``line_edit`` with the selected path."""
        default_dir = self._default_placeholder_dir()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Placeholder Image",
            default_dir,
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp)",
        )
        if file_path:
            line_edit.setText(file_path)

    def _default_placeholder_dir(self) -> str:
        """Return the demo logo directory when it exists on disk."""
        root = Path(__file__).resolve()
        assets_dir = root.parents[3] / "assets" / "logos"
        if assets_dir.is_dir():
            return str(assets_dir)
        return ""

    def _label_for(self, name: str, override: str | None = None) -> str:
        """Render a human-friendly label for a config path."""
        return override or name.replace("_", " ").title()

    @staticmethod
    def _positive_int_or_zero(value: object) -> int:
        """Return a positive int or 0 when value is not a valid positive int."""
        if isinstance(value, int) and value > 0:
            return int(value)
        return 0

    @staticmethod
    def _normalize_size_value(value: object) -> tuple[int, int] | None:
        """Return a sanitized width/height pair when valid."""
        if isinstance(value, (tuple, list)) and len(value) == 2:
            try:
                width = int(value[0])
                height = int(value[1])
            except (TypeError, ValueError):
                return None
            if width > 0 and height > 0:
                return (width, height)
        return None

    def _add_concurrency_advanced_section(self) -> None:
        """Insert the advanced concurrency editor and capture its defaults."""
        data = (
            self._original_snapshot.get("concurrency")
            if isinstance(self._original_snapshot, Mapping)
            else {}
        ) or {}
        raw_device_limits = {
            key: dict(value)
            for key, value in dict(data.get("device_limits", {})).items()
        }
        adv = ConcurrencyAdvancedWidget(
            priorities=dict(data.get("category_priorities", {})),
            category_limits=dict(data.get("category_limits", {})),
            pending_limits=dict(data.get("pending_limits", {})),
            device_limits=_filter_device_limits(raw_device_limits),
            parent=self,
            active_features=self._active_features,
        )
        layout = self._tab_layouts.get("Concurrency")
        if layout is None:
            return
        box = QGroupBox("Concurrency (Advanced): Priorities & Limits", self)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.addWidget(adv)
        layout.addWidget(box)
        self._concurrency_adv = adv
        self._concurrency_initial = self._concurrency_maps_from_widget()
        defaults = self._concurrency_maps_from_config(self._baseline_snapshot)
        self._concurrency_defaults = self._normalize_concurrency_reference(
            defaults,
            self._concurrency_initial,
        )
        adv.valueChanged.connect(self._trigger_preview_update)

    def result(self) -> ConfigResult:
        """Return the dialog results plus config metadata for application."""
        values = self._diff_against(
            self._original_snapshot, self._concurrency_initial or None
        )
        restart_fields = self._sam_restart_fields(values)
        return ConfigResult(
            values=values,
            config_fields=set(self._config_fields),
            all_fields=set(self._all_fields),
            restart_fields=restart_fields,
        )

    @staticmethod
    def collapse_values(values: Mapping[str, object]) -> Dict[str, object]:
        """Convert dotted keys into nested dictionaries for downstream consumers."""
        collapsed: Dict[str, object] = {}
        for key, value in values.items():
            if "." not in key:
                collapsed[key] = value
                continue
            parts = key.split(".")
            head = parts[0]
            cursor = collapsed.get(head)
            if not isinstance(cursor, dict):
                cursor = {}
                collapsed[head] = cursor
            for part in parts[1:-1]:
                next_node = cursor.get(part)
                if not isinstance(next_node, dict):
                    next_node = {}
                    cursor[part] = next_node
                cursor = next_node
            cursor[parts[-1]] = value
        return collapsed

    def _collect_section_terms(
        self,
        tab_title: str,
        groups: tuple[FieldGroupSpec, ...],
    ) -> set[str]:
        """Assemble lowercase tokens for matching filter queries."""
        terms: set[str] = {tab_title.lower()}
        for group in groups:
            terms.add(group.title.lower())
        return terms

    def _apply_filter(self, text: str | None = None) -> None:
        """Toggle tab visibility so only matching sections remain."""
        query = (
            (text if text is not None else self._filter_input.text()).strip().lower()
        )
        any_visible = False
        for title, widget in self._section_items.items():
            tokens = self._section_terms.get(title, set())
            matches = not query or any(query in token for token in tokens)
            widget.setVisible(matches)
            index = self._tab_indices.get(title)
            if index is not None:
                self._tabs.setTabVisible(index, matches)
            if matches:
                any_visible = True
        self._no_matches_label.setVisible(bool(query) and not any_visible)

    def _wire_widget_signals(self, widget: QWidget) -> None:
        """Attach change listeners so the preview stays in sync."""
        if isinstance(widget, QSpinBox):
            widget.valueChanged.connect(self._trigger_preview_update)
        elif isinstance(widget, QDoubleSpinBox):
            widget.valueChanged.connect(self._trigger_preview_update)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(self._trigger_preview_update)
        elif isinstance(widget, QLineEdit):
            widget.textChanged.connect(self._trigger_preview_update)
        elif isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(self._trigger_preview_update)
        elif isinstance(widget, DomainCheckboxGroup):
            for box in widget.checkboxes():
                box.toggled.connect(self._trigger_preview_update)

    def _handle_cache_mode_changed(self, *_args) -> None:
        """Toggle cache budget controls when the mode changes."""
        self._sync_cache_mode_fields()
        self._trigger_preview_update()

    def _handle_placeholder_zoom_mode_changed(self, *_args) -> None:
        """Toggle placeholder lock controls when zoom mode changes."""
        self._sync_placeholder_mode_fields()
        self._trigger_preview_update()

    def _handle_placeholder_scale_mode_changed(self, *_args) -> None:
        """Toggle placeholder sizing controls when scale mode changes."""
        self._sync_placeholder_mode_fields()
        self._trigger_preview_update()

    def _sync_placeholder_mode_fields(self) -> None:
        """Show only the placeholder lock controls relevant to the zoom mode."""
        mode_widget = self._placeholder_zoom_mode
        mode = (mode_widget.currentText() if mode_widget is not None else "").lower()
        show_locked_zoom = mode == "locked_zoom"
        show_locked_size = mode == "locked_size"
        self._set_field_visible("placeholder.locked_zoom", show_locked_zoom)
        self._set_field_visible("placeholder.locked_size", show_locked_size)
        locked_zoom_widget = self._widgets.get("placeholder.locked_zoom")
        if locked_zoom_widget is not None:
            locked_zoom_widget.setEnabled(show_locked_zoom)
        locked_size_widget = self._widgets.get("placeholder.locked_size")
        if locked_size_widget is not None:
            locked_size_widget.setEnabled(show_locked_size)
        scale_widget = self._placeholder_scale_mode
        scale_mode = (
            scale_widget.currentText().lower() if scale_widget is not None else ""
        )
        sizing_visible = scale_mode in {"logical_fit", "physical_fit", "relative_fit"}
        for path in (
            "placeholder.display_size",
            "placeholder.min_display_size",
            "placeholder.max_display_size",
        ):
            self._set_field_visible(path, sizing_visible)
            widget = self._widgets.get(path)
            if widget is not None:
                widget.setEnabled(sizing_visible)
        scale_factor_visible = scale_mode == "relative_fit"
        self._set_field_visible("placeholder.scale_factor", scale_factor_visible)
        widget = self._widgets.get("placeholder.scale_factor")
        if widget is not None:
            widget.setEnabled(scale_factor_visible)

    def _sync_cache_mode_fields(self) -> None:
        """Show Auto headroom controls or Hard budget based on the selected mode."""
        mode_widget = self._cache_mode
        mode_source = (
            mode_widget.currentText()
            if mode_widget is not None
            else str(self._config_value("cache.mode") or "")
        )
        mode = mode_source.lower()
        auto_mode = mode == "auto"
        self._set_field_visible("cache.headroom_percent", auto_mode)
        self._set_field_visible("cache.headroom_cap_mb", auto_mode)
        self._set_field_visible("cache.budget_mb", not auto_mode)
        for path, enabled in (
            ("cache.headroom_percent", auto_mode),
            ("cache.headroom_cap_mb", auto_mode),
            ("cache.budget_mb", not auto_mode),
        ):
            widget = self._widgets.get(path)
            if widget is not None:
                widget.setEnabled(enabled)

    def _set_field_visible(self, path: str, visible: bool) -> None:
        """Toggle both the input widget and its label visibility."""
        container = self._field_containers.get(path)
        if container is not None:
            container.setVisible(visible)
        label = self._field_labels.get(path)
        if label is not None:
            label.setVisible(visible)

    def _sam_download_mode_value(self) -> str:
        """Return the selected SAM download mode for restart guidance."""
        widget = self._sam_download_mode
        if widget is not None:
            return widget.currentText().strip().lower()
        value = self._config_value("sam_download_mode")
        return "" if value is None else str(value).strip().lower()

    def _sam_restart_fields(self, values: Mapping[str, object]) -> set[str]:
        """Return SAM fields that require restart based on the current mode."""
        changed = {name for name in values if name in self.SAM_FIELDS}
        if not changed:
            return set()
        mode = self._sam_download_mode_value()
        if mode == "background":
            return set()
        return changed

    def _trigger_preview_update(self, *_args) -> None:
        """Recompute the preview when any field value changes."""
        self._update_preview()

    def _update_preview(self) -> None:
        """Render the collapsed config diff in the preview qpane."""
        preview_values = self._diff_against(
            self._baseline_snapshot, self._concurrency_defaults or None
        )
        if not preview_values:
            self._preview_status_label.setText("No changes yet")
            self._preview_text.clear()
            return
        restart_fields = self._sam_restart_fields(preview_values)
        if restart_fields:
            self._preview_status_label.setText(
                "Restart required for SAM changes (blocking/disabled)."
            )
        else:
            self._preview_status_label.setText("Applies live")
        collapsed = self.collapse_values(preview_values)
        self._preview_text.setPlainText(json.dumps(collapsed, indent=2, sort_keys=True))

    def _diff_against(
        self,
        reference_snapshot: Mapping[str, object],
        concurrency_reference: Dict[str, object] | None,
    ) -> Dict[str, object]:
        """Return the flattened config diff relative to the provided baselines."""
        values: Dict[str, object] = {}
        for name, widget in self._widgets.items():
            spec = self._field_specs.get(name)
            reference_value = (
                self._config_value(name, source=reference_snapshot) if spec else None
            )
            current, normalized_reference = self._widget_state(
                widget, spec, reference_value
            )
            if current != normalized_reference:
                values[name] = current
        if self._concurrency_adv is not None and concurrency_reference is not None:
            current_maps = self._concurrency_maps_from_widget()
            for key, current_value in current_maps.items():
                if concurrency_reference.get(key) != current_value:
                    values[key] = current_value
        return values

    def _widget_state(
        self,
        widget: QWidget,
        spec: FieldSpec | None,
        reference_value: object,
    ) -> tuple[object, object]:
        """Return the current widget value and normalized reference."""
        if isinstance(widget, QSpinBox):
            raw_value = widget.value()
            current: object = int(raw_value)
            normalized_reference = (
                int(reference_value) if reference_value is not None else None
            )
            if spec and spec.special_value_text and spec.minimum is not None:
                threshold = int(spec.minimum)
                current = None if raw_value <= threshold else current
                if normalized_reference is not None:
                    normalized_reference = (
                        None
                        if int(normalized_reference) <= threshold
                        else int(normalized_reference)
                    )
            if (
                normalized_reference is None
                and spec is not None
                and spec.minimum is not None
                and current == int(spec.minimum)
            ):
                normalized_reference = current
            return current, normalized_reference
        if isinstance(widget, QDoubleSpinBox):
            raw_value = widget.value()
            current = float(raw_value)
            normalized_reference = (
                float(reference_value) if reference_value is not None else None
            )
            if spec and spec.special_value_text and spec.minimum is not None:
                threshold = float(spec.minimum)
                current = None if raw_value <= threshold else current
                if normalized_reference is not None:
                    normalized_reference = (
                        None
                        if float(normalized_reference) <= threshold
                        else float(normalized_reference)
                    )
            if spec and spec.path == "placeholder.locked_zoom":
                mode_widget = self._placeholder_zoom_mode
                mode = (
                    mode_widget.currentText().lower() if mode_widget is not None else ""
                )
                if mode != "locked_zoom":
                    return normalized_reference, normalized_reference
            if spec and spec.path == "placeholder.scale_factor":
                scale_widget = self._placeholder_scale_mode
                scale_mode = (
                    scale_widget.currentText().lower()
                    if scale_widget is not None
                    else ""
                )
                if scale_mode != "relative_fit":
                    return normalized_reference, normalized_reference
            return current, normalized_reference
        if isinstance(widget, QCheckBox):
            current = widget.isChecked()
            normalized_reference = bool(reference_value)
            return current, normalized_reference
        if isinstance(widget, QLineEdit):
            current = widget.text()
            normalized_reference = (
                "" if reference_value is None else str(reference_value)
            )
            return current, normalized_reference
        if isinstance(widget, QComboBox):
            current = widget.currentText()
            normalized_reference = (
                "" if reference_value is None else str(reference_value)
            )
            return current, normalized_reference
        if isinstance(widget, DomainCheckboxGroup):
            current = widget.selected_domains()
            reference_set = set(reference_value or ())
            normalized_reference = tuple(
                domain for domain in widget.domains() if domain in reference_set
            )
            return current, normalized_reference
        if isinstance(widget, LockedSizeWidget):
            current_tuple = widget.value()
            current = (
                (int(current_tuple[0]), int(current_tuple[1]))
                if current_tuple and all(v > 0 for v in current_tuple)
                else None
            )
            normalized_reference = self._normalize_size_value(reference_value)
            path = spec.path if spec else ""
            if path == "placeholder.locked_size":
                mode_widget = self._placeholder_zoom_mode
                mode = (
                    mode_widget.currentText().lower() if mode_widget is not None else ""
                )
                if mode != "locked_size":
                    return normalized_reference, normalized_reference
            if path in {
                "placeholder.display_size",
                "placeholder.min_display_size",
                "placeholder.max_display_size",
            }:
                scale_widget = self._placeholder_scale_mode
                scale_mode = (
                    scale_widget.currentText().lower()
                    if scale_widget is not None
                    else ""
                )
                if scale_mode not in {"logical_fit", "physical_fit"}:
                    return normalized_reference, normalized_reference
            return current, normalized_reference
        return reference_value, reference_value

    def _concurrency_maps_from_widget(self) -> Dict[str, object]:
        """Return the current concurrency tuning maps from the advanced widget."""
        prios, cats, pend, devs = self._concurrency_adv.value_maps()
        result = {
            "concurrency_category_priorities_map": prios,
            "concurrency_category_limits_map": cats,
            "concurrency_pending_limits_map": pend,
        }
        if devs:
            result["concurrency_device_limits_map"] = devs
        return result

    @staticmethod
    def _normalize_concurrency_reference(
        reference: Dict[str, object],
        template: Dict[str, object],
    ) -> Dict[str, object]:
        """Align a reference mapping to the structure of ``template``."""
        if not reference:
            return template
        normalized: Dict[str, object] = {}
        for key, template_map in template.items():
            if key == "concurrency_device_limits_map":
                normalized_devices: dict[str, dict[str, int]] = {}
                ref_devices = reference.get(key)
                ref_devices = ref_devices if isinstance(ref_devices, dict) else {}
                for device, template_cats in template_map.items():
                    template_cats = (
                        template_cats if isinstance(template_cats, dict) else {}
                    )
                    ref_device = (
                        ref_devices.get(device) if isinstance(ref_devices, dict) else {}
                    )
                    normalized_devices[device] = {
                        category: int((ref_device or {}).get(category, template_value))
                        for category, template_value in template_cats.items()
                    }
                normalized[key] = normalized_devices
                continue
            ref_map = reference.get(key)
            ref_map = ref_map if isinstance(ref_map, dict) else {}
            normalized[key] = {
                name: int(ref_map.get(name, template_value))
                for name, template_value in template_map.items()
            }
        return normalized

    @staticmethod
    def _concurrency_maps_from_config(
        config_snapshot: Mapping[str, object] | None,
    ) -> Dict[str, object]:
        """Build normalized concurrency maps from a Config dict snapshot."""
        data = (
            config_snapshot.get("concurrency")
            if isinstance(config_snapshot, Mapping)
            else {}
        ) or {}
        priorities = dict(data.get("category_priorities", {}))
        category_limits = dict(data.get("category_limits", {}))
        pending_limits = dict(data.get("pending_limits", {}))
        device_limits = _filter_device_limits(
            {
                device: dict(values)
                for device, values in dict(data.get("device_limits", {})).items()
            }
        )
        categories = set(DEFAULT_CONCURRENCY_CATEGORIES)
        categories.update(priorities.keys())
        categories.update(category_limits.keys())
        categories.update(pending_limits.keys())
        devices = set(DEFAULT_CONCURRENCY_DEVICES)
        devices.update(device_limits.keys())
        normalized_prios = {cat: int(priorities.get(cat, 0) or 0) for cat in categories}
        normalized_cat_limits = {
            cat: int(category_limits.get(cat, 0) or 0) for cat in categories
        }
        normalized_pending = {
            cat: int(pending_limits.get(cat, 0) or 0) for cat in categories
        }
        normalized_devices: dict[str, dict[str, int]] = {}
        for device in devices:
            source = device_limits.get(device) or {}
            normalized_devices[device] = {
                cat: int(source.get(cat, 0) or 0) for cat in DEVICE_LIMIT_CATEGORIES
            }
        return {
            "concurrency_category_priorities_map": normalized_prios,
            "concurrency_category_limits_map": normalized_cat_limits,
            "concurrency_pending_limits_map": normalized_pending,
            "concurrency_device_limits_map": normalized_devices,
        }
