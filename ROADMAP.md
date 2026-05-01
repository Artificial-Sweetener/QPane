# QPane Roadmap

This roadmap captures ideas on deck after the 1.0 release. It's not me making promises, but it's where I see Pane going the next time I dump a lot of time into it.

## Guiding Principles

- Dependencies stay light. The core viewer remains PySide6-only, with `psutil` used
  solely for reactive cache logic when enabled.
- OpenCV remains the primary dependency for editing workflows (already required
  for masking), so new editing features should build on it rather than adding
  heavy alternatives.
- Torch is always optional, reserved for SAM or other AI-backed tools, and never
  required by the core viewer.

## Performance Hunting (Rendering Path)

Goal: keep tightening the hot paths anywhere they show up. This is a standing
invite to optimize the rendering pipeline and adjacent systems when you find
a real win.

Possible integration points
- `qpane/rendering/`: tile composition, buffer reuse, dirty-region math.
- `qpane/swap/`: scheduling, cache hit rate improvements, prefetch heuristics.
- `qpane/cache/`: memory accounting or eviction logic improvements.

Key behaviors to define
- Improvements must be measurable and not regress image fidelity.

Testing focus (when implemented)
- Before/after benchmarks that capture visible wins (latency, memory, FPS).

## Prefetch 2.0 (Never Interrupt the User)

Goal: prefetch the catalog over time without stealing cycles from navigation or
inspection. User-path work should always win, with dedicated lanes for
background prefetch that never interrupt the viewer.

Possible integration points
- `qpane/concurrency/`: introduce dedicated worker lanes for user vs background work.
- `qpane/swap/`: expand scheduling to support idle prefetch waves and priority rules.
- `qpane/core/config.py`: host-configurable depth and caps for prefetch behavior.

Key behaviors to define
- User actions always preempt background work.
- Hosts can choose how aggressive prefetching should be.

Testing focus (when implemented)
- No regressions in navigation responsiveness under heavy background work.

## Enhance Input: Touch & Gesture, Add Mapping API

Goal: make QPane feel native on touch screens, trackpads, and pen input (pinch-to-zoom,
two-finger pan, etc) while refactoring input into a
first-class domain so hosts can define keybindings and interaction rules without
reaching into tool internals.

Possible integration points
- `qpane/qpane.py`: accept gesture/touch events at the widget boundary and forward them into the
  interaction layer (consider `event()` override or explicit `gestureEvent`/`touchEvent` hooks).
- `qpane/ui/widget_props.py`: enable Qt touch attributes (`Qt.WA_AcceptTouchEvents`) and
  `grabGesture(Qt.PinchGesture)`/`Qt.PanGesture` where appropriate.
- `qpane/tools/delegate.py` and `qpane/tools/base.py`: add a gesture/keybinding dispatch path
  alongside wheel/mouse events so tools can opt in without polluting QPane.
- `qpane/rendering/viewport.py`: support anchor-aware zoom from pinch scale deltas and smooth
  pan updates from gesture deltas.
- `qpane/input/`: move input orchestration into a dedicated domain module so the widget stays
  thin while input policies, mappings, and gesture normalization live in one place.
- `qpane/core/` + `qpane/qpane.pyi`: introduce a public-facing input mapping surface that lets
  hosts register shortcuts or replace default bindings without touching tool classes.
- `qpane/core/config.py`: optional config flags to enable/disable touch gestures and tune
  sensitivities without breaking existing mouse-centric defaults.
- Trinity updates: evolve `qpane.pyi`, `qpane.py`, `docs/`, and `examples/` together for any new
  input mapping API surface, with tutorialized demos that stick to the public API.

Key behaviors to define
- Pinch-to-zoom anchored to the gesture center, with clamped zoom limits and DPI-correct scaling.
- Two-finger pan that respects drag-out rules and placeholder modes.
- Gesture priority rules
- Host-configurable shortcuts for mode switching and "hold to pan" without custom event filters.
- Clear fallbacks when the host platform does not emit pinch/pan gestures.

Testing focus (when implemented)
- Touch-enabled devices and trackpads across Windows, macOS, and Linux.
- Pen tablets and stylus input (pressure-agnostic baseline, smooth pan/zoom handoffs).
- Mixed input: pen + touch + mouse in the same session.
- No regressions in existing wheel zoom or drag-out behavior.

## Expand SAM Model Support (CPU-First)

Goal: support more SAM variants so hosts can choose the tradeoffs they want.
MobileSAM stays the default because it is the fastest on CPU, but other models
should be easy to swap in.

Possible integration points
- `qpane/sam/`: abstract model selection and model-specific metadata.
- `qpane/core/config.py`: allow hosts to select a model and configure weights.

Key behaviors to define
- CPU-first defaults remain unchanged.
- Model selection is explicit and documented.

Testing focus (when implemented)
- Existing MobileSAM workflows remain stable.

## Split QPane Into a Core Viewer + Feature Packages

Goal: keep the core viewer lean and make masks/SAM their own packages so advanced
features can evolve independently without bloating the base install.

Possible integration points
- `qpane/`: keep the viewer-only facade with minimal dependencies.
- `qpane/masks/` + `qpane/sam/`: move into feature packages with explicit extras.
- Packaging: introduce optional installs that pull in masks/SAM separately.

Key behaviors to define
- Core viewer remains fully functional without masks/SAM.
- Feature packages plug in cleanly through the public API without private hooks.

Testing focus (when implemented)
- Core viewer installs cleanly with only PySide6.
- Feature packages integrate without changing existing host code.

## Generalize Scene Layers (Rendering + Public Mutation)

Goal: build on QPane's existing scene/layer foundation so stored scene
compositions can grow from catalog-backed image layouts into richer layered
documents. The current scene model already handles catalog image layers, mask
layers internally, opacity, clipping, hit testing, scene overlays, render-plan
snapshots, and layer-scoped cache identity. The next step is making the parts
hosts need to control explicit and polished without turning QPane into a full
editor by default.

Possible integration points
- `qpane/scene/`: add new layer kinds and source descriptors only when a real
  rendering owner exists for them.
- `qpane/composition/`: keep stored scene compositions as the authoritative home
  for host-created layered views, including browser order and active selection.
- `qpane/rendering/`: extend the existing scene render-plan path for additional
  layer strategies, blend modes, and cache invalidation rules.
- `qpane/masks/`: keep mask behavior owned by the mask domain while exposing only
  the scene-layer data needed for rendering and host inspection.
- `qpane/swap/`: continue scheduling layer-scoped assets so navigation, pyramid
  work, tile work, masks, and future layer assets share the same cancellation and
  prefetch discipline.
- `qpane/core/` + `qpane/qpane.pyi`: expose public layer mutation APIs where hosts
  need them, such as visibility, opacity, ordering, and blend settings.
- Trinity updates: evolve `qpane.pyi`, `qpane.py`, `docs/`, and `examples/`
  together for any new public layer surface with tutorialized demos.

Key behaviors to define
- Which layer mutations are public host controls and which remain owned by
  feature domains.
- Ordering, visibility, opacity, and blend semantics across catalog image layers,
  mask layers, and future adjustment layers.
- Whether multi-image layer stacks belong to catalog entries, stored scene
  compositions, or a new document type.
- How hit testing, overlays, comparison state, and active image selection behave
  when several catalog images are visible in one scene.
- Cache invalidation rules when layer parameters change without changing the
  underlying catalog image.

Testing focus (when implemented)
- Correct visual ordering for mixed catalog image, mask, and future adjustment
  layers.
- Public layer mutations update rendering, overlays, hit testing, and composition
  snapshots consistently.
- Navigation and prefetch stay responsive when multiple layer assets are active.
- Viewer-only mode remains identical when hosts use only generated default image
  compositions.

## Modularize Core Infrastructure (Concurrency + Cache)

Goal: split QPane’s concurrency executor and cache coordination into standalone
packages so host apps can reuse the same architecture without pulling the full viewer.

Possible integration points
- `qpane/concurrency/`: extract TaskExecutor, policies, and retry hooks into a dedicated package.
- `qpane/cache/`: lift cache coordinator + registry into its own package with minimal dependencies.
- `qpane/core/`: keep QPane as a thin integration layer and treat the extracted packages as optional runtime deps.

Key behaviors to define
- Stable public APIs for the new packages.
- QPane remains a consumer, not the owner.

Testing focus (when implemented)
- Backward compatibility for QPane.
- Standalone adoption in non-QPane apps.

## Adjustment Layers (Non-Destructive Editing)

Goal: introduce parameterized adjustment layers (levels, curves, color balance,
exposure, LUT) that sit in the layer stack and render without altering base pixels.
These should be composited alongside image/mask layers to power non-destructive
workflows and future editing tools.

Possible integration points
- `qpane/rendering/`: extend the render pipeline to evaluate adjustment layers in
  the ordered stack (CPU-first, tiling-aware, and cacheable).
- `qpane/catalog/`: allow entries to store adjustment layers alongside image layers
  so adjustments travel with catalog navigation.
- `qpane/layers/` (or `qpane/masks/` until layers land): define a Layer interface
  that supports parameterized transforms and visibility/opacity.
- `qpane/concurrency/`: offload adjustment evaluation and caching to the executor
  to keep the UI thread responsive.
- Trinity updates: evolve `qpane.pyi`, `qpane.py`, `docs/`, and `examples/`
  together for any new adjustment APIs and tutorialized demos.

Key behaviors to define
- Adjustment evaluation order, stacking semantics, and blend rules.
- Cache invalidation strategy when adjustment parameters change.
- Viewer-only mode remains unaffected when no adjustments are present.

Testing focus (when implemented)
- Visual correctness across zoom levels and pyramid resolutions.
- Performance under repeated parameter tweaks (debounced updates).
- Consistent output across platforms and DPI configurations.

## Advanced Editing Tools (Layer-Backed)

Goal: deliver pro-style editing workflows (cut/paste/move, transform, clone/stamp)
powered by the Layer abstraction while keeping the core viewer experience unchanged.

Possible integration points
- `qpane/tools/` + `qpane/input/`: add opt-in tools with clear activation modes and
  host-configurable shortcuts that do not override viewer defaults.
- `qpane/layers/` (or `qpane/masks/` until layers land): represent editable regions
  as layer content so edits remain non-destructive and reversible.
- `qpane/rendering/` + `qpane/swap/`: ensure edited layers are composited and
  cached without changing baseline image viewing performance.
- Trinity updates: evolve `qpane.pyi`, `qpane.py`, `docs/`, and `examples/`
  together for any new editing APIs and tutorialized demos.

Key behaviors to define
- Editing tools are feature-gated and safe to leave disabled.
- Clone/stamp respects zoom, pan, and pixel alignment across DPI changes.
- Cut/paste and move operate on layer content without mutating base image pixels.

Testing focus (when implemented)
- Editing tools do not regress pan/zoom/navigation in viewer-only mode.
- Layer edits are undoable and stable across catalog navigation.
