**← Previous:** [Configuration Reference](configuration-reference.md)

# Catalog and Navigation

The catalog is the source inventory for host-facing image identity. It maps UUIDs to images, paths, and ordering. A composition is the renderable view QPane is showing. Loading catalog images creates generated one-image default compositions, so image navigation keeps working while hosts can also browse, open, and remove stored compositions.

## Build an Image Catalog
To populate the viewer, you convert your images into an ordered map. QPane handles the ID generation and internal mapping, letting you focus on the content.

Use `QPane.imageMapFromLists` to create the catalog structure, then apply it with `QPane.setImagesByID`.

```python
from PySide6.QtGui import QImage
from qpane import QPane

# 1. Prepare your data (filter out nulls first!)
images = [QImage("one.png"), QImage("two.png")]
paths = ["one.png", "two.png"]

# 2. Build the map
# If you skip 'ids', QPane generates UUIDs for you.
image_map = QPane.imageMapFromLists(images, paths=paths)

# 3. Load it and pick the starting image
start_id = next(iter(image_map))
viewer.setImagesByID(image_map, current_id=start_id)
```

> **Pro Tip:** Always check `image.isNull()` before adding it to the list. `QPane.imageMapFromLists` raises a `ValueError` if the input lists (images/paths/IDs) have mismatched lengths.

### Understanding Entries
`CatalogEntry` is the value stored for each catalog row. `CatalogEntry.image` holds the original `QImage`, and `CatalogEntry.path` holds the optional source path used for labels or persistence. Most hosts build entries through `QPane.imageMapFromLists`, but snapshot-driven UIs may read `CatalogEntry` values from `QPane.getCatalogSnapshot`.

### Managing the List
* **Snapshots:** Building a sidebar? `QPane.getCatalogSnapshot` returns a structured `CatalogSnapshot` of catalog entries, order, linked groups, and active IDs for host UI code.
* **Clearing:** `QPane.clearImages` wipes the catalog and shows the placeholder.
* **Pruning:** Use `QPane.removeImageByID` or `QPane.removeImagesByID` to drop specific items. This happens in-place and won't force a full reload.

## Work With Compositions
Use compositions when your host is browsing what the viewer renders rather than raw source inventory. Every catalog image gets a generated default composition. `QPane.compose` creates a persistent explicit composition from one or two catalog image IDs and opens it immediately.

```python
ids = viewer.imageIDs()
if len(ids) >= 2:
    review_id = viewer.compose(images=[ids[0], ids[1]], title="A/B Review")
    viewer.openComposition(review_id)
```

To build a browser panel, ask QPane for a `CompositionSnapshot` and render rows in the order QPane gives you. `CompositionSnapshot.order` is the display order, `CompositionSnapshot.compositions` is the lookup table for row data, and `CompositionSnapshot.current_composition_id` tells your panel which row should be selected.

```python
snapshot = viewer.getCompositionSnapshot()
for composition_id in snapshot.order:
    entry = snapshot.compositions[composition_id]
    selected = composition_id == snapshot.current_composition_id
    add_row(
        row_id=entry.composition_id,
        label=entry.title,
        selected=selected,
    )
```

Treat each `CompositionEntry` as the row model for one stored view.

* Use `CompositionEntry.composition_id` for the row action that calls `QPane.openComposition`.
* Use `CompositionEntry.title` as the visible label when rendering the browser row.
* Use `CompositionEntry.kind` to choose row styling such as "Image", "Comparison", or "Scene".
* Use `CompositionEntry.source_image_ids` when a row needs thumbnails or source-count badges.

Image-backed rows and scene rows expose different shortcuts.

* `CompositionEntry.current_image_id` is the base catalog image for generated default and explicit image compositions, so image-specific actions can use it directly.
* Layered scene rows use `None` for the current image; show scene summary text from `CompositionEntry.scene_layer_count` and `CompositionEntry.scene_bounds` instead.
* `CompositionEntry.comparison` gives compare controls the state they should mirror when a compared row opens.

`QPane.compositionIDs` is useful for simple previous/next navigation when you do not need full row data. `QPane.currentCompositionID` returns the active stored view. Use `QPane.openComposition` when the user selects a browser row, and use `QPane.removeComposition` for host-created explicit and layered scene compositions. Generated default compositions are removed by removing their catalog image.

## Navigate Between Images
To move through source-backed default views, use `QPane.setCurrentImageID`. Pass a UUID to navigate to an image's generated default composition, or `None` to clear the active view. This handles the heavy lifting: it suspends overlays, swaps the render buffers, and fires the selection signals.

You can inspect the current catalog state with `QPane.currentImageID`, `QPane.currentImage`, and `QPane.currentImagePath`. `currentImage` returns the selected base catalog image, or `None` when no image is selected; it is not a flattened copy of masks or overlays. For the full list, check `QPane.imageIDs` or `QPane.hasImages`.

```python
# Cycle to the next image
ids = viewer.imageIDs()
if len(ids) > 1:
    current = viewer.currentImageID()
    # Find current index, defaulting to 0 if not found
    idx = ids.index(current) if current in ids else 0
    next_id = ids[(idx + 1) % len(ids)]
    
    viewer.setCurrentImageID(next_id)
```

> **Heads-up:** `setCurrentImageID` temporarily suspends public overlays and tool chrome during the swap to prevent visual glitches. If you're writing a custom tool, expect a brief reset when navigation occurs.

If a layered scene composition is active, `setCurrentImageID` opens the requested image's generated default composition. `currentImageID` remains the catalog selection value; `currentCompositionID` identifies the rendered composition.

### Looping and Looking Up
Need to find a specific file or process every loaded item?
* `QPane.imagePath(id)`: Look up the filesystem path for a specific UUID.
* `QPane.allImages`: Get a list of all `QImage` objects in catalog order.
* `QPane.allImagePaths`: Get the corresponding list of paths (some might be `None`).

## Compose Catalog-Backed Scenes
Use `QPane.composeScene` when the host wants QPane to store and render an arranged scene from catalog images. Scene layers reference catalog UUIDs and scene-coordinate placements, so QPane still resolves each layer through the normal pyramid and tile renderer. Use `QPane.composeSceneFromTemplate` when the host reuses the same layout with different catalog bindings, `QPane.currentScene` to inspect the active normalized scene, and `QPane.sceneHitTest` to map pointer positions back to public layer metadata. See [Scene Composition](scenes.md) for the tutorial, layer fields, templates, hit testing, and scene overlays.

## Programmatic View Control
While users typically pan and zoom with the mouse, your host application often needs to drive the view directly—for example, to implement a "Reset" button or a "100%" zoom shortcut.

* **Fitting & Resetting:** Use `QPane.setZoomFit()` to instantly re-frame the content within the viewport.
* **Precision Zooming:** Use `QPane.applyZoom(zoom, anchor=None)`. Unlike a raw property setter, this helper clamps values to safe limits and handles High-DPR scaling so that `1.0` truly means "1 image pixel = 1 screen pixel."
* **Snapping:** `QPane.setZoom1To1(anchor=None)` is a shortcut for that native pixel-perfect view.
* **Inspection:** Read `QPane.currentZoom` to get the effective zoom level.

Both zoom methods accept an optional `anchor` (QPoint or QPointF) to keep a specific point stationary while the scale changes.

> **Demo Tip:** The demonstration app exposes these via a percent-only zoom input in the status bar and a nearby toggle button that switches between Fit and 1:1. Use that layout as a reference for tutorializing zoom controls in your own host.

> **Note:** The default Pan/Zoom tool snaps wheel zoom steps to the native 1:1 scale when crossing it, ensuring users hit 100% on the way in or out.

## Link Views (Synchronized Pan/Zoom)
To compare images side-by-side—like before/after shots or exposure brackets—you can link them. Linked images share their pan and zoom state; moving one moves them all.

* **Quick Link:** `QPane.setAllImagesLinked(True)` locks every image in the catalog together.
* **Custom Groups:** `QPane.setLinkedGroups` lets you define specific subsets using `LinkedGroup` objects.
* **Inspection:** `QPane.linkedGroups` returns the active definitions so host panels can mirror link state without tracking their own copy.

`QPane.linkGroupsChanged` emits whenever those definitions change. Connect to it when a host toolbar or catalog dock needs to refresh linked-view badges.

> **Note:** Linking requires at least two images. For the best "lockstep" feel, ensure linked images share the same aspect ratio.

## Compare Images In One View
Use QPane's comparison helpers when you want to inspect two catalog images in the same viewport with a split reveal. `QPane.setComparisonImageID` uses an existing catalog image as the comparison source. The current catalog image remains the base image returned by `QPane.currentImage`, and comparison changes do not move the catalog selection.

Comparison belongs to the active composition. Opening another default or explicit composition reports that composition's comparison state; returning to a compared composition restores its source, split position, and orientation.

Comparison is intended for same-shaped or closely matching images, such as before/after renders or exposure brackets. When the compared images differ in pixel dimensions, QPane uses the larger image as the authority for Fit, 1:1 zoom, pan limits, and minimum zoom while comparison is active. Images with very different aspect ratios remain best-effort comparison inputs.

```python
from qpane import ComparisonOrientation

ids = viewer.imageIDs()
current_id = viewer.currentImageID()
if current_id in ids and len(ids) > 1:
    next_id = ids[(ids.index(current_id) + 1) % len(ids)]
    viewer.setComparisonImageID(next_id)
    viewer.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
```

Use `ComparisonOrientation` when host controls switch between vertical and horizontal reveal directions. `QPane.setComparisonSplit` accepts a normalized position from `0.0` to `1.0` plus either `ComparisonOrientation.VERTICAL` or `ComparisonOrientation.HORIZONTAL`, so a slider and orientation segmented control can drive the reveal together.

```python
def apply_compare_controls(position, horizontal):
    orientation = (
        ComparisonOrientation.HORIZONTAL
        if horizontal
        else ComparisonOrientation.VERTICAL
    )
    viewer.setComparisonSplit(position, orientation)
```

Use `QPane.comparisonState` to mirror the active composition's compare setup into host controls. The returned `ComparisonState` is the host-facing model for compare buttons, source labels, and split controls.

* `ComparisonState.enabled` drives whether the controls are active for the selected composition.
* `ComparisonState.split_position` seeds the slider before the user drags it.
* `ComparisonState.orientation` seeds the vertical/horizontal control for the current reveal direction.
* `ComparisonState.source_id` identifies the catalog source behind the comparison image.
* `ComparisonState.source_path` gives you a display path when one is available.
* `ComparisonState.source_kind` tells you what kind of comparison source is active.

```python
def refresh_compare_controls():
    state = viewer.comparisonState()
    compare_group.setEnabled(state.enabled)
    split_slider.setValue(round(state.split_position * 100))
    horizontal_button.setChecked(
        state.orientation == ComparisonOrientation.HORIZONTAL
    )
    source_label.setText(str(state.source_path or state.source_id or "No comparison"))


viewer.comparisonChanged.connect(lambda _state: refresh_compare_controls())
```

Connect `QPane.comparisonChanged` when a toolbar or inspector should refresh after either host code or built-in dragging changes comparison state.

Use `QPane.clearComparisonImage` for the host action that turns comparison off and returns the active composition to normal single-image viewing.

Comparison split dragging is built in. QPane draws no divider chrome: the visible boundary between the two images is the drag affordance, and the cursor changes when the pointer is inside the grab region. The grab region is tied to the rendered image boundary, so it moves with pan and zoom. If that boundary is outside the viewport, there is no in-viewport divider target until the user pans it back into view. `QPane.comparisonDividerInteractive` reports whether built-in dragging is enabled, and `QPane.setComparisonDividerInteractive` lets hosts disable or restore that interaction.

Hosts that want a visible divider can draw one with a normal public overlay. `QPane.comparisonDividerState` returns a `ComparisonDividerState` snapshot for that overlay.

* Check `ComparisonDividerState.enabled` before drawing so hidden or inactive comparison state does not leave divider chrome behind.
* Use `ComparisonDividerState.visible_segment` for the line the user can currently see.
* Use `ComparisonDividerState.full_segment` when the host needs the projected boundary even outside the widget.
* Use `ComparisonDividerState.hovered` and `ComparisonDividerState.dragging` for host-owned hover or active visuals.
* Use `ComparisonDividerState.orientation` to adapt vertical and horizontal artwork before drawing host-owned divider visuals.
* Use `ComparisonDividerState.interactive` and `ComparisonDividerState.hit_width` when mirroring the built-in drag affordance.

```python
def draw_compare_divider(painter, _state):
    divider = viewer.comparisonDividerState()
    if divider.visible_segment is None:
        return
    painter.drawLine(divider.visible_segment)


viewer.registerOverlay("host-compare-divider", draw_compare_divider)
```

## Listen for Events
To keep your UI in sync, connect to QPane's signals. They tell you exactly when the viewer state changes so you can update labels, buttons, or sidebars.

### Navigation Events
* `QPane.catalogSelectionChanged`: Fires when the active image ID changes (or becomes `None`). Use this to update your window title or "Next" button.
* `QPane.currentImageChanged`: Similar to selection changed, but strictly emits the UUID.
* `QPane.catalogChanged`: Fires on structural changes (add, remove, reorder). The signal carries a `CatalogMutationEvent` which tells you the `reason` (e.g., "maskCreated") and the `affected_ids`.
* `QPane.compositionSelectionChanged`: Fires when the active composition changes so browsers can update their selected row.
* `QPane.compositionChanged`: Fires with a `CompositionSnapshot` after composition records change, including scene composition creation, replacement, and removal.

### Content Events
* `QPane.imageLoaded`: Fires when the new image pixels are actually ready to render.
* `QPane.linkGroupsChanged`: Fires when the link definitions are updated.

### View State Events
* `QPane.zoomChanged`: Emits the zoom factor (`float`) whenever it changes, which is the right signal for status bars and zoom controls.
* `QPane.viewportRectChanged`: Emits the physical viewport rectangle (`QRectF`) so overlays and host layout code can respond to resize or device-pixel-ratio changes.
    * *Why this matters:* This fires not just on resize, but when the window moves between screens with different DPIs. If you're drawing custom overlays, use this signal (or `QPane.currentViewportRect()`) to keep your coordinates aligned.

### Hit Testing
Need to know where the mouse is? `QPane.panelHitTest(pos)` converts widget coordinates into image coordinates, handling all the aspect-ratio and zoom math for you. The returned `PanelHitTest` exposes:

- `PanelHitTest.panel_point`: Panel-space position that was tested by the host pointer or tool event.
- `PanelHitTest.raw_point`: Unclamped image-space coordinate as a float, useful for showing out-of-bounds hover feedback.
- `PanelHitTest.clamped_point`: Image-space coordinate clamped to image bounds, useful when tools need a valid pixel.
- `PanelHitTest.inside_image`: True when the raw point lies inside the image.

**Continue →** [Interaction Modes](interaction-modes.md)
