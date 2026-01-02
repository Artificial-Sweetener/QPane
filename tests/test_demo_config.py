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

"""Example configuration dialog integration and persistence tests."""

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QTransform
from PySide6.QtWidgets import QCheckBox

from examples.demo import ExampleOptions, ExampleWindow, parse_args
from examples.demonstration.config.dialog import ConfigDialog, DomainCheckboxGroup
from qpane.features.registry import FeatureInstallError
from examples.demonstration.config.spec import (
    build_sections_for_features,
    field_sets_for_sections,
)
from qpane import Config, QPane
from qpane.rendering import RenderState, RenderStrategy

MB = 1024 * 1024


def test_live_config_applies_without_rebuild(qapp):
    """Live dialog updates apply without rebuilding the QPane."""
    demo_config = Config()
    demo_config.cache.mode = "hard"
    demo_config.cache.budget_mb = 1024
    window = ExampleWindow(ExampleOptions(feature_set="core"), config=demo_config)
    try:
        sections = build_sections_for_features(window._active_features)
        _, config_fields, _ = field_sets_for_sections(sections)
        old_qpane = window.qpane
        cache_settings = old_qpane.settings.cache
        budgets_bytes = cache_settings.resolved_consumer_budgets_bytes()
        budgets = {key: int(value // MB) for key, value in budgets_bytes.items()}
        overrides = {
            "cache.tiles.mb": budgets.get("tiles", 0) + 128,
        }
        if "cache.masks.mb" in config_fields:
            overrides["cache.masks.mb"] = budgets.get("masks", 0) + 64
        if "cache.predictors.mb" in config_fields:
            overrides["cache.predictors.mb"] = budgets.get("predictors", 0) + 32
        window._apply_configuration(
            overrides,
            config_fields=config_fields,
        )
        assert window.qpane is old_qpane
        cache_settings = window.qpane.settings.cache
        assert cache_settings.override_mb("tiles") == overrides["cache.tiles.mb"]
        if "cache.masks.mb" in config_fields:
            assert cache_settings.override_mb("masks") == overrides["cache.masks.mb"]
        else:
            assert cache_settings.override_mb("masks") is None
        if "cache.predictors.mb" in config_fields:
            assert (
                cache_settings.override_mb("predictors")
                == overrides["cache.predictors.mb"]
            )
        else:
            assert cache_settings.override_mb("predictors") is None
        demo_cache = demo_config.cache
        assert demo_cache.override_mb("tiles") == overrides["cache.tiles.mb"]
        if "cache.masks.mb" in config_fields:
            assert demo_cache.override_mb("masks") == overrides["cache.masks.mb"]
        else:
            assert demo_cache.override_mb("masks") is None
        if "cache.predictors.mb" in config_fields:
            assert (
                demo_cache.override_mb("predictors") == overrides["cache.predictors.mb"]
            )
        else:
            assert demo_cache.override_mb("predictors") is None
        expected_bytes = cache_settings.resolve_active_budget_bytes()
        coordinator = window.qpane.cacheCoordinator
        assert coordinator is not None
        assert coordinator.active_budget_bytes == expected_bytes
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_demo_persists_concurrency_settings(qapp):
    """Concurrency tuning from the dialog persists into the demo config."""
    demo_config = Config()
    window = ExampleWindow(ExampleOptions(feature_set="masksam"), config=demo_config)
    try:
        sections = build_sections_for_features(window._active_features)
        _, config_fields, _ = field_sets_for_sections(sections)
        values = {
            "concurrency_max_workers": 12,
            "concurrency_max_pending_total": 0,
            "concurrency_category_priorities_map": {"tiles": 40, "sam": -5},
            "concurrency_category_limits_map": {"tiles": 3},
            "concurrency_pending_limits_map": {"sam": 4},
            "concurrency_device_limits_map": {
                "cuda": {"sam": 2, "tiles": 99},
                "cpu": {"sam": 1},
            },
        }
        window._apply_configuration(
            values,
            config_fields=config_fields,
        )
        concurrency = demo_config.concurrency
        assert concurrency["max_workers"] == 12
        assert concurrency["max_pending_total"] is None
        assert concurrency["category_priorities"]["tiles"] == 40
        assert concurrency["category_priorities"]["sam"] == -5
        assert concurrency["category_limits"]["tiles"] == 3
        assert concurrency["pending_limits"]["sam"] == 4
        assert concurrency["device_limits"]["cuda"]["sam"] == 2
        assert concurrency["device_limits"]["cpu"]["sam"] == 1
        assert "tiles" not in concurrency["device_limits"]["cuda"]
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_demo_applies_internal_concurrency_live(qapp):
    """Concurrency map tweaks trigger live QPane reconfigure even though internal."""
    demo_config = Config()
    window = ExampleWindow(ExampleOptions(feature_set="masksam"), config=demo_config)
    try:
        sections = build_sections_for_features(window._active_features)
        _, config_fields, _ = field_sets_for_sections(sections)
        executor = window.qpane.executor
        before = executor.snapshot()
        assert before.category_limits.get("tiles") in (None, 0)
        window._apply_configuration(
            {"concurrency_category_limits_map": {"tiles": 4}},
            config_fields=config_fields,
        )
        after = executor.snapshot()
        assert after.category_limits.get("tiles") == 4
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_config_dialog_skips_device_limits_without_sam(qapp):
    """Device limits inputs collapse when SAM feature is inactive."""
    dialog = ConfigDialog(Config(), active_features=())
    try:
        adv = dialog._concurrency_adv
        assert adv is not None
        assert adv._devices == []
        assert not adv._device_widgets
        widget = next(iter(adv._prio_widgets.values()), None)
        assert widget is not None
        widget.setValue(widget.value() + 1)
        result = dialog.result()
        assert "concurrency_device_limits_map" not in result.values
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_exposes_device_limits_with_sam(qapp):
    dialog = ConfigDialog(Config(), active_features=("mask", "sam"))
    try:
        adv = dialog._concurrency_adv
        assert adv is not None
        assert "cuda" in adv._devices
        assert adv._device_widgets
        maps = dialog._concurrency_maps_from_widget()
        assert "concurrency_device_limits_map" in maps
        assert maps["concurrency_device_limits_map"]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_limits_device_categories_to_sam(qapp):
    config = Config()
    config.concurrency = {
        "device_limits": {
            "cuda": {"sam": 5, "tiles": 3},
            "cpu": {"tiles": 2},
        }
    }
    dialog = ConfigDialog(config, active_features=("mask", "sam"))
    try:
        adv = dialog._concurrency_adv
        assert adv is not None
        assert adv._device_categories == ["sam"]
        maps = dialog._concurrency_maps_from_widget()
        dev_map = maps["concurrency_device_limits_map"]
        assert set(dev_map["cuda"].keys()) == {"sam"}
        assert set(dev_map["cpu"].keys()) == {"sam"}
        assert dev_map["cuda"]["sam"] == 5
        assert dev_map["cpu"]["sam"] == 0
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_diagnostics_include_pyramid_level(qapp):
    qpane_widget = QPane(features=())
    try:
        qpane_widget.resize(400, 400)
        base_image = QImage(2048, 2048, QImage.Format_ARGB32)
        base_image.fill(0)
        qpane_widget.original_image = base_image
        view = qpane_widget.view()
        render_state = RenderState(
            source_image=QImage(512, 512, QImage.Format_ARGB32),
            pyramid_scale=0.25,
            transform=QTransform(),
            zoom=1.0,
            strategy=RenderStrategy.DIRECT,
            render_hint_enabled=False,
            debug_draw_tile_grid=False,
            tiles_to_draw=[],
            tile_size=view.tile_manager.tile_size,
            tile_overlap=view.tile_manager.tile_overlap,
            max_tile_cols=0,
            max_tile_rows=0,
            qpane_rect=qpane_widget.rect(),
            current_pan=QPointF(),
            physical_viewport_rect=qpane_widget.physicalViewportRect(),
            visible_tile_range=None,
        )
        view.renderer._current_render_state = render_state
        snapshot = qpane_widget.gatherDiagnostics()
        assert any(
            record.label == "Pyramid Level"
            and "512px" in record.value
            and "0.250x" in record.value
            for record in snapshot.records
        )
    finally:
        qpane_widget.deleteLater()
        qapp.processEvents()


def test_config_dialog_preview_tracks_changes(qapp):
    dialog = ConfigDialog(Config())
    try:
        widget = dialog._widgets["mask_prefetch_enabled"]
        widget.setChecked(False)
        dialog._update_preview()
        assert dialog._preview_text.toPlainText()
        assert "mask_prefetch_enabled" in dialog._preview_text.toPlainText()
        widget.setChecked(True)
        dialog._update_preview()
        assert dialog._preview_status_label.text() == "No changes yet"
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_sam_restart_guidance(qapp):
    dialog = ConfigDialog(Config(), active_features=("mask", "sam"))
    try:
        mode_widget = dialog._widgets["sam_download_mode"]
        path_widget = dialog._widgets["sam_model_path"]
        mode_widget.setCurrentText("blocking")
        path_widget.setText("C:/tmp/mobile_sam.pt")
        dialog._update_preview()
        assert "Restart required" in dialog._preview_status_label.text()
        result = dialog.result()
        assert "sam_download_mode" in result.restart_fields
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_sam_background_applies_live(qapp):
    dialog = ConfigDialog(Config(), active_features=("mask", "sam"))
    try:
        path_widget = dialog._widgets["sam_model_path"]
        path_widget.setText("C:/tmp/mobile_sam.pt")
        dialog._update_preview()
        assert dialog._preview_status_label.text() == "Applies live"
        result = dialog.result()
        assert not result.restart_fields
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_filter_hides_sections(qapp):
    dialog = ConfigDialog(Config())
    try:
        dialog._filter_input.setText("mask")
        qapp.processEvents()
        assert not dialog._section_items["Masks"].isHidden()
        assert dialog._section_items["Viewer"].isHidden()
        dialog._filter_input.setText("zzzz")
        qapp.processEvents()
        assert dialog._no_matches_label.isVisible()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_exposes_zoom_normalization_toggles(qapp):
    dialog = ConfigDialog(Config())
    try:
        widget = dialog._widgets.get("normalize_zoom_on_screen_change")
        assert isinstance(widget, QCheckBox)
        widget.setChecked(True)
        one_to_one_widget = dialog._widgets.get("normalize_zoom_for_one_to_one")
        assert isinstance(one_to_one_widget, QCheckBox)
        one_to_one_widget.setChecked(True)
        result = dialog.result()
        assert result.values["normalize_zoom_on_screen_change"] is True
        assert result.values["normalize_zoom_for_one_to_one"] is True
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_hides_mask_fields_without_feature(qapp):
    dialog = ConfigDialog(Config(), active_features=())
    try:
        assert "Masks" not in dialog._section_items
        assert "SAM" not in dialog._section_items
        assert "mask_prefetch_enabled" not in dialog._widgets
        assert "mask_autosave_enabled" not in dialog._widgets
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_hides_sam_fields_when_mask_only(qapp):
    dialog = ConfigDialog(Config(), active_features=("mask",))
    try:
        domains_widget = dialog._widgets.get("diagnostics_domains_enabled")
        assert isinstance(domains_widget, DomainCheckboxGroup)
        assert "sam" not in domains_widget.domains()
        assert "cache.weights.predictors" not in dialog._widgets
        assert "cache.prefetch.predictors" not in dialog._widgets
        assert "sam_download_mode" not in dialog._widgets
        assert "sam_model_path" not in dialog._widgets
        assert "sam_model_url" not in dialog._widgets
        assert "sam_model_hash" not in dialog._widgets
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_config_dialog_toggles_cache_mode_fields(qapp):
    dialog = ConfigDialog(Config(), active_features=())
    try:
        dialog.show()
        qapp.processEvents()
        mode = dialog._cache_mode
        assert mode is not None
        headroom_percent = dialog._field_containers.get("cache.headroom_percent")
        headroom_cap = dialog._field_containers.get("cache.headroom_cap_mb")
        budget = dialog._field_containers.get("cache.budget_mb")
        assert headroom_percent is not None
        assert headroom_cap is not None
        assert budget is not None
        mode.setCurrentText("hard")
        qapp.processEvents()
        assert budget.isVisible()
        assert not headroom_percent.isVisible()
        assert not headroom_cap.isVisible()
        mode.setCurrentText("auto")
        qapp.processEvents()
        assert headroom_percent.isVisible()
        assert headroom_cap.isVisible()
        assert not budget.isVisible()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_concurrency_widget_hides_sam_without_feature(qapp):
    dialog = ConfigDialog(Config(), active_features=("core",))
    try:
        widget = dialog._concurrency_adv
        assert widget is not None
        assert "sam" not in widget._categories
        assert "sam" not in widget._prio_widgets
        assert "cuda" not in widget._devices
        assert not any(dev == "cuda" for dev, _ in widget._device_widgets)
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_concurrency_widget_includes_sam_for_full_features(qapp):
    dialog = ConfigDialog(Config(), active_features=("mask", "sam"))
    try:
        widget = dialog._concurrency_adv
        assert widget is not None
        assert "sam" in widget._categories
        assert "sam" in widget._prio_widgets
        assert "cuda" in widget._devices
        assert any(dev == "cuda" for dev, _ in widget._device_widgets)
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_parse_args_enables_config_strict() -> None:
    opts = parse_args(["--features", "core", "--config-strict"])
    assert opts.feature_set == "core"
    assert opts.config_strict is True


def test_demo_window_respects_config_strict(qapp):
    demo_config = Config()
    demo_config.mask_border_enabled = True
    with pytest.raises(ValueError):
        ExampleWindow(
            ExampleOptions(feature_set="core", config_strict=True),
            config=demo_config,
        )


def test_diagnostics_menu_excludes_sam_when_disabled(qapp):
    demo_config = Config()
    window = ExampleWindow(ExampleOptions(feature_set="core"), config=demo_config)
    try:
        assert "sam" not in window._overlay_detail_actions
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_demo_qpane_mask_prefetch_disabled_without_feature(qapp):
    qpane_widget = QPane(config=Config(), features=())
    try:
        with pytest.raises(FeatureInstallError):
            _ = qpane_widget.settings.mask_prefetch_enabled
    finally:
        qpane_widget.deleteLater()
        qapp.processEvents()


def test_demo_qpane_mask_prefetch_tracks_slice(qapp):
    demo_config = Config()
    demo_config.mask_prefetch_enabled = False
    qpane_widget = QPane(config=demo_config, features=("mask",))
    try:
        assert qpane_widget.settings.mask_prefetch_enabled is False
        qpane_widget.applySettings(mask_prefetch_enabled=True)
        assert qpane_widget.settings.mask_prefetch_enabled is True
    finally:
        qpane_widget.deleteLater()
        qapp.processEvents()
