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

"""Focused tests for pyramid cache admission and eviction helpers."""

from __future__ import annotations

from collections import OrderedDict
import uuid

import pytest
from PySide6.QtGui import QImage

from qpane import Config
from qpane.rendering.pyramid import ImagePyramid, PyramidManager
from qpane.scene.identity import default_catalog_asset_key
from tests.helpers.executor_stubs import StubExecutor


@pytest.mark.usefixtures("qapp")
def test_pyramid_allow_cache_insert_guard(caplog):
    """Admission guard should block oversized entries once per key."""
    manager = PyramidManager(config=Config(), executor=StubExecutor())
    manager.cache_limit_bytes = 100
    manager.set_admission_guard(lambda _size: False)
    key = default_catalog_asset_key(uuid.uuid4(), revision=0, source_path=None)
    caplog.set_level("WARNING")
    assert manager._allow_cache_insert(50, key) is False
    assert manager._allow_cache_insert(50, key) is False
    warnings = [
        record
        for record in caplog.records
        if "requested item exceeds budget" in record.message
    ]
    assert len(warnings) == 1


@pytest.mark.usefixtures("qapp")
def test_pyramid_eviction_batch_drops_entries():
    """Eviction should remove cached pyramids and update byte counts."""
    manager = PyramidManager(config=Config(), executor=StubExecutor())
    manager.cache_limit_bytes = 0
    image_id = uuid.uuid4()
    key = default_catalog_asset_key(image_id, revision=0, source_path=None)
    pyramid = ImagePyramid(
        asset_key=key,
        full_resolution_image=QImage(4, 4, QImage.Format_ARGB32),
    )
    pyramid.size_bytes = 8
    manager._cache = OrderedDict({key: pyramid})
    manager._pyramids[key] = pyramid
    manager._cache_size_bytes = pyramid.size_bytes
    manager._run_eviction_batch()
    assert manager._cache_size_bytes == 0
    assert key not in manager._cache
    assert key not in manager._pyramids
    assert manager._evictions_total == 1
