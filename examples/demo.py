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

"""Entry point for the QPane example demo application."""

from __future__ import annotations
import argparse
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, TYPE_CHECKING

if __package__ is None or __package__ == "":
    # Allow running as a script (python examples/demo.py) by adding repo root.
    sys.path.append(str(Path(__file__).resolve().parent.parent))
from examples.demo_settings import load_demo_settings, save_demo_settings

if TYPE_CHECKING:
    from examples.demonstration.demo_window import ExampleOptions, ExampleWindow

__all__ = ["ExampleOptions", "ExampleWindow", "parse_args", "main"]
_TIERS = {
    "core": {"extra": None, "features": "core", "label": "Core"},
    "mask": {"extra": "mask", "features": "mask", "label": "Masks"},
    "masksam": {"extra": "full", "features": "masksam", "label": "Mask+SAM"},
}
_SAM_DOWNLOAD_MODES = ["background", "blocking", "disabled"]
ExampleOptions: Any | None = None
ExampleWindow: Any | None = None


def _load_example_types() -> tuple[Any, Any]:
    """Import and cache the demo window symbols."""
    global ExampleOptions, ExampleWindow
    if ExampleOptions is None or ExampleWindow is None:
        from examples.demonstration.demo_window import (
            ExampleOptions as DemoExampleOptions,
            ExampleWindow as DemoExampleWindow,
        )

        ExampleOptions = DemoExampleOptions
        ExampleWindow = DemoExampleWindow
    return ExampleOptions, ExampleWindow


def _pyside_available() -> bool:
    """Return True when PySide6 is available for imports."""
    return importlib.util.find_spec("PySide6") is not None


def _resolve_fallback_app_data_dir() -> Path | None:
    """Return an OS-appropriate app data directory without Qt."""
    base: str | None
    if sys.platform.startswith("win"):
        base = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    if not base:
        return None
    try:
        return (Path(base) / Path(sys.executable).stem).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _resolve_app_data_dir() -> Path | None:
    """Resolve the app data directory using Qt when available."""
    try:
        from PySide6.QtCore import QCoreApplication, QStandardPaths
    except ModuleNotFoundError:
        return _resolve_fallback_app_data_dir()
    if not QCoreApplication.applicationName():
        QCoreApplication.setApplicationName(Path(sys.executable).stem)
    app_data = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not app_data:
        return _resolve_fallback_app_data_dir()
    try:
        return Path(app_data).resolve()
    except (OSError, RuntimeError, ValueError):
        return _resolve_fallback_app_data_dir()


def _parse_bootstrap_args(argv: list[str]) -> argparse.Namespace:
    """Parse the CLI arguments needed for bootstrapping."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--features",
        choices=["core", "mask", "masksam"],
        default="masksam",
    )
    parser.add_argument(
        "--config-strict",
        action="store_true",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    parser.add_argument(
        "--skip-menu",
        action="store_true",
    )
    parser.add_argument(
        "--sam-download-mode",
        choices=_SAM_DOWNLOAD_MODES,
        default=None,
    )
    parser.add_argument(
        "--sam-model-path",
        default=None,
    )
    parser.add_argument(
        "--sam-model-url",
        default=None,
    )
    parser.add_argument(
        "--sam-model-hash",
        default=None,
    )
    return parser.parse_args(argv)


if _pyside_available():
    _load_example_types()


def _venv_dir(tier: str) -> Path:
    """Resolve the directory path for the specified environment tier."""
    return Path(__file__).resolve().parent / f"venv-{tier}"


def _venv_python(tier: str) -> Path:
    """Locate the Python executable within the tier's virtual environment."""
    return (
        _venv_dir(tier)
        / ("Scripts" if sys.platform.startswith("win") else "bin")
        / ("python.exe" if sys.platform.startswith("win") else "python")
    )


def _create_venv(tier: str, rebuild: bool = False) -> None:
    """Initialize a fresh virtual environment for the specified tier."""
    target = _venv_dir(tier)
    if rebuild and target.exists():
        shutil.rmtree(target)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)


def _install_extras(tier: str) -> None:
    """Install the package with tier-specific extras into the environment."""
    info = _TIERS[tier]
    venv_python = _venv_python(tier)
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], check=True
    )
    project_root = Path(__file__).resolve().parent.parent
    target = str(project_root)
    if info["extra"]:
        target = f"{target}[{info['extra']}]"
    subprocess.run([str(venv_python), "-m", "pip", "install", "-e", target], check=True)


def _venv_exists(tier: str) -> bool:
    """Check if the virtual environment for the tier is already created."""
    return _venv_python(tier).exists()


def _ensure_venv_ready(
    tier: str, *, rebuild: bool = False, skip_install_if_present: bool = False
) -> None:
    """Create or rebuild the tier-specific venv and install extras."""
    if rebuild or not _venv_exists(tier):
        _create_venv(tier, rebuild=rebuild)
        _install_extras(tier)
        return
    if not skip_install_if_present:
        _install_extras(tier)


def _launch_in_venv(
    tier: str,
    log_level: str = "INFO",
    config_strict: bool = False,
    sam_download_mode: str | None = None,
    sam_model_path: str | None = None,
    sam_model_url: str | None = None,
    sam_model_hash: str | None = None,
) -> int:
    """Spawn the demo using the tier-specific venv and feature selection."""
    python_bin = _venv_python(tier)
    info = _TIERS[tier]
    cmd = [
        str(python_bin),
        "-m",
        "examples.demo",
        "--features",
        info["features"],
        "--log-level",
        log_level,
        "--skip-menu",
    ]
    if config_strict:
        cmd.append("--config-strict")
    if tier == "masksam" and sam_download_mode:
        cmd.extend(["--sam-download-mode", sam_download_mode])
    if tier == "masksam" and sam_model_path:
        cmd.extend(["--sam-model-path", sam_model_path])
    if tier == "masksam" and sam_model_url:
        cmd.extend(["--sam-model-url", sam_model_url])
    if tier == "masksam" and sam_model_hash:
        cmd.extend(["--sam-model-hash", sam_model_hash])
    return subprocess.call(cmd)


def _interactive_menu() -> int:
    """Present a dashboard menu to rebuild/install venvs and launch the demo."""
    tiers = ["core", "mask", "masksam"]
    log_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
    sam_modes = list(_SAM_DOWNLOAD_MODES)
    # Load defaults from disk or fall back to Mask+SAM/WARNING
    saved = load_demo_settings()
    default_tier = saved.get("tier", "masksam")
    default_level = saved.get("log_level", "WARNING")
    default_sam_mode = saved.get("sam_download_mode", "background")
    default_sam_path = saved.get("sam_model_path")
    default_sam_url = saved.get("sam_model_url")
    default_sam_hash = saved.get("sam_model_hash")
    try:
        tier_idx = tiers.index(default_tier)
    except ValueError:
        tier_idx = 2
    try:
        level_idx = log_levels.index(default_level)
    except ValueError:
        level_idx = 2
    try:
        sam_idx = sam_modes.index(default_sam_mode)
    except ValueError:
        sam_idx = 0
    state = {
        "tier_idx": tier_idx,
        "level_idx": level_idx,
        "sam_idx": sam_idx,
        "sam_model_path": default_sam_path,
        "sam_model_url": default_sam_url,
        "sam_model_hash": default_sam_hash,
        "sam_clear_checkpoint": False,
    }

    def _current_tier() -> str:
        """Return the tier name currently selected in the menu."""
        return tiers[state["tier_idx"]]

    def _resolve_sam_checkpoint_path(value: str | None) -> Path | None:
        """Resolve the SAM checkpoint path for the current menu settings."""
        normalized = value.strip() if isinstance(value, str) else ""
        if normalized:
            try:
                return Path(normalized).expanduser().resolve()
            except (OSError, RuntimeError, ValueError):
                return None
        app_data = _resolve_app_data_dir()
        if app_data is None:
            return None
        try:
            return (app_data / "mobile_sam.pt").resolve()
        except (OSError, RuntimeError, ValueError):
            return None

    def _format_optional_value(value: str | None) -> str:
        """Return a human-friendly label for optional settings values."""
        if value is None or not value.strip():
            return "(default)"
        return value

    def _build_menu_rows() -> list[dict[str, str]]:
        """Build the menu rows based on the selected feature tier."""
        rows: list[dict[str, str]] = [
            {
                "kind": "option",
                "key": "feature",
                "label": "Feature Set",
                "value": _TIERS[_current_tier()]["label"],
                "help": (
                    "Select feature tier. Left/Right to cycle. "
                    "(Core: Viewer, Masks: +Masks, Mask+SAM: +AI)"
                ),
            },
            {
                "kind": "option",
                "key": "log_level",
                "label": "Log Level",
                "value": log_levels[state["level_idx"]],
                "help": (
                    "Set logging verbosity. Left/Right to cycle. "
                    "(DEBUG for dev, INFO for standard)"
                ),
            },
        ]
        if _current_tier() == "masksam":
            rows.append(
                {
                    "kind": "option",
                    "key": "sam_download_mode",
                    "label": "SAM Download",
                    "value": sam_modes[state["sam_idx"]],
                    "help": (
                        "Choose SAM download mode. Left/Right to cycle. "
                        "(background, blocking, or disabled; disabled needs a checkpoint)"
                    ),
                }
            )
            rows.append(
                {
                    "kind": "input",
                    "key": "sam_model_path",
                    "label": "SAM Path",
                    "value": _format_optional_value(state["sam_model_path"]),
                    "help": (
                        "Set a local checkpoint path. Enter to edit, blank to use default."
                    ),
                }
            )
            rows.append(
                {
                    "kind": "input",
                    "key": "sam_model_url",
                    "label": "SAM URL",
                    "value": _format_optional_value(state["sam_model_url"]),
                    "help": (
                        "Set a download URL override. Enter to edit, blank to use default."
                    ),
                }
            )
            rows.append(
                {
                    "kind": "input",
                    "key": "sam_model_hash",
                    "label": "SAM Hash",
                    "value": _format_optional_value(state["sam_model_hash"]),
                    "help": (
                        "Set SHA-256 for the checkpoint. Use 'default' to force the "
                        "built-in hash, or blank to skip verification."
                    ),
                }
            )
            checkpoint_path = _resolve_sam_checkpoint_path(state["sam_model_path"])
            if checkpoint_path is not None and checkpoint_path.exists():
                rows.append(
                    {
                        "kind": "option",
                        "key": "sam_clear_checkpoint",
                        "label": "Clear SAM Checkpoint",
                        "value": "Yes" if state["sam_clear_checkpoint"] else "No",
                        "help": (
                            "Delete the resolved SAM checkpoint before launch. "
                            f"Path: {checkpoint_path}"
                        ),
                    }
                )
            else:
                state["sam_clear_checkpoint"] = False
        rows.extend(
            [
                {
                    "kind": "action",
                    "key": "run",
                    "label": "Run Demo",
                    "help": "Launch the demo application with the current settings.",
                },
                {
                    "kind": "action",
                    "key": "rebuild",
                    "label": "Rebuild Environment",
                    "help": (
                        "Recreate the virtual environment for the selected tier "
                        "(fixes dependency issues)."
                    ),
                },
                {
                    "kind": "action",
                    "key": "exit",
                    "label": "Exit",
                    "help": "Exit the launcher.",
                },
            ]
        )
        return rows

    def _clear_sam_checkpoint(path: Path) -> None:
        """Delete the resolved SAM checkpoint before launch."""
        if not path.exists():
            return
        try:
            path.unlink()
        except OSError as exc:
            print(f"\nError clearing SAM checkpoint: {exc}")
            input("Press Enter...")

    def _print_dashboard(selected_row: int, rows: list[dict[str, str]]) -> None:
        """Clear the screen and display the interactive dashboard."""
        os.system("cls" if os.name == "nt" else "clear")
        print("QPane Demo Dashboard (Arrow keys to navigate/change, Enter to select)\n")
        for idx, row in enumerate(rows):
            prefix = ">" if selected_row == idx else " "
            if row["kind"] == "option":
                print(f"{prefix} {row['label']:<16} < {row['value']} >")
            elif row["kind"] == "input":
                print(f"{prefix} {row['label']:<16} {row['value']}")
            else:
                print(f"{prefix} [ {row['label']} ]")
        print("\n")
        msg = rows[selected_row]["help"]
        # White background (47), Black text (30)
        print(f"\033[47;30m {msg:<78} \033[0m")

    def _handle_input() -> str:
        """Return action based on key press."""
        if not sys.stdin.isatty():
            return "EXIT"
        try:
            import msvcrt  # type: ignore

            ch = msvcrt.getch()
            if ch == b"\x1b":
                return "EXIT"
            if ch in {b"\r", b"\n"}:
                return "SELECT"
            if ch in {b"\x00", b"\xe0"}:
                arrow = msvcrt.getch()
                if arrow == b"H":
                    return "UP"
                if arrow == b"P":
                    return "DOWN"
                if arrow == b"K":
                    return "LEFT"
                if arrow == b"M":
                    return "RIGHT"
        except ImportError:
            return "EXIT"
        return "NONE"

    def _prompt_setting(label: str, current: str | None) -> str | None:
        """Prompt for a new setting value or clear to defaults."""
        os.system("cls" if os.name == "nt" else "clear")
        print(f"Set {label} (leave blank to use the default).\n")
        if current:
            print(f"Current: {current}")
        value = input("New value: ").strip()
        return value or None

    rows = _build_menu_rows()
    selected_row = next((idx for idx, row in enumerate(rows) if row["key"] == "run"), 0)
    while True:
        rows = _build_menu_rows()
        if selected_row >= len(rows):
            selected_row = max(len(rows) - 1, 0)
        _print_dashboard(selected_row, rows)
        action = _handle_input()
        if action == "EXIT":
            return 0
        if action == "UP":
            selected_row = (selected_row - 1) % len(rows)
        elif action == "DOWN":
            selected_row = (selected_row + 1) % len(rows)
        elif action == "LEFT":
            row = rows[selected_row]
            if row["key"] == "feature":
                state["tier_idx"] = (state["tier_idx"] - 1) % len(tiers)
            elif row["key"] == "log_level":
                state["level_idx"] = (state["level_idx"] - 1) % len(log_levels)
            elif row["key"] == "sam_download_mode":
                state["sam_idx"] = (state["sam_idx"] - 1) % len(sam_modes)
            elif row["key"] == "sam_clear_checkpoint":
                state["sam_clear_checkpoint"] = not state["sam_clear_checkpoint"]
        elif action == "RIGHT":
            row = rows[selected_row]
            if row["key"] == "feature":
                state["tier_idx"] = (state["tier_idx"] + 1) % len(tiers)
            elif row["key"] == "log_level":
                state["level_idx"] = (state["level_idx"] + 1) % len(log_levels)
            elif row["key"] == "sam_download_mode":
                state["sam_idx"] = (state["sam_idx"] + 1) % len(sam_modes)
            elif row["key"] == "sam_clear_checkpoint":
                state["sam_clear_checkpoint"] = not state["sam_clear_checkpoint"]
        elif action == "SELECT":
            row = rows[selected_row]
            if row["key"] == "run":
                tier = tiers[state["tier_idx"]]
                level = log_levels[state["level_idx"]]
                sam_mode = sam_modes[state["sam_idx"]]
                sam_path = state["sam_model_path"]
                sam_url = state["sam_model_url"]
                sam_hash = state["sam_model_hash"]
                if tier == "masksam" and state["sam_clear_checkpoint"]:
                    checkpoint_path = _resolve_sam_checkpoint_path(sam_path)
                    if checkpoint_path is not None:
                        _clear_sam_checkpoint(checkpoint_path)
                save_demo_settings(tier, level, sam_mode, sam_path, sam_url, sam_hash)
                try:
                    _ensure_venv_ready(tier, skip_install_if_present=True)
                    return _launch_in_venv(
                        tier,
                        log_level=level,
                        sam_download_mode=sam_mode,
                        sam_model_path=sam_path,
                        sam_model_url=sam_url,
                        sam_model_hash=sam_hash,
                    )
                except subprocess.CalledProcessError as exc:
                    print(f"\nError: {exc}")
                    input("Press Enter...")
            elif row["key"] == "rebuild":
                tier = tiers[state["tier_idx"]]
                level = log_levels[state["level_idx"]]
                sam_mode = sam_modes[state["sam_idx"]]
                sam_path = state["sam_model_path"]
                sam_url = state["sam_model_url"]
                sam_hash = state["sam_model_hash"]
                save_demo_settings(tier, level, sam_mode, sam_path, sam_url, sam_hash)
                try:
                    print(f"\nRebuilding {tier} environment...")
                    _ensure_venv_ready(tier, rebuild=True)
                    print("Done.")
                    input("Press Enter...")
                except subprocess.CalledProcessError as exc:
                    print(f"\nError: {exc}")
                    input("Press Enter...")
            elif row["key"] == "exit":
                tier = tiers[state["tier_idx"]]
                level = log_levels[state["level_idx"]]
                sam_mode = sam_modes[state["sam_idx"]]
                sam_path = state["sam_model_path"]
                sam_url = state["sam_model_url"]
                sam_hash = state["sam_model_hash"]
                save_demo_settings(tier, level, sam_mode, sam_path, sam_url, sam_hash)
                return 0
            elif row["key"] == "sam_model_path":
                state["sam_model_path"] = _prompt_setting(
                    "SAM model path", state["sam_model_path"]
                )
            elif row["key"] == "sam_model_url":
                state["sam_model_url"] = _prompt_setting(
                    "SAM model URL", state["sam_model_url"]
                )
            elif row["key"] == "sam_model_hash":
                state["sam_model_hash"] = _prompt_setting(
                    "SAM model hash", state["sam_model_hash"]
                )


def parse_args(argv: Optional[Iterable[str]] = None) -> ExampleOptions:
    """Parse CLI arguments controlling feature selection and config strictness."""
    _load_example_types()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features",
        choices=["core", "mask", "masksam"],
        default="masksam",
        help="Select example feature set (core viewer, mask tools, or Mask+SAM).",
    )
    parser.add_argument(
        "--config-strict",
        action="store_true",
        help="Raise errors when presets override config namespaces for inactive features.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set the logging verbosity level.",
    )
    parser.add_argument(
        "--skip-menu",
        action="store_true",
        help="Bypass the interactive menu (used by the launcher).",
    )
    parser.add_argument(
        "--sam-download-mode",
        choices=_SAM_DOWNLOAD_MODES,
        default=None,
        help=(
            "Choose how SAM checkpoints are acquired "
            "(background, blocking, or disabled)."
        ),
    )
    parser.add_argument(
        "--sam-model-path",
        default=None,
        help="Override the local SAM checkpoint path for Mask+SAM demos.",
    )
    parser.add_argument(
        "--sam-model-url",
        default=None,
        help="Override the SAM checkpoint download URL for Mask+SAM demos.",
    )
    parser.add_argument(
        "--sam-model-hash",
        default=None,
        help=(
            "Provide a SHA-256 checksum for the SAM checkpoint. "
            "Use 'default' to request the built-in MobileSAM hash."
        ),
    )
    ns = parser.parse_args(list(argv) if argv is not None else None)
    return ExampleOptions(
        feature_set=ns.features,
        config_strict=bool(ns.config_strict),
        log_level=ns.log_level,
        sam_download_mode=ns.sam_download_mode,
        sam_model_path=ns.sam_model_path,
        sam_model_url=ns.sam_model_url,
        sam_model_hash=ns.sam_model_hash,
    )


def _configure_logging(level_name: str = "INFO") -> None:
    """Ensure example logging emits messages to the console at the requested level."""
    root = logging.getLogger()
    level = getattr(logging, level_name.upper())
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    """Entry point for launching the example application."""
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        return _interactive_menu()
    if not _pyside_available():
        bootstrap = _parse_bootstrap_args(args)
        try:
            _ensure_venv_ready(bootstrap.features, skip_install_if_present=True)
        except subprocess.CalledProcessError as exc:
            print(f"\nError: {exc}")
            return 1
        return _launch_in_venv(
            bootstrap.features,
            log_level=bootstrap.log_level,
            config_strict=bootstrap.config_strict,
            sam_download_mode=bootstrap.sam_download_mode,
            sam_model_path=bootstrap.sam_model_path,
            sam_model_url=bootstrap.sam_model_url,
            sam_model_hash=bootstrap.sam_model_hash,
        )
    _load_example_types()
    from PySide6.QtGui import QImageReader
    from PySide6.QtWidgets import QApplication
    from qpane import Config

    opts = parse_args(args)
    _configure_logging(opts.log_level)
    app = QApplication(sys.argv[:1])
    QImageReader.setAllocationLimit(0)
    config = Config()
    if opts.feature_set == "masksam":
        sam_overrides: dict[str, object] = {}
        if opts.sam_download_mode:
            sam_overrides["sam_download_mode"] = opts.sam_download_mode
        if opts.sam_model_path:
            sam_overrides["sam_model_path"] = opts.sam_model_path
        if opts.sam_model_url:
            sam_overrides["sam_model_url"] = opts.sam_model_url
        if opts.sam_model_hash:
            sam_overrides["sam_model_hash"] = opts.sam_model_hash
        if sam_overrides:
            config.configure(**sam_overrides)
    window = ExampleWindow(opts, config=config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
