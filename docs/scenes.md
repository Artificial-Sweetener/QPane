**← Previous:** [Catalog and Navigation](catalog-and-navigation.md)

# Scene Composition

Scene composition lets a host arrange catalog images into a stored renderable view. QPane keeps the scene as a composition, renders each layer through the catalog-backed pyramid and tile path, and exposes passive hit testing so the host can decide what a click means.

Use scene composition when the host wants a contact sheet, review grid, two-up layout, or catalog browser surface that still benefits from QPane's fast raster pipeline. Use normal catalog navigation when the host only needs to show one image's generated default composition.

## Compose An Ad-Hoc Scene

Start with catalog image IDs. A `QPaneSceneRequest` is the host's command to store a scene composition, and each `QPaneCatalogImageLayerRequest` inside it points at one catalog image. Build the request the same way you would build a UI layout: choose a scene rectangle, choose a slot for one layer, fit the image into that slot, then add more layers.

```python
import uuid

from PySide6.QtCore import QRectF
from qpane import QPane, QPaneCatalogImageLayerRequest, QPaneSceneRequest

ids = viewer.imageIDs()
catalog = viewer.getCatalogSnapshot()
left_cell = QRectF(0, 0, 320, 320)

left_layer = QPaneCatalogImageLayerRequest(
    layer_id=uuid.uuid4(),
    image_id=ids[0],
    placement=QPane.fitSceneRect(catalog.catalog[ids[0]].image.size(), left_cell),
)

request = QPaneSceneRequest(
    composition_id=None,
    title="Review grid",
    bounds=QRectF(0, 0, 640, 320),
    layers=(left_layer,),
)
```

`QPaneSceneRequest.composition_id` is `None` when QPane should generate a new stored composition ID. Provide an existing host-created scene composition ID when the host wants to replace that scene in place. `QPaneSceneRequest.title` is the browser label, `QPaneSceneRequest.bounds` is the scene-coordinate canvas, and `QPaneSceneRequest.layers` is the draw order.

For the first layer, `QPaneCatalogImageLayerRequest.layer_id` is the stable host ID for the layer, `QPaneCatalogImageLayerRequest.image_id` selects the catalog image, and `QPaneCatalogImageLayerRequest.placement` places it inside the scene. Placement is exact: QPane maps the source pixels into that rectangle. If a host passes a square slot for a portrait image, the image stretches to the square. `QPane.fitSceneRect` avoids that distortion by returning the largest centered rectangle that fits inside the slot while preserving the source aspect ratio.

When a host wants a tight thumbnail grid, it can use the fitted rectangle as the item being packed. Fit each image into a local slot, move the returned rectangle beside the previous fitted rectangle, and use the requested gap between those actual placements. That keeps portrait thumbnails close together without asking QPane to invent layout policy.

Once that shape is clear, add the second layer beside it.

```python
right_cell = QRectF(320, 0, 320, 320)

request = QPaneSceneRequest(
    composition_id=None,
    title="Review grid",
    bounds=QRectF(0, 0, 640, 320),
    layers=(
        left_layer,
        QPaneCatalogImageLayerRequest(
            layer_id=uuid.uuid4(),
            image_id=ids[1],
            placement=QPane.fitSceneRect(
                catalog.catalog[ids[1]].image.size(),
                right_cell,
            ),
            role="thumbnail",
            metadata={"slot": 1},
        ),
    ),
)

composition_id = viewer.composeScene(request)
```

Add `QPaneCatalogImageLayerRequest.role` and `QPaneCatalogImageLayerRequest.metadata` when the host needs context later in hit tests, overlays, or sidebars. In the example above, a hit or overlay can read the `"thumbnail"` role and the metadata slot to know which catalog cell the user is pointing at.

Use `QPane.fillSceneRect` for cover-style layouts. It returns the smallest centered rectangle that covers the target slot while preserving the source aspect ratio, so the result may extend outside the slot. When the host wants that cover result clipped back to the slot, pass the returned placement on the layer and add a `QPaneSceneClip` for the visible cell.

Use the optional layer controls as refinements instead of rebuilding the scene model. `QPaneCatalogImageLayerRequest.visible` hides a layer while keeping its definition in the stored scene. `QPaneCatalogImageLayerRequest.opacity` lets a host show transparent reference layers. `QPaneCatalogImageLayerRequest.clip` trims a layer to a clip rectangle, and `QPaneCatalogImageLayerRequest.hit_test` decides whether that layer can be returned from `QPane.sceneHitTest`.

`QPane.composeScene` stores the request as a layered scene composition and returns the composition UUID. With the default `activate=True`, QPane opens the new composition immediately and fits the scene bounds when `fit_view=True`. With `activate=False`, QPane stores the composition without changing selection; if the request replaces the already active scene composition, QPane still emits `QPane.sceneChanged` because the active normalized scene changed.

QPane stores detached scene data. Later changes to the request objects passed to `QPane.composeScene` do not alter stored compositions. To update a stored scene, compose a replacement request with the same `QPaneSceneRequest.composition_id`.

## Store, Open, Replace, And Remove

Scene lifecycle uses the same composition browser APIs as normal image compositions. `QPane.compositionIDs` returns the browser order, `QPane.currentCompositionID` returns the active composition UUID, and `QPane.openComposition` reopens a stored scene composition by ID. `QPane.getCompositionSnapshot` gives hosts a structured browser snapshot, while `QPane.removeComposition` removes host-created explicit and layered scene compositions. Generated default image compositions are removed by removing their catalog images.

```python
composition_id = viewer.composeScene(request, activate=False)
viewer.openComposition(composition_id)

snapshot = viewer.getCompositionSnapshot()
for row_id in snapshot.order:
    row = snapshot.compositions[row_id]
    print(row.title, row.kind)
```

Calling `QPane.setCurrentImageID` from an active scene opens that image's generated default composition. `QPane.currentImageID` remains the catalog selection value; `QPane.currentCompositionID` is the authoritative answer for which stored view QPane is rendering.

## Compose From A Template

Templates are host-owned value objects. QPane does not keep a template registry; it stores only the composition produced when the host combines a `QPaneSceneTemplate` with `QPaneSceneTemplateBindings`.

Build the template around reusable slots. `QPaneSceneTemplate.template_id` is the host's reusable template identifier, `QPaneSceneTemplate.bounds` is the scene rectangle every call will fill, `QPaneSceneTemplate.layers` holds the reusable layer layout, and `QPaneSceneTemplate.title` is the default browser label.

Start each `QPaneTemplateLayer` with the fields that make the slot useful:

* `QPaneTemplateLayer.layer_id` is the stable host key for that layer.
* `QPaneTemplateLayer.source_slot` is the binding name the host will fill later.
* `QPaneTemplateLayer.placement` is where the eventual catalog image appears inside the composed scene.

Then add the same refinements you would add to a one-off scene layer. Use `QPaneTemplateLayer.visible` for hidden template layers, `QPaneTemplateLayer.opacity` for transparency, `QPaneTemplateLayer.clip` for clipped layers, and `QPaneTemplateLayer.hit_test` for pointer behavior. Use `QPaneTemplateLayer.role` and `QPaneTemplateLayer.metadata` when hits, overlays, or browser rows need host context from the template.

When the host calls the template, `QPaneSceneTemplateBindings.catalog_images` maps each slot to a catalog image UUID. `QPaneSceneTemplateBindings.composition_id` selects the stored composition ID or lets QPane generate one, `QPaneSceneTemplateBindings.title` overrides the template title for this call, and `QPaneSceneTemplateBindings.metadata` adds slot-level metadata that merges into the resulting scene layers.

```python
from qpane import QPaneSceneTemplate, QPaneSceneTemplateBindings, QPaneTemplateLayer

template = QPaneSceneTemplate(
    template_id=uuid.uuid4(),
    title="Two-up",
    bounds=QRectF(0, 0, 640, 320),
    layers=(
        QPaneTemplateLayer(
            layer_id=uuid.uuid4(),
            source_slot="left",
            placement=QRectF(0, 0, 320, 320),
        ),
        QPaneTemplateLayer(
            layer_id=uuid.uuid4(),
            source_slot="right",
            placement=QRectF(320, 0, 320, 320),
        ),
    ),
)

composition_id = viewer.composeSceneFromTemplate(
    template,
    QPaneSceneTemplateBindings(
        composition_id=None,
        title="Catalog pair",
        catalog_images={"left": ids[0], "right": ids[1]},
    ),
)
```

`QPane.composeSceneFromTemplate` expands the template into the same stored scene composition shape as `QPane.composeScene`. Every template source slot used by a layer must appear in the binding map, extra bindings are ignored, and the stored composition has no dependency on the template object after composition.

## Clip Layers

Use `QPaneSceneClip` when a layer should render or hit-test through a rectangle. `QPaneSceneClip.coordinate_space` selects whether `QPaneSceneClip.rect` is in scene coordinates, normalized scene coordinates, viewport coordinates, or normalized viewport coordinates. Keep clip rectangles simple and deterministic; QPane uses them while deciding which catalog-backed tile work is visible.

## Update UI From The Active Scene

Use `QPane.currentScene` when a sidebar, inspector, or status bar needs to describe the layered scene QPane is rendering right now. It returns a detached `QPaneScene` snapshot for the active layered scene composition, or `None` when the active composition is a generated or explicit image composition. The snapshot is read-only host information; hosts do not pass it back to compose a new scene.

```python
def refresh_scene_panel():
    scene = viewer.currentScene()
    if scene is None:
        scene_title.setText("No scene")
        layer_list.clear()
        return

    scene_title.setText(scene.title)
    scene_size.setText(f"{scene.bounds.width():.0f} x {scene.bounds.height():.0f}")
    layer_list.clear()
    for layer in scene.layers:
        layer_list.addItem(f"{layer.role}: {layer.image_id}")
```

Use the `QPaneScene` fields for different UI jobs:

* `QPaneScene.composition_id` is the stored composition ID your UI can compare with `QPane.currentCompositionID`.
* `QPaneScene.scene_id` is the render-scene identity used by hit testing and overlay state; it helps hosts ignore stale async UI work.
* `QPaneScene.title` is the practical sidebar label for the active scene.
* `QPaneScene.bounds` gives the scene size or canvas rectangle for inspector text.
* `QPaneScene.layers` is the ordered layer list to render in an inspector.

Layer rows usually need three kinds of information from each `QPaneSceneLayer` object.

* Identity: `QPaneSceneLayer.layer_id` is the host layer key, and `QPaneSceneLayer.image_id` is the catalog image behind the layer.
* Layout: `QPaneSceneLayer.placement` is the scene rectangle to show in an inspector.
* Display and interaction: `QPaneSceneLayer.visible`, `QPaneSceneLayer.opacity`, `QPaneSceneLayer.clip`, and `QPaneSceneLayer.hit_test` drive badges and disabled states.
* Host context: `QPaneSceneLayer.role` and `QPaneSceneLayer.metadata` are the values you attached when composing the scene.

`QPane.sceneChanged` emits whenever this normalized active scene snapshot changes. Connect it when a panel should refresh after `QPane.composeScene`, `QPane.composeSceneFromTemplate`, `QPane.openComposition`, `QPane.setCurrentImageID`, or removal of the active composition.

## Navigate From A Scene Hit

`QPane.sceneHitTest` accepts a widget-space point and returns the topmost hit-testable scene layer under that point. The result is passive: QPane does not change catalog selection, composition selection, comparison state, or layer selection. Hosts decide whether a hit opens a catalog image, selects a layer row, shows a detail panel, or does nothing.

```python
def handle_scene_click(event):
    hit = viewer.sceneHitTest(event.position().toPoint())
    if hit is None:
        return

    if hit.composition_id != viewer.currentCompositionID():
        return

    select_layer(hit.layer_id)
    if hit.role == "thumbnail":
        viewer.setCurrentImageID(hit.image_id)
```

The `QPaneSceneHit` object gives the host enough context to make that decision.

* Use `QPaneSceneHit.composition_id` and `QPaneSceneHit.scene_id` to confirm which active view produced the hit.
* Use `QPaneSceneHit.layer_id` when the host selects the matching row in a layer list.
* Use `QPaneSceneHit.image_id` when the host opens the catalog image.
* Use `QPaneSceneHit.role` and `QPaneSceneHit.metadata` when different layer roles trigger different host behavior.
* Use `QPaneSceneHit.panel_point`, `QPaneSceneHit.scene_point`, and `QPaneSceneHit.source_point` when follow-up UI needs widget, scene, or source-image coordinates.

## Draw Labels And Outlines With Scene Overlays

Use scene overlays for host chrome tied to active layered scene compositions, such as labels, badges, hover outlines, and selection rectangles. Register a callback with `QPane.registerSceneOverlay`, remove it with `QPane.unregisterSceneOverlay`, and inspect registered callbacks with the read-only snapshot returned by `QPane.sceneOverlays`.

```python
from PySide6.QtCore import Qt

def draw_labels(painter, state):
    painter.setPen(Qt.white)
    for layer in state.layers:
        if not layer.visible:
            continue
        painter.drawText(layer.panel_bounds.adjusted(6, 6, -6, -6), layer.role)

viewer.registerSceneOverlay("labels", draw_labels)
```

The callback receives `QPaneSceneOverlayState` for the active scene.

* Use `QPaneSceneOverlayState.zoom` when stroke widths or text sizes should scale with zoom.
* Use `QPaneSceneOverlayState.qpane_rect` to anchor widget chrome such as a scene-level badge.
* Use `QPaneSceneOverlayState.physical_viewport_rect` when device-pixel alignment matters.
* Use `QPaneSceneOverlayState.composition_id`, `QPaneSceneOverlayState.scene_id`, and `QPaneSceneOverlayState.scene_bounds` to identify the active scene being annotated.
* Iterate `QPaneSceneOverlayState.layers` when drawing per-layer labels, outlines, badges, or hover chrome.

Each `QPaneSceneOverlayLayer` is already mapped for overlay drawing.

* Use `QPaneSceneOverlayLayer.panel_bounds` for labels or outlines.
* Use `QPaneSceneOverlayLayer.visible` to skip hidden layers so overlays match what QPane rendered.
* Use `QPaneSceneOverlayLayer.transform` when you need to map source-image pixels into widget coordinates.
* Use `QPaneSceneOverlayLayer.source_size` for source-pixel math.
* Use `QPaneSceneOverlayLayer.placement` when overlay text should include scene coordinates.
* Use `QPaneSceneOverlayLayer.layer_id`, `QPaneSceneOverlayLayer.image_id`, `QPaneSceneOverlayLayer.role`, and `QPaneSceneOverlayLayer.metadata` to connect overlay decisions back to host scene data.

Scene overlays draw chrome only. They do not render image pixels and they do not own navigation or selection policy.

## Browser Rows For Scene Compositions

Composition snapshots let hosts show scene compositions next to generated default image compositions and explicit comparison compositions. In a browser, `CompositionSnapshot.order` is the row order, `CompositionSnapshot.compositions` maps each row ID to a `CompositionEntry`, and `CompositionSnapshot.current_composition_id` marks the selected row.

Use the row entry to decide what the browser shows.

* `CompositionEntry.composition_id` is the value to pass to `QPane.openComposition` when the row is clicked.
* `CompositionEntry.title` is the row label.
* `CompositionEntry.kind` tells the browser whether to draw an image row, explicit composition row, or scene row.
* `CompositionEntry.source_image_ids` can drive thumbnails or source-count badges.
* `CompositionEntry.current_image_id` is the base catalog image for image-backed row actions.
* `CompositionEntry.comparison` lets the browser show a comparison badge.
* `CompositionEntry.scene_layer_count` and `CompositionEntry.scene_bounds` give compact scene summary text for layered scenes.

## Constraints

Scene layers are catalog-backed image layers in this release. QPane does not accept raw `QImage` scene layers, and hosts should not flatten arranged scenes into temporary images just to render them. Keeping layers catalog-backed is what lets QPane reuse pyramid levels, tiles, culling, diagnostics, and hit testing.

While a layered scene composition is active, image-scoped mask and comparison mutation APIs do not operate on a stale catalog selection. Open a generated default image composition or an explicit image composition before editing active image masks or comparison state.

**Continue →** [Interaction Modes](interaction-modes.md)
