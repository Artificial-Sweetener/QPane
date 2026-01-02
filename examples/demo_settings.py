#!/usr/bin/env python3
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

"""Shared helpers for reading and writing the demo settings file."""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

_SETTINGS_FILE = Path(__file__).resolve().parent / "demo_settings.json"


def load_demo_settings() -> dict[str, object]:
    """Load persisted dashboard settings for the demo launcher."""
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    required_keys = {
        "tier",
        "log_level",
        "sam_download_mode",
        "sam_model_path",
        "sam_model_url",
    }
    if not required_keys.issubset(data.keys()):
        return {}
    if not all(
        isinstance(data[key], str) for key in ("tier", "log_level", "sam_download_mode")
    ):
        return {}
    if data["sam_model_path"] is not None and not isinstance(
        data["sam_model_path"], str
    ):
        return {}
    if data["sam_model_url"] is not None and not isinstance(data["sam_model_url"], str):
        return {}
    sam_model_hash = data.get("sam_model_hash")
    if sam_model_hash is not None and not isinstance(sam_model_hash, str):
        return {}
    settings: dict[str, object] = {
        "tier": data["tier"],
        "log_level": data["log_level"],
        "sam_download_mode": data["sam_download_mode"],
        "sam_model_path": data["sam_model_path"],
        "sam_model_url": data["sam_model_url"],
        "sam_model_hash": sam_model_hash,
    }
    window_geometry = _coerce_geometry_payload(data.get("window_geometry"))
    if window_geometry is not None:
        settings["window_geometry"] = window_geometry
    window_size = _coerce_int_pair(data.get("window_size"), minimum=1)
    if window_size is not None:
        settings["window_size"] = window_size
    window_position = _coerce_int_pair(data.get("window_position"), minimum=None)
    if window_position is not None:
        settings["window_position"] = window_position
    return settings


def save_demo_settings(
    tier: str,
    log_level: str,
    sam_download_mode: str,
    sam_model_path: str | None,
    sam_model_url: str | None,
    sam_model_hash: str | None,
    *,
    window_geometry: str | None = None,
    window_size: tuple[int, int] | None = None,
    window_position: tuple[int, int] | None = None,
) -> None:
    """Persist dashboard settings to a local JSON file."""
    existing = load_demo_settings()
    if window_geometry is None:
        existing_geometry = existing.get("window_geometry")
        if isinstance(existing_geometry, str):
            window_geometry = existing_geometry
    if window_size is None:
        existing_size = existing.get("window_size")
        if isinstance(existing_size, tuple):
            window_size = existing_size
    if window_position is None:
        existing_position = existing.get("window_position")
        if isinstance(existing_position, tuple):
            window_position = existing_position
    payload: dict[str, object] = {
        "tier": tier,
        "log_level": log_level,
        "sam_download_mode": sam_download_mode,
        "sam_model_path": sam_model_path,
        "sam_model_url": sam_model_url,
        "sam_model_hash": sam_model_hash,
    }
    if window_geometry is not None:
        payload["window_geometry"] = window_geometry
    if window_size is not None:
        payload["window_size"] = [int(window_size[0]), int(window_size[1])]
    if window_position is not None:
        payload["window_position"] = [int(window_position[0]), int(window_position[1])]
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass


def _coerce_int_pair(value: object, *, minimum: int | None) -> tuple[int, int] | None:
    """Return a validated integer pair from a JSON payload."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    first, second = value
    if not _is_valid_int(first) or not _is_valid_int(second):
        return None
    first = int(first)
    second = int(second)
    if minimum is not None and (first < minimum or second < minimum):
        return None
    return (first, second)


def _coerce_geometry_payload(value: object) -> str | None:
    """Return a base64 geometry payload string when valid."""
    if not isinstance(value, str) or not value:
        return None
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError):
        return None
    if not decoded:
        return None
    return value


def _is_valid_int(value: object) -> bool:
    """Return True when the value is a non-bool int."""
    return isinstance(value, int) and not isinstance(value, bool)
