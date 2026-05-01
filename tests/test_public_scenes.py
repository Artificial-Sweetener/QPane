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

"""Tests for public catalog-backed scene composition."""

from __future__ import annotations

import uuid

import pytest
from PySide6.QtCore import QPoint, QRectF, QSize
from PySide6.QtGui import QColor, QImage, Qt

from examples.demonstration import scene_composition
from qpane import (
    ComparisonOrientation,
    QPane,
    QPaneCatalogImageLayerRequest,
    QPaneScene,
    QPaneSceneClip,
    QPaneSceneRequest,
    QPaneSceneTemplate,
    QPaneSceneTemplateBindings,
    QPaneTemplateLayer,
)
from qpane.scene.identity import default_catalog_asset_key
from qpane.scene.render_plan import RasterLayerRenderItem, RenderStrategy


def _solid_image(
    width: int = 100,
    height: int = 100,
    color: Qt.GlobalColor | QColor = Qt.white,
) -> QImage:
    """Return a solid image for scene tests."""
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(color)
    return image


def _cleanup_qpane(qpane: QPane, qapp) -> None:
    """Release a QPane through Qt's event loop."""
    qpane.deleteLater()
    qapp.processEvents()


def _load_images(qpane: QPane, *, large: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
    """Load two catalog images and return their IDs."""
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    size = 1024 if large else 100
    qpane.setImagesByID(
        QPane.imageMapFromLists(
            [
                _solid_image(size, size, Qt.red),
                _solid_image(size, size, Qt.blue),
            ],
            [None, None],
            [first_id, second_id],
        ),
        first_id,
    )
    return first_id, second_id


def _scene_request(first_id: uuid.UUID, second_id: uuid.UUID) -> QPaneSceneRequest:
    """Return a two-layer public scene request."""
    return QPaneSceneRequest(
        composition_id=None,
        title="Contact sheet",
        bounds=QRectF(0.0, 0.0, 200.0, 100.0),
        layers=(
            QPaneCatalogImageLayerRequest(
                layer_id=uuid.uuid4(),
                image_id=first_id,
                placement=QRectF(0.0, 0.0, 100.0, 100.0),
                role="thumbnail",
                metadata={"slot": 0},
            ),
            QPaneCatalogImageLayerRequest(
                layer_id=uuid.uuid4(),
                image_id=second_id,
                placement=QRectF(100.0, 0.0, 100.0, 100.0),
                role="thumbnail",
                metadata={"slot": 1},
            ),
        ),
    )


def _clipped_scene_request(
    image_id: uuid.UUID,
    clip: QPaneSceneClip,
) -> QPaneSceneRequest:
    """Return a one-layer scene request using ``clip``."""
    return QPaneSceneRequest(
        composition_id=None,
        title="Clipped scene",
        bounds=QRectF(0.0, 0.0, 100.0, 100.0),
        layers=(
            QPaneCatalogImageLayerRequest(
                layer_id=uuid.uuid4(),
                image_id=image_id,
                placement=QRectF(0.0, 0.0, 100.0, 100.0),
                clip=clip,
            ),
        ),
    )


def _assert_rect(
    rect: QRectF,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    """Assert QRectF coordinates with float tolerance."""
    assert rect.x() == pytest.approx(x)
    assert rect.y() == pytest.approx(y)
    assert rect.width() == pytest.approx(width)
    assert rect.height() == pytest.approx(height)


def test_fit_scene_rect_preserves_portrait_aspect_inside_landscape_target() -> None:
    """fitSceneRect should fit portrait sources without distortion."""
    rect = QPane.fitSceneRect(QSize(200, 400), QRectF(0.0, 0.0, 320.0, 240.0))

    _assert_rect(rect, x=100.0, y=0.0, width=120.0, height=240.0)


def test_fit_scene_rect_preserves_landscape_aspect_inside_portrait_target() -> None:
    """fitSceneRect should fit landscape sources without distortion."""
    rect = QPane.fitSceneRect(QSize(400, 200), QRectF(0.0, 0.0, 100.0, 300.0))

    _assert_rect(rect, x=0.0, y=125.0, width=100.0, height=50.0)


def test_fill_scene_rect_covers_target_without_distortion() -> None:
    """fillSceneRect should cover the target while preserving source aspect."""
    rect = QPane.fillSceneRect(QSize(200, 400), QRectF(0.0, 0.0, 320.0, 240.0))

    _assert_rect(rect, x=0.0, y=-200.0, width=320.0, height=640.0)


def test_scene_rect_helpers_center_zero_area_targets() -> None:
    """Aspect helpers should return centered zero-area rectangles for empty slots."""
    rect = QPane.fitSceneRect(QSize(10, 20), QRectF(10.0, 20.0, 0.0, 240.0))

    _assert_rect(rect, x=10.0, y=140.0, width=0.0, height=0.0)


def test_scene_rect_helpers_reject_invalid_dimensions() -> None:
    """Aspect helpers should reject invalid source and target dimensions."""
    with pytest.raises(ValueError, match="source_size dimensions must be positive"):
        QPane.fitSceneRect(QSize(0, 10), QRectF(0.0, 0.0, 10.0, 10.0))
    with pytest.raises(ValueError, match="target_rect dimensions must be non-negative"):
        QPane.fillSceneRect(QSize(10, 10), QRectF(0.0, 0.0, -1.0, 10.0))


def test_contact_sheet_demo_packs_fitted_thumbnail_placements() -> None:
    """The demo contact sheet should gap actual thumbnails, not wide slots."""
    first_id, second_id, third_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    request = scene_composition.build_contact_sheet_request(
        (first_id, second_id, third_id),
        image_sizes={
            first_id: QSize(200, 400),
            second_id: QSize(200, 400),
            third_id: QSize(200, 400),
        },
        columns=2,
        cell_width=320.0,
        cell_height=240.0,
        gap=16.0,
    )

    first, second, third = (layer.placement for layer in request.layers)
    assert first.width() / first.height() == pytest.approx(0.5)
    assert second.x() - (first.x() + first.width()) == pytest.approx(16.0)
    assert third.x() == pytest.approx(0.0)
    assert third.y() == pytest.approx(256.0)
    assert request.bounds.width() == pytest.approx(256.0)
    assert request.bounds.height() == pytest.approx(496.0)


def test_compose_scene_renders_catalog_layers_and_reuses_pyramids(qapp) -> None:
    """Public scenes should render catalog-backed layers through pyramid assets."""
    qpane = QPane(features=())
    qpane.resize(200, 100)
    try:
        first_id, second_id = _load_images(qpane)
        request = _scene_request(first_id, second_id)

        changed: list[QPaneScene | None] = []
        qpane.sceneChanged.connect(changed.append)
        composition_id = qpane.composeScene(request)
        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert qpane.currentScene() == changed[-1]
        assert qpane.currentScene().composition_id == composition_id
        assert qpane.currentScene().scene_id == composition_id
        assert composition_id in qpane.compositionIDs()
        assert plan is not None
        raster_items = [
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        ]
        assert len(raster_items) == 2
        assert [item.asset_key.source_id for item in raster_items] == [
            first_id,
            second_id,
        ]
        assert [item.pyramid_asset_key for item in raster_items] == [
            default_catalog_asset_key(first_id, revision=1, source_path=None),
            default_catalog_asset_key(second_id, revision=1, source_path=None),
        ]

        qpane.view().allocate_buffers()
        qpane.view().renderer.paint(plan)
        buffer = qpane.view().renderer.get_base_buffer()
        assert buffer is not None
        assert buffer.pixelColor(50, 50) == QColor(Qt.red)
        assert buffer.pixelColor(150, 50) == QColor(Qt.blue)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compose_scene_detaches_request_clip(qapp) -> None:
    """Mutating the original clip after composition should not alter stored scene state."""
    qpane = QPane(features=())
    try:
        first_id, _second_id = _load_images(qpane)
        clip = QPaneSceneClip("scene", QRectF(0.0, 0.0, 10.0, 10.0))

        qpane.composeScene(_clipped_scene_request(first_id, clip))

        scene = qpane.currentScene()
        assert scene is not None
        assert scene.layers[0].clip is not None
        assert scene.layers[0].clip.rect.width() == pytest.approx(10.0)

        clip.rect.setWidth(99.0)

        fresh_scene = qpane.currentScene()
        assert fresh_scene is not None
        assert fresh_scene.layers[0].clip is not None
        assert fresh_scene.layers[0].clip.rect.width() == pytest.approx(10.0)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_current_scene_detaches_returned_clip_snapshot(qapp) -> None:
    """Mutating a returned scene clip should not alter later scene snapshots."""
    qpane = QPane(features=())
    try:
        first_id, _second_id = _load_images(qpane)
        clip = QPaneSceneClip("scene", QRectF(0.0, 0.0, 12.0, 12.0))

        qpane.composeScene(_clipped_scene_request(first_id, clip))
        scene = qpane.currentScene()
        assert scene is not None
        snapshot_clip = scene.layers[0].clip
        assert snapshot_clip is not None

        snapshot_clip.rect.setWidth(99.0)

        fresh_scene = qpane.currentScene()
        assert fresh_scene is not None
        assert fresh_scene.layers[0].clip is not None
        assert fresh_scene.layers[0].clip.rect.width() == pytest.approx(12.0)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_scene_raster_smoothing_is_decided_per_layer(qapp) -> None:
    """Scene thumbnails should smooth when minified but stay sharp when magnified."""
    qpane = QPane(features=())
    qpane.resize(110, 50)
    try:
        base_id = uuid.uuid4()
        minified_id = uuid.uuid4()
        magnified_id = uuid.uuid4()
        minified_layer_id = uuid.uuid4()
        magnified_layer_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [
                    _solid_image(100, 100, Qt.red),
                    _solid_image(1000, 1000, Qt.blue),
                    _solid_image(10, 10, Qt.green),
                ],
                [None, None, None],
                [base_id, minified_id, magnified_id],
            ),
            base_id,
        )
        qpane.composeScene(
            QPaneSceneRequest(
                composition_id=None,
                title="Scale checks",
                bounds=QRectF(0.0, 0.0, 110.0, 50.0),
                layers=(
                    QPaneCatalogImageLayerRequest(
                        layer_id=uuid.uuid4(),
                        image_id=base_id,
                        placement=QRectF(0.0, 0.0, 10.0, 10.0),
                    ),
                    QPaneCatalogImageLayerRequest(
                        layer_id=minified_layer_id,
                        image_id=minified_id,
                        placement=QRectF(20.0, 0.0, 50.0, 50.0),
                    ),
                    QPaneCatalogImageLayerRequest(
                        layer_id=magnified_layer_id,
                        image_id=magnified_id,
                        placement=QRectF(80.0, 0.0, 30.0, 30.0),
                    ),
                ),
            )
        )

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        raster_items = {
            item.descriptor.layer_id: item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        }
        assert raster_items[minified_layer_id].render_hint_enabled is True
        assert raster_items[magnified_layer_id].render_hint_enabled is False
        assert raster_items[minified_layer_id].pyramid_asset_key == (
            default_catalog_asset_key(minified_id, revision=1, source_path=None)
        )
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compose_scene_validates_public_inputs(qapp) -> None:
    """Public scene activation should reject invalid scenes before mutation."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        valid_layer = QPaneCatalogImageLayerRequest(
            layer_id=uuid.uuid4(),
            image_id=first_id,
            placement=QRectF(0.0, 0.0, 100.0, 100.0),
        )
        with pytest.raises(KeyError):
            qpane.composeScene(
                QPaneSceneRequest(
                    composition_id=None,
                    title=None,
                    bounds=QRectF(0.0, 0.0, 100.0, 100.0),
                    layers=(
                        QPaneCatalogImageLayerRequest(
                            layer_id=uuid.uuid4(),
                            image_id=uuid.uuid4(),
                            placement=QRectF(0.0, 0.0, 100.0, 100.0),
                        ),
                    ),
                )
            )
        with pytest.raises(ValueError):
            qpane.composeScene(
                QPaneSceneRequest(
                    composition_id=None,
                    title=None,
                    bounds=QRectF(0.0, 0.0, 100.0, 100.0),
                    layers=(valid_layer, valid_layer),
                )
            )
        with pytest.raises(ValueError):
            qpane.composeScene(
                QPaneSceneRequest(
                    composition_id=None,
                    title=None,
                    bounds=QRectF(0.0, 0.0, 0.0, 100.0),
                    layers=(valid_layer,),
                )
            )
        with pytest.raises(ValueError):
            qpane.composeScene(
                QPaneSceneRequest(
                    composition_id=None,
                    title=None,
                    bounds=QRectF(0.0, 0.0, 100.0, 100.0),
                    layers=(
                        QPaneCatalogImageLayerRequest(
                            layer_id=uuid.uuid4(),
                            image_id=second_id,
                            placement=QRectF(0.0, 0.0, 100.0, 100.0),
                            opacity=1.5,
                        ),
                    ),
                )
            )
        assert qpane.currentScene().layers[0].image_id == first_id
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compose_scene_stores_reopens_and_replaces_composition(qapp) -> None:
    """Layered scenes should behave like stored composition records."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        request = _scene_request(first_id, second_id)

        stored_id = qpane.composeScene(request, activate=False)

        assert stored_id in qpane.compositionIDs()
        assert qpane.currentScene().layers[0].image_id == first_id
        entry = qpane.getCompositionSnapshot().compositions[stored_id]
        assert entry.kind == "layered-scene"
        assert entry.current_image_id is None
        assert entry.source_image_ids == (first_id, second_id)
        assert entry.scene_layer_count == 2
        assert entry.scene_bounds == request.bounds

        qpane.openComposition(stored_id)
        assert qpane.currentCompositionID() == stored_id
        assert qpane.currentScene().composition_id == stored_id

        replacement = QPaneSceneRequest(
            composition_id=stored_id,
            title="Replacement",
            bounds=QRectF(0.0, 0.0, 100.0, 100.0),
            layers=(
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=second_id,
                    placement=QRectF(0.0, 0.0, 100.0, 100.0),
                ),
            ),
        )
        assert qpane.composeScene(replacement) == stored_id
        assert qpane.currentScene().title == "Replacement"
        assert qpane.currentScene().layers[0].image_id == second_id
    finally:
        _cleanup_qpane(qpane, qapp)


def test_active_scene_replacement_without_activation_refreshes_scene(qapp) -> None:
    """Replacing the active scene in place should refresh content without reselecting."""
    qpane = QPane(features=())
    qpane.resize(200, 100)
    try:
        first_id, second_id = _load_images(qpane)
        composition_id = qpane.composeScene(_scene_request(first_id, second_id))
        replacement = QPaneSceneRequest(
            composition_id=composition_id,
            title="Active replacement",
            bounds=QRectF(0.0, 0.0, 50.0, 100.0),
            layers=(
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=second_id,
                    placement=QRectF(0.0, 0.0, 50.0, 100.0),
                    metadata={"slot": "replacement"},
                ),
            ),
        )
        composition_events = []
        scene_events: list[QPaneScene | None] = []
        selection_events = []
        qpane.compositionChanged.connect(composition_events.append)
        qpane.sceneChanged.connect(scene_events.append)
        qpane.compositionSelectionChanged.connect(selection_events.append)

        assert qpane.composeScene(replacement, activate=False) == composition_id

        scene = qpane.currentScene()
        assert len(composition_events) == 1
        assert scene_events == [scene]
        assert selection_events == []
        assert scene is not None
        assert scene.composition_id == composition_id
        assert scene.title == "Active replacement"
        assert scene.bounds == replacement.bounds
        assert [layer.image_id for layer in scene.layers] == [second_id]
        assert scene.layers[0].metadata["slot"] == "replacement"

        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        raster_items = [
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        ]
        assert len(raster_items) == 1
        assert raster_items[0].asset_key.source_id == second_id
        assert raster_items[0].placement.width == 50.0
    finally:
        _cleanup_qpane(qpane, qapp)


def test_inactive_scene_replacement_without_activation_only_updates_browser(
    qapp,
) -> None:
    """Replacing an inactive scene should not disturb the active render scene."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        stored_id = qpane.composeScene(
            _scene_request(first_id, second_id), activate=False
        )
        active_scene = qpane.currentScene()
        replacement = QPaneSceneRequest(
            composition_id=stored_id,
            title="Inactive replacement",
            bounds=QRectF(0.0, 0.0, 50.0, 50.0),
            layers=(
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=second_id,
                    placement=QRectF(0.0, 0.0, 50.0, 50.0),
                ),
            ),
        )
        composition_events = []
        scene_events = []
        selection_events = []
        qpane.compositionChanged.connect(composition_events.append)
        qpane.sceneChanged.connect(scene_events.append)
        qpane.compositionSelectionChanged.connect(selection_events.append)

        qpane.composeScene(replacement, activate=False)

        assert len(composition_events) == 1
        assert scene_events == []
        assert selection_events == []
        assert qpane.currentScene() == active_scene
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compose_scene_from_template_expands_bindings(qapp) -> None:
    """Scene templates should expand into stored layered compositions."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        template = QPaneSceneTemplate(
            template_id=uuid.uuid4(),
            title="Template title",
            bounds=QRectF(0.0, 0.0, 200.0, 100.0),
            layers=(
                QPaneTemplateLayer(
                    layer_id=uuid.uuid4(),
                    source_slot="left",
                    placement=QRectF(0.0, 0.0, 100.0, 100.0),
                    metadata={"slot": "template"},
                ),
                QPaneTemplateLayer(
                    layer_id=uuid.uuid4(),
                    source_slot="right",
                    placement=QRectF(100.0, 0.0, 100.0, 100.0),
                ),
            ),
        )
        composition_id = qpane.composeSceneFromTemplate(
            template,
            QPaneSceneTemplateBindings(
                composition_id=None,
                title="Bound title",
                catalog_images={
                    "left": first_id,
                    "right": second_id,
                    "ignored": first_id,
                },
                metadata={"left": {"slot": "binding"}},
            ),
        )

        scene = qpane.currentScene()
        assert scene.composition_id == composition_id
        assert scene.title == "Bound title"
        assert [layer.image_id for layer in scene.layers] == [first_id, second_id]
        assert scene.layers[0].metadata["slot"] == "binding"

        with pytest.raises(ValueError):
            qpane.composeSceneFromTemplate(
                template,
                QPaneSceneTemplateBindings(
                    composition_id=None,
                    catalog_images={"left": first_id},
                ),
            )
    finally:
        _cleanup_qpane(qpane, qapp)


def test_scene_hit_test_returns_public_layer_metadata_without_selection(qapp) -> None:
    """Public scene hit testing should return opaque host metadata without navigation."""
    qpane = QPane(features=())
    qpane.resize(200, 100)
    try:
        first_id, second_id = _load_images(qpane)
        qpane.composeScene(_scene_request(first_id, second_id))
        before = qpane.currentImageID()

        hit = qpane.sceneHitTest(QPoint(150, 50))

        assert hit is not None
        assert hit.image_id == second_id
        assert hit.metadata["slot"] == 1
        assert hit.role == "thumbnail"
        assert qpane.currentImageID() == before
    finally:
        _cleanup_qpane(qpane, qapp)


def test_scene_hit_test_respects_clips(qapp) -> None:
    """Scene clips should constrain public hit testing."""
    qpane = QPane(features=())
    qpane.resize(100, 100)
    try:
        first_id, second_id = _load_images(qpane)
        request = QPaneSceneRequest(
            composition_id=None,
            title=None,
            bounds=QRectF(0.0, 0.0, 100.0, 100.0),
            layers=(
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=first_id,
                    placement=QRectF(0.0, 0.0, 100.0, 100.0),
                ),
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=second_id,
                    placement=QRectF(0.0, 0.0, 100.0, 100.0),
                    clip=QPaneSceneClip(
                        coordinate_space="scene",
                        rect=QRectF(50.0, 0.0, 50.0, 100.0),
                    ),
                ),
            ),
        )
        qpane.composeScene(request)

        left_hit = qpane.sceneHitTest(QPoint(25, 50))
        right_hit = qpane.sceneHitTest(QPoint(75, 50))

        assert left_hit is not None
        assert right_hit is not None
        assert left_hit.image_id == first_id
        assert right_hit.image_id == second_id
    finally:
        _cleanup_qpane(qpane, qapp)


def test_large_public_scene_layers_use_tiles(qapp) -> None:
    """Large public scene layers should enter tiled rendering."""
    qpane = QPane(features=())
    qpane.resize(100, 100)
    try:
        first_id, second_id = _load_images(qpane, large=True)
        qpane.composeScene(
            QPaneSceneRequest(
                composition_id=None,
                title=None,
                bounds=QRectF(0.0, 0.0, 2048.0, 1024.0),
                layers=(
                    QPaneCatalogImageLayerRequest(
                        layer_id=uuid.uuid4(),
                        image_id=first_id,
                        placement=QRectF(0.0, 0.0, 1024.0, 1024.0),
                    ),
                    QPaneCatalogImageLayerRequest(
                        layer_id=uuid.uuid4(),
                        image_id=second_id,
                        placement=QRectF(1024.0, 0.0, 1024.0, 1024.0),
                    ),
                ),
            )
        )
        qpane.setZoom1To1()

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        raster_items = [
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        ]
        assert raster_items
        assert all(item.strategy == RenderStrategy.TILE for item in raster_items)
        assert all(item.visible_tile_range is not None for item in raster_items)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_layered_scene_returns_to_default_compositions_after_catalog_changes(
    qapp,
) -> None:
    """Catalog removals and navigation should reopen normal composition snapshots."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        cleared: list[object | None] = []
        qpane.sceneChanged.connect(cleared.append)
        layered_id = qpane.composeScene(_scene_request(first_id, second_id))

        qpane.removeImageByID(second_id)

        assert qpane.currentScene() is not None
        assert qpane.currentScene().composition_id != layered_id
        assert qpane.currentScene().layers[0].image_id == first_id
        assert cleared[-1] == qpane.currentScene()

        first_id, second_id = _load_images(qpane)
        layered_id = qpane.composeScene(_scene_request(first_id, second_id))
        qpane.setCurrentImageID(second_id)

        assert qpane.currentScene().composition_id != layered_id
        assert qpane.currentScene().layers[0].image_id == second_id
        assert qpane.currentImageID() == second_id
        assert cleared[-1] == qpane.currentScene()
    finally:
        _cleanup_qpane(qpane, qapp)


def test_scene_survives_catalog_replacement_with_same_ids(qapp) -> None:
    """Replacing catalog pixels with the same IDs should keep the scene active."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        qpane.composeScene(_scene_request(first_id, second_id))

        qpane.addImage(second_id, _solid_image(color=Qt.yellow), None)

        assert qpane.currentScene() is not None
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        raster_items = [
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        ]
        assert raster_items[1].pyramid_asset_key.source_id == second_id
    finally:
        _cleanup_qpane(qpane, qapp)


def test_public_scene_suppresses_comparison_contributions(qapp) -> None:
    """Public scenes should render their declared layers without comparison layers."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        qpane.setComparisonImageID(second_id)
        qpane.composeScene(
            QPaneSceneRequest(
                composition_id=None,
                title=None,
                bounds=QRectF(0.0, 0.0, 100.0, 100.0),
                layers=(
                    QPaneCatalogImageLayerRequest(
                        layer_id=uuid.uuid4(),
                        image_id=first_id,
                        placement=QRectF(0.0, 0.0, 100.0, 100.0),
                    ),
                ),
            )
        )

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        raster_items = [
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        ]
        assert len(raster_items) == 1
        assert raster_items[0].descriptor.source.image_id == first_id
    finally:
        _cleanup_qpane(qpane, qapp)


def test_layered_scene_rejects_image_scoped_comparison_mutations(qapp) -> None:
    """Layered scenes should not mutate comparison state through stale image scope."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        qpane.setComparisonSplit(0.25, ComparisonOrientation.HORIZONTAL)
        qpane.composeScene(_scene_request(first_id, second_id))

        with pytest.raises(RuntimeError):
            qpane.setComparisonImageID(second_id)
        with pytest.raises(RuntimeError):
            qpane.clearComparisonImage()
        with pytest.raises(RuntimeError):
            qpane.setComparisonSplit(0.75, ComparisonOrientation.VERTICAL)

        state = qpane.comparisonState()
        assert not state.enabled
        assert state.source_id is None
        assert state.split_position == 0.25
        assert state.orientation == ComparisonOrientation.HORIZONTAL
    finally:
        _cleanup_qpane(qpane, qapp)


def test_layered_scene_guards_active_image_mask_facade(qapp, monkeypatch) -> None:
    """Layered scenes should expose only explicit-image mask operations."""
    qpane = QPane(features=())
    try:
        first_id, second_id = _load_images(qpane)
        qpane.composeScene(_scene_request(first_id, second_id))
        controller = qpane._masks_controller
        mask_id = uuid.uuid4()
        delegated_calls = []

        monkeypatch.setattr(controller, "getActiveMaskID", lambda: mask_id)
        monkeypatch.setattr(
            controller,
            "maskIDsForImage",
            lambda image_id=None: delegated_calls.append(("maskIDs", image_id))
            or [mask_id],
        )
        monkeypatch.setattr(
            controller,
            "listMasksForImage",
            lambda image_id=None: delegated_calls.append(("listMasks", image_id))
            or ("mask-info",),
        )
        monkeypatch.setattr(
            controller,
            "get_active_mask_image",
            lambda: _solid_image(1, 1),
        )
        monkeypatch.setattr(
            controller,
            "get_mask_undo_state",
            lambda queried_id: delegated_calls.append(("undoState", queried_id)),
        )

        assert qpane.activeMaskID() is None
        assert qpane.maskIDsForImage() == []
        assert qpane.listMasksForImage() == ()
        assert qpane.getActiveMaskImage() is None
        assert qpane.maskIDsForImage(first_id) == [mask_id]
        assert qpane.listMasksForImage(first_id) == ("mask-info",)
        assert qpane.getMaskUndoState(mask_id) is None
        assert delegated_calls == [
            ("maskIDs", first_id),
            ("listMasks", first_id),
            ("undoState", mask_id),
        ]

        def fail_if_delegated(*_args, **_kwargs):
            raise AssertionError("active-image mask operation delegated")

        monkeypatch.setattr(controller, "create_blank_mask", fail_if_delegated)
        monkeypatch.setattr(controller, "load_mask_from_file", fail_if_delegated)
        monkeypatch.setattr(controller, "set_active_mask_id", fail_if_delegated)
        monkeypatch.setattr(controller, "cycle_masks_forward", fail_if_delegated)
        monkeypatch.setattr(controller, "cycle_masks_backward", fail_if_delegated)
        monkeypatch.setattr(controller, "undo_mask_edit", fail_if_delegated)
        monkeypatch.setattr(controller, "redo_mask_edit", fail_if_delegated)

        with pytest.raises(RuntimeError):
            qpane.createBlankMask(QSize(1, 1))
        with pytest.raises(RuntimeError):
            qpane.loadMaskFromFile("mask.png")
        with pytest.raises(RuntimeError):
            qpane.setActiveMaskID(mask_id)
        with pytest.raises(RuntimeError):
            qpane.cycleMasksForward()
        with pytest.raises(RuntimeError):
            qpane.cycleMasksBackward()
        with pytest.raises(RuntimeError):
            qpane.undoMaskEdit()
        with pytest.raises(RuntimeError):
            qpane.redoMaskEdit()

        delegated_calls.clear()
        monkeypatch.setattr(
            controller,
            "remove_mask_from_image",
            lambda image_id, removed_id: delegated_calls.append(
                ("remove", image_id, removed_id)
            )
            or False,
        )
        monkeypatch.setattr(
            controller,
            "set_mask_properties",
            lambda edited_id, color=None, opacity=None: delegated_calls.append(
                ("properties", edited_id, color, opacity)
            )
            or False,
        )
        monkeypatch.setattr(
            controller,
            "prefetch_mask_overlays",
            lambda image_id, *, reason: delegated_calls.append(
                ("prefetch", image_id, reason)
            )
            or True,
        )

        assert not qpane.removeMaskFromImage(first_id, mask_id)
        assert not qpane.setMaskProperties(mask_id, opacity=0.5)
        assert qpane.prefetchMaskOverlays(first_id, reason="test")
        assert not qpane.prefetchMaskOverlays(None, reason="test")
        assert delegated_calls == [
            ("remove", first_id, mask_id),
            ("properties", mask_id, None, 0.5),
            ("prefetch", first_id, "test"),
        ]
    finally:
        _cleanup_qpane(qpane, qapp)


def test_scene_overlays_receive_layer_geometry(qapp) -> None:
    """Scene overlays should receive public layer snapshots with transforms."""
    qpane = QPane(features=())
    qpane.resize(200, 100)
    try:
        first_id, second_id = _load_images(qpane)
        request = _scene_request(first_id, second_id)
        qpane.composeScene(request)
        states = []

        def draw_scene_overlay(_painter, state) -> None:
            states.append(state)

        qpane.registerSceneOverlay("labels", draw_scene_overlay)
        qpane.paintEvent(None)

        assert states
        state = states[-1]
        assert state.scene_id == qpane.currentScene().scene_id
        assert [layer.layer_id for layer in state.layers] == [
            layer.layer_id for layer in request.layers
        ]
        assert [layer.image_id for layer in state.layers] == [first_id, second_id]
        assert state.layers[0].role == "thumbnail"
        assert state.layers[0].metadata == {"slot": 0}
        assert state.layers[0].placement == request.layers[0].placement
        assert state.layers[0].source_size == QSize(100, 100)
        assert state.layers[0].panel_bounds.width() > 0
        qpane.unregisterSceneOverlay("labels")
        assert qpane.sceneOverlays() == {}
    finally:
        _cleanup_qpane(qpane, qapp)
