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

"""Regression tests for documentation consistency checks."""

from __future__ import annotations

from pathlib import Path

import tools.check_consistency as consistency


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_public_symbol_in_api_reference_fails(tmp_path, monkeypatch):
    """Missing API reference coverage remains a hard failure."""
    docs = tmp_path / "docs"
    docs.mkdir()
    _write(
        docs / "api-reference.md",
        "- QPane.foo - Open the active test workflow for the host.\n",
    )
    _write(
        docs / "guide.md",
        "Use QPane.foo when the host needs to open the active test workflow.\n"
        "Use QPane.bar when the host needs to close that workflow cleanly.\n",
    )
    monkeypatch.setattr(consistency, "DOCS_DIR", docs)

    errors = consistency.check_doc_consistency(
        {"QPane.foo", "QPane.bar"},
        set(),
    )

    assert "[API Reference] Missing: QPane.bar" in errors


def test_api_reference_symbol_without_explainer_fails(tmp_path):
    """API reference entries must include a short explainer."""
    pattern = consistency.build_symbol_pattern({"QPane"})
    path = _write(tmp_path / "api-reference.md", "- QPane.foo\n")

    _symbols, errors = consistency.collect_api_reference_symbols(path, pattern)

    assert any("Missing short explainer" in error for error in errors)


def test_missing_guide_symbol_fails_even_when_reference_has_it(tmp_path, monkeypatch):
    """Guide coverage remains mandatory in addition to API reference coverage."""
    docs = tmp_path / "docs"
    docs.mkdir()
    _write(
        docs / "api-reference.md",
        "- QPane.foo - Open the active test workflow for the host.\n"
        "- QPane.bar - Close the active test workflow for the host.\n",
    )
    _write(
        docs / "guide.md",
        "Use QPane.foo when the host needs to open the active test workflow.\n",
    )
    monkeypatch.setattr(consistency, "DOCS_DIR", docs)

    errors = consistency.check_doc_consistency(
        {"QPane.foo", "QPane.bar"},
        set(),
    )

    assert "[Guides] Missing: QPane.bar" in errors


def test_dataclass_field_missing_from_guides_fails(tmp_path, monkeypatch):
    """Dataclass field symbols must be explained in guides too."""
    docs = tmp_path / "docs"
    docs.mkdir()
    _write(
        docs / "api-reference.md",
        "- QPaneScene.title - Host-facing scene title shown in composition browsers.\n",
    )
    _write(docs / "guide.md", "Scene titles label composition rows for hosts.\n")
    monkeypatch.setattr(consistency, "DOCS_DIR", docs)

    errors = consistency.check_doc_consistency(
        {"QPaneScene.title"},
        set(),
    )

    assert "[Guides] Missing: QPaneScene.title" in errors


def test_reference_section_symbols_do_not_count_for_guides(tmp_path):
    """Reference-style guide sections fail and do not satisfy guide coverage."""
    pattern = consistency.build_symbol_pattern({"QPane"})
    guide = _write(
        tmp_path / "guide.md",
        "## Scene API Reference\n\n"
        "QPane.foo stores the requested scene composition for later use.\n",
    )

    symbols, errors = consistency.collect_valid_guide_symbols([guide], pattern)

    assert "QPane.foo" not in symbols
    assert any("Reference-style heading" in error for error in errors)


def test_long_symbol_list_does_not_count_for_guides(tmp_path):
    """Long symbol runs are rejected as guide coverage."""
    pattern = consistency.build_symbol_pattern({"QPane"})
    bullets = "\n".join(f"- QPane.method{i}" for i in range(13))
    guide = _write(tmp_path / "guide.md", bullets)

    symbols, errors = consistency.collect_valid_guide_symbols([guide], pattern)

    assert symbols == set()
    assert any("Symbol dump" in error for error in errors)


def test_related_apis_pass_with_host_context(tmp_path):
    """Short related API callouts pass when they explain host usage."""
    pattern = consistency.build_symbol_pattern({"QPane"})
    guide = _write(
        tmp_path / "guide.md",
        "## Related APIs\n\n"
        "- QPane.foo opens the active workflow when the host wants to show it.\n",
    )

    symbols, errors = consistency.collect_valid_guide_symbols([guide], pattern)

    assert "QPane.foo" in symbols
    assert errors == []


def test_prose_or_nearby_code_explains_symbol(tmp_path):
    """Tutorial prose and adjacent examples both count as guide coverage."""
    pattern = consistency.build_symbol_pattern({"QPane"})
    guide = _write(
        tmp_path / "guide.md",
        "Use QPane.foo when the host needs to open the active workflow.\n\n"
        "QPane.bar prepares the workflow state before the host activates it.\n\n"
        "```python\n"
        "viewer.bar()\n"
        "```\n",
    )

    symbols, errors = consistency.collect_valid_guide_symbols([guide], pattern)

    assert "QPane.foo" in symbols
    assert "QPane.bar" in symbols
    assert errors == []
