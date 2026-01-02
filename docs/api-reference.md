**← Previous:** [Extensibility](extensibility.md)

# API Reference (Facade)

Quick index to the QPane facade. Each entry is a short reminder; use the guides for deeper narrative context.

**Jump within this file:**
* [QPane Setup and Settings](#qpane-setup-and-settings)
* [Config](#config)
* [Types](#types)
* [Catalog and Navigation](#catalog-and-navigation)
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
- QPane.setControlMode — Switch to a registered mode; unknown IDs are ignored.
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

### Data Structures
- qpane.CatalogEntry — Structured catalog value containing an image and optional path.
- qpane.LinkedGroup — Linked-view group descriptor with a stable UUID and members.
- qpane.MaskInfo — Mask metadata shape returned by mask helpers.
- qpane.DiagnosticRecord — Label/value diagnostic entry used in overlays.
- qpane.CatalogMutationEvent — Catalog mutation payload emitted on catalog changes.
- qpane.CatalogSnapshot — Structured catalog state (catalog entries, linked groups, ordering, active IDs).
- qpane.OverlayState — Stable overlay snapshot passed to `draw_fn`.
	- OverlayState.zoom — Current zoom factor.
	- OverlayState.qpane_rect — Widget-space bounds of the viewer.
	- OverlayState.physical_viewport_rect — Device-pixel viewport bounds.
	- OverlayState.transform — Image-to-widget transform for coordinate anchoring.
	- OverlayState.current_pan — Current pan offset in widget space.
	- OverlayState.source_image — Resolved image used for the current render pass.
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
- QPane.currentImage — Return the current image object (or None when empty).
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
- QPane.prefetchMaskOverlays — Queue background colorization for a specific image's masks.
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
- QPane.contentOverlays — Return the current overlay registry (name -> draw_fn).
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

These helpers delegate through the same hook layer QPane uses internally, keeping the public
surface stable while feature installers share signatures.

See also: [Extensibility](extensibility.md) and [Interaction Modes](interaction-modes.md).

## View State & Geometry
- QPane.currentZoom — Read the current zoom factor (float) as a device-pixel normalized value. Matches the payload emitted via `QPane.zoomChanged`.
- QPane.setZoomFit — Fit the current image to the viewport and recenter pan.
- QPane.setZoom1To1 — Snap zoom to native scale while keeping `anchor` steady when provided.
- QPane.applyZoom — Clamp zoom requests and remap unity to the device-native scale.
- QPane.viewportRectChanged — `QRectF` signal fired whenever the physical viewport changes size (resizes or monitor/DPR changes). Emits once after initialization so status bars and overlays can seed layout state before user interaction.
- QPane.currentViewportRect — Returns the most recent physical viewport rect snapshot, falling back to the live `physicalViewportRect()` when no emission occurred yet.
- QPane.panelHitTest — Facade helper returning the DPR-aware `PanelHitTest` metadata (raw/clamped coordinates plus inside-image flag) for a panel-space `QPoint`.

See also: [Catalog and Navigation](catalog-and-navigation.md) and [Interaction Modes](interaction-modes.md).

## Signals and Events

### Navigation & Catalog
- QPane.imageLoaded — Path payload (empty when unknown) emitted after a swap applies.
- QPane.currentImageChanged — Image UUID payload emitted after navigation completes.
- QPane.catalogChanged — `CatalogMutationEvent` payload emitted after catalog mutations.
- QPane.catalogSelectionChanged — Image UUID or `None` payload emitted when selection changes.
- QPane.linkGroupsChanged — Emit with no payload when link definitions change.

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
