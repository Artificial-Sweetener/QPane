# Maintenance Tools

This directory contains scripts to enforce code quality, architectural boundaries, and documentation consistency. These tools are designed to be run during CI or before committing changes.

## 1. `check_consistency.py` (The "Trinity" Check)

This is the primary validation tool for the project. It treats `qpane.pyi` (the public stub file) as the single source of truth and verifies that the implementation, documentation, and examples all agree with it.

**Usage:**
```bash
python tools/check_consistency.py
```

**Checks Performed:**
- **Implementation Reality:** Verifies that every method defined in `qpane.pyi` actually exists in `qpane.py`.
- **Demo Compliance:** Scans `examples/` to ensure demo code *only* uses public APIs exposed in `qpane.pyi`. It flags usage of internal methods or hidden attributes.
- **Documentation Completeness:** Verifies that every public symbol in `qpane.pyi` is mentioned in the Markdown guides in `docs/`.
- **Config Accuracy:** Compares the default values shown in `docs/configuration-reference.md` against the actual `Config` class defaults in the code.

**Output:**
- `SUCCESS`: All checks passed.
- `FAILED`: Lists specific violations (e.g., "Demo uses hidden method", "Missing doc for symbol X").

## 2. `check_api_order.py`

Enforces the physical layout of the main `qpane.py` file to match the project's architectural guidelines.

**Usage:**
```bash
python tools/check_api_order.py
```

**Checks Performed:**
- **Public API Visibility:** Ensures all methods defined in `qpane.pyi` are physically located *above* the `# Internal Implementation` banner in `qpane.py`.
- **Internal Encapsulation:** Ensures all methods *not* in `qpane.pyi` are located *below* the banner.

**Output:**
- `SUCCESS`: File layout is correct.
- `[FAIL] API Organization Violation`: Lists methods that are in the wrong section (Hidden Public API or Leaking Internal API).

## 3. `check_docstrings.py`

A linter that enforces the project's documentation standards.

**Usage:**
```bash
python tools/check_docstrings.py
```

**Checks Performed:**
- Scans `qpane/` and `examples/` directories.
- Ensures every module, class, and function has a docstring.
- Skips property setters (assuming the getter is documented) and empty `__init__.py` files.

**Output:**
- `SUCCESS`: No missing docstrings found.
- `FAILED`: Lists files and line numbers where docstrings are missing, along with a summary of the guidelines.

## 4. `add_license_headers.py`

Automates copyright compliance for the project.

**Usage:**
```bash
python tools/add_license_headers.py
```

**Actions:**
- Scans all git-tracked `.py` and `.pyi` files.
- Adds the standard GPLv3-or-later license header if it is missing.
- Updates the header if an older version is detected.

**Output:**
- Prints the path of any file that was updated or added.

## 5. `fix_encoding.py`

Ensures cross-platform compatibility by enforcing UTF-8 encoding.

**Usage:**
```bash
python tools/fix_encoding.py
```

**Actions:**
- Attempts to read every tracked Python file as UTF-8.
- If reading fails, it tries fallback encodings (like `cp1252` or `latin1`).
- If a fallback succeeds, it re-saves the file as valid UTF-8.

**Output:**
- Reports files that were converted or files that could not be recovered.
