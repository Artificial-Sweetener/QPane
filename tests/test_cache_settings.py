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

"""Tests for cache settings normalization and overrides."""

from __future__ import annotations
import json
import pytest
import logging
import qpane.core.config as cache_config_module
from qpane import Config, QPane
from qpane.core.config import CacheSettings as CacheConfig
from qpane.core.config import CacheWeights

MB = 1024 * 1024


class _FakePsutil:
    class _VM:
        total = 10 * MB
        available = 8 * MB

    @staticmethod
    def virtual_memory():
        return _FakePsutil._VM()


class _MissingPsutil:
    @staticmethod
    def virtual_memory():
        raise RuntimeError("psutil missing")


def test_cache_settings_default_overrides_are_none() -> None:
    settings = CacheConfig()
    for bucket in ("tiles", "pyramids", "masks", "predictors"):
        assert settings.override_mb(bucket) is None


def test_cache_settings_auto_budget_uses_headroom() -> None:
    settings = CacheConfig(mode="auto", headroom_percent=0.1, headroom_cap_mb=4096)
    budget_bytes = settings.resolve_active_budget_bytes(psutil_module=_FakePsutil)
    assert budget_bytes == 7 * MB


def test_cache_settings_hard_defaults_to_1024_when_missing_budget() -> None:
    settings = CacheConfig(mode="hard", budget_mb=256)
    assert settings.resolve_active_budget_bytes() == 256 * MB
    default_settings = CacheConfig(mode="hard")
    assert default_settings.resolve_active_budget_bytes() == 1024 * MB


def test_cache_settings_auto_fallbacks_to_default_when_psutil_missing() -> None:
    settings = CacheConfig(mode="auto")
    budget_bytes = settings.resolve_active_budget_bytes(psutil_module=_MissingPsutil)
    assert budget_bytes == 1024 * MB


def test_cache_settings_distribution_honors_weights_and_overrides() -> None:
    settings = CacheConfig(
        mode="hard",
        budget_mb=100,
        weights=CacheWeights(tiles=2, pyramids=1, masks=0, predictors=0),
    )
    budget_bytes = settings.resolve_active_budget_bytes()
    base = settings.resolve_consumer_budgets_bytes(
        budget_bytes, active_consumers=("tiles", "pyramids")
    )
    assert base["tiles"] > base["pyramids"]
    settings.set_override_mb("pyramids", 10)
    base["pyramids"] = settings.override_mb("pyramids") * MB
    assert base["pyramids"] == 10 * MB


def test_cache_settings_apply_mapping_rejects_legacy_keys() -> None:
    settings = CacheConfig()
    with pytest.raises(ValueError):
        settings.apply_mapping({"total_mb": 100})
    settings.apply_mapping({"mode": "auto", "budget_mb": 10})
    assert settings.budget_mb is None
    settings.apply_mapping({"mode": "hard", "headroom_percent": 0.2, "budget_mb": 10})
    assert settings.headroom_percent == 0.1
    assert settings.budget_mb == 10
    with pytest.raises(ValueError):
        settings.apply_mapping({"mask_minimum_mb": 2})
    with pytest.raises(ValueError):
        settings.apply_mapping({"ratios": {"tiles": 1}})


def test_config_as_dict_preserves_cache_fields() -> None:
    config = Config()
    data = config.as_dict()
    assert "cache" in data
    assert "prefetch" in data["cache"]
    assert data["cache"]["mode"] in {"auto", "hard"}


def test_config_as_dict_is_json_serializable() -> None:
    """Config snapshots should contain only JSON-serializable primitives."""
    json.dumps(Config().as_dict())


def test_cache_settings_resolved_budgets_apply_overrides() -> None:
    settings = CacheConfig(mode="hard", budget_mb=100)
    settings.set_override_mb("tiles", 12)
    budgets = settings.resolved_consumer_budgets_bytes()
    assert budgets["tiles"] == 12 * MB


def test_cache_settings_to_dict_validates_mode_union() -> None:
    settings = CacheConfig(mode="auto", budget_mb=1)
    snapshot = settings.to_dict()
    assert snapshot["budget_mb"] is None
    assert snapshot["headroom_percent"] is not None
    settings = CacheConfig(mode="hard")
    snapshot = settings.to_dict()
    assert snapshot["budget_mb"] == 1024
    assert snapshot["headroom_percent"] is None


def test_cache_settings_prefetch_defaults() -> None:
    settings = CacheConfig()
    assert settings.prefetch.pyramids == 2
    assert settings.prefetch.tiles == 2
    assert settings.prefetch.masks == -1
    assert settings.prefetch.predictors == 0
    assert settings.prefetch.tiles_per_neighbor == 4


def test_cache_settings_prefetch_apply_mapping() -> None:
    settings = CacheConfig()
    settings.apply_mapping(
        {"prefetch": {"tiles": 0, "pyramids": 3, "tiles_per_neighbor": 2}}
    )
    assert settings.prefetch.tiles == 0
    assert settings.prefetch.pyramids == 3
    assert settings.prefetch.tiles_per_neighbor == 2


def test_config_configure_prefetch_block() -> None:
    config = Config()
    config.configure(cache={"prefetch": {"masks": 0, "predictors": 0}})
    assert config.cache.prefetch.masks == 0
    assert config.cache.prefetch.predictors == 0


def test_config_updates_prefetch_mapping() -> None:
    config = Config()
    config.configure(cache={"prefetch": {"tiles": 1, "tiles_per_neighbor": 3}})
    assert config.cache.prefetch.tiles == 1
    assert config.cache.prefetch.tiles_per_neighbor == 3


def test_cache_settings_ignore_incompatible_fields_with_warning(caplog) -> None:
    """Ignore incompatible cache fields while emitting warnings."""
    cache_config_module._AUTO_BUDGET_WARNING_EMITTED = False
    cache_config_module._HARD_HEADROOM_WARNING_EMITTED = False
    settings = CacheConfig(mode="auto")
    with caplog.at_level(logging.WARNING):
        settings.apply_mapping({"budget_mb": 10})
    assert settings.budget_mb is None
    assert any(
        "Ignoring budget_mb in auto cache mode" in record.message
        for record in caplog.records
    )
    settings = CacheConfig(mode="hard")
    with caplog.at_level(logging.WARNING):
        settings.apply_mapping({"headroom_percent": 0.2})
    assert settings.headroom_percent == 0.1
    assert any(
        "Ignoring headroom setting in hard cache mode" in record.message
        for record in caplog.records
    )


def test_qpane_updates_consumer_budgets(qapp) -> None:
    qpane_widget = QPane(features=())
    try:
        qpane_widget.applySettings(cache={"mode": "hard", "budget_mb": 1024})
        cache_settings = qpane_widget.settings.cache
        budgets_bytes = cache_settings.resolve_consumer_budgets_bytes(
            cache_settings.resolve_active_budget_bytes()
        )
        view = qpane_widget.view()
        tile_limit = view.tile_manager.cache_limit_bytes
        pyramid_manager = qpane_widget.catalog().pyramidManager()
        assert tile_limit == budgets_bytes["tiles"]
        assert pyramid_manager.cache_limit_bytes == budgets_bytes["pyramids"]
    finally:
        qpane_widget.deleteLater()
        qapp.processEvents()
