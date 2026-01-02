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

from __future__ import annotations
from pathlib import Path
import hashlib
import numpy as np
import pytest
from PySide6.QtCore import QStandardPaths
from qpane.sam import service


class _RecordingPredictor:
    def __init__(self, masks: np.ndarray):
        self._masks = masks
        self.calls: list[tuple[np.ndarray, bool]] = []

    def predict(self, *, box: np.ndarray, multimask_output: bool):
        self.calls.append((box, multimask_output))
        scores = np.ones((self._masks.shape[0],), dtype=float)
        logits = np.ones_like(scores)
        return self._masks, scores, logits


def test_predict_mask_from_box_rejects_invalid_bbox():
    class _FailingPredictor:
        def predict(self, *, box: np.ndarray, multimask_output: bool):
            raise AssertionError("predict should not be invoked for invalid bbox")

    with pytest.raises(ValueError):
        service.predict_mask_from_box(_FailingPredictor(), np.array([1, 2, 3]))


def test_predict_mask_from_box_normalizes_bbox_shape():
    masks = np.ones((1, 1, 1), dtype=bool)
    predictor = _RecordingPredictor(masks)
    result = service.predict_mask_from_box(predictor, np.array([1, 2, 3, 4]))
    assert predictor.calls, "predict should be invoked"
    box_arg, multimask_flag = predictor.calls[-1]
    assert box_arg.shape == (1, 4)
    assert box_arg.dtype == np.float32
    assert multimask_flag is False
    np.testing.assert_array_equal(result, masks[0])


def test_predict_mask_from_box_returns_none_when_no_masks():
    empty_masks = np.zeros((0, 1, 1), dtype=bool)
    predictor = _RecordingPredictor(empty_masks)
    mask = service.predict_mask_from_box(predictor, np.array([0, 0, 1, 1]))
    assert mask is None


def test_load_predictor_forwards_device(monkeypatch):
    service._import_dependencies.cache_clear()

    class DummyPredictor:
        def __init__(self, model):
            self.model = model

    torch_stub = object()
    registry_stub = object()
    load_calls: list[str] = []

    def fake_import():
        return torch_stub, DummyPredictor, registry_stub

    def fake_load_model(device: str, checkpoint_path: Path):
        load_calls.append((device, checkpoint_path))
        return f"model:{device}"

    monkeypatch.setattr(service, "_import_dependencies", fake_import)
    monkeypatch.setattr(service, "_load_model", fake_load_model)
    checkpoint_path = Path("checkpoint.pt")
    predictor = service.load_predictor(checkpoint_path, device="cuda:0")
    assert isinstance(predictor, DummyPredictor)
    assert predictor.model == "model:cuda:0"
    assert load_calls == [("cuda:0", checkpoint_path)]


def test_load_predictor_rejects_missing_checkpoint_path(monkeypatch):
    service._import_dependencies.cache_clear()

    def fake_import():
        return object(), object(), object()

    monkeypatch.setattr(service, "_import_dependencies", fake_import)
    with pytest.raises(service.SamDependencyError) as excinfo:
        service.load_predictor(None, device="cpu")
    message = str(excinfo.value).lower()
    assert "resolve_checkpoint_path" in message


def test_load_model_cache_keys_include_checkpoint_path(tmp_path, monkeypatch):
    service._load_model.cache_clear()
    first = tmp_path / "checkpoint-a.pt"
    second = tmp_path / "checkpoint-b.pt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    registry_calls: list[Path] = []

    class _Model:
        def to(self, *, device: str):
            return self

        def eval(self):
            return self

    def fake_registry(checkpoint: Path):
        registry_calls.append(checkpoint)
        return _Model()

    def fake_import():
        return object(), object(), {"vit_t": fake_registry}

    monkeypatch.setattr(service, "_import_dependencies", fake_import)
    service._load_model(device="cpu", checkpoint_path=first)
    service._load_model(device="cpu", checkpoint_path=second)
    service._load_model(device="cpu", checkpoint_path=first)
    assert registry_calls == [first, second]


def test_resolve_checkpoint_path_defaults_to_app_data():
    QStandardPaths.setTestModeEnabled(True)
    try:
        base_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        resolved = service.resolve_checkpoint_path(None)
        assert resolved == (Path(base_dir) / "mobile_sam.pt").resolve()
    finally:
        QStandardPaths.setTestModeEnabled(False)


def test_resolve_checkpoint_path_rejects_empty_override():
    with pytest.raises(service.SamDependencyError):
        service.resolve_checkpoint_path(" ")


def test_resolve_checkpoint_path_rejects_missing_app_data(monkeypatch):
    monkeypatch.setattr(
        service.QStandardPaths,
        "writableLocation",
        lambda *_args, **_kwargs: "",
    )
    with pytest.raises(service.SamDependencyError):
        service.resolve_checkpoint_path(None)


def test_resolve_checkpoint_path_accepts_override(tmp_path):
    override = tmp_path / "mobile_sam.pt"
    resolved = service.resolve_checkpoint_path(str(override))
    assert resolved == override.resolve()


def test_ensure_checkpoint_returns_existing_file(tmp_path, monkeypatch):
    checkpoint = tmp_path / "mobile_sam.pt"
    payload = b"ready"
    checkpoint.write_bytes(payload)
    called = False

    def fake_urlopen(_url):
        nonlocal called
        called = True
        raise AssertionError("download should not be invoked")

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)
    expected_hash = hashlib.sha256(payload).hexdigest()
    resolved = service.ensure_checkpoint(
        checkpoint,
        download_mode="blocking",
        model_url="https://example.invalid/sam.pt",
        expected_hash=expected_hash,
    )
    assert resolved == checkpoint.resolve()
    assert called is False


def test_ensure_checkpoint_rejects_disabled_mode(tmp_path):
    checkpoint = tmp_path / "mobile_sam.pt"
    with pytest.raises(service.SamDependencyError) as excinfo:
        service.ensure_checkpoint(
            checkpoint,
            download_mode="disabled",
            model_url="https://example.invalid/sam.pt",
        )
    assert "disabled" in str(excinfo.value).lower()


def test_ensure_checkpoint_downloads_file(tmp_path, monkeypatch, capsys):
    checkpoint = tmp_path / "mobile_sam.pt"
    payload = b"sam-checkpoint-data"
    expected_hash = hashlib.sha256(payload).hexdigest()

    class _Response:
        def __init__(self, data: bytes):
            self._data = data
            self._offset = 0
            self.headers = {"Content-Length": str(len(data))}

        def read(self, size: int) -> bytes:
            if self._offset >= len(self._data):
                return b""
            chunk = self._data[self._offset : self._offset + size]
            self._offset += len(chunk)
            return chunk

        def getheader(self, name, default=None):
            return self.headers.get(name, default)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_url):
        return _Response(payload)

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)
    resolved = service.ensure_checkpoint(
        checkpoint,
        download_mode="blocking",
        model_url="https://example.invalid/sam.pt",
        expected_hash=expected_hash,
    )
    assert resolved == checkpoint.resolve()
    assert checkpoint.read_bytes() == payload
    captured = capsys.readouterr().out
    assert "Downloading SAM checkpoint" in captured


def test_ensure_checkpoint_downloads_in_background_with_callback(
    tmp_path, monkeypatch, capsys
):
    checkpoint = tmp_path / "mobile_sam.pt"
    payload = b"sam-checkpoint-data"
    expected_hash = hashlib.sha256(payload).hexdigest()

    class _Response:
        def __init__(self, data: bytes):
            self._data = data
            self._offset = 0
            self.headers = {"Content-Length": str(len(data))}

        def read(self, size: int) -> bytes:
            if self._offset >= len(self._data):
                return b""
            chunk = self._data[self._offset : self._offset + size]
            self._offset += len(chunk)
            return chunk

        def getheader(self, name, default=None):
            return self.headers.get(name, default)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_url):
        return _Response(payload)

    progress: list[tuple[int, int | None]] = []

    def record_progress(downloaded: int, total: int | None) -> None:
        progress.append((downloaded, total))

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)
    resolved = service.ensure_checkpoint(
        checkpoint,
        download_mode="background",
        model_url="https://example.invalid/sam.pt",
        expected_hash=expected_hash,
        progress_callback=record_progress,
    )
    assert resolved == checkpoint.resolve()
    assert checkpoint.read_bytes() == payload
    assert progress
    assert capsys.readouterr().out == ""


def test_ensure_checkpoint_cleans_up_failed_download(tmp_path, monkeypatch):
    checkpoint = tmp_path / "mobile_sam.pt"
    part_path = checkpoint.with_suffix(".pt.part")

    def fake_urlopen(_url):
        raise OSError("network failure")

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(service.SamDependencyError):
        service.ensure_checkpoint(
            checkpoint,
            download_mode="blocking",
            model_url="https://example.invalid/sam.pt",
        )
    assert not part_path.exists()


def test_ensure_checkpoint_rejects_mismatched_hash(tmp_path, monkeypatch):
    checkpoint = tmp_path / "mobile_sam.pt"
    payload = b"sam-checkpoint-data"

    class _Response:
        def __init__(self, data: bytes):
            self._data = data
            self._offset = 0
            self.headers = {"Content-Length": str(len(data))}

        def read(self, size: int) -> bytes:
            if self._offset >= len(self._data):
                return b""
            chunk = self._data[self._offset : self._offset + size]
            self._offset += len(chunk)
            return chunk

        def getheader(self, name, default=None):
            return self.headers.get(name, default)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_url):
        return _Response(payload)

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(service.SamDependencyError):
        service.ensure_checkpoint(
            checkpoint,
            download_mode="blocking",
            model_url="https://example.invalid/sam.pt",
            expected_hash=hashlib.sha256(b"other").hexdigest(),
        )
    assert not checkpoint.exists()
