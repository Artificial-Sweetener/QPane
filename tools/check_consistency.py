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

"""Verify consistency between Implementation, Docs, Demo, and Stubs.

This script acts as the "Trinity" check with qpane.pyi as the Filter/Contract:
1.  **Anchor**: The Stubs (qpane.pyi) define the Public API surface.
2.  **Demo Check**: Ensures the demo only uses methods exposed in qpane.pyi.
3.  **Doc Check**: Ensures everything in qpane.pyi is documented.
4.  **Impl Check**: Ensures qpane.py implements everything in qpane.pyi.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator, Mapping, MutableMapping, Pattern

# --- Constants ---

DEMO_ROOT = pathlib.Path("examples")
QPANE_ROOT = pathlib.Path("qpane")
STUB_FILE = QPANE_ROOT / "qpane.pyi"
DOCS_DIR = Path("docs")
API_REFERENCE_NAME = "api-reference.md"

DEMO_INCLUDE = {DEMO_ROOT / "demo.py", DEMO_ROOT / "demonstration"}
SNIPPET_FILE = DEMO_ROOT / "demonstration" / "hooks_examples.py"
SNIPPET_CONSTANTS = {"CURSOR_SNIPPET", "OVERLAY_SNIPPET", "LENS_SNIPPET"}
EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".venv",
    "venv",
    "venv-full",
    "venv-core",
    "venv-mask",
}
QT_BASE_ATTRS = {
    "connect",
    "emit",
    "setFocusPolicy",
    "size",
    "isNull",
    "mapToGlobal",
    "isAncestorOf",
    "update",
}
SNAPSHOT_ATTR_NAMES = {
    "color",
    "mask_id",
    "image_id",
    "image_count",
    "groups",
    "index",
    "is_active",
    "is_current",
    "label",
    "masks",
    "path",
}
ENUM_BASE_ATTRS = {"value", "name"}


# --- Data Structures ---


@dataclasses.dataclass
class Symbol:
    """Description of a top-level symbol."""

    name: str
    kind: str  # class, function, constant, enum, protocol, type, unknown, lazy
    module: pathlib.Path
    public: bool


@dataclasses.dataclass
class ClassInfo:
    """Describe class members to support chain resolution."""

    name: str
    kind: str  # class, enum, protocol
    methods: dict[str, str | None]
    properties: set[str]
    attributes: set[str]


@dataclasses.dataclass
class Usage:
    """Record how a symbol is used in the demo."""

    name: str
    kind: str
    public: bool
    locations: list[str]
    via_qpane: bool


@dataclasses.dataclass
class ChainUsage:
    """Capture method/property usage on classes."""

    methods: MutableMapping[str, str]
    properties: MutableMapping[str, str]
    unresolved: MutableMapping[str, str]


@dataclasses.dataclass
class DemoSource:
    """Represent a parsed demo source fragment."""

    path: pathlib.Path
    source: str
    inject_qpane_alias: bool = False
    inject_qpane_instance: bool = False


@dataclasses.dataclass(frozen=True)
class MarkdownBlock:
    """Represent a parsed markdown block for documentation checks."""

    kind: str
    text: str
    path: Path
    line: int
    heading_path: tuple[str, ...]
    rejected_section: bool = False
    rejected_list_run: bool = False


# --- Scanner Logic (formerly api_scanner.py) ---


def iter_python_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    """Yield Python files under root respecting include/exclude rules."""
    if root.is_file() and root.suffix == ".py":
        yield root
        return
    for path in root.rglob("*.py"):
        if any(part.startswith(".") for part in path.parts):
            continue
        if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
            continue
        yield path


def load_public_symbols(
    init_path: pathlib.Path,
) -> tuple[set[str], dict[str, tuple[str, str]]]:
    """Parse __all__ and _LAZY_SYMBOLS from qpane/__init__.py without importing."""
    public: set[str] = set()
    lazy_symbols: dict[str, tuple[str, str]] = {}
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            target_ids = {getattr(target, "id", None) for target in node.targets}
            if "__all__" in target_ids:
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    value = None
                if isinstance(value, list) and all(
                    isinstance(item, str) for item in value
                ):
                    public.update(value)
            if "_LAZY_SYMBOLS" in target_ids:
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    value = None
                if isinstance(value, dict):
                    # Only keep string->(module, attr) entries.
                    lazy_symbols.update(
                        {
                            name: tuple(target)  # type: ignore[arg-type]
                            for name, target in value.items()
                            if isinstance(name, str)
                            and isinstance(target, tuple)
                            and len(target) == 2
                            and all(isinstance(part, str) for part in target)
                        }
                    )
    return public, lazy_symbols


def is_enum_base(base: ast.expr) -> bool:
    """Return True if base class looks like an Enum."""
    if isinstance(base, ast.Name):
        return base.id in {"Enum", "IntEnum", "StrEnum"}
    if isinstance(base, ast.Attribute):
        return base.attr in {"Enum", "IntEnum", "StrEnum"}
    return False


def is_protocol_base(base: ast.expr) -> bool:
    """Return True if base class looks like a typing Protocol."""
    if isinstance(base, ast.Name):
        return base.id == "Protocol"
    if isinstance(base, ast.Attribute):
        return base.attr == "Protocol"
    return False


def extract_return_annotation(node: ast.AST) -> str | None:
    """Convert a return annotation to a string if available."""
    ann = getattr(node, "returns", None)
    if ann is None:
        return None
    try:
        return ast.unparse(ann)
    except Exception:
        return None


def base_type_name(text: str | None) -> str | None:
    """Extract a bare type name from an annotation string."""
    if not text:
        return None
    cleaned = (
        text.replace("Optional[", "")
        .replace("]", "")
        .replace("None", "")
        .replace("Union[", "")
        .replace("'", "")
        .replace('"', "")
    )
    for token in cleaned.replace("|", " ").replace(",", " ").split():
        if token.isidentifier():
            return token
    return None


def build_package_index(
    public_symbols: set[str],
    lazy_symbols: Mapping[str, tuple[str, str]],
) -> tuple[dict[str, Symbol], dict[str, ClassInfo]]:
    """Parse the qpane package into symbol and class lookups."""
    symbols: dict[str, Symbol] = {}
    classes: dict[str, ClassInfo] = {}
    for path in iter_python_files(QPANE_ROOT):
        module_rel = path.relative_to(QPANE_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                base_kinds = [b for b in node.bases]
                if any(is_enum_base(base) for base in base_kinds):
                    kind = "enum"
                elif any(is_protocol_base(base) for base in base_kinds):
                    kind = "protocol"
                else:
                    kind = "class"
                symbol = Symbol(
                    name=node.name,
                    kind=kind,
                    module=module_rel,
                    public=node.name in public_symbols,
                )
                symbols.setdefault(node.name, symbol)
                methods: dict[str, str | None] = {}
                properties: set[str] = set()
                attributes: set[str] = set()
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_name = member.name
                        is_property = any(
                            isinstance(dec, ast.Name) and dec.id == "property"
                            for dec in member.decorator_list
                        )
                        if is_property:
                            properties.add(method_name)
                            continue
                        methods[method_name] = extract_return_annotation(member)
                    elif isinstance(member, ast.Assign):
                        for target in member.targets:
                            if isinstance(target, ast.Name):
                                attributes.add(target.id)
                    elif isinstance(member, ast.AnnAssign):
                        target = getattr(member, "target", None)
                        if isinstance(target, ast.Name):
                            attributes.add(target.id)
                classes[node.name] = ClassInfo(
                    name=node.name,
                    kind=kind,
                    methods=methods,
                    properties=properties,
                    attributes=attributes,
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = Symbol(
                    name=node.name,
                    kind="function",
                    module=module_rel,
                    public=node.name in public_symbols,
                )
                symbols.setdefault(node.name, symbol)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        symbol = Symbol(
                            name=target.id,
                            kind="constant",
                            module=module_rel,
                            public=target.id in public_symbols,
                        )
                        symbols.setdefault(target.id, symbol)
    for name, target in lazy_symbols.items():
        module_path = target[0].replace("qpane.", "").replace(".", "/") + ".py"
        symbol = Symbol(
            name=name,
            kind="lazy",
            module=pathlib.Path(module_path),
            public=name in public_symbols,
        )
        symbols.setdefault(name, symbol)
    return symbols, classes


def load_hook_snippets() -> list[DemoSource]:
    """Return demo snippet sources embedded as strings (cursor/overlay/lens)."""
    if not SNIPPET_FILE.exists():
        return []
    text = SNIPPET_FILE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SNIPPET_FILE))
    snippets: list[DemoSource] = []
    for node in tree.body:
        targets: list[ast.Name] = []
        value_node = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    targets.append(target)
            value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                targets.append(node.target)
            value_node = node.value
        if not targets or value_node is None:
            continue
        try:
            literal = ast.literal_eval(value_node)
        except Exception:
            continue
        if not isinstance(literal, str):
            continue
        for target in targets:
            if target.id not in SNIPPET_CONSTANTS:
                continue
            pseudo_path = pathlib.Path(f"{SNIPPET_FILE.as_posix()}::{target.id}")
            snippets.append(
                DemoSource(
                    path=pseudo_path,
                    source=literal,
                    inject_qpane_instance=True,
                )
            )
    return snippets


def collect_demo_sources() -> list[DemoSource]:
    """Collect file-backed and snippet-backed demo sources for analysis."""
    sources: list[DemoSource] = []
    demo_files: list[pathlib.Path] = []
    for include in DEMO_INCLUDE:
        if include.is_file():
            demo_files.append(include)
        elif include.is_dir():
            demo_files.extend(iter_python_files(include))
    for path in demo_files:
        sources.append(
            DemoSource(
                path=path,
                source=path.read_text(encoding="utf-8"),
            )
        )
    sources.extend(load_hook_snippets())
    return sources


def extract_chain(node: ast.AST) -> list[tuple[str, str]] | None:
    """Return a chain of (kind, name) pairs starting from a Name.
    kind is one of: name, attr, call.
    """
    chain: list[tuple[str, str]] = []
    current = node
    while True:
        if isinstance(current, ast.Call):
            chain.append(("call", "()"))
            current = current.func
            continue
        if isinstance(current, ast.Attribute):
            chain.append(("attr", current.attr))
            current = current.value
            continue
        if isinstance(current, ast.Name):
            chain.append(("name", current.id))
            break
        return None
    chain.reverse()
    return chain


def resolve_chain(
    chain: list[tuple[str, str]],
    alias: str,
    symbols: Mapping[str, Symbol],
    classes: Mapping[str, ClassInfo],
) -> str | None:
    """Return the top-level qpane symbol name if resolvable."""
    if not chain or chain[0] != ("name", alias):
        return None
    for idx, (kind, name) in enumerate(chain[1:], start=1):
        if kind != "attr":
            continue
        if name in symbols:
            return name
        if idx == 1:
            return None
    return None


def record_location(locations: list[str], path: pathlib.Path, lineno: int) -> None:
    """Append path:line if not already present."""
    entry = f"{path.as_posix()}:{lineno}"
    if entry not in locations:
        locations.append(entry)


def analyze_demo_usage(
    symbols: Mapping[str, Symbol],
    classes: Mapping[str, ClassInfo],
    stub_symbols: set[str],
) -> tuple[dict[str, Usage], dict[str, ChainUsage], list[str]]:
    """Scan demo files for qpane usage."""
    usages: dict[str, Usage] = {}
    class_usage: dict[str, ChainUsage] = {}
    dynamic_notes: list[str] = []
    self_attr_classes: dict[str, dict[str, str]] = {}
    subclass_of: dict[str, str] = {}
    self_assigned_attrs: dict[str, set[str]] = {}
    all_subclass_members: dict[str, set[str]] = {}
    subclass_member_names: set[str] = set()

    def touch_class_chain(
        class_name: str,
        chain: list[tuple[str, str]],
        start_idx: int,
        path: pathlib.Path,
        lineno: int,
        subclass_name: str | None = None,
    ) -> None:
        """Record methods/properties along a chain for a given class context."""
        current_class = classes.get(class_name)
        if current_class is None:
            if class_name in all_subclass_members:
                return
            return
        record = class_usage.setdefault(
            current_class.name, ChainUsage(methods={}, properties={}, unresolved={})
        )
        idx = start_idx
        while current_class and idx < len(chain):
            kind, name = chain[idx]
            if kind != "attr":
                idx += 1
                continue
            next_is_call = idx + 1 < len(chain) and chain[idx + 1][0] == "call"
            if subclass_name and name in all_subclass_members.get(subclass_name, set()):
                idx += 2 if next_is_call else 1
                continue
            if name == current_class.name:
                idx += 1
                continue
            if name in current_class.methods:
                record.methods.setdefault(name, f"{path.as_posix()}:{lineno}")
                target = base_type_name(current_class.methods.get(name))
                if target and target in classes:
                    current_class = classes.get(target, current_class)
                    record = class_usage.setdefault(
                        current_class.name,
                        ChainUsage(methods={}, properties={}, unresolved={}),
                    )
                idx += 2 if next_is_call else 1
                continue
            if not next_is_call and (
                name in current_class.properties or name in current_class.attributes
            ):
                record.properties.setdefault(name, f"{path.as_posix()}:{lineno}")
                idx += 1
                continue
            if current_class.kind == "enum" and name in ENUM_BASE_ATTRS:
                idx += 2 if next_is_call else 1
                continue
            unresolved_entry = f"{current_class.name}.{name}"
            if subclass_name and name in all_subclass_members.get(subclass_name, set()):
                idx += 2 if next_is_call else 1
                continue
            if subclass_name and name in self_assigned_attrs.get(subclass_name, set()):
                idx += 2 if next_is_call else 1
                continue
            if (
                name not in QT_BASE_ATTRS
                and name not in SNAPSHOT_ATTR_NAMES
                and name not in subclass_member_names
                and unresolved_entry not in record.unresolved
            ):
                record.unresolved[unresolved_entry] = f"{path.as_posix()}:{lineno}"
            idx += 2 if next_is_call else 1

    def get_usage(name: str, kind: str, public: bool) -> Usage:
        if name not in usages:
            usages[name] = Usage(
                name=name, kind=kind, public=public, locations=[], via_qpane=False
            )
        return usages[name]

    demo_sources = collect_demo_sources()
    for source in demo_sources:
        path = source.path
        tree = ast.parse(source.source, filename=str(path))
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child.parent = parent  # type: ignore[attr-defined]
        aliases: set[str] = set()
        if source.inject_qpane_alias:
            aliases.add("qpane")
        from_imports: dict[str, str] = {}
        qpane_subclasses: set[str] = set()
        instance_names: set[str] = set()
        class_instances: dict[str, str] = {}
        subclass_members: dict[str, set[str]] = {}
        if source.inject_qpane_instance:
            instance_names.add("qpane")
            class_instances["qpane"] = "QPane"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    if n.name == "qpane":
                        aliases.add(n.asname or n.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "qpane":
                if node.names and node.names[0].name == "*":
                    dynamic_notes.append(
                        f"{path.as_posix()}:{node.lineno} uses from qpane import *"
                    )
                else:
                    for n in node.names:
                        from_imports[n.asname or n.name] = n.name
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("qpane.")
            ):
                module_tail = node.module.split("qpane.", 1)[1]
                for n in node.names:
                    symbol_name = n.asname or n.name
                    usage = get_usage(
                        f"{module_tail}.{symbol_name}", "module_import", False
                    )
                    record_location(usage.locations, path, node.lineno)
        # Gather qpane-like function args as instance names.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in list(node.args.args) + list(node.args.kwonlyargs):
                    annotation_text = None
                    if arg.annotation is not None:
                        try:
                            annotation_text = ast.unparse(arg.annotation)
                        except Exception:
                            annotation_text = None
                    if "qpane" in arg.arg.lower() or (
                        annotation_text and "QPane" in annotation_text
                    ):
                        instance_names.add(arg.arg)
                    if annotation_text:
                        for sym_name, sym in symbols.items():
                            if (
                                sym.kind in {"class", "enum", "protocol"}
                                and sym_name in annotation_text
                            ):
                                class_instances[arg.arg] = sym_name
        # Identify QPane and Catalog subclasses defined in this file.
        changed = True
        qpane_subclasses.add("QPane")
        qpane_subclasses.add("Catalog")
        while changed:
            changed = False
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if node.name in qpane_subclasses:
                    continue
                for base in node.bases:
                    if isinstance(base, ast.Attribute):
                        if (
                            isinstance(base.value, ast.Name)
                            and base.value.id in aliases
                        ):
                            if base.attr == "QPane" or base.attr == "Catalog":
                                qpane_subclasses.add(node.name)
                                changed = True
                                break
                    if isinstance(base, ast.Name) and base.id in qpane_subclasses:
                        qpane_subclasses.add(node.name)
                        changed = True
                        break
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in qpane_subclasses:
                members: set[str] = set()
                for member in node.body:
                    if isinstance(
                        member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    ):
                        members.add(member.name)
                    elif isinstance(member, ast.Assign):
                        for target in member.targets:
                            if isinstance(target, ast.Name):
                                members.add(target.id)
                    elif isinstance(member, ast.AnnAssign) and isinstance(
                        member.target, ast.Name
                    ):
                        members.add(member.target.id)
                subclass_members[node.name] = members
                base_name = None
                for base in node.bases:
                    if (
                        isinstance(base, ast.Attribute)
                        and isinstance(base.value, ast.Name)
                        and base.value.id in aliases
                    ):
                        if base.attr in {"QPane", "Catalog"}:
                            base_name = base.attr
                            break
                    if isinstance(base, ast.Name) and base.id in {"QPane", "Catalog"}:
                        base_name = base.id
                        break
                if base_name:
                    subclass_of[node.name] = base_name
        all_subclass_members.update(subclass_members)
        for names in subclass_members.values():
            subclass_member_names.update(names)
        # Track self.<attr> assignments to treat subclass-defined attributes as resolved.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not node.targets:
                continue
            target = node.targets[0]
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                attr_name = target.attr
                class_ctx = None
                parent = getattr(node, "parent", None)
                while parent:
                    if isinstance(parent, ast.ClassDef):
                        class_ctx = parent.name
                        break
                    parent = getattr(parent, "parent", None)
                if class_ctx:
                    self_assigned_attrs.setdefault(class_ctx, set()).add(attr_name)
        # Track self.<attr> that hold QPane instances inside classes.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not node.targets:
                continue
            target = node.targets[0]
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                attr_name = target.attr
                class_ctx = None
                parent = getattr(node, "parent", None)
                while parent:
                    if isinstance(parent, ast.ClassDef):
                        class_ctx = parent.name
                        break
                    parent = getattr(parent, "parent", None)
                if class_ctx is None:
                    continue
                value = node.value
                is_qpane_like = False
                candidate_class: str | None = None
                if isinstance(value, ast.Name) and value.id in instance_names:
                    is_qpane_like = True
                    candidate_class = class_instances.get(value.id, None)
                if isinstance(value, ast.Call):
                    callee = value.func
                    if isinstance(callee, ast.Attribute):
                        ch = extract_chain(callee)
                        if (
                            ch
                            and ch[0][1] in aliases
                            and (
                                ch[-1][1] in qpane_subclasses
                                or ch[-1][1].endswith("QPane")
                            )
                        ):
                            is_qpane_like = True
                            candidate_class = ch[-1][1]
                    elif isinstance(callee, ast.Name) and (
                        callee.id in qpane_subclasses or callee.id.endswith("QPane")
                    ):
                        is_qpane_like = True
                        candidate_class = callee.id
                if is_qpane_like:
                    class_map = self_attr_classes.setdefault(class_ctx, {})
                    class_map[attr_name] = candidate_class or "QPane"
        # Identify variables assigned from QPane() or QPane subclass constructors.
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Call):
                    target_names = [
                        t.id for t in node.targets if isinstance(t, ast.Name)
                    ]
                    callee = node.value.func
                    callee_name = None
                    if isinstance(callee, ast.Attribute):
                        ch = extract_chain(callee)
                        if (
                            ch
                            and ch[0][1] in aliases
                            and len(ch) >= 2
                            and (
                                ch[-1][1] in qpane_subclasses
                                or ch[-1][1].endswith("QPane")
                            )
                        ):
                            callee_name = ch[-1][1]
                    elif isinstance(callee, ast.Name) and (
                        callee.id in qpane_subclasses or callee.id.endswith("QPane")
                    ):
                        callee_name = callee.id
                    if callee_name:
                        instance_names.update(target_names)
                        for t in target_names:
                            class_instances[t] = callee_name
                    else:
                        # Handle from-imported qpane classes.
                        if isinstance(callee, ast.Name) and callee.id in from_imports:
                            target_sym = from_imports[callee.id]
                            if (
                                symbols.get(target_sym, None)
                                and symbols[target_sym].kind == "class"
                            ):
                                class_instances.update(
                                    {t: target_sym for t in target_names}
                                )
        if not aliases and not instance_names and "QPane" not in qpane_subclasses:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "getattr":
                    if (
                        node.args
                        and isinstance(node.args[0], ast.Name)
                        and node.args[0].id in aliases
                    ):
                        dynamic_notes.append(
                            f"{path.as_posix()}:{node.lineno} getattr on qpane alias"
                        )
                if isinstance(node.func, ast.Attribute):
                    attr_chain = extract_chain(node.func)
                    if attr_chain and attr_chain[0] == ("name", "importlib"):
                        dynamic_notes.append(
                            f"{path.as_posix()}:{node.lineno} importlib usage"
                        )
            if isinstance(node, ast.Attribute):
                parent = getattr(node, "parent", None)
                if isinstance(parent, ast.Call) and parent.func is node:
                    continue
            if not isinstance(node, (ast.Call, ast.Attribute)):
                continue
            chain = extract_chain(node)
            if not chain or chain[0][0] != "name":
                continue
            start_name = chain[0][1]
            lineno = getattr(node, "lineno", 0) or 0
            in_qpane_subclass = False
            parent = getattr(node, "parent", None)
            while parent:
                if isinstance(parent, ast.ClassDef) and parent.name in qpane_subclasses:
                    in_qpane_subclass = True
                    break
                parent = getattr(parent, "parent", None)
            subclass_name = None
            parent = getattr(node, "parent", None)
            while parent:
                if isinstance(parent, ast.ClassDef):
                    subclass_name = parent.name
                    break
                parent = getattr(parent, "parent", None)
            if start_name in aliases:
                top_name = resolve_chain(chain, start_name, symbols, classes)
                if not top_name:
                    continue
                symbol = symbols.get(top_name)
                usage = get_usage(
                    top_name,
                    symbol.kind if symbol else "unknown",
                    symbol.public if symbol else False,
                )
                usage.via_qpane = True
                record_location(usage.locations, path, lineno)
                if symbol and symbol.kind in {"class", "enum", "protocol"}:
                    class_idx = next(
                        (
                            i
                            for i, (k, n) in enumerate(chain)
                            if k == "attr" and n == symbol.name
                        ),
                        1,
                    )
                    touch_class_chain(symbol.name, chain, class_idx, path, lineno)
            elif start_name in from_imports:
                top_name = from_imports[start_name]
                symbol = symbols.get(top_name)
                usage = get_usage(
                    top_name,
                    symbol.kind if symbol else "unknown",
                    symbol.public if symbol else False,
                )
                record_location(usage.locations, path, lineno)
                if symbol and symbol.kind in {"class", "enum", "protocol"}:
                    touch_class_chain(symbol.name, chain, 1, path, lineno)
            elif start_name in instance_names or (
                start_name == "self" and in_qpane_subclass
            ):
                class_info = classes.get("QPane")
                if not class_info:
                    continue
                touch_class_chain(
                    "QPane", chain, 1, path, lineno, subclass_name=subclass_name
                )
            elif start_name in class_instances:
                class_name = class_instances[start_name]
                touch_class_chain(
                    class_name, chain, 1, path, lineno, subclass_name=subclass_name
                )
            elif start_name == "super" and len(chain) > 2 and chain[2][0] == "attr":
                # Handle super().method() calls
                # Chain: [('name', 'super'), ('call', '()'), ('attr', 'method'), ...]
                method_name = chain[2][1]
                # Check if this is a violation (calling internal method via super)
                is_violation = False
                if subclass_name:
                    base_class_name = subclass_of.get(subclass_name)
                    if base_class_name and base_class_name in classes:
                        base_class_info = classes[base_class_name]
                        # If method is defined in implementation...
                        if method_name in base_class_info.methods:
                            # ...but NOT in public stub
                            stub_key = f"{base_class_name}.{method_name}"
                            if stub_key not in stub_symbols:
                                is_violation = True
                if is_violation:
                    dynamic_notes.append(
                        f"{path.as_posix()}:{lineno} Demo uses super().{method_name}() - inheritance is discouraged in demos."
                    )
                if subclass_name:
                    # Find what this class inherits from
                    base_class = subclass_of.get(subclass_name)
                    if base_class and base_class in classes:
                        touch_class_chain(
                            base_class,
                            chain,
                            2,  # Skip 'super' and '()'
                            path,
                            lineno,
                            subclass_name=subclass_name,
                        )
            elif start_name == "self" and len(chain) > 1 and chain[1][0] == "attr":
                mapped = self_attr_classes.get(subclass_name or "", {})
                target_class = mapped.get(chain[1][1])
                if target_class:
                    if target_class in classes:
                        touch_class_chain(
                            target_class,
                            chain,
                            2,
                            path,
                            lineno,
                            subclass_name=subclass_name,
                        )
                    elif target_class in subclass_of and subclass_of[target_class] in {
                        "QPane",
                        "Catalog",
                    }:
                        touch_class_chain(
                            subclass_of[target_class],
                            chain,
                            2,
                            path,
                            lineno,
                            subclass_name=target_class,
                        )
                    elif target_class.endswith("QPane"):
                        touch_class_chain(
                            "QPane",
                            chain,
                            2,
                            path,
                            lineno,
                            subclass_name=target_class,
                        )
    return usages, class_usage, dynamic_notes


# --- Consistency Checks ---


def build_symbol_pattern(classes: set[str]) -> Pattern[str]:
    """Return a regex that matches facade-style symbols for the given classes."""
    if not classes:
        return re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_]+\b")
    joined = "|".join(sorted(re.escape(cls) for cls in classes))
    # Match "Class.Member" OR just "Class"
    return re.compile(rf"\b(?:{joined})(?:\.[A-Za-z0-9_]+)?\b")


def extract_symbols_from_text(text: str, pattern: Pattern[str]) -> list[str]:
    """Return all facade symbols found in the provided text (with duplicates)."""
    return pattern.findall(text)


REFERENCE_HEADING_RE = re.compile(
    r"\b(?:API Reference|Full API|Field Reference|Fields)\b",
    re.IGNORECASE,
)
SYMBOL_LIST_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:`)?[A-Z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)?(?:`)?"
    r"\s*(?:[:\-–—]?\s*)?$"
)
CODE_SPAN_RE = re.compile(r"`[^`]*`")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")


def parse_markdown_blocks(path: Path) -> list[MarkdownBlock]:
    """Parse markdown into coarse blocks for prose-floor documentation checks."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[MarkdownBlock] = []
    heading_stack: list[tuple[int, str, bool]] = []
    paragraph: list[str] = []
    paragraph_line = 0
    in_code = False
    code_lines: list[str] = []
    code_line = 0

    def active_heading_path() -> tuple[str, ...]:
        return tuple(heading for _level, heading, _rejected in heading_stack)

    def active_rejected() -> bool:
        return any(rejected for _level, _heading, rejected in heading_stack)

    def flush_paragraph() -> None:
        nonlocal paragraph, paragraph_line
        if not paragraph:
            return
        text = " ".join(part.strip() for part in paragraph).strip()
        if text:
            blocks.append(
                MarkdownBlock(
                    kind="paragraph",
                    text=text,
                    path=path,
                    line=paragraph_line,
                    heading_path=active_heading_path(),
                    rejected_section=active_rejected(),
                )
            )
        paragraph = []
        paragraph_line = 0

    def flush_code() -> None:
        nonlocal code_lines, code_line
        if not code_lines:
            return
        blocks.append(
            MarkdownBlock(
                kind="code",
                text="\n".join(code_lines),
                path=path,
                line=code_line,
                heading_path=active_heading_path(),
                rejected_section=active_rejected(),
            )
        )
        code_lines = []
        code_line = 0

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_paragraph()
                in_code = True
                code_line = lineno
                code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append(
                (level, title, bool(REFERENCE_HEADING_RE.search(title)))
            )
            blocks.append(
                MarkdownBlock(
                    kind="heading",
                    text=title,
                    path=path,
                    line=lineno,
                    heading_path=active_heading_path(),
                    rejected_section=active_rejected(),
                )
            )
            continue
        if not stripped:
            flush_paragraph()
            continue
        list_item = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$", line)
        if list_item:
            flush_paragraph()
            blocks.append(
                MarkdownBlock(
                    kind="list",
                    text=list_item.group(1).strip(),
                    path=path,
                    line=lineno,
                    heading_path=active_heading_path(),
                    rejected_section=active_rejected(),
                )
            )
            continue
        if not paragraph:
            paragraph_line = lineno
        paragraph.append(line)
    flush_paragraph()
    if in_code:
        flush_code()
    return mark_rejected_symbol_list_runs(blocks)


def mark_rejected_symbol_list_runs(blocks: list[MarkdownBlock]) -> list[MarkdownBlock]:
    """Mark long consecutive bare-symbol list runs as rejected coverage."""
    result = list(blocks)
    run: list[int] = []

    def flush_run() -> None:
        nonlocal run, result
        if len(run) > 12:
            for idx in run:
                block = result[idx]
                result[idx] = dataclasses.replace(block, rejected_list_run=True)
        run = []

    for idx, block in enumerate(result):
        if block.kind == "list" and SYMBOL_LIST_RE.match(block.text):
            run.append(idx)
        else:
            flush_run()
    flush_run()
    return result


def explanatory_word_count(text: str, symbol_pattern: Pattern[str]) -> int:
    """Return a conservative count of non-symbol words in markdown text."""
    cleaned = CODE_SPAN_RE.sub(" ", text)
    cleaned = symbol_pattern.sub(" ", cleaned)
    tokens = TOKEN_RE.findall(cleaned)
    ignored = {"http", "https", "md", "py", "true", "false", "none"}
    return sum(1 for token in tokens if token.lower() not in ignored)


def block_is_bare_symbol_mention(
    block: MarkdownBlock, symbol_pattern: Pattern[str]
) -> bool:
    """Return True when a markdown block is mostly public symbols and syntax."""
    symbol_tokens = extract_symbols_from_text(block.text, symbol_pattern)
    if not symbol_tokens:
        return False
    word_count = explanatory_word_count(block.text, symbol_pattern)
    return word_count < len(symbol_tokens)


def block_has_explanatory_prose(
    block: MarkdownBlock,
    symbol_pattern: Pattern[str],
    *,
    minimum_words: int = 8,
) -> bool:
    """Return True when a block has enough non-symbol words to count as prose."""
    if block.rejected_section or block.rejected_list_run:
        return False
    if block.kind not in {"paragraph", "list"}:
        return False
    if SYMBOL_LIST_RE.match(block.text):
        return False
    if block_is_bare_symbol_mention(block, symbol_pattern):
        return False
    return explanatory_word_count(block.text, symbol_pattern) >= minimum_words


def collect_valid_guide_symbols(
    paths: Iterable[Path],
    symbol_pattern: Pattern[str],
) -> tuple[set[str], list[str]]:
    """Return guide symbols that appear with explanatory context and quality errors."""
    symbols: set[str] = set()
    errors: list[str] = []
    for path in paths:
        blocks = parse_markdown_blocks(path)
        for block in blocks:
            if block.kind == "heading" and block.rejected_section:
                errors.append(
                    f"[Guides] Reference-style heading in {path.name}:{block.line}: {block.text}"
                )
            if block.kind == "list" and block.rejected_list_run:
                errors.append(
                    f"[Guides] Symbol dump in {path.name}:{block.line}; rewrite as workflow prose"
                )
        for idx, block in enumerate(blocks):
            block_symbols = set(extract_symbols_from_text(block.text, symbol_pattern))
            if not block_symbols:
                continue
            if block_has_explanatory_prose(block, symbol_pattern):
                symbols.update(block_symbols)
                continue
            if block.kind == "code" and not block.rejected_section:
                nearby = (
                    blocks[max(0, idx - 2) : idx]
                    + blocks[idx + 1 : min(len(blocks), idx + 3)]
                )
                for name in block_symbols:
                    if any(
                        name
                        in extract_symbols_from_text(candidate.text, symbol_pattern)
                        and block_has_explanatory_prose(candidate, symbol_pattern)
                        for candidate in nearby
                    ):
                        symbols.add(name)
                continue
            if block.kind in {"paragraph", "list"} and (
                block.rejected_section
                or block.rejected_list_run
                or SYMBOL_LIST_RE.match(block.text)
                or block_is_bare_symbol_mention(block, symbol_pattern)
            ):
                joined = ", ".join(sorted(block_symbols))
                errors.append(
                    f"[Guides] Bare symbol mention in {path.name}:{block.line}: {joined}"
                )
    return symbols, errors


def collect_api_reference_symbols(
    path: Path,
    symbol_pattern: Pattern[str],
) -> tuple[set[str], list[str]]:
    """Return API reference symbols that have a short same-block explainer."""
    if not path.exists():
        return set(), [f"API Reference file missing: {API_REFERENCE_NAME}"]
    symbols: set[str] = set()
    errors: list[str] = []
    for block in parse_markdown_blocks(path):
        block_symbols = set(extract_symbols_from_text(block.text, symbol_pattern))
        if not block_symbols:
            continue
        if "](" in block.text:
            continue
        if block.kind == "heading":
            symbols.update(block_symbols)
            continue
        if block.kind not in {"paragraph", "list"}:
            continue
        if explanatory_word_count(block.text, symbol_pattern) >= 2:
            symbols.update(block_symbols)
        else:
            joined = ", ".join(sorted(block_symbols))
            errors.append(
                f"[API Reference] Missing short explainer in {path.name}:{block.line}: {joined}"
            )
    return symbols, errors


def collect_doc_symbols(
    paths: Iterable[Path], pattern: Pattern[str]
) -> tuple[set[str], dict[str, int], dict[str, dict[str, int]]]:
    """Scan markdown files for facade symbols and return symbol, count, and location maps."""
    symbols: set[str] = set()
    counts: dict[str, int] = {}
    locations: dict[str, dict[str, int]] = {}
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for name in extract_symbols_from_text(text, pattern):
            symbols.add(name)
            counts[name] = counts.get(name, 0) + 1
            file_counts = locations.setdefault(name, {})
            file_counts[path.name] = file_counts.get(path.name, 0) + 1
    return symbols, counts, locations


def check_demo_consistency(
    usages: dict, class_usage: dict, classes: dict, stub_symbols: set[str]
) -> list[str]:
    """Check if demo uses any internal symbols (defined in impl but hidden in stub)."""
    errors = []
    # Check top-level symbols
    for name, usage in usages.items():
        if not usage.public:
            errors.append(
                f"Demo uses internal symbol: {name} (at {usage.locations[0]})"
            )
    # Check class members
    for cls_name, record in class_usage.items():
        if cls_name not in classes:
            continue
        impl_class = classes[cls_name]
        # Check methods
        for method, loc in record.methods.items():
            full_name = f"{cls_name}.{method}"
            if full_name in stub_symbols:
                continue
            # It's used in demo, but not in stub.
            # Is it defined in our implementation?
            if method in impl_class.methods:
                errors.append(
                    f"Demo uses hidden method (not in .pyi): {full_name} (at {loc})"
                )
            else:
                # It's likely an inherited method (e.g. QWidget.show).
                # We allow this unless we want to force stubs for all inherited methods too.
                pass
        # Check properties
        for prop, loc in record.properties.items():
            full_name = f"{cls_name}.{prop}"
            if full_name in stub_symbols:
                continue
            if prop in impl_class.properties or prop in impl_class.attributes:
                errors.append(
                    f"Demo uses hidden property (not in .pyi): {full_name} (at {loc})"
                )
    return errors


def get_stub_symbols(stub_path: Path) -> set[str]:
    """Extract all defined classes and methods from the .pyi file."""
    symbols = set()
    if not stub_path.exists():
        return symbols
    tree = ast.parse(stub_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.add(node.name)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.add(f"{node.name}.{member.name}")
                elif isinstance(member, ast.AnnAssign) and isinstance(
                    member.target, ast.Name
                ):
                    symbols.add(f"{node.name}.{member.target.id}")
                elif isinstance(member, ast.Assign):
                    # Handle assignments in class body (e.g. constants or properties defined as assign)
                    for target in member.targets:
                        if isinstance(target, ast.Name):
                            symbols.add(f"{node.name}.{target.id}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
    return symbols


def check_impl_consistency(
    stub_symbols: set[str], impl_symbols: dict, impl_classes: dict
) -> list[str]:
    """Check if all stubbed symbols exist in the implementation."""
    errors = []
    for symbol in stub_symbols:
        # Ignore private symbols (starting with _)
        if symbol.startswith("_") or "._" in symbol:
            continue
        if "." not in symbol:
            # Top-level symbol
            if symbol not in impl_symbols:
                # It might be a type alias or something not captured as a "symbol" by api_scanner?
                # api_scanner captures classes, functions, constants.
                # It also captures lazy symbols.
                errors.append(f"Stubbed symbol not found in implementation: {symbol}")
        else:
            # Class member
            cls_name, member_name = symbol.split(".", 1)
            if cls_name not in impl_classes:
                # If the class itself is missing, we likely reported it above.
                continue
            cls_info = impl_classes[cls_name]
            # Check methods, properties, attributes
            if (
                member_name not in cls_info.methods
                and member_name not in cls_info.properties
                and member_name not in cls_info.attributes
            ):
                errors.append(f"Stubbed member not found in implementation: {symbol}")
    return errors


def check_doc_consistency(
    stub_symbols: set[str], public_symbols: set[str]
) -> list[str]:
    """Check if all stubbed symbols are documented in both Reference and Guides."""
    errors = []
    # We expect documentation for everything in the stub,
    # PLUS the top-level public symbols (classes themselves).
    expected = set(stub_symbols)
    expected.update(public_symbols)
    # Filter out things that look like private members if they slipped into stubs
    expected = {s for s in expected if not s.split(".")[-1].startswith("_")}
    # Scan docs
    class_names = {name.split(".")[0] for name in expected}
    class_names.update(public_symbols)
    symbol_pattern = build_symbol_pattern(class_names)
    # Split docs into Reference and Guides
    api_ref_path = DOCS_DIR / API_REFERENCE_NAME
    guide_paths = [p for p in DOCS_DIR.glob("*.md") if p.name != API_REFERENCE_NAME]
    # 1. Check API Reference
    if api_ref_path.exists():
        ref_symbols, ref_quality_errors = collect_api_reference_symbols(
            api_ref_path, symbol_pattern
        )
        errors.extend(ref_quality_errors)
        missing_ref = expected - ref_symbols
        for name in sorted(missing_ref):
            errors.append(f"[API Reference] Missing: {name}")
        extra_ref = ref_symbols - expected
        for name in sorted(extra_ref):
            errors.append(f"[API Reference] Ghost (not in API): {name}")
    else:
        errors.append(f"API Reference file missing: {API_REFERENCE_NAME}")
    # 2. Check Guides
    guide_symbols, guide_quality_errors = collect_valid_guide_symbols(
        guide_paths, symbol_pattern
    )
    errors.extend(guide_quality_errors)
    missing_guide = expected - guide_symbols
    for name in sorted(missing_guide):
        errors.append(f"[Guides] Missing: {name}")
    all_guide_symbols, _, _ = collect_doc_symbols(guide_paths, symbol_pattern)
    extra_guide = all_guide_symbols - expected
    for name in sorted(extra_guide):
        errors.append(f"[Guides] Ghost (not in API): {name}")
    return errors


def check_config_docs_consistency() -> list[str]:
    """Check if configuration-reference.md matches the actual Config defaults."""
    import qpane.core.config

    # Silence psutil warning during static analysis
    qpane.core.config._PSUTIL_WARNING_EMITTED = True
    from qpane.core.config import Config
    from dataclasses import is_dataclass, asdict

    errors = []
    doc_path = DOCS_DIR / "configuration-reference.md"
    if not doc_path.exists():
        return [f"Config reference file missing: {doc_path}"]
    content = doc_path.read_text(encoding="utf-8")
    # Extract the python code block
    code_blocks = []
    in_block = False
    current_block = []
    for line in content.splitlines():
        if line.strip().startswith("```python"):
            in_block = True
            continue
        if line.strip().startswith("```") and in_block:
            in_block = False
            code_blocks.append("\n".join(current_block))
            current_block = []
            continue
        if in_block:
            current_block.append(line)
    if not code_blocks:
        return ["No python code blocks found in configuration-reference.md"]
    config_code = code_blocks[0]
    # Parse the dict
    try:
        ns = {}
        exec(config_code, {}, ns)
        doc_config = ns.get("config")
    except Exception as e:
        return [f"Error parsing config from docs: {e}"]
    if not isinstance(doc_config, dict):
        return ["'config' variable in docs is not a dict"]
    actual_config = Config().as_dict()
    # Helper for deep comparison

    def check_dict(path: str, doc_d: dict, actual_d: dict):
        # Check for missing keys in docs
        for k, v in actual_d.items():
            if is_dataclass(v):
                v = asdict(v)
            # Special exemption for cache overrides which are flattened in to_dict
            # but might not be in the doc example if they are considered advanced/internal
            if path == "cache" and k in ("tiles", "pyramids", "masks", "predictors"):
                if k not in doc_d:
                    continue
            if k not in doc_d:
                errors.append(f"[Config Reference] Missing key at {path}: {k}")
                continue
            doc_val = doc_d[k]
            if isinstance(v, dict) and isinstance(doc_val, dict):
                check_dict(f"{path}.{k}", doc_val, v)
            else:
                # Value compare
                match = False
                if v == doc_val:
                    match = True
                elif isinstance(v, float) and isinstance(doc_val, (float, int)):
                    if abs(v - doc_val) < 1e-9:
                        match = True
                elif isinstance(v, tuple) and isinstance(doc_val, list):
                    if v == tuple(doc_val):
                        match = True
                if not match:
                    errors.append(
                        f"[Config Reference] Value mismatch at {path}.{k}: Doc={doc_val!r}, Actual={v!r}"
                    )
        # Check for extra keys in docs
        for k in doc_d:
            if k not in actual_d:
                errors.append(f"[Config Reference] Extra key at {path}: {k}")

    check_dict("root", doc_config, actual_config)
    return errors


def main() -> None:
    print("Running Consistency Checks (Filter Mode)...")
    # 1. Load Data
    print("Loading implementation and stubs...")
    public_symbols, lazy_symbols = load_public_symbols(QPANE_ROOT / "__init__.py")
    symbols, classes = build_package_index(public_symbols, lazy_symbols)
    stub_symbols = get_stub_symbols(STUB_FILE)
    all_errors = []
    # 2. Demo Check (Compliance)
    print("Checking demo compliance...")
    usages, class_usage, dynamic_notes = analyze_demo_usage(
        symbols, classes, stub_symbols
    )
    demo_errors = check_demo_consistency(usages, class_usage, classes, stub_symbols)
    # Add dynamic notes (like super() usage) to errors
    for note in dynamic_notes:
        if "Demo uses super()" in note:
            demo_errors.append(f"Demo inheritance violation: {note}")
    if demo_errors:
        print(f"  Found {len(demo_errors)} demo compliance issues.")
        all_errors.extend(demo_errors)
    else:
        print("  Demo compliant.")
    # 3. Doc Check (Completeness & Ghosts)
    print("Checking documentation completeness...")
    doc_errors = check_doc_consistency(stub_symbols, public_symbols)
    if doc_errors:
        print(f"  Found {len(doc_errors)} documentation issues.")
        all_errors.extend(doc_errors)
    else:
        print("  Docs complete.")
    # 4. Impl Check (Reality)
    print("Checking implementation reality...")
    impl_errors = check_impl_consistency(stub_symbols, symbols, classes)
    if impl_errors:
        print(f"  Found {len(impl_errors)} implementation issues.")
        all_errors.extend(impl_errors)
    else:
        print("  Implementation matches stubs.")
    # 5. Config Check (Defaults)
    print("Checking config reference accuracy...")
    config_errors = check_config_docs_consistency()
    if config_errors:
        print(f"  Found {len(config_errors)} config reference issues.")
        all_errors.extend(config_errors)
    else:
        print("  Config reference matches defaults.")
    print("-" * 40)
    if all_errors:
        print(f"FAILED: Found {len(all_errors)} consistency errors.")
        for err in all_errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("SUCCESS: All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
