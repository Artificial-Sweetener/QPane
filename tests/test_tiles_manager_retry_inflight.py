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

"""Tests for tile manager retry handling for inflight requests."""

from __future__ import annotations
from pathlib import Path
import uuid
import pytest
from PySide6.QtGui import QImage
from qpane import Config
from qpane.rendering import TileManager
from tests.helpers.render_plan import make_tile_key
from tests.helpers.executor_stubs import StubExecutor


@pytest.mark.usefixtures("qapp")
def test_tiles_inflight_tracked_via_retry_controller(qapp) -> None:
    """Ensure tile submissions via RetryController are recorded as inflight to avoid duplicates."""
    config = Config(cache={"tiles": {"mb": 8}})
    executor = StubExecutor()
    manager = TileManager(config=config, executor=executor)
    image = QImage(128, 128, QImage.Format_ARGB32)
    image.fill(0)
    image_id = uuid.uuid4()
    ident = make_tile_key(image_id, Path("rt.png"), 1.0, 0, 0)
    # First request should submit exactly one task and record inflight
    assert manager.get_tile(ident, image) is None
    pending = list(executor.pending_tasks())
    assert len(pending) == 1 and pending[0].handle.category == "tiles"
    assert ident in manager._worker_state
    # Second request while inflight should not enqueue another task
    assert manager.get_tile(ident, image) is None
    assert len(list(executor.pending_tasks())) == 1
