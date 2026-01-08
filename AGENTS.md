# Assistant Engineering Guidelines

You are contributing to `QPane`, a high-performance production library. Your primary goal is **Stability, Consistency, and Polish**.

**IMPORTANT:** You are bound by the rules in `CONTRIBUTING.md`. Read it. It defines the architectural boundaries, naming conventions, and strict tooling requirements that apply to all contributors, human or AI.

## 1. The Prime Directive: Production Quality
*   **Stability > Velocity:** Prioritize robust, safe code over quick fixes.
*   **Zero Debt:** Never commit `print()` statements, commented-out code, or temporary `TODO`s.
*   **Graceful Failure:** The application must never crash. Handle errors at the appropriate boundary.

## 2. Architecture & Separation of Concerns
The `QPane` widget is a **Facade**. Keep it thin. Delegate logic to these subsystems:

*   **Core (`qpane/core/`)**: `QPaneState` (lifecycle), `Config` (settings), `FeatureRegistry` (plugins).
*   **Catalog (`qpane/catalog/`)**: Image data model. `Catalog` (facade), `CatalogController` (logic).
*   **Rendering (`qpane/rendering/`)**: `RenderingPresenter` (draw pipeline), `Viewport` (transforms).
*   **Tools (`qpane/tools/`)**: `ToolInteractionDelegate` (input handling). Tool logic lives in isolated classes.
*   **Masks (`qpane/masks/`)**: `MaskService` (facade for masking/SAM).
*   **Concurrency (`qpane/concurrency/`)**: `TaskExecutor` handles all heavy lifting. **Never block the UI thread.**

## 3. The Trinity: Consistency is Mandatory
The "Trinity" ensures the Public API is consistent across four pillars. **When one changes, they ALL change.**

0.  **Contract (`qpane.pyi`):** The frozen public API definition.
1.  **Implementation (`qpane.py`):** The code itself.
2.  **Documentation (`docs/`):** The user manuals.
3.  **Demonstration (`examples/`):** The tutorialized proof-of-concept.

**Rule:** You must update all four in the same turn. Never leave the demo or docs "for later."

**Demo Style:** Demos must be "tutorialized"—clean, readable code that teaches the user how to use the new feature (see `examples/demonstration/`).

**Strict Constraint:** Demos must rely *exclusively* on the public API defined in `qpane.pyi`. Never reach into private internals (`_underscore_methods`) from example code.

**Docs Guardrail:** Documentation is for host developers using the public facade. Describe only supported API and behaviors; never mention internal wiring or unsupported swaps (e.g., replacing managers).

## 4. Compatibility & Refactoring Strategy
We distinguish strictly between the **Public API** and the **Internal Implementation**.

### Public API: Frozen & Sacred
Defined by `qpane.pyi` and `qpane/__init__.py`.
*   **Rule:** **NEVER** break the public contract.
*   **Verification:** The "Trinity" check ensures `qpane.py` (impl), `qpane.pyi` (stub), and `docs/` align.
*   **Changes:** If you must change the public API, you must update the stub (`.pyi`), documentation (`docs/`), and demonstration (`examples/`) in the same turn.

### Internal Implementation: Fluid & Clean
Internal modules (`qpane.core`, `qpane.masks`, etc.) are **NOT** subject to backward compatibility rules within the library.
*   **Rule:** Refactor ruthlessly for quality.
*   **NO SHIMS:** Do not leave backward-compatibility shims (e.g., `def old(): return new()`) in internal code.
*   **Complete Refactors:** If you change an internal signature, you **MUST** find and update **ALL** internal callers immediately.
*   **Outcome:** The codebase should look as if the new design was the original design.

## 5. Coding Standards
*   **Type Hints:** Mandatory for all new code. Use `typing.TYPE_CHECKING` to avoid circular imports.
*   **Docstrings:** Mandatory.
    *   *Public:* Google-style sections (`Args:`, `Returns:`, `Side effects:`).
    *   *Internal:* Concise summary.
*   **Self-Documenting Code:**
    *   **Code tells the "What":** Logic should be clear enough to read like a sentence. If a block is complex, extract it into a named method.
    *   **Comments:** Use *only* for non-obvious logic or complex constraints. Docstrings and naming should cover the rest.
*   **Naming:**
    *   *Principle:* **Precise and Self-Documenting.** Names should be unambiguous but concise. Avoid generic terms (`data`, `obj`) and cryptic abbreviations.
    *   Public Widget Methods: `camelCase` (matches Qt).
    *   Internal Logic/Helpers: `snake_case` (standard Python).
    *   Enums: `PascalCase` classes, `UPPER_CASE` members.
*   **Module Layout:**
    *   **Preamble:** Docstring -> Imports -> Logger.
    *   **Public Interface:** Constants -> Enums -> Exceptions.
    *   **Implementation:** Main Classes/Functions first.
    *   **Internals:** Private helpers last.
*   **Class Layout:**
    *   **Public First:** `__init__` -> Public Methods -> Properties.
    *   **Group by Intent:** Keep related logic together (e.g., all Zoom methods).
    *   **Internals Last:** Private methods at the bottom.
*   **The Banner (`qpane.py` ONLY):**
    *   Public methods **ABOVE** the `# Internal Implementation` banner.
    *   Internal methods **BELOW** the banner.
    *   **Note:** Do not use this banner pattern in any other module.

## 6. Drafting Commit Messages
**DO NOT COMMIT CODE AUTOMATICALLY.**
When asked to commit, you should draft a commit message for the user following the Conventional Commits standard. This helps the user verify the scope of your changes.

Format: `type(scope): subject`
*   `feat`: New feature (Minor bump).
*   `fix`: Bug fix (Patch bump).
*   `docs`: Documentation only.
*   `style`: Formatting/whitespace.
*   `refactor`: Code change that neither fixes a bug nor adds a feature.
*   `perf`: Performance improvement.
*   `test`: Adding/fixing tests.
*   `chore`: Build/tooling changes.
*   **BREAKING CHANGE:** Append `!` (e.g., `feat(api)!:`) for Major bump.

## 7. Verification (The Safety Net)
You must run the same checks as the git hooks before reporting success. Always run these in the `.venv`.

1.  **Format & Lint:**
    ```powershell
    .venv\Scripts\python -m ruff check --fix .
    .venv\Scripts\python -m black .
    ```
2.  **Project Tools (Mandatory):**
    ```powershell
    .venv\Scripts\python tools\fix_encoding.py
    .venv\Scripts\python tools\check_docstrings.py
    .venv\Scripts\python tools\check_api_order.py
    .venv\Scripts\python tools\check_consistency.py
    .venv\Scripts\python tools\add_license_headers.py
    ```
3.  **Test (Parallelized):**
    ```powershell
    .venv\Scripts\python -m pytest -n auto
    ```
    **Note:** Allow a longer timeout for this command in automation/harness runs so the
    parallel suite can complete cleanly.
    **Do not ignore failures.** If tests fail, fix them.
