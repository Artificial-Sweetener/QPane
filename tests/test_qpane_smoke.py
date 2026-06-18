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

"""QPane smoke tests covering core interactions, overlays, and feature fallbacks."""

from __future__ import annotations

import logging
import time
import uuid

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QTransform

from qpane import Config, LinkedGroup, OverlayState, QPane
from qpane.catalog import CatalogMutationEvent
from qpane.features import FeatureInstallError
from qpane.rendering import Tile, ViewportZoomMode
from qpane.scene.render_plan import RenderStrategy
from qpane.scene.identity import SceneLayerTileKey, default_catalog_asset_key
from tests.helpers.render_plan import make_render_plan, make_tile_key
from tests.helpers.executor_stubs import StubExecutor
from tests.helpers.mask_test_utils import drain_mask_jobs
from tests.test_mask_workflows import (
    _mask_service,
    _prepare_qpane_with_mask_feature,
    _queue_pending_stroke,
)


def _cleanup_qpane(qpane, qapp):
    """Delete a QPane and flush Qt events."""
    qpane.deleteLater()
    qapp.processEvents()


def _solid_image(
    width: int = 32, height: int = 32, color: Qt.GlobalColor = Qt.white
) -> QImage:
    """Return a solid-colour QImage for rendering assertions."""
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(color)
    return image


@pytest.mark.usefixtures("qapp")
def test_qpane_general_smoke(qapp, monkeypatch, caplog, tmp_path):
    """Exercise the bulk of QPane facade behaviors with a single widget instance."""
    qpane = QPane(features=())
    baseline_config = qpane.settings
    try:
        _assert_core_initial_state(qpane)
        _assert_view_alignment(qpane)
        _assert_view_handles_tile_ready(qpane, monkeypatch)
        _assert_view_handles_pyramid_ready(qpane, monkeypatch)
        _assert_mark_dirty_delegates(qpane, monkeypatch)
        _assert_image_facade_helpers(qpane)
        _assert_catalog_signals_emit(qpane, qapp)
        _assert_zoom_signal_matches_accessor(qpane, qapp)
        _assert_viewport_rect_signal_on_resize(qpane, qapp)
        _assert_panel_hit_test_facade(qpane, qapp)
        _assert_linked_groups_facade(qpane)
        _assert_diagnostics_signals_emit(qpane)
        _assert_overlay_registry_handles_registration(qpane)
        _assert_overlay_draw_invoked_during_paint(qpane, monkeypatch)
        _assert_missing_mask_warning_once(qpane, caplog)
        _assert_missing_sam_warning_once(qpane, caplog)
        _assert_apply_settings_updates_dependants(qpane, baseline_config)
        _assert_apply_settings_clears_tile_cache(qpane, tmp_path, baseline_config)
        _assert_minimum_size_hint_clamps_to_safe_zoom(qpane, baseline_config)
        _assert_cursor_falls_back_when_tool_missing(qpane, monkeypatch)
        _assert_is_drag_out_allowed_respects_zoom_mode(qpane)
        _assert_is_drag_out_allowed_respects_config(qpane, baseline_config)
        _assert_presenter_ensure_view_alignment_updates_fit(qpane, monkeypatch)
        _assert_presenter_ensure_view_alignment_detects_dpr_change(qpane, monkeypatch)
        _assert_qpane_rebase_zoom_behaviors(qpane, monkeypatch)
        _assert_presenter_ensure_view_alignment_preserves_custom(qpane, monkeypatch)
        _assert_swap_apply_image_realigns_view(qpane, monkeypatch, tmp_path)
        _assert_qpane_resize_event_forces_alignment(qpane, monkeypatch)
        _assert_qpane_paint_event_triggers_alignment(qpane, monkeypatch)
    finally:
        qpane.applySettings(config=baseline_config)
        qpane.clearImages()
        _cleanup_qpane(qpane, qapp)


_CURSOR_STRESS_POINTS = 5
_CURSOR_IMAGE_EDGE = 32


@pytest.mark.usefixtures("qapp")
def test_qpane_mask_smoke(qapp):
    """Aggregate mask-specific smoke checks to minimise QPane instantiations."""
    executor = StubExecutor(auto_finish=True)
    qpane, image = _prepare_qpane_with_mask_feature(executor=executor)
    baseline_config = qpane.settings
    try:
        _assert_apply_settings_accepts_external_config_snapshot(qpane, baseline_config)
        _assert_mask_helper_wrappers_delegate_to_workflow(qpane, image)
    finally:
        qpane.applySettings(config=baseline_config)
        _cleanup_qpane(qpane, qapp)
    slow_executor = StubExecutor(auto_finish=False)
    slow_qpane, slow_image = _prepare_qpane_with_mask_feature(
        executor=slow_executor,
        image_size_px=_CURSOR_IMAGE_EDGE,
    )
    try:
        _assert_brush_cursor_stays_responsive_with_worker_load(
            slow_qpane,
            slow_image,
            slow_executor,
        )
    finally:
        _cleanup_qpane(slow_qpane, qapp)


@pytest.mark.usefixtures("qapp")
def test_qpane_feature_installation_paths(monkeypatch, caplog, qapp):
    """Validate feature installation failures and fallbacks share the fast path."""
    _assert_failed_mask_install_warning(monkeypatch, caplog, qapp)
    _assert_mask_feature_installs_with_stub(monkeypatch, qapp)
    _assert_full_feature_install_with_stubs(monkeypatch, qapp)


def _assert_core_initial_state(qpane: QPane) -> None:
    """Validate default feature wiring before any catalog state loads."""
    assert qpane.installedFeatures == ()
    assert qpane.mask_controller is None
    assert qpane.samManager() is None
    assert qpane.autosaveManager() is None
    random_mask_id = uuid.uuid4()
    assert not qpane.setActiveMaskID(random_mask_id)
    assert qpane.getActiveMaskImage() is None
    assert qpane.failedFeatures() == {}


def _assert_view_alignment(qpane: QPane) -> None:
    """Ensure the view exposes the same presenter managed by the facade."""
    view = qpane.view()
    presenter = qpane.presenter()
    assert view.presenter is presenter
    assert view.viewport is presenter.viewport
    assert view.tile_manager is presenter.tile_manager


def _assert_view_handles_tile_ready(qpane: QPane, monkeypatch) -> None:
    """Verify tile-ready callbacks delegate to the swap layer."""
    view = qpane.view()
    sentinel = make_tile_key()
    calls: list[SceneLayerTileKey] = []
    monkeypatch.setattr(view.swap_delegate, "handle_tile_ready", calls.append)
    view.handle_tile_ready(sentinel)
    assert calls == [sentinel]


def _assert_view_handles_pyramid_ready(qpane: QPane, monkeypatch) -> None:
    """Verify pyramid-ready callbacks delegate to the swap layer."""
    view = qpane.view()
    sentinel = default_catalog_asset_key(uuid.uuid4(), revision=0, source_path=None)
    calls: list[uuid.UUID | None] = []
    monkeypatch.setattr(view.swap_delegate, "handle_pyramid_ready", calls.append)
    view.handle_pyramid_ready(sentinel)
    assert calls == [sentinel]


def _assert_mark_dirty_delegates(qpane: QPane, monkeypatch) -> None:
    """Ensure QPane.markDirty forwards the request to the View."""
    sentinel = QRect(0, 0, 5, 5)
    calls: list[QRect] = []
    monkeypatch.setattr(qpane.view(), "mark_dirty", lambda rect: calls.append(rect))
    qpane.markDirty(sentinel)
    assert calls == [sentinel]


def _assert_image_facade_helpers(qpane: QPane) -> None:
    """Exercise QPane.imageMap helpers and current image navigation."""
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    image_map = QPane.imageMapFromLists(
        images=[_solid_image(), _solid_image()],
        paths=[None, None],
        ids=[first_id, second_id],
    )
    qpane.setImagesByID(image_map, current_id=first_id)
    assert qpane.hasImages() is True
    assert qpane.imageIDs() == [first_id, second_id]
    assert qpane.currentImageID() == first_id
    qpane.setCurrentImageID(second_id)
    assert qpane.currentImageID() == second_id
    qpane.clearImages()


def _assert_catalog_signals_emit(qpane: QPane, qapp) -> None:
    """Confirm catalogChanged and catalogSelectionChanged emit for mutations."""
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    image_map = QPane.imageMapFromLists(
        images=[_solid_image(), _solid_image()],
        paths=[None, None],
        ids=[first_id, second_id],
    )
    mutations: list[CatalogMutationEvent] = []
    selections: list[uuid.UUID | None] = []
    qpane.catalogChanged.connect(mutations.append)
    qpane.catalogSelectionChanged.connect(selections.append)
    try:
        qpane.setImagesByID(image_map, current_id=first_id)
        qpane.setCurrentImageID(second_id)
        qapp.processEvents()
        assert mutations and mutations[0].reason == "setImagesByID"
        assert first_id in selections and second_id in selections
    finally:
        qpane.catalogChanged.disconnect(mutations.append)
        qpane.catalogSelectionChanged.disconnect(selections.append)
        qpane.clearImages()


def _assert_zoom_signal_matches_accessor(qpane: QPane, qapp) -> None:
    """Verify zoomChanged emits and matches QPane.currentZoom."""
    zooms: list[float] = []
    qpane.zoomChanged.connect(zooms.append)
    try:
        qapp.processEvents()
        if not zooms:
            qpane._emit_zoom_snapshot()
        assert zooms
        assert zooms[-1] == pytest.approx(qpane.currentZoom())
    finally:
        qpane.zoomChanged.disconnect(zooms.append)


def _assert_viewport_rect_signal_on_resize(qpane: QPane, qapp) -> None:
    """Ensure viewportRectChanged fires when the widget resizes."""
    rects: list[QRectF] = []
    qpane.viewportRectChanged.connect(rects.append)
    try:
        qpane.show()
        qapp.processEvents()
        initial = len(rects)
        qpane.resize(qpane.width() + 17, qpane.height() + 11)
        qapp.processEvents()
        assert len(rects) > initial
        assert rects[-1] == qpane.currentViewportRect()
    finally:
        qpane.viewportRectChanged.disconnect(rects.append)


def _assert_panel_hit_test_facade(qpane: QPane, qapp) -> None:
    """Check qpane.panelHitTest surfaces the same data as the view."""
    image_id = uuid.uuid4()
    image_map = QPane.imageMapFromLists([_solid_image()], [None], [image_id])
    qpane.setImagesByID(image_map, current_id=image_id)
    qapp.processEvents()
    panel_point = QPoint(qpane.width() // 2, qpane.height() // 2)
    facade_hit = qpane.panelHitTest(panel_point)
    direct_hit = qpane.view().panel_hit_test(panel_point)
    assert facade_hit == direct_hit
    qpane.clearImages()


def _assert_linked_groups_facade(qpane: QPane) -> None:
    """Verify link groups emit changes only when membership changes."""
    first_id, second_id, third_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    image_map = QPane.imageMapFromLists(
        images=[_solid_image(), _solid_image(), _solid_image()],
        paths=[None, None, None],
        ids=[first_id, second_id, third_id],
    )
    qpane.setImagesByID(image_map, current_id=first_id)
    emissions: list[bool] = []

    def _record_link_change() -> None:
        emissions.append(True)

    qpane.linkGroupsChanged.connect(_record_link_change)
    try:
        group_id = uuid.uuid4()
        qpane.setLinkedGroups(
            (LinkedGroup(group_id=group_id, members=(first_id, second_id)),)
        )
        groups = qpane.linkedGroups()
        assert len(groups) == 1
        assert groups[0].group_id == group_id
        assert set(groups[0].members) == {first_id, second_id}
        assert len(emissions) == 1
        qpane.setLinkedGroups(
            (LinkedGroup(group_id=group_id, members=(first_id, second_id)),)
        )
        assert len(emissions) == 1
        qpane.setLinkedGroups(
            (LinkedGroup(group_id=group_id, members=(first_id, third_id)),)
        )
        assert len(emissions) == 2
    finally:
        qpane.linkGroupsChanged.disconnect(_record_link_change)
        qpane.setLinkedGroups(tuple())
        qpane.clearImages()


def _assert_diagnostics_signals_emit(qpane: QPane) -> None:
    """Ensure diagnostics overlay and domain toggles emit their signals."""
    overlay_events: list[bool] = []
    domain_events: list[tuple[str, bool]] = []
    qpane.diagnosticsOverlayToggled.connect(overlay_events.append)

    def _record_domain(domain: str, enabled: bool) -> None:
        domain_events.append((domain, enabled))

    qpane.diagnosticsDomainToggled.connect(_record_domain)
    try:
        qpane.setDiagnosticsOverlayEnabled(True)
        qpane.setDiagnosticsOverlayEnabled(False)
        assert overlay_events == [True, False]
        domains = qpane.diagnosticsDomains()
        target_domain = domains[0] if domains else "cache"
        qpane.setDiagnosticsDomainEnabled(target_domain, True)
        qpane.setDiagnosticsDomainEnabled(target_domain, False)
        assert domain_events[-2:] == [(target_domain, True), (target_domain, False)]
    finally:
        qpane.diagnosticsOverlayToggled.disconnect(overlay_events.append)
        qpane.diagnosticsDomainToggled.disconnect(_record_domain)


def _assert_overlay_registry_handles_registration(qpane: QPane) -> None:
    """Confirm overlays can be registered, rejected when duplicated, and removed."""

    def draw_fn(painter, state):
        return None

    qpane.registerOverlay("diagnostics", draw_fn)
    try:
        assert "diagnostics" in qpane.contentOverlays()
        with pytest.raises(ValueError):
            qpane.registerOverlay("diagnostics", draw_fn)
    finally:
        qpane.unregisterOverlay("diagnostics")
    assert "diagnostics" not in qpane.contentOverlays()


def _assert_overlay_draw_invoked_during_paint(qpane: QPane, monkeypatch) -> None:
    """Ensure overlay draw functions receive the presenter state during paint."""
    qpane.resize(20, 20)
    source_image = _solid_image(2, 2)
    render_plan = make_render_plan(
        QRect(0, 0, 20, 20),
        source_image=source_image,
        transform=QTransform(),
        zoom=1.25,
        strategy=RenderStrategy.DIRECT,
        tile_size=256,
        max_tile_cols=0,
        max_tile_rows=0,
        current_pan=QPointF(0, 0),
        physical_viewport_rect=QRectF(0, 0, 20, 20),
    )

    class DummyRenderer:
        def __init__(self):
            self.calls: list[object] = []
            self._image = QImage(2, 2, QImage.Format_ARGB32)
            self._image.fill(Qt.black)

        def paint(self, plan):
            self.calls.append(plan)

        def get_base_buffer(self):
            return self._image

        def get_subpixel_pan_offset(self):
            return QPointF(0, 0)

        def draw_base_buffer(self, painter):
            painter.drawImage(0, 0, self._image)

        def buffer_matches_viewport(self, size, dpr):
            return self._image.size() == size

        def allocate_buffers(self, size, dpr):
            self._image = QImage(size.width(), size.height(), QImage.Format_ARGB32)
            self._image.fill(Qt.black)

        def markDirty(self, rect):
            return None

    presenter = qpane.view().presenter
    original_renderer = presenter.renderer
    original_plan_fn = presenter.calculateRenderPlan
    renderer = DummyRenderer()
    presenter.renderer = renderer
    presenter.calculateRenderPlan = lambda **_: render_plan
    original_overlay = qpane._tools_manager.draw_overlay
    qpane._tools_manager.draw_overlay = lambda painter: None
    calls: list[OverlayState] = []

    def overlay(painter, state):
        calls.append(state)

    qpane.registerOverlay("unit-test", overlay)
    try:
        qpane.paintEvent(None)
        assert len(calls) == 1
        overlay_state = calls[0]
        base_item = render_plan.base_raster_item
        assert base_item is not None
        assert overlay_state.zoom == render_plan.zoom
        assert overlay_state.qpane_rect == render_plan.qpane_rect
        assert overlay_state.source_image.size() == base_item.source_image.size()
        assert overlay_state.source_image.pixelColor(
            0, 0
        ) == base_item.source_image.pixelColor(0, 0)
        assert overlay_state.transform == base_item.transform
        assert overlay_state.current_pan == render_plan.current_pan
        assert (
            overlay_state.physical_viewport_rect == render_plan.physical_viewport_rect
        )
    finally:
        qpane.unregisterOverlay("unit-test")
        presenter.renderer = original_renderer
        presenter.calculateRenderPlan = original_plan_fn
        qpane._tools_manager.draw_overlay = original_overlay


def _assert_missing_mask_warning_once(qpane: QPane, caplog) -> None:
    """Check that missing mask feature warnings emit once per session."""
    qpane.featureFallbacks().reset()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="qpane.fallbacks"):
        assert not qpane.setActiveMaskID(uuid.uuid4())
    assert any("Feature 'mask'" in record.message for record in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="qpane.fallbacks"):
        assert not qpane.setActiveMaskID(uuid.uuid4())
    assert not caplog.records


def _assert_missing_sam_warning_once(qpane: QPane, caplog) -> None:
    """Check that missing SAM feature warnings also dedupe."""
    qpane.featureFallbacks().reset()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="qpane.fallbacks"):
        qpane.setControlMode(QPane.CONTROL_MODE_SMART_SELECT)
    assert any("Feature 'sam'" in record.message for record in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="qpane.fallbacks"):
        qpane.setControlMode(QPane.CONTROL_MODE_SMART_SELECT)
    assert not caplog.records


def _assert_apply_settings_updates_dependants(
    qpane: QPane, baseline_config: Config
) -> None:
    """Ensure applySettings refreshes viewer collaborators and cache config."""
    original_settings = qpane.settings
    catalog = qpane.catalog().imageCatalog()
    qpane.applySettings(tile_size=512, cache={"pyramids": {"mb": 4096}})
    assert qpane.settings is not original_settings
    assert qpane.settings.tile_size == 512
    qpane_view = qpane.view()
    assert qpane_view.tile_manager.tile_size == 512
    assert catalog.pyramid_manager.cache_limit_bytes == 4096 * 1024 * 1024
    assert qpane_view.viewport._config is qpane.settings
    assert catalog._config is qpane.settings
    qpane.applySettings(config=baseline_config)


def _assert_apply_settings_clears_tile_cache(
    qpane: QPane, tmp_path, baseline_config: Config
) -> None:
    """Changing tile size should flush tile cache and worker metadata."""
    tile_manager = qpane.view().tile_manager
    image_id = uuid.uuid4()
    identifier = make_tile_key(image_id, tmp_path / "image.png", 1.0, 0, 0)
    tile_manager._tile_cache[identifier] = Tile(key=identifier, image=QImage())
    tile_manager._cache_size_bytes = 128
    tile_manager._worker_state[identifier] = {"worker": None, "handle": None}
    new_tile_size = tile_manager.tile_size // 2
    qpane.applySettings(tile_size=new_tile_size)
    assert tile_manager.tile_size == new_tile_size
    assert tile_manager._cache_size_bytes == 0
    assert not tile_manager._tile_cache
    assert not tile_manager._worker_state
    qpane.applySettings(config=baseline_config)


def _assert_minimum_size_hint_clamps_to_safe_zoom(
    qpane: QPane, baseline_config: Config
) -> None:
    """Minimum size hints should never drop below one pixel even with tiny config."""
    config = Config(min_view_size_px=0.05, safe_min_zoom=1e-5)
    qpane.applySettings(config=config)
    qpane.resize(32, 32)
    image = QImage(16, 16, QImage.Format_ARGB32)
    image.fill(Qt.white)
    image_id = uuid.uuid4()
    image_map = QPane.imageMapFromLists([image], [None], [image_id])
    qpane.setImagesByID(image_map, image_id)
    hint = qpane.minimumSizeHint()
    assert hint.isValid()
    assert hint.width() >= 1
    assert hint.height() >= 1
    qpane.clearImages()
    qpane.applySettings(config=baseline_config)


def _assert_cursor_falls_back_when_tool_missing(qpane: QPane, monkeypatch) -> None:
    """If no tool provides a cursor, QPane should fall back to Arrow."""
    monkeypatch.setattr(qpane._tools_manager, "get_active_tool", lambda: None)
    qpane.refreshCursor()
    assert qpane.cursor().shape() == Qt.CursorShape.ArrowCursor


def _assert_is_drag_out_allowed_respects_zoom_mode(qpane: QPane) -> None:
    """Drag-out availability depends on the current zoom mode and factor."""
    qpane.resize(64, 64)
    image = QImage(16, 16, QImage.Format_ARGB32)
    image.fill(Qt.white)
    image_id = uuid.uuid4()
    image_map = QPane.imageMapFromLists([image], [None], [image_id])
    qpane.setImagesByID(image_map, image_id)
    viewport = qpane.view().viewport
    assert viewport.get_zoom_mode() == ViewportZoomMode.FIT
    assert qpane.isDragOutAllowed() is True
    baseline_zoom = viewport.zoom
    viewport.zoom_mode = ViewportZoomMode.CUSTOM
    viewport.zoom = baseline_zoom / 2
    assert qpane.isDragOutAllowed() is True
    viewport.zoom = baseline_zoom * 4
    assert qpane.isDragOutAllowed() is False
    viewport.zoom_mode = ViewportZoomMode.FIT
    viewport.zoom = baseline_zoom
    assert qpane.isDragOutAllowed() is True
    qpane.clearImages()


def _assert_is_drag_out_allowed_respects_config(
    qpane: QPane, baseline_config: Config
) -> None:
    """Global drag-out disablement should override zoom-based allowances."""
    config = Config()
    config.drag_out_enabled = False
    qpane.applySettings(config=config)
    qpane.resize(64, 64)
    image = QImage(16, 16, QImage.Format_ARGB32)
    image.fill(Qt.white)
    image_id = uuid.uuid4()
    image_map = QPane.imageMapFromLists([image], [None], [image_id])
    qpane.setImagesByID(image_map, image_id)
    assert qpane.isDragOutAllowed() is False
    qpane.clearImages()
    qpane.applySettings(config=baseline_config)


def _assert_presenter_ensure_view_alignment_updates_fit(
    qpane: QPane, monkeypatch
) -> None:
    """ensure_view_alignment should re-fit and allocate buffers when view size changes."""
    qpane.resize(120, 80)
    qpane.original_image = _solid_image(64, 48)
    presenter = qpane.view().presenter
    viewport = presenter.viewport
    viewport.setContentSize(qpane.original_image.size())
    fit_calls: list[str] = []
    alloc_calls: list[str] = []
    monkeypatch.setattr(
        presenter.viewport, "setZoomFit", lambda: fit_calls.append("fit")
    )
    monkeypatch.setattr(
        presenter, "allocate_buffers", lambda: alloc_calls.append("alloc")
    )
    presenter._last_view_size = QSize(10, 10)
    presenter.viewport.zoom_mode = ViewportZoomMode.FIT
    presenter.ensure_view_alignment()
    assert fit_calls == ["fit"]
    assert alloc_calls == ["alloc"]
    assert presenter._last_view_size == qpane.size()


def _assert_presenter_ensure_view_alignment_detects_dpr_change(
    qpane: QPane, monkeypatch
) -> None:
    """DPR changes should trigger fit + buffer reallocation even without size change."""
    qpane.resize(120, 80)
    qpane.original_image = _solid_image(64, 48)
    presenter = qpane.view().presenter
    viewport = presenter.viewport
    viewport.setContentSize(qpane.original_image.size())
    viewport.zoom_mode = ViewportZoomMode.FIT
    presenter._last_view_size = QSize(qpane.size())
    presenter._last_device_pixel_ratio = 1.0
    fit_calls: list[str] = []
    alloc_calls: list[str] = []
    monkeypatch.setattr(
        presenter.viewport, "setZoomFit", lambda: fit_calls.append("fit")
    )
    monkeypatch.setattr(
        presenter, "allocate_buffers", lambda: alloc_calls.append("alloc")
    )
    monkeypatch.setattr(qpane, "devicePixelRatioF", lambda: 2.0)
    presenter.ensure_view_alignment()
    assert fit_calls == ["fit"]
    assert alloc_calls == ["alloc"]
    assert presenter._last_device_pixel_ratio == 2.0


def _assert_qpane_rebase_zoom_behaviors(qpane: QPane, monkeypatch) -> None:
    """Cover the zoom rebasing permutations without repeated QPane construction."""
    original_normalize = qpane.settings.normalize_zoom_on_screen_change
    original_one_to_one = qpane.settings.normalize_zoom_for_one_to_one
    view = qpane.view()
    presenter = view.presenter
    viewport = presenter.viewport

    def _configure_view(width: int, height: int, img_w: int, img_h: int) -> None:
        qpane.resize(width, height)
        qpane.original_image = _solid_image(img_w, img_h)
        viewport.setContentSize(qpane.original_image.size())

    def _run_rebase(prev: float, new: float) -> list[bool]:
        align_calls: list[bool] = []
        with monkeypatch.context() as patch:
            patch.setattr(
                presenter,
                "ensure_view_alignment",
                lambda *, force=False: align_calls.append(force),
            )
            qpane._rebase_zoom_for_screen_change(prev, new)
        return align_calls

    try:
        qpane.settings.normalize_zoom_on_screen_change = True
        qpane.settings.normalize_zoom_for_one_to_one = True
        _configure_view(200, 120, 400, 260)
        viewport.zoom = 1.25
        viewport.pan = QPointF(10.0, -6.0)
        calls = _run_rebase(1.0, 2.0)
        assert viewport.zoom == pytest.approx(2.5, rel=1e-6)
        assert viewport.pan.x() == pytest.approx(20.0, rel=1e-6)
        assert viewport.pan.y() == pytest.approx(-12.0, rel=1e-6)
        assert calls and calls[-1] is True
        qpane.settings.normalize_zoom_on_screen_change = False
        _configure_view(180, 110, 500, 300)
        viewport.zoom = 1.4
        viewport.pan = QPointF(6.0, 4.0)
        calls = _run_rebase(1.0, 2.0)
        assert viewport.zoom == pytest.approx(1.4, rel=1e-6)
        assert viewport.pan.x() == pytest.approx(6.0, rel=1e-6)
        assert viewport.pan.y() == pytest.approx(4.0, rel=1e-6)
        assert not calls
        qpane.settings.normalize_zoom_on_screen_change = True
        qpane.settings.normalize_zoom_for_one_to_one = False
        _configure_view(210, 140, 420, 280)
        viewport.zoom = 1.0
        viewport.pan = QPointF(-8.0, 6.0)
        viewport.zoom_mode = ViewportZoomMode.ONE_TO_ONE
        calls = _run_rebase(1.0, 2.0)
        assert viewport.zoom == pytest.approx(1.0, rel=1e-6)
        assert viewport.pan.x() == pytest.approx(-8.0, rel=1e-6)
        assert viewport.pan.y() == pytest.approx(6.0, rel=1e-6)
        assert not calls
        qpane.settings.normalize_zoom_for_one_to_one = True
        _configure_view(220, 150, 440, 300)
        viewport.zoom = 0.8
        viewport.pan = QPointF(5.0, -3.0)
        viewport.zoom_mode = ViewportZoomMode.ONE_TO_ONE
        calls = _run_rebase(1.0, 1.5)
        assert viewport.zoom == pytest.approx(1.2, rel=1e-6)
        assert viewport.pan.x() == pytest.approx(7.5, rel=1e-6)
        assert viewport.pan.y() == pytest.approx(-4.5, rel=1e-6)
        assert calls and calls[-1] is True
        qpane.settings.normalize_zoom_for_one_to_one = False
        _configure_view(230, 160, 460, 320)
        viewport.zoom_mode = ViewportZoomMode.CUSTOM
        viewport.zoom = 1.3
        viewport.pan = QPointF(-3.0, 5.0)
        monkeypatch.setattr(viewport, "nativeZoom", lambda: 1.3)
        calls = _run_rebase(1.0, 1.6)
        assert viewport.zoom == pytest.approx(1.3, rel=1e-6)
        assert viewport.pan.x() == pytest.approx(-3.0, rel=1e-6)
        assert viewport.pan.y() == pytest.approx(5.0, rel=1e-6)
        assert not calls
        qpane.settings.normalize_zoom_for_one_to_one = True
        _configure_view(240, 170, 480, 340)
        viewport.zoom = 0.9
        viewport.pan = QPointF(4.0, -7.0)
        monkeypatch.setattr(viewport, "nativeZoom", lambda: 0.9)
        calls = _run_rebase(1.0, 1.4)
        assert viewport.zoom == pytest.approx(1.26, rel=1e-6)
        assert viewport.pan.x() == pytest.approx(5.6, rel=1e-6)
        assert viewport.pan.y() == pytest.approx(-9.8, rel=1e-6)
        assert calls and calls[-1] is True
    finally:
        qpane.settings.normalize_zoom_on_screen_change = original_normalize
        qpane.settings.normalize_zoom_for_one_to_one = original_one_to_one


def _assert_presenter_ensure_view_alignment_preserves_custom(
    qpane: QPane, monkeypatch
) -> None:
    """Custom zoom modes should avoid forcing a fit while still realigning pan."""
    qpane.resize(120, 80)
    qpane.original_image = _solid_image(64, 48)
    presenter = qpane.view().presenter
    viewport = presenter.viewport
    viewport.setContentSize(qpane.original_image.size())
    viewport.zoom_mode = ViewportZoomMode.CUSTOM
    fit_calls: list[str] = []
    pan_calls: list[QPointF] = []
    monkeypatch.setattr(
        presenter.viewport, "setZoomFit", lambda: fit_calls.append("fit")
    )

    def fake_set_pan(value: QPointF) -> None:
        pan_calls.append(value)

    monkeypatch.setattr(presenter.viewport, "setPan", fake_set_pan)
    presenter._last_view_size = QSize(10, 10)
    presenter.ensure_view_alignment(force=True)
    assert not fit_calls
    assert pan_calls and pan_calls[-1] == viewport.pan


def _assert_swap_apply_image_realigns_view(qpane: QPane, monkeypatch, tmp_path) -> None:
    """Swap delegate should force alignment when a new image lands."""
    qpane.resize(80, 60)
    qpane.original_image = _solid_image(40, 30)
    presenter = qpane.view().presenter
    presenter.viewport.setContentSize(qpane.original_image.size())
    calls: list[bool] = []

    def fake_align(*, force=False):
        calls.append(bool(force))

    monkeypatch.setattr(presenter, "ensure_view_alignment", fake_align)
    qpane.view().swap_delegate.apply_image(
        _solid_image(16, 16),
        tmp_path / "swap.png",
        image_id=uuid.uuid4(),
        fit_view=True,
    )
    assert calls and calls[-1] is True


def _assert_qpane_resize_event_forces_alignment(qpane: QPane, monkeypatch) -> None:
    """QPane.resizeEvent should always force view alignment."""
    calls: list[bool] = []

    def fake_align(*, force=False):
        calls.append(bool(force))

    monkeypatch.setattr(qpane.view().presenter, "ensure_view_alignment", fake_align)
    qpane.resizeEvent(None)
    assert calls == [True]


def _assert_qpane_paint_event_triggers_alignment(qpane: QPane, monkeypatch) -> None:
    """Non-blank paint events should request alignment without flagging force."""
    qpane.resize(64, 64)
    qpane._is_blank = False
    original_overlay_draw = qpane._tools_manager.draw_overlay
    qpane._tools_manager.draw_overlay = lambda painter: None
    presenter = qpane.view().presenter
    original_plan_fn = presenter.calculateRenderPlan
    presenter.calculateRenderPlan = lambda **_: None

    class DummyRenderer:
        def __init__(self):
            self._image = _solid_image(8, 8)

        def paint(self, plan):
            return None

        def get_base_buffer(self):
            return self._image

        def get_subpixel_pan_offset(self):
            return QPointF(0, 0)

        def draw_base_buffer(self, painter):
            painter.drawImage(0, 0, self._image)

        def buffer_matches_viewport(self, size, dpr):
            return True

    original_renderer = presenter.renderer
    presenter.renderer = DummyRenderer()
    calls: list[bool] = []

    def fake_align(*, force=False):
        calls.append(bool(force))

    monkeypatch.setattr(presenter, "ensure_view_alignment", fake_align)
    try:
        qpane.paintEvent(None)
        assert calls == [False]
    finally:
        qpane._tools_manager.draw_overlay = original_overlay_draw
        presenter.calculateRenderPlan = original_plan_fn
        presenter.renderer = original_renderer


def _assert_apply_settings_accepts_external_config_snapshot(
    qpane: QPane, baseline_config: Config
) -> None:
    """Reapplying an external Config snapshot should clone and propagate defaults."""
    new_config = Config(default_brush_size=47, mask_autosave_enabled=False)
    qpane.interaction.brush_size = qpane.settings.default_brush_size
    qpane.applySettings(config=new_config)
    assert qpane.settings is not new_config
    assert qpane.settings.default_brush_size == 47
    assert qpane.interaction.brush_size == 47
    assert qpane.settings.mask_autosave_enabled is False
    qpane.applySettings(config=baseline_config)


def _assert_mask_helper_wrappers_delegate_to_workflow(
    qpane: QPane, image: QImage
) -> None:
    """Mask helper facade methods should round-trip through the workflow service."""
    service = _mask_service(qpane)
    assert service is not None
    mask_id = qpane.createBlankMask(image.size())
    assert mask_id is not None
    qpane.setActiveMaskID(mask_id)
    drain_mask_jobs(qpane)
    assert qpane.activeMaskID() == mask_id
    assert mask_id in qpane.maskIDsForImage(qpane.currentImageID())


def _assert_brush_cursor_stays_responsive_with_worker_load(
    qpane: QPane,
    image: QImage,
    executor: StubExecutor,
) -> None:
    """Cursor refreshes must stay responsive while stroke work queues drain."""
    service = _mask_service(qpane)
    assert service is not None
    mask_id = service.createBlankMask(image.size())
    assert mask_id is not None
    assert qpane.setActiveMaskID(mask_id)
    qpane.interaction.brush_size = 7
    durations: list[float] = []
    for idx in range(_CURSOR_STRESS_POINTS):
        _queue_pending_stroke(qpane, QPoint(idx + 2, idx + 2))
        qpane.interaction.alt_key_held = bool(idx % 2)
        start = time.perf_counter()
        qpane.refreshCursor()
        durations.append(time.perf_counter() - start)
    pending, tokens = drain_mask_jobs(qpane, executor=executor)
    assert not pending and not tokens
    assert durations and max(durations) < 0.1


def _assert_failed_mask_install_warning(monkeypatch, caplog, qapp) -> None:
    """Mask installation failures should surface their hint in the fallback warning."""
    from qpane.masks import install as mask

    def failing_install(qpane):
        raise FeatureInstallError("Requires OpenCV")

    monkeypatch.setattr(mask, "install_mask_feature", failing_install)
    qpane = QPane(features=("mask",))
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="qpane.fallbacks"):
            assert not qpane.setActiveMaskID(uuid.uuid4())
        assert any("Requires OpenCV" in record.message for record in caplog.records)
    finally:
        _cleanup_qpane(qpane, qapp)


def _assert_mask_feature_installs_with_stub(monkeypatch, qapp) -> None:
    """Mask feature installers should register overlays and autosave managers."""
    from qpane.masks import install as mask

    def fake_install(qpane):
        qpane.mask_controller = object()
        qpane._set_autosave_manager(object())
        qpane.registerOverlay("mask_stub", lambda painter, state: None)

    monkeypatch.setattr(mask, "install_mask_feature", fake_install)
    qpane = QPane(features=("mask",))
    try:
        assert qpane.installedFeatures == ("mask",)
        assert qpane.mask_controller is not None
        assert qpane.autosaveManager() is not None
        assert "mask_stub" in qpane.contentOverlays()
    finally:
        qpane.unregisterOverlay("mask_stub")
        _cleanup_qpane(qpane, qapp)


def _assert_full_feature_install_with_stubs(monkeypatch, qapp) -> None:
    """Installing mask + SAM should attach both controllers via their stubs."""
    from qpane.masks import install as mask
    from qpane.masks import sam_feature as sam

    def mask_install(qpane):
        qpane.mask_controller = object()
        qpane._set_autosave_manager(object())

    def sam_install(qpane):
        qpane._set_sam_manager(object())

    monkeypatch.setattr(mask, "install_mask_feature", mask_install)
    monkeypatch.setattr(sam, "install_sam_feature", sam_install)
    qpane = QPane(features=("mask", "sam"))
    try:
        assert set(qpane.installedFeatures) == {"mask", "sam"}
        assert qpane.samManager() is not None
        assert qpane.mask_controller is not None
        assert qpane.autosaveManager() is not None
    finally:
        _cleanup_qpane(qpane, qapp)
