# Assistant Engineering Guidelines

You are contributing to `QPane`, a high-performance production library. Your primary goal is **Stability, Consistency, and Polish**.

**IMPORTANT:** You are bound by the rules in `CONTRIBUTING.md`. Read it. It defines the architectural boundaries, naming conventions, and strict tooling requirements that apply to all contributors, human or AI.

## 1. The Prime Directive: Production Quality
*   **Stability > Velocity:** Prioritize robust, safe code over quick fixes.
*   **Zero Debt:** Never commit `print()` statements, commented-out code, or temporary `TODO`s.
*   **Graceful Failure:** The application must never crash. Handle errors at the appropriate boundary.

## 2. Architecture & Separation of Concerns
The `QPane` widget is a **Facade**. Keep it thin: public widget methods validate
inputs, preserve the public contract, and delegate to the owner of the concern.

### Architecture Guidance Is Living
The architecture guidance below describes QPane's current ownership map. It is
not a freeze on future structure.

When reassessment shows that a concern belongs somewhere else, update this
architecture section in the same change that moves the boundary. Do not preserve
an outdated boundary because it is documented here.

Update this section only when ownership or dependency boundaries change, such as
when a concern gains a new authoritative owner, a subsystem is split or
extracted, or a new architectural layer becomes part of the intended design.

### Ownership Reassessment Before Editing
Before extending an existing class, module, subsystem, or workflow, reassess
whether the current owner is still the correct owner for the concern being
changed.

Do not assume the existing location is correct because related code is already
there. Identify the concern, its authoritative owner, and the dependency
direction before editing.

If the change introduces a distinct responsibility, change cadence, state owner,
collaboration boundary, or public/private API boundary, split or extract that
responsibility as part of the change instead of deferring cleanup.

If behavior spans multiple components, trace the current ownership and data flow
before editing. Prefer correcting the ownership model over layering
compensating patches across consumers.

Place new code by ownership and dependency direction, not convenience,
proximity, or minimal diff size.

### Object-Oriented Ownership
Use strict object-oriented design for stateful behavior and system
collaboration. A class, service, controller, presenter, or domain object that
owns state must also own the behavior that mutates, interprets, validates, or
coordinates that state.

Collaborators should communicate through explicit public methods, protocols, or
injected dependencies. Do not reach into private collaborator attributes or
duplicate another component's state, geometry, lifecycle, cache policy, input
policy, rendering rules, or workflow rules.

Stateless helper functions and value types are acceptable only when they clarify
ownership and do not hide responsibilities that belong to a stateful owner.

### DRY Means Single Ownership
Favor DRY when it reduces repeated change risk, but do not create abstractions
that obscure ownership or intent.

The most important duplication to remove is duplicated responsibility. State,
geometry, lifecycle, cache policy, input policy, rendering rules, and workflow
rules must have one authoritative owner.

Other components may observe, delegate to, adapt, or cache derived results from
that owner, but must not re-implement the concern in parallel. If multiple
components need the same behavior, move it to the owner or extract a new owner
with an explicit boundary.

### Current Ownership Map
The current ownership map is:

*   **Core (`qpane/core/`)**: `QPaneState` owns lifecycle, configuration application, and feature installation. `Config` owns settings. `FeatureRegistry` owns plugin registration.
*   **Catalog (`qpane/catalog/`)**: `Catalog` owns host-facing image identity, paths, and navigation. `CatalogController` and `ImageCatalog` own catalog mutation and bookkeeping.
*   **Composition (`qpane/composition/`)**: `CompositionService` owns public composition records, browser order, generated default image compositions, layered scene composition records, active composition selection, composition-scoped comparison state, and conversion from stored layered compositions into private scene-provider contributions.
*   **Scene (`qpane/scene/`)**: Internal scene descriptors, layer descriptors, layer sources, placement, hit-test metadata, scene/layer identity, provider/resolver contracts, internal scene mutation routing, private multi-image layout state, scene-layer selection state, and future render-plan snapshot types.
*   **Rendering (`qpane/rendering/`)**: `RenderingPresenter` owns draw orchestration and render-work planning. `Viewport` owns pan/zoom state, transforms, and coordinate conversion. Visibility planning owns layer-visible source geometry used to cull tile work before painting.
*   **Tools (`qpane/tools/`)**: `ToolInteractionDelegate` owns input routing and tool activation. Tool classes own tool-specific behavior.
*   **Masks (`qpane/masks/`)**: `MaskService` owns mask workflows, state transitions, async mask operations, autosave, and undo integration.
*   **SAM (`qpane/sam/`)**: `SamManager` owns predictor lifecycle and checkpoint readiness. SAM inference belongs behind the SAM service boundary.
*   **Compare (`qpane/compare/`)**: `CompareService` owns catalog-backed compare scene contributions and delegates comparison source selection, split state, and source revisions to the active composition.
*   **Swap (`qpane/swap/`)**: `SwapCoordinator` owns navigation-time swap, prefetch, cancellation, and pending-work orchestration.
*   **Cache (`qpane/cache/`)**: `CacheCoordinator` owns cache budgeting and consumer coordination.
*   **Concurrency (`qpane/concurrency/`)**: `TaskExecutor` owns heavy/background work, retry policy, and scheduling. **Never block the UI thread.**
*   **UI (`qpane/ui/`)**: Qt-only helpers own widget plumbing, overlays, drag/drop, clipboard, and diagnostics presentation.

### Structural Change Rules
For behavior-critical areas, work in two steps:

1. Add characterization or regression tests for existing behavior.
2. Perform structural changes behind those tests.

Do not start structural changes in an area without behavior safeguards for that
area.

Prefer clean replacement over internal compatibility layers. Structural changes
must be complete: update callsites, remove dead code, remove temporary bridges,
and leave the codebase looking as if the new design was the original design.

Prefer vertical slices that land safely over large unverified rewrites. If
behavior changes are intentional, call them out explicitly and test them as new
behavior.

## 3. The Trinity: Consistency is Mandatory
The "Trinity" ensures the Public API is consistent across four pillars. **When one changes, they ALL change.**

0.  **Contract (`qpane.pyi`):** The frozen public API definition.
1.  **Implementation (`qpane.py`):** The code itself.
2.  **Documentation (`docs/`):** The user manuals.
3.  **Demonstration (`examples/`):** The tutorialized proof-of-concept.

**Rule:** You must update all four in the same turn. Never leave the demo or docs "for later."

**Demo Style:** Demos must be "tutorialized"—clean, readable code that teaches the user how to use the new feature (see `examples/demonstration/`).

**Strict Constraint:** Demos must rely *exclusively* on the public API defined in `qpane.pyi`. Never reach into private internals (`_underscore_methods`) from example code.

**Docs Guardrail:** Documentation is for host developers using the public facade. Describe only supported API and behaviors; never mention internal wiring or unsupported swaps (e.g., replacing managers). Every public symbol must have a concise explainer in `docs/api-reference.md` and tutorialized coverage in the relevant narrative guide; bare symbol lists do not satisfy the guide standard.

## 4. Compatibility & Refactoring Strategy
We distinguish strictly between the **Public API** and the **Internal Implementation**.

### Public API: Frozen & Sacred
Defined by `qpane.pyi` and `qpane/__init__.py`.
*   **Rule:** **NEVER** break the public contract.
*   **Verification:** The "Trinity" check ensures `qpane.py` (impl), `qpane.pyi` (stub), and `docs/` align.
*   **Changes:** If you must change the public API, you must update the stub (`.pyi`), documentation (`docs/`), and demonstration (`examples/`) in the same turn.

### Internal Implementation: Fluid & Clean
Internal modules (`qpane.core`, `qpane.masks`, etc.) are **NOT** subject to backward compatibility rules within the library.
*   **Rule:** Refactor ruthlessly for quality while following the ownership reassessment rules above.
*   **NO SHIMS:** Do not leave backward-compatibility shims (e.g., `def old(): return new()`) in internal code.
*   **Complete Refactors:** If you change an internal signature, you **MUST** find and update **ALL** internal callers immediately.
*   **Outcome:** The codebase should look as if the new design was the original design.
*   **Architecture Updates:** If the refactor changes the ownership map or dependency boundaries, update the architecture guidance in this file in the same change.

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
**Only commit when explicitly asked.**
When asked to commit, use the Conventional Commits standard so the scope of the change is clear and release automation can infer the correct version impact.

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
