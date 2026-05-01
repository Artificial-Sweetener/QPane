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

"""Provider and resolver contracts for assembling internal scene descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .model import SceneDescriptor


class SceneProvider(Protocol):
    """Contributor that can supply scene data for the current context."""

    def scene_contribution(self) -> "SceneContribution | None":
        """Return a scene contribution, or None when inactive."""
        ...


@dataclass(frozen=True, slots=True)
class SceneContribution:
    """Single provider contribution consumed by the scene resolver."""

    scene: SceneDescriptor
    order: int = 0


@dataclass(frozen=True, slots=True)
class SceneResolver:
    """Resolve provider contributions into one deterministic scene descriptor."""

    providers: Sequence[SceneProvider]

    def resolve(self) -> SceneDescriptor | None:
        """Return a deterministic scene assembled from ordered contributions."""
        contributions = [
            contribution
            for provider in self.providers
            if (contribution := provider.scene_contribution()) is not None
        ]
        if not contributions:
            return None
        ordered = sorted(
            contributions,
            key=lambda contribution: (
                contribution.order,
                str(contribution.scene.scene_id),
            ),
        )
        base_scene = ordered[0].scene
        layers = []
        for contribution in ordered:
            scene = contribution.scene
            if scene.scene_id != base_scene.scene_id:
                continue
            layers.extend(scene.layers)
        return SceneDescriptor(
            scene_id=base_scene.scene_id,
            kind=base_scene.kind,
            bounds=base_scene.bounds,
            layers=tuple(layers),
        )
