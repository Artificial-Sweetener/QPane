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

"""Exercises string semantics for enums previously backed by StrEnum."""

import json
from qpane.rendering import PyramidStatus, ViewportZoomMode
from qpane.scene.render_plan import RenderStrategy


def test_render_strategy_behaves_like_string() -> None:
    """Ensure RenderStrategy values preserve plain string semantics."""
    direct = RenderStrategy.DIRECT
    assert isinstance(direct, str)
    assert direct == "direct"
    assert RenderStrategy("direct") is direct
    assert json.loads(json.dumps({"strategy": direct})) == {"strategy": direct}
    assert {direct} == {"direct"}


def test_viewport_zoom_mode_behaves_like_string() -> None:
    """Ensure ViewportZoomMode supports string comparisons and JSON."""
    fit = ViewportZoomMode.FIT
    assert isinstance(fit, str)
    assert fit == "fit"
    assert ViewportZoomMode("fit") is fit
    assert json.loads(json.dumps({"zoom": fit})) == {"zoom": fit}
    assert {fit} == {"fit"}


def test_pyramid_status_behaves_like_string() -> None:
    """Ensure PyramidStatus exposes string semantics for diagnostics serialization."""
    pending = PyramidStatus.PENDING
    assert isinstance(pending, str)
    assert pending == "pending"
    assert PyramidStatus("pending") is pending
    assert json.loads(json.dumps({"status": pending})) == {"status": pending}
    assert {pending} == {"pending"}
