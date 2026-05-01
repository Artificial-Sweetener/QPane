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

"""Targeted tests for tile cache admission and eviction helpers."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import uuid

import pytest

from qpane import Config
from qpane.rendering.tiles import TileManager
from tests.helpers.render_plan import make_tile_key
from tests.helpers.executor_stubs import StubExecutor


@pytest.mark.usefixtures("qapp")
def test_allow_cache_insert_honors_guard(caplog):
    """Admission guards should veto inserts and log only once per key."""
    manager = TileManager(config=Config(), executor=StubExecutor())
    manager.cache_limit_bytes = 100
    manager.set_admission_guard(lambda _size: False)
    image_id = uuid.uuid4()
    key = make_tile_key(image_id, Path("a.png"), 1.0, 0, 0)
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
def test_schedule_cache_eviction_requires_executor():
    """Scheduling eviction should fail when executor wiring is missing."""
    manager = TileManager(config=Config(), executor=StubExecutor())
    manager._executor = None
    manager.cache_limit_bytes = 10
    manager._cache_size_bytes = 20
    manager._tile_cache = OrderedDict({object(): object()})
    with pytest.raises(RuntimeError, match="TileManager executor is missing"):
        manager._schedule_cache_eviction()


@pytest.mark.usefixtures("qapp")
def test_evict_cache_batch_drops_entries():
    """Eviction should remove cached tiles and update bytes."""
    manager = TileManager(config=Config(), executor=StubExecutor())
    image_id = uuid.uuid4()
    key = make_tile_key(image_id, Path("a.png"), 1.0, 0, 0)
    manager.cache_limit_bytes = 0
    manager._tile_cache = OrderedDict({key: type("Tile", (), {"size_bytes": 5})()})
    manager._cache_size_bytes = 5
    manager._evict_cache_batch()
    assert manager._cache_size_bytes == 0
    assert not manager._tile_cache
    assert manager._evictions_total == 1
