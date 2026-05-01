**← Previous:** [Extensibility](extensibility.md)

# API Reference (Facade)

Quick index to the QPane facade. Each entry includes a concise explainer; use the guides for tutorialized workflow context.

**Jump within this file:**
* [QPane Setup and Settings](#qpane-setup-and-settings)
* [Config](#config)
* [Types](#types)
* [Catalog and Navigation](#catalog-and-navigation)
* [Scene Composition](#scene-composition)
* [Compositions](#compositions)
* [Comparison](#comparison)
* [Diagnostics](#diagnostics)
* [Masks and SAM](#masks-and-sam)
* [Extensibility](#extensibility)
* [View State & Geometry](#view-state--geometry)
* [Signals and Events](#signals-and-events)

**Start with the guides:**
* [Getting Started](getting-started.md)
* [Configuration](configuration.md)
* [Configuration Reference](configuration-reference.md)
* [Catalog and Navigation](catalog-and-navigation.md)
* [Scene Composition](scenes.md)
* [Interaction Modes](interaction-modes.md)
* [Masks and SAM](masks-and-sam.md)
* [Diagnostics](diagnostics.md)
* [Extensibility](extensibility.md)

## QPane Setup and Settings
- QPane.applySettings — Apply a new `Config` to a live QPane, optionally merging keyword overrides for one-off tweaks.
- QPane.settings — Read the current settings snapshot; treat it as read-only and mutate copies instead.
- QPane.installedFeatures — Report which optional features (mask, SAM) are active after initialization.
- QPane.availableControlModes — List all registered control modes, including custom tools.
- QPane.getControlMode — Return the currently active control mode ID.
- QPane.setControlMode — Switch to a registered mode; unavailable mask/SAM modes are ignored while the placeholder is active, and unknown mode IDs raise `ValueError`.
- QPane.CONTROL_MODE_CURSOR — Built-in inert cursor mode (no pan/zoom).
- QPane.CONTROL_MODE_PANZOOM — Built-in pan/zoom mode for navigation.

See also: [Configuration](configuration.md) and [Interaction Modes](interaction-modes.md).

## Config
- Config — Immutable-like settings object handed to QPane; fields are JSON-serializable.
- Config.copy — Deep-clone a config so you can branch without mutating the original.
- Config.as_dict — Return the configuration as a plain dictionary.
- Config.configure — Merge another config/mapping plus keyword overrides; unknown keys raise and enum-backed values (cache mode, placeholder scale/zoom, diagnostics domains) accept enums or canonical strings only.
- Config.feature_descriptors — Expose feature schemas/validators for building UI around optional settings.

See also: [Configuration](configuration.md) and [Configuration Reference](configuration-reference.md).

## Types

### Enums
- qpane.CacheMode — Cache budgeting modes.
	- CacheMode.AUTO — Adapts to OS pressure using headroom settings (`auto`).
	- CacheMode.HARD — Uses a fixed budget (`hard`).
- qpane.PlaceholderScaleMode - Placeholder scaling rules.
	- PlaceholderScaleMode.AUTO — Default scaling (`auto`).
	- PlaceholderScaleMode.LOGICAL_FIT — Fit to logical viewport (`logical_fit`).
	- PlaceholderScaleMode.PHYSICAL_FIT — Fit to physical viewport (`physical_fit`).
	- PlaceholderScaleMode.RELATIVE_FIT — Scale relative to viewport (`relative_fit`).
- qpane.ZoomMode — Placeholder zoom strategies.
	- ZoomMode.FIT — Fit to viewport (`fit`).
	- ZoomMode.LOCKED_ZOOM — Keep zoom level constant (`locked_zoom`).
	- ZoomMode.LOCKED_SIZE — Keep size constant (`locked_size`).
- qpane.DiagnosticsDomain — Diagnostics overlay domains; use enum members (or `.value`) when configuring diagnostics. The base overlay always shows paint/zoom/pyramid rows; the toggles below control additional detail domains.
	- DiagnosticsDomain.CACHE — Cache budgets, usage, and eviction/entitlement detail.
	- DiagnosticsDomain.SWAP — Navigation, renderer queues, and prefetch metrics.
	- DiagnosticsDomain.MASK — Mask status, autosave, job queues, and brush info.
	- DiagnosticsDomain.EXECUTOR — Executor identity, queue depth, thread/device limits, wait times.
	- DiagnosticsDomain.RETRY — Retry queues per resource plus compact summaries.
	- DiagnosticsDomain.SAM — SAM cache, readiness, worker counts, and max threads.
- qpane.ControlMode — Built-in control mode identifiers for tool registration.
	- ControlMode.CURSOR — Inert cursor mode (`cursor`).
	- ControlMode.PANZOOM — Pan/zoom mode (`panzoom`).
	- ControlMode.DRAW_BRUSH — Mask painting mode (`draw-brush`).
	- ControlMode.SMART_SELECT — SAM-based selection mode (`smart-select`).
- qpane.ComparisonOrientation — Split direction for comparison rendering.
	- ComparisonOrientation.VERTICAL — Reveal the comparison image to the right of a vertical divider.
	- ComparisonOrientation.HORIZONTAL — Reveal the comparison image below a horizontal divider.

### Data Structures
- qpane.CatalogEntry — Structured catalog value containing source image data and an optional path.
	- CatalogEntry.image — Original catalog `QImage` used by QPane rendering and host snapshots.
	- CatalogEntry.path — Optional source path used for labels, persistence, or host lookup.
- qpane.LinkedGroup — Linked-view group descriptor with a stable UUID and members.
- qpane.ComparisonState — Snapshot returned by `QPane.comparisonState`.
	- ComparisonState.enabled — Whether comparison rendering is active.
	- ComparisonState.source_id — Catalog image UUID for the comparison source.
	- ComparisonState.source_path — Optional path associated with the comparison image.
	- ComparisonState.source_kind — `"catalog"` when enabled, or None.
	- ComparisonState.split_position — Normalized split position from `0.0` to `1.0`.
	- ComparisonState.orientation — Active `ComparisonOrientation` used for vertical or horizontal split rendering.
- qpane.ComparisonDividerState — Host-facing comparison divider interaction and geometry snapshot.
	- ComparisonDividerState.enabled — Whether authoritative divider geometry is available.
	- ComparisonDividerState.interactive — Whether built-in divider dragging is enabled.
	- ComparisonDividerState.hovered — Whether the pointer is currently over the divider hit target.
	- ComparisonDividerState.dragging — Whether a divider drag is active.
	- ComparisonDividerState.orientation — Current comparison split orientation.
	- ComparisonDividerState.hit_width — Invisible grab tolerance around the rendered boundary.
	- ComparisonDividerState.full_segment — Full projected boundary in widget coordinates, or None.
	- ComparisonDividerState.visible_segment — Portion of the boundary visible inside the widget, or None.
- qpane.CompositionEntry — Snapshot row for one renderable composition.
	- CompositionEntry.composition_id — Stable UUID used with `QPane.openComposition`.
	- CompositionEntry.kind — `"default-image"` for generated catalog compositions, `"explicit"` for one-image or comparison compositions, or `"layered-scene"` for scene compositions.
	- CompositionEntry.title — Host-facing browser title.
	- CompositionEntry.source_image_ids — Catalog image UUIDs used by the composition.
	- CompositionEntry.current_image_id — Base catalog image UUID for default/explicit compositions; None for layered scene compositions.
	- CompositionEntry.comparison — Composition-scoped `ComparisonState` restored when the row reopens.
	- CompositionEntry.scene_layer_count — Number of stored scene layers for layered scene compositions.
	- CompositionEntry.scene_bounds — Scene-coordinate bounds for layered scene compositions, or None.
- qpane.CompositionSnapshot — Structured composition browser state.
	- CompositionSnapshot.compositions — Mapping of composition UUID to `CompositionEntry`.
	- CompositionSnapshot.order — Composition UUIDs in browser order.
	- CompositionSnapshot.current_composition_id — Active composition UUID, or None.
- qpane.MaskInfo — Mask metadata shape returned by mask helpers.
- qpane.DiagnosticRecord — Label/value diagnostic entry used in overlays.
- qpane.CatalogMutationEvent — Catalog mutation payload emitted on catalog changes.
- qpane.CatalogSnapshot — Structured catalog state (catalog entries, linked groups, ordering, active IDs).
- qpane.OverlayState — Stable public-overlay snapshot passed to `draw_fn`.
	- OverlayState.zoom — Current zoom factor.
	- OverlayState.qpane_rect — Widget-space bounds of the viewer.
	- OverlayState.physical_viewport_rect — Device-pixel viewport bounds.
	- OverlayState.transform — Image-to-widget transform for coordinate anchoring.
	- OverlayState.current_pan — Current pan offset in widget space.
	- OverlayState.source_image — Base catalog raster resolved for the current overlay pass, not flattened rendered content.
- qpane.QPaneSceneRequest — Host request used to create or replace a stored scene composition.
	- QPaneSceneRequest.composition_id — Optional composition UUID to create or replace; None generates a new UUID.
	- QPaneSceneRequest.title — Optional host-facing composition title.
	- QPaneSceneRequest.bounds — Host-defined scene-coordinate bounds.
	- QPaneSceneRequest.layers — Ordered `QPaneCatalogImageLayerRequest` entries.
- qpane.QPaneCatalogImageLayerRequest — Catalog-backed image layer in a scene composition request.
	- QPaneCatalogImageLayerRequest.layer_id — Stable layer UUID supplied by the host.
	- QPaneCatalogImageLayerRequest.image_id — Catalog image UUID rendered for this layer.
	- QPaneCatalogImageLayerRequest.placement — Scene-coordinate rectangle for this layer.
	- QPaneCatalogImageLayerRequest.visible — Whether the layer renders and hit-tests.
	- QPaneCatalogImageLayerRequest.opacity — Layer opacity from `0.0` to `1.0`.
	- QPaneCatalogImageLayerRequest.clip — Optional `QPaneSceneClip` limiting rendered or hit-tested layer area.
	- QPaneCatalogImageLayerRequest.hit_test — Whether `QPane.sceneHitTest` can return this layer.
	- QPaneCatalogImageLayerRequest.role — Host label carried into hits and overlays.
	- QPaneCatalogImageLayerRequest.metadata — Opaque host metadata carried into hits and overlays.
- qpane.QPaneSceneTemplate — Host-owned reusable template for building scene composition requests.
	- QPaneSceneTemplate.template_id — Stable template UUID owned by the host.
	- QPaneSceneTemplate.bounds — Host-defined scene-coordinate bounds.
	- QPaneSceneTemplate.layers — Ordered `QPaneTemplateLayer` entries.
	- QPaneSceneTemplate.title — Optional default title used by template composition.
- qpane.QPaneTemplateLayer — Template layer bound by source slot at composition time.
	- QPaneTemplateLayer.layer_id — Stable layer UUID supplied by the host.
	- QPaneTemplateLayer.source_slot — Binding key resolved by `QPaneSceneTemplateBindings.catalog_images`.
	- QPaneTemplateLayer.placement — Scene-coordinate rectangle for this layer.
	- QPaneTemplateLayer.visible — Whether the layer renders and hit-tests.
	- QPaneTemplateLayer.opacity — Layer opacity from `0.0` to `1.0`.
	- QPaneTemplateLayer.clip — Optional `QPaneSceneClip` applied when the template becomes a stored scene.
	- QPaneTemplateLayer.hit_test — Whether `QPane.sceneHitTest` can return this layer.
	- QPaneTemplateLayer.role — Host label carried into hits and overlays.
	- QPaneTemplateLayer.metadata — Opaque host metadata merged into composed layers.
- qpane.QPaneSceneTemplateBindings — Concrete catalog image bindings for a scene template.
	- QPaneSceneTemplateBindings.composition_id — Optional composition UUID to create or replace.
	- QPaneSceneTemplateBindings.title — Optional title overriding the template title.
	- QPaneSceneTemplateBindings.catalog_images — Mapping of template source slots to catalog image UUIDs.
	- QPaneSceneTemplateBindings.metadata — Optional source-slot metadata merged into composed layers.
- qpane.QPaneScene — Public catalog-backed scene snapshot returned by QPane.
	- QPaneScene.composition_id — Stored composition UUID.
	- QPaneScene.scene_id — Render scene UUID; layered scene compositions use the composition UUID.
	- QPaneScene.title — Host-facing composition title.
	- QPaneScene.bounds — Host-defined scene-coordinate bounds.
	- QPaneScene.layers — Ordered `QPaneSceneLayer` entries.
- qpane.QPaneSceneLayer — Catalog-backed image layer in a composed scene.
	- QPaneSceneLayer.layer_id — Stable layer UUID supplied by the host.
	- QPaneSceneLayer.image_id — Catalog image UUID rendered for this layer.
	- QPaneSceneLayer.placement — Scene-coordinate rectangle for this layer.
	- QPaneSceneLayer.visible — Whether the layer renders and hit-tests.
	- QPaneSceneLayer.opacity — Layer opacity from `0.0` to `1.0`.
	- QPaneSceneLayer.clip — Optional `QPaneSceneClip` preserved from the normalized request layer.
	- QPaneSceneLayer.hit_test — Whether `QPane.sceneHitTest` can return this layer.
	- QPaneSceneLayer.role — Host label carried into hits and overlays.
	- QPaneSceneLayer.metadata — Opaque host metadata carried into hits and overlays.
- qpane.QPaneSceneClip — Optional layer clip rectangle.
	- QPaneSceneClip.coordinate_space — Coordinate system for `rect`: `"scene"`, `"normalized-scene"`, `"viewport"`, or `"normalized-viewport"`.
	- QPaneSceneClip.rect — Clip rectangle in the selected coordinate space.
- qpane.QPaneSceneHit — Public scene hit result returned by `QPane.sceneHitTest`.
	- QPaneSceneHit.composition_id — Active composition UUID.
	- QPaneSceneHit.scene_id — Scene UUID.
	- QPaneSceneHit.layer_id — Hit layer UUID.
	- QPaneSceneHit.image_id — Catalog image UUID for the hit layer.
	- QPaneSceneHit.role — Host role copied from the layer.
	- QPaneSceneHit.metadata — Opaque metadata copied from the layer.
	- QPaneSceneHit.panel_point — Tested widget coordinate.
	- QPaneSceneHit.scene_point — Hit point in scene coordinates.
	- QPaneSceneHit.source_point — Hit point in source image pixel coordinates.
- qpane.QPaneSceneOverlayState — Scene-overlay snapshot passed to `registerSceneOverlay` callbacks.
	- QPaneSceneOverlayState.zoom — Current zoom factor.
	- QPaneSceneOverlayState.qpane_rect — Widget-space bounds.
	- QPaneSceneOverlayState.physical_viewport_rect — Device-pixel viewport bounds.
	- QPaneSceneOverlayState.composition_id — Active composition UUID.
	- QPaneSceneOverlayState.scene_id — Active scene UUID.
	- QPaneSceneOverlayState.scene_bounds — Scene-coordinate bounds.
	- QPaneSceneOverlayState.layers — Rendered public scene layers.
- qpane.QPaneSceneOverlayLayer — Rendered layer geometry for scene overlays.
	- QPaneSceneOverlayLayer.layer_id — Public layer UUID.
	- QPaneSceneOverlayLayer.image_id — Catalog image UUID.
	- QPaneSceneOverlayLayer.role — Host role copied from the layer.
	- QPaneSceneOverlayLayer.metadata — Opaque metadata copied from the layer.
	- QPaneSceneOverlayLayer.placement — Scene-coordinate placement.
	- QPaneSceneOverlayLayer.source_size — Resolved source raster size.
	- QPaneSceneOverlayLayer.transform — Source-pixel to widget-coordinate transform.
	- QPaneSceneOverlayLayer.panel_bounds — Layer bounds in widget coordinates.
	- QPaneSceneOverlayLayer.visible — Whether the rendered layer is visible.
- qpane.PanelHitTest — Hit-test metadata from `QPane.panelHitTest`.
	- PanelHitTest.panel_point — Panel-space position that was tested.
	- PanelHitTest.raw_point — Unclamped image-space coordinate as float.
	- PanelHitTest.clamped_point — Image-space coordinate clamped to image bounds.
	- PanelHitTest.inside_image — True when the raw point lies inside the image.

## Catalog and Navigation

### Catalog Management
- QPane.imageMapFromLists — Build an ordered catalog mapping from images plus optional paths/IDs; values are `CatalogEntry` objects; length mismatches raise `ValueError`.
- QPane.setImagesByID — Replace the catalog and set the current image in one call.
- QPane.clearImages — Drop the entire catalog and show the placeholder/blank view.
- QPane.removeImageByID — Remove a single catalog entry without rebuilding.
- QPane.removeImagesByID — Remove multiple entries without rebuilding.

### Navigation & Current State
- QPane.setCurrentImageID — Navigate to a specific UUID (or `None` to clear); unknown IDs no-op.
- QPane.currentImageID — Return the current catalog UUID (or None when empty).
- QPane.currentImage — Return the current `QImage`, or None when no image is selected.
- QPane.currentImagePath — Return the current image path (or None when missing).
- QPane.placeholderActive — Return True when the placeholder policy is active.

### Catalog Queries
- QPane.imageIDs — List all catalog UUIDs in order.
- QPane.hasImages — Quick guard to see if any images are loaded.
- QPane.allImages — Return all catalog images in order.
- QPane.allImagePaths — Return all catalog paths in order.
- QPane.imagePath — Return the path for a specific ID (or None when missing).
- QPane.getCatalogSnapshot — Return structured catalog state (entries, order, linked groups, active IDs, mask capability) for host consumption.

### Linked Views
- QPane.setAllImagesLinked — Link every image into one pan/zoom group (requires 2+ entries).
- QPane.setLinkedGroups — Define custom linked groups with `LinkedGroup` objects; invalid/overlapping groups are ignored.
- QPane.linkedGroups — Read current linked groups as `LinkedGroup` instances.

See also: [Catalog and Navigation](catalog-and-navigation.md) and [Interaction Modes](interaction-modes.md) for how linking interacts with tools.

## Scene Composition
- QPane.composeScene — Store a host-authored `QPaneSceneRequest` whose raster layers reference catalog image IDs and optionally open it.
- QPane.composeSceneFromTemplate — Expand a host-owned scene template and bindings into a stored scene composition.
- QPane.fitSceneRect — Return the largest centered aspect-preserving scene rectangle inside a target rectangle.
- QPane.fillSceneRect — Return the smallest centered aspect-preserving scene rectangle covering a target rectangle; the result may extend outside the target.
- QPane.currentScene — Return QPane's normalized public scene snapshot, or None.
- QPane.sceneHitTest — Return topmost public scene-layer metadata for a widget-space point.
- QPane.registerSceneOverlay — Add a named scene overlay; order follows registration.
- QPane.unregisterSceneOverlay — Remove a scene overlay; no-op if it is absent.
- QPane.sceneOverlays — Return a read-only snapshot of registered scene overlays; use register/unregister helpers to change it.

Layered scene compositions use catalog-backed layers only. QPane resolves those layers through the normal pyramid and tile rendering path. Hit testing is passive; calling `QPane.setCurrentImageID` after a scene hit opens the selected image's generated default composition.

See also: [Scene Composition](scenes.md) and [Extensibility](extensibility.md).

## Compositions
- QPane.compose — Create and open a persistent composition from one or two catalog image IDs.
- QPane.openComposition — Open an existing composition UUID.
- QPane.currentCompositionID — Return the active composition UUID, or None.
- QPane.compositionIDs — Return composition UUIDs in browser order.
- QPane.getCompositionSnapshot — Return composition rows for host browsers.
- QPane.removeComposition — Remove a host-created explicit composition; generated default catalog compositions are removed with their catalog image.

Loading catalog images creates generated one-image default compositions. `setCurrentImageID(image_id)` remains supported and opens that image's default composition. `currentImageID`, `currentImage`, `currentImagePath`, and `imageIDs` remain catalog/source APIs.

See also: [Catalog and Navigation](catalog-and-navigation.md).

## Comparison
- QPane.setComparisonImageID — Use an existing catalog image as the comparison reveal source.
- QPane.clearComparisonImage — Disable comparison rendering.
- QPane.setComparisonSplit — Set the normalized split position and optional `ComparisonOrientation`.
- QPane.comparisonState — Return the active `ComparisonState` snapshot.
- QPane.comparisonDividerInteractive — Return whether built-in comparison-divider dragging is enabled.
- QPane.setComparisonDividerInteractive — Enable or disable built-in split-boundary dragging.
- QPane.comparisonDividerState — Return `ComparisonDividerState` for host-owned divider drawing.

Comparison state belongs to the active composition. Opening another composition reports that composition's comparison state, and returning to a compared composition restores its source, split, and orientation.

While comparison is active, Fit, 1:1 zoom, pan limits, and minimum zoom use the larger compared image as the authority. Comparison is intended for same-shaped or closely matching images.

See also: [Catalog and Navigation](catalog-and-navigation.md).

## Diagnostics
- QPane.diagnosticsOverlayEnabled — Read whether the diagnostics HUD is visible.
- QPane.setDiagnosticsOverlayEnabled — Enable or disable the diagnostics HUD.
- QPane.diagnosticsDomains — List available diagnostics domains.
- QPane.diagnosticsDomainEnabled — Read whether a given domain is enabled; raises when the domain is unavailable.
- QPane.setDiagnosticsDomainEnabled — Enable or disable a domain; raises when the domain is unavailable.

See also: [Diagnostics](diagnostics.md).

## Masks and SAM
### Masks
- QPane.maskFeatureAvailable — Check whether the mask feature is installed.
- QPane.activeMaskID — Read the active mask UUID (or None).
- QPane.maskIDsForImage — List mask UUIDs for the given/current image.
- QPane.listMasksForImage — Return mask metadata as a tuple (ID, color, label, opacity, membership, active).
- QPane.createBlankMask — Create a transparent mask layer for the current image.
- QPane.loadMaskFromFile — Import a mask file and return its UUID on success.
- QPane.removeMaskFromImage — Detach a mask from an image and clean up caches.
- QPane.setActiveMaskID — Select a mask for editing (or clear with None).
- QPane.getActiveMaskImage — Snapshot the active mask as a grayscale image.
- QPane.getMaskUndoState — Return a `qpane.MaskUndoState` snapshot with undo/redo depth for a mask ID.
- QPane.setMaskProperties — Update mask color and/or opacity for an existing mask.
- QPane.prefetchMaskOverlays — Queue background colorization for a specific image's mask renders.
- QPane.cycleMasksForward — Rotate the mask stack forward for the current image.
- QPane.cycleMasksBackward — Rotate the mask stack backward for the current image.
- QPane.undoMaskEdit — Undo the last mask edit when a mask is active.
- QPane.redoMaskEdit — Redo the last reverted mask edit when a mask is active.
- QPane.CONTROL_MODE_DRAW_BRUSH — Built-in brush mode for mask painting.

### SAM
- QPane.samFeatureAvailable — Check whether the SAM feature is installed.
- QPane.samCheckpointReady — Check whether the resolved SAM checkpoint exists on disk.
- QPane.samCheckpointPath — Return the resolved SAM checkpoint path when available.
- QPane.samCheckpointStatusChanged — Signal that reports SAM checkpoint readiness changes (status, path); `"downloading"` also covers integrity verification when a hash is required.
- QPane.samCheckpointProgress — Signal that reports checkpoint download progress (downloaded, total or None).
- QPane.refreshSamFeature — Reinstall SAM tooling using the current configuration snapshot.
- QPane.CONTROL_MODE_SMART_SELECT — Built-in smart-select mode using SAM predictions.

See also: [Masks and SAM](masks-and-sam.md) and [Interaction Modes](interaction-modes.md).

## Extensibility

### Overlays
- QPane.registerOverlay — Add a named overlay; order follows registration.
- QPane.unregisterOverlay — Remove an overlay; no-op if it is absent.
- QPane.contentOverlays — Return a read-only snapshot of registered content overlays; use register/unregister helpers to change it.
- QPane.registerSceneOverlay — Add a named scene overlay for active layered scene composition layers.
- QPane.unregisterSceneOverlay — Remove a scene overlay; no-op if it is absent.
- QPane.sceneOverlays — Return a read-only snapshot of registered scene overlays.
- QPane.overlaysSuspended — Report whether overlays are temporarily suppressed.
- QPane.overlaysResumePending — Indicate overlays should resume after activation work.
- QPane.resumeOverlays — Resume overlays without forcing a repaint.
- QPane.resumeOverlaysAndUpdate — Resume overlays and schedule a repaint.
- QPane.maybeResumeOverlays — Resume overlays when pending activation work completes.

### Tool Registration
- QPane.registerTool — Register a custom tool/control mode (unique ID required).
- QPane.unregisterTool — Remove a custom tool; cannot remove the active mode or built-ins.
- QPane.registerCursorProvider — Attach a cursor provider to a control mode.
- QPane.unregisterCursorProvider — Remove a cursor provider and refresh if active.

### ExtensionTool API
- qpane.ExtensionTool — Base class for custom tools; emit `self.signals` requests to pan, zoom, or repaint.
- ExtensionTool.activate — Called when the tool becomes active; receives dependency hooks.
- ExtensionTool.deactivate — Called when the tool is deactivated so it can clean up.
- ExtensionTool.mousePressEvent — Handle pointer press events forwarded by QPane.
- ExtensionTool.mouseMoveEvent — Handle pointer move events forwarded by QPane.
- ExtensionTool.mouseReleaseEvent — Handle pointer release events forwarded by QPane.
- ExtensionTool.mouseDoubleClickEvent — Optional double-click handling.
- ExtensionTool.wheelEvent — Handle wheel or trackpad gestures forwarded by QPane.
- ExtensionTool.enterEvent — Optional cursor-enter handling.
- ExtensionTool.leaveEvent — Optional cursor-leave handling.
- ExtensionTool.keyPressEvent — Optional key press handling.
- ExtensionTool.keyReleaseEvent — Optional key release handling.
- ExtensionTool.draw_overlay — Optional overlay paint hook for the active tool.
- ExtensionTool.getCursor — Return a custom cursor or None to defer to cursor providers.

### Tool Signals
- qpane.ExtensionToolSignals — Signal hub exposed on `ExtensionTool` for requesting QPane actions.
- ExtensionTool.signals — ExtensionToolSignals instance used to emit tool requests.
- ExtensionToolSignals.pan_requested — Ask QPane to pan to a new QPointF.
- ExtensionToolSignals.zoom_requested — Ask QPane to zoom around a QPointF anchor.
- ExtensionToolSignals.repaint_overlay_requested — Ask QPane to repaint overlays.
- ExtensionToolSignals.cursor_update_requested — Ask QPane to refresh the cursor.

These helpers delegate through the same hook layer QPane uses internally, keeping the public surface stable while feature installers share signatures.

See also: [Extensibility](extensibility.md) and [Interaction Modes](interaction-modes.md).

## View State & Geometry
- QPane.currentZoom — Read the current zoom factor (float) as a device-pixel normalized value. Matches the payload emitted via `QPane.zoomChanged`.
- QPane.setZoomFit — Fit the current image to the viewport and recenter pan.
- QPane.setZoom1To1 — Snap zoom to native scale while keeping `anchor` steady when provided.
- QPane.applyZoom — Clamp zoom requests and remap unity to the device-native scale.
- QPane.viewportRectChanged — `QRectF` signal fired whenever the physical viewport changes size (resizes or monitor/DPR changes). Emits once after initialization so status bars and overlays can seed layout state before user interaction.
- QPane.currentViewportRect — Returns the most recent physical viewport rect snapshot, falling back to the live `physicalViewportRect()` when no emission occurred yet.
- QPane.panelHitTest — Facade helper returning the DPR-aware `PanelHitTest` metadata (raw/clamped coordinates plus inside-image flag) for a panel-space `QPoint`.
- QPane.sceneHitTest — Return scene-layer hit metadata for a panel-space `QPoint` when a layered scene composition is active.

See also: [Catalog and Navigation](catalog-and-navigation.md) and [Interaction Modes](interaction-modes.md).

## Signals and Events

### Navigation & Catalog
- QPane.imageLoaded — Path payload (empty when unknown) emitted after a swap applies.
- QPane.currentImageChanged — Image UUID payload emitted after navigation completes.
- QPane.catalogChanged — `CatalogMutationEvent` payload emitted after catalog mutations.
- QPane.catalogSelectionChanged — Image UUID or `None` payload emitted when selection changes.
- QPane.linkGroupsChanged — Emit with no payload when link definitions change.
- QPane.comparisonChanged — `ComparisonState` payload emitted after comparison source, split, or orientation changes.
- QPane.compositionChanged — `CompositionSnapshot` payload emitted after composition records change.
- QPane.compositionSelectionChanged — Composition UUID or `None` payload emitted when selection changes.
- QPane.sceneChanged — `QPaneScene` or `None` payload emitted when the normalized active render scene changes.

### View State
- QPane.zoomChanged — Float payload emitted when viewport zoom changes; seeds once during initialization so listeners can prime UI without peeking at the viewport.
- QPane.viewportRectChanged — `QRectF` payload emitted when the physical viewport size or device pixel ratio changes (resize/show/screen hop) so overlays and tiles stay aligned.

### Masks
- QPane.maskSaved — `qpane.MaskSavedPayload` (`mask_id`, `path`) emitted after a mask autosave completes.
- QPane.maskUndoStackChanged — Mask UUID (`uuid.UUID`) payload emitted when a mask undo stack mutates.

### Diagnostics
- QPane.diagnosticsOverlayToggled — Bool payload emitted when the diagnostics HUD visibility changes.
- QPane.diagnosticsDomainToggled — `(domain: str, enabled: bool)` payload emitted when a diagnostics domain toggles.

### SAM
- QPane.samCheckpointStatusChanged — `(status: str, path: Path)` payload emitted during SAM checkpoint readiness changes (`downloading`, `ready`, `failed`, `missing`); `"downloading"` also covers integrity verification when a hash is required.
- QPane.samCheckpointProgress — `(downloaded: int, total: int | None)` payload emitted during SAM checkpoint downloads.

See also: [Catalog and Navigation](catalog-and-navigation.md), [Diagnostics](diagnostics.md), and [Masks and SAM](masks-and-sam.md).
