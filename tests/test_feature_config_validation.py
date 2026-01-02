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

"""Validation behaviour for feature-aware configuration slices."""

from __future__ import annotations
from pathlib import Path
import pytest
from qpane.core.config import Config, FeatureAwareConfig
from qpane.core.config_features import iter_descriptors


def test_non_strict_validation_warns_and_uses_defaults(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(tile_size=0)
    descriptors = iter_descriptors()
    with caplog.at_level("WARNING"):
        settings = FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("mask", "sam"),
            strict=False,
        )
    assert settings.tile_size == 1024
    failures = settings.validation_failures()
    assert failures["core"].lower().startswith("tile_size")
    assert any("Validation failed for feature 'core'" in msg for msg in caplog.messages)


def test_strict_validation_raises() -> None:
    config = Config(mask_autosave_debounce_ms=-1)
    descriptors = iter_descriptors()
    with pytest.raises(
        ValueError, match="Feature 'mask' configuration failed validation"
    ):
        FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("mask",),
            strict=True,
        )


def test_tile_overlap_validation_rejects_invalid_stride() -> None:
    config = Config(tile_size=256, tile_overlap=256)
    descriptors = iter_descriptors()
    with pytest.raises(
        ValueError, match="Feature 'core' configuration failed validation"
    ):
        FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("core",),
            strict=True,
        )


def test_mask_validation_failure_defaults_restored() -> None:
    config = Config(mask_autosave_debounce_ms=-5)
    descriptors = iter_descriptors()
    settings = FeatureAwareConfig(
        config,
        descriptors=descriptors,
        installed_features=("mask",),
        strict=False,
    )
    assert settings.mask_autosave_debounce_ms == 2000
    assert "mask" in settings.validation_failures()


def test_sam_device_cuda_unavailable_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class _Torch:
        cuda = _Cuda()
        mps = None

    monkeypatch.setattr(
        "qpane.core.config_features._import_torch",
        lambda: _Torch(),
    )
    config = Config(sam_device="cuda")
    descriptors = iter_descriptors()
    with caplog.at_level("WARNING"):
        settings = FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("sam",),
            strict=False,
        )
    assert settings.sam_device == "cpu"
    failures = settings.validation_failures()
    assert "sam" in failures
    assert "cuda" in failures["sam"].lower()
    assert any("feature 'sam'" in msg.lower() for msg in caplog.messages)


def test_sam_device_strict_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "qpane.core.config_features._import_torch",
        lambda: None,
    )
    config = Config(sam_device="quantum")
    descriptors = iter_descriptors()
    with pytest.raises(
        ValueError, match="Feature 'sam' configuration failed validation"
    ):
        FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("sam",),
            strict=True,
        )


def test_sam_download_mode_strict_rejects_unknown() -> None:
    config = Config(sam_download_mode="eventually")
    descriptors = iter_descriptors()
    with pytest.raises(
        ValueError, match="Feature 'sam' configuration failed validation"
    ):
        FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("sam",),
            strict=True,
        )


def test_sam_model_url_strict_rejects_empty() -> None:
    config = Config(sam_model_url=" ")
    descriptors = iter_descriptors()
    with pytest.raises(
        ValueError, match="Feature 'sam' configuration failed validation"
    ):
        FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("sam",),
            strict=True,
        )


def test_sam_model_path_strict_rejects_empty() -> None:
    config = Config(sam_model_path="")
    descriptors = iter_descriptors()
    with pytest.raises(
        ValueError, match="Feature 'sam' configuration failed validation"
    ):
        FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("sam",),
            strict=True,
        )


def test_sam_model_path_accepts_pathlike() -> None:
    config = Config(sam_model_path=Path("checkpoint.pt"))
    descriptors = iter_descriptors()
    settings = FeatureAwareConfig(
        config,
        descriptors=descriptors,
        installed_features=("sam",),
        strict=True,
    )
    assert settings.sam_model_path == Path("checkpoint.pt")


def test_sam_model_hash_strict_rejects_empty() -> None:
    config = Config(sam_model_hash=" ")
    descriptors = iter_descriptors()
    with pytest.raises(
        ValueError, match="Feature 'sam' configuration failed validation"
    ):
        FeatureAwareConfig(
            config,
            descriptors=descriptors,
            installed_features=("sam",),
            strict=True,
        )


def test_sam_model_hash_accepts_default() -> None:
    config = Config(sam_model_hash="default")
    descriptors = iter_descriptors()
    settings = FeatureAwareConfig(
        config,
        descriptors=descriptors,
        installed_features=("sam",),
        strict=True,
    )
    assert settings.sam_model_hash == "default"
