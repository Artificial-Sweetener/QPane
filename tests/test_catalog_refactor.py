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

"""Tests covering catalog refactor behaviours and placeholders."""

import uuid
from pathlib import Path
import pytest
from PySide6.QtGui import QImage
from qpane.catalog import ImageCatalog
from qpane.scene.identity import SceneLayerAssetKey
from qpane.types import CatalogEntry
from qpane import Config
from tests.helpers.executor_stubs import StubExecutor


class StubPyramidManager:
    def __init__(self):
        self.generated: list[tuple[SceneLayerAssetKey, QImage]] = []
        self.removed: list[SceneLayerAssetKey] = []
        self.cleared = False
        self.apply_calls: list[Config] = []

    def generate_pyramid_for_asset(
        self, asset_key: SceneLayerAssetKey, image: QImage
    ) -> None:
        self.generated.append((asset_key, image))

    def apply_config(self, config: Config) -> None:
        self.apply_calls.append(config)

    def remove_pyramid(self, asset_key: SceneLayerAssetKey) -> None:
        self.removed.append(asset_key)

    def clear(self) -> None:
        self.cleared = True


def _make_image(fmt: QImage.Format, fill: int = 0) -> QImage:
    image = QImage(4, 4, fmt)
    image.fill(fill)
    return image


def test_set_images_normalizes_and_uses_consistent_format(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    image_id = uuid.uuid4()
    image_map = {
        image_id: CatalogEntry(
            image=_make_image(QImage.Format_RGB32), path=Path("foo.png")
        ),
    }
    mutation = catalog.setImagesByID(image_map, image_id)
    stored_image = catalog.getImage(image_id)
    assert stored_image is not None
    assert stored_image.format() == QImage.Format_ARGB32_Premultiplied
    assert mutation.removed_ids == ()
    assert mutation.content_changed_ids == (image_id,)
    generated_key, generated_image = stub.generated[-1]
    assert generated_key.source_id == image_id
    assert generated_key.source_path == Path("foo.png")
    assert generated_image.format() == QImage.Format_ARGB32_Premultiplied


def test_set_images_reports_only_removed_and_content_changed_ids(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    unchanged_id = uuid.uuid4()
    changed_id = uuid.uuid4()
    removed_id = uuid.uuid4()
    initial_map = {
        unchanged_id: CatalogEntry(
            image=_make_image(QImage.Format_ARGB32_Premultiplied, 0),
            path=Path("unchanged.png"),
        ),
        changed_id: CatalogEntry(
            image=_make_image(QImage.Format_ARGB32_Premultiplied, 32),
            path=Path("changed.png"),
        ),
        removed_id: CatalogEntry(
            image=_make_image(QImage.Format_ARGB32_Premultiplied, 64),
            path=Path("removed.png"),
        ),
    }
    catalog.setImagesByID(initial_map, unchanged_id)
    stub.generated.clear()
    stub.removed.clear()
    replacement_map = {
        unchanged_id: CatalogEntry(
            image=_make_image(QImage.Format_ARGB32_Premultiplied, 0),
            path=Path("unchanged-renamed.png"),
        ),
        changed_id: CatalogEntry(
            image=_make_image(QImage.Format_ARGB32_Premultiplied, 128),
            path=Path("changed.png"),
        ),
    }
    mutation = catalog.setImagesByID(
        replacement_map,
        changed_id,
    )
    assert mutation.removed_ids == (removed_id,)
    assert mutation.content_changed_ids == (changed_id,)
    assert mutation.path_changed_ids == (unchanged_id,)
    assert [key.source_id for key in stub.removed] == [
        unchanged_id,
        changed_id,
        removed_id,
    ]
    assert [entry[0].source_id for entry in stub.generated] == [
        unchanged_id,
        changed_id,
    ]
    assert catalog.getImageIds() == [unchanged_id, changed_id]
    assert catalog.getCurrentId() == changed_id
    assert catalog.getPath(unchanged_id) == Path("unchanged-renamed.png")


def test_add_image_normalizes_before_storage_and_pyramid(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    image_id = uuid.uuid4()
    src_image = _make_image(QImage.Format_RGB32)
    catalog.addImage(image_id, src_image, Path("bar.png"))
    stored_image = catalog.getImage(image_id)
    assert stored_image is not None
    assert stored_image.format() == QImage.Format_ARGB32_Premultiplied
    generated_key, generated_image = stub.generated[-1]
    assert generated_key.source_id == image_id
    assert generated_key.source_path == Path("bar.png")
    assert generated_image.format() == QImage.Format_ARGB32_Premultiplied


def test_add_image_replaces_existing_id_without_reordering_unchanged_content(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first_image = _make_image(QImage.Format_ARGB32_Premultiplied, 48)
    second_image = _make_image(QImage.Format_ARGB32_Premultiplied, 96)
    catalog.addImage(first_id, first_image, Path("first.png"))
    catalog.addImage(second_id, second_image, Path("second.png"))
    stub.removed.clear()
    catalog.addImage(first_id, first_image.copy(), Path("first-renamed.png"))
    assert catalog.getImageIds() == [first_id, second_id]
    assert catalog.getPath(first_id) == Path("first-renamed.png")
    assert [key.source_id for key in stub.removed] == [first_id]
    generated_key, _ = stub.generated[-1]
    assert generated_key.source_id == first_id
    assert generated_key.source_path == Path("first-renamed.png")


def test_add_image_removes_pyramid_when_existing_content_changes(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    image_id = uuid.uuid4()
    catalog.addImage(
        image_id,
        _make_image(QImage.Format_ARGB32_Premultiplied, 12),
        Path("base.png"),
    )
    stub.removed.clear()
    catalog.addImage(
        image_id,
        _make_image(QImage.Format_ARGB32_Premultiplied, 220),
        Path("base.png"),
    )
    assert [key.source_id for key in stub.removed] == [image_id]


def test_add_image_raises_on_null(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    with pytest.raises(ValueError):
        catalog.addImage(uuid.uuid4(), QImage(), None)
    assert catalog.getImageIds() == []


def test_update_current_entry_normalizes_and_refreshes_pyramid(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    image_id = uuid.uuid4()
    initial_image = _make_image(QImage.Format_ARGB32_Premultiplied)
    catalog.setImagesByID(
        {image_id: CatalogEntry(image=initial_image, path=Path("old.png"))}, image_id
    )
    replacement_image = _make_image(QImage.Format_RGB32)
    mutation = catalog.updateCurrentEntry(
        image=replacement_image,
        path=Path("new.png"),
    )
    stored_image = catalog.getImage(image_id)
    assert stored_image is not None
    assert stored_image.format() == QImage.Format_ARGB32_Premultiplied
    assert mutation.path_changed_ids == (image_id,)
    # ensure the old pyramid was removed and the new one uses normalized data
    assert stub.removed[-1].source_id == image_id
    generated_key, generated_image = stub.generated[-1]
    assert generated_key.source_id == image_id
    assert generated_key.source_path == Path("new.png")
    assert generated_image.format() == QImage.Format_ARGB32_Premultiplied


def test_update_current_entry_path_change_invalidates_current_pyramid(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    image_id = uuid.uuid4()
    initial_image = _make_image(QImage.Format_ARGB32_Premultiplied, 24)
    catalog.setImagesByID(
        {image_id: CatalogEntry(image=initial_image, path=Path("old.png"))},
        image_id,
    )
    stub.generated.clear()
    stub.removed.clear()
    mutation = catalog.updateCurrentEntry(path=Path("new.png"))
    assert mutation.path_changed_ids == (image_id,)
    assert mutation.content_changed_ids == ()
    assert catalog.getCurrentImage() is initial_image
    assert catalog.getCurrentPath() == Path("new.png")
    assert [key.source_id for key in stub.removed] == [image_id]
    assert [
        (key.source_id, image, key.source_path) for key, image in stub.generated
    ] == [(image_id, initial_image, Path("new.png"))]


def test_update_current_entry_without_selection_is_noop(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    mutation = catalog.updateCurrentEntry(
        image=_make_image(QImage.Format_RGB32, 44),
        path=Path("ignored.png"),
    )
    assert mutation == type(mutation)()
    assert catalog.getCurrentImage() is None
    assert catalog.getCurrentPath() is None
    assert stub.generated == []
    assert stub.removed == []


def test_catalog_revisions_increment_only_for_content_changes(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    image_id = uuid.uuid4()
    initial = _make_image(QImage.Format_ARGB32_Premultiplied, 10)
    catalog.setImagesByID(
        {image_id: CatalogEntry(image=initial, path=Path("first.png"))},
        image_id,
    )
    assert catalog.getRevision(image_id) == 1
    unchanged = _make_image(QImage.Format_ARGB32_Premultiplied, 10)
    path_only = catalog.setImagesByID(
        {image_id: CatalogEntry(image=unchanged, path=Path("renamed.png"))},
        image_id,
    )
    assert catalog.getRevision(image_id) == 1
    assert path_only.content_changed_ids == ()
    assert path_only.path_changed_ids == (image_id,)
    changed = _make_image(QImage.Format_ARGB32_Premultiplied, 200)
    content_update = catalog.setImagesByID(
        {image_id: CatalogEntry(image=changed, path=Path("renamed.png"))},
        image_id,
    )
    assert catalog.getRevision(image_id) == 2
    assert content_update.content_changed_ids == (image_id,)
    assert content_update.path_changed_ids == ()


def test_update_current_entry_reports_content_and_path_changes_separately(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    image_id = uuid.uuid4()
    catalog.setImagesByID(
        {
            image_id: CatalogEntry(
                image=_make_image(QImage.Format_ARGB32_Premultiplied, 20),
                path=Path("first.png"),
            )
        },
        image_id,
    )
    mutation = catalog.updateCurrentEntry(
        image=_make_image(QImage.Format_ARGB32_Premultiplied, 40),
        path=Path("second.png"),
    )
    assert mutation.content_changed_ids == (image_id,)
    assert mutation.path_changed_ids == (image_id,)
    assert catalog.getRevision(image_id) == 2
    path_only = catalog.updateCurrentEntry(path=Path("third.png"))
    assert path_only.content_changed_ids == ()
    assert path_only.path_changed_ids == (image_id,)
    assert catalog.getRevision(image_id) == 2


def test_apply_config_regenerates_current_image(qapp):
    catalog = ImageCatalog(config=Config(), executor=StubExecutor())
    stub = StubPyramidManager()
    catalog.pyramid_manager = stub
    image_id = uuid.uuid4()
    image = _make_image(QImage.Format_ARGB32_Premultiplied)
    path = Path("regen.png")
    catalog.setImagesByID({image_id: CatalogEntry(image=image, path=path)}, image_id)
    stub.generated.clear()
    updated = Config(cache={"pyramids": {"mb": 32}})
    catalog.apply_config(updated)
    assert stub.apply_calls[-1] is updated
    assert stub.generated
    generated_key, generated_image = stub.generated[-1]
    assert generated_key.source_id == image_id
    assert generated_key.source_path == path
    assert generated_image.format() == QImage.Format_ARGB32_Premultiplied


def test_catalog_facade_rejects_null_image(qpane_core):
    catalog = qpane_core.catalog()
    with pytest.raises(ValueError):
        catalog.addImage(uuid.uuid4(), QImage(), None)


@pytest.mark.usefixtures("qapp")
def test_catalog_passes_executor_to_pyramid_manager() -> None:
    """ImageCatalog should supply the shared executor to PyramidManager."""
    executor = StubExecutor()
    catalog = ImageCatalog(config=Config(), executor=executor)
    assert getattr(catalog.pyramid_manager, "_executor") is executor
