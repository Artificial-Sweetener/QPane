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

"""Local micro-benchmark for scene render-planning latency."""

from __future__ import annotations

import os
import statistics
import sys
import time
import uuid
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QImage, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qpane import (  # noqa: E402
    QPane,
    QPaneCatalogImageLayerRequest,
    QPaneSceneRequest,
)

FrameSetup = Callable[[QPane, int], None]


def main() -> int:
    """Run render-planning benchmark cases and print timing summaries."""
    app = QApplication.instance() or QApplication(sys.argv)
    frames = 500
    cases = (
        ("default-4k", _default_scene_qpane, _pan_frame),
        ("two-layer-4k", _two_layer_scene_qpane, _pan_frame),
    )
    for name, factory, frame_setup in cases:
        qpane = factory()
        try:
            _warm_up(qpane)
            timings = _measure_case(qpane, frames=frames, frame_setup=frame_setup)
            print(_format_result(name, timings))
        finally:
            qpane.deleteLater()
            app.processEvents()
    return 0


def _solid_image(width: int, height: int, color: Qt.GlobalColor) -> QImage:
    """Return a solid ARGB image for benchmark scenes."""
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(color)
    return image


def _default_scene_qpane() -> QPane:
    """Return a QPane containing one 4096x4096 catalog image."""
    qpane = QPane(features=())
    qpane.resize(1024, 768)
    image_id = uuid.uuid4()
    qpane.setImagesByID(
        QPane.imageMapFromLists(
            [_solid_image(4096, 4096, Qt.red)],
            [None],
            [image_id],
        ),
        image_id,
    )
    qpane.view().viewport.zoom = 1.0
    return qpane


def _two_layer_scene_qpane() -> QPane:
    """Return a QPane containing a two-layer explicit 4096px scene."""
    qpane = QPane(features=())
    qpane.resize(1024, 768)
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    qpane.setImagesByID(
        QPane.imageMapFromLists(
            [
                _solid_image(4096, 4096, Qt.red),
                _solid_image(4096, 4096, Qt.blue),
            ],
            [None, None],
            [first_id, second_id],
        ),
        first_id,
    )
    qpane.composeScene(
        QPaneSceneRequest(
            composition_id=None,
            title="Two layer benchmark",
            bounds=QRectF(0.0, 0.0, 4096.0, 4096.0),
            layers=(
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=first_id,
                    placement=QRectF(0.0, 0.0, 4096.0, 4096.0),
                    role="base",
                ),
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=second_id,
                    placement=QRectF(1024.0, 1024.0, 2048.0, 2048.0),
                    role="overlay",
                ),
            ),
        )
    )
    qpane.view().viewport.zoom = 1.0
    return qpane


def _warm_up(qpane: QPane) -> None:
    """Build initial caches before measuring repeated frame planning."""
    for index in range(10):
        _pan_frame(qpane, index)
        qpane.view().calculateRenderPlan(is_blank=False)


def _pan_frame(qpane: QPane, frame: int) -> None:
    """Apply a deterministic pan value for one benchmark frame."""
    qpane.view().viewport.pan = QPointF(float(frame % 128), float(-(frame % 96)))


def _measure_case(
    qpane: QPane,
    *,
    frames: int,
    frame_setup: FrameSetup,
) -> list[float]:
    """Return per-frame render-plan timings in milliseconds."""
    timings: list[float] = []
    for frame in range(frames):
        frame_setup(qpane, frame)
        started = time.perf_counter()
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if plan is None:
            raise RuntimeError("render planning returned no plan")
        timings.append(elapsed_ms)
    return timings


def _format_result(name: str, timings: list[float]) -> str:
    """Return a one-line benchmark summary."""
    ordered = sorted(timings)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return (
        f"case={name} frames={len(timings)} "
        f"avg_ms={statistics.fmean(timings):.4f} "
        f"median_ms={statistics.median(timings):.4f} "
        f"p95_ms={ordered[p95_index]:.4f} "
        f"max_ms={max(timings):.4f}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
