**← Previous:** [Interaction Modes](interaction-modes.md)

# Masks and SAM

QPane provides a lightweight masking engine designed for AI inpainting, region selection, and image processing workflows. Whether you are preparing masks for inpainting, redacting sensitive data, marking defects for quality control, or refining segmentation datasets, QPane gives you pixel-perfect control. Unlike a dataset labeler, QPane does not store bounding boxes, text labels, or vector shapes. Instead, it produces high-fidelity 8-bit grayscale raster masks that you can hand off directly to PyTorch, OpenCV, or Stable Diffusion pipelines.

## Setup and Availability
These features are optional extras. You'll need to install them specifically:

```bash
pip install "qpane[mask,sam]"
```

If you want your application to be robust against missing dependencies or load failures, you can check availability at runtime:

*   **Check support:** `QPane.maskFeatureAvailable()` and `QPane.samFeatureAvailable()` return `True` if the libraries are installed and loaded.
*   **Graceful degradation:** If you request `features=("mask",)` but lack dependencies, QPane initializes safely but returns `False` for these checks.

## The Mask Lifecycle
QPane treats masks as independent 8-bit grayscale layers associated with an image. You can have multiple masks per image, but only one is "active" (editable) at a time.

### Create and Load
You can start with a blank slate or import existing work.

*   **New Layer:** `QPane.createBlankMask(size)` adds a transparent layer. Pass `image.size()` to match the current image.
*   **Import:** `QPane.loadMaskFromFile(path)` reads an image from disk, converts it to a mask layer, and returns its UUID.

```python
# Create a new layer for the current image
if viewer.maskFeatureAvailable():
    size = viewer.currentImage().size()
    mask_id = viewer.createBlankMask(size)
    viewer.setActiveMaskID(mask_id)
```

### Manage Layers
*   **List:** `QPane.maskIDsForImage(image_id)` gives you the UUIDs. For more detail (like labels and colors), use `QPane.listMasksForImage(image_id)`.
    *   *Returns:* A list of `MaskInfo` objects containing `label`, `color`, `opacity`, and `is_active`.
*   **Remove:** `QPane.removeMaskFromImage(image_id, mask_id)` deletes a layer.
*   **Active State:** `QPane.activeMaskID()` returns the UUID of the layer currently receiving edits. Use `QPane.setActiveMaskID(uuid)` to switch layers.
*   **Content:** `QPane.getActiveMaskImage()` returns the actual `QImage` data of the active mask (useful for custom processing).

### Appearance
Masks are grayscale internally but rendered with a color overlay. Use `QPane.setMaskProperties` to customize how they look.

```python
viewer.setMaskProperties(
    mask_id,
    color=QColor("magenta"),
    opacity=0.5
)
```

> **Pro Tip:** QPane renders layers in a stack. Use `QPane.cycleMasksForward()` and `QPane.cycleMasksBackward()` to rotate the active layer to the top, which is great for cycling through overlapping segmentations.

## Tools: Brush and SAM
Once you have an active mask, you can start editing.

### Switching Modes
Use `QPane.setControlMode` to activate the tools.

*   **Brush:** `QPane.CONTROL_MODE_DRAW_BRUSH` enables freehand drawing. Perfect for rough defect marking or cleaning up noisy model predictions.
*   **Smart Select:** `QPane.CONTROL_MODE_SMART_SELECT` enables the SAM-powered box selector. Ideal for quickly grabbing objects for removal, redaction, or alpha matting.

```python
# Activate the brush tool
viewer.setControlMode(QPane.CONTROL_MODE_DRAW_BRUSH)
```

> **Heads-up:** These modes are disabled when the catalog is empty (placeholder active).

### Smart Select (SAM)
When a user drags a box in Smart Select mode, QPane runs the Segment Anything Model to predict a mask shape.
1.  **Predictor Loading:** The first time you use SAM on an image, QPane loads the image embedding. This happens in a background thread.
2.  **Caching:** Embeddings are cached per device and checkpoint path to make subsequent edits instant.
3.  **Merging:** The prediction is automatically merged into the active mask layer.

## Edits and History
QPane manages a robust undo/redo stack for mask operations.

*   **Actions:** `QPane.undoMaskEdit()` and `QPane.redoMaskEdit()` step through history.
*   **State:** Listen to `QPane.maskUndoStackChanged` to know when to update your UI.
*   **Counts:** Call `QPane.getMaskUndoState(mask_id)` to get the current `undo_depth` and `redo_depth`.

```python
# Update UI buttons when the stack changes
def update_buttons(mask_id):
    state = viewer.getMaskUndoState(mask_id)
    undo_btn.setEnabled(state.undo_depth > 0)
    redo_btn.setEnabled(state.redo_depth > 0)

viewer.maskUndoStackChanged.connect(update_buttons)
```

## Configuration and Autosave
You can tune performance and persistence via the `Config` object.

### Autosave
Autosave writes masks to disk as PNGs, ensuring you don't lose work. It's disabled by default.

```python
config = qpane.Config().configure(
    mask_autosave_enabled=True,
    mask_autosave_debounce_ms=500,  # Wait 500ms after last stroke
    mask_autosave_path_template="masks/{image_id}_{mask_id}.png"
)
```

*   **Debounce:** The save timer resets on every stroke, so we don't spam the disk while drawing.
*   **Creation:** Set `mask_autosave_on_creation=True` if you want empty files created immediately.
*   **Signal:** Connect to `QPane.maskSaved` to get the `(mask_id, path)` payload when a file is written. This tuple is formally known as `MaskSavedPayload`.

### Performance Tuning
*   **SAM Device:** Set `sam_device="cuda"` if you have a GPU; otherwise defaults to `"cpu"`.
*   **Caching:** `sam_cache_limit` controls how many heavy image embeddings we keep in RAM.
*   **Prefetch:** `mask_prefetch_enabled` and `sam_prefetch_depth` allow background workers to prepare data before the user navigates.
*   **Manual Prefetch:** Call `QPane.prefetchMaskOverlays(image_id)` to manually trigger warming for the next image in your sequence.

### Checkpoint Management
Checkpoint controls let you decide *when* the SAM model is fetched and *where* it lives. This matters in real apps: you might want to avoid startup stalls, ship a pre-bundled model in a managed environment, or route downloads through your own hosting or caching layer.

*   **Download Modes:** `sam_download_mode` chooses how QPane acquires the checkpoint. By default, QPane will download the MobileSAM weights in the background the first time it needs them (`"background"` mode).
    *   `"background"` downloads missing weights after startup so the UI stays responsive.
    *   `"blocking"` blocks app startup until the checkpoint is ready; pair it with a splash screen when you need SAM fully ready the moment the UI appears.
    *   `"disabled"` never downloads; your app must provide the file up front.
*   **Path vs URL:** `sam_model_path` points at a local checkpoint (for pre-provisioned models or shared caches). `sam_model_url` overrides the download source; when unset, QPane uses the MobileSAM GitHub weights and stores them under `QStandardPaths.AppDataLocation/mobile_sam.pt` unless you set a custom path.
*   **Hash Verification:** `sam_model_hash` lets you supply a SHA-256 checksum for the checkpoint; set it to `"default"` to use the built-in MobileSAM hash. When the default URL is used and `sam_model_path` is unset, the built-in hash is enforced after downloads. Custom URLs without a hash log a warning and are downloaded without integrity verification.
*   **Preflight Behavior:** When downloads are enabled, QPane checks for the file at startup and fetches it if missing; disabled mode requires the file to exist already.
*   **Readiness + Progress:** Connect to `QPane.samCheckpointStatusChanged` (`"downloading"`, `"ready"`, `"failed"`, `"missing"`) and `QPane.samCheckpointProgress` (`downloaded`, `total`) to drive UI state or show progress; `"downloading"` also covers integrity verification when a hash is required.
*   **Runtime Helpers:** `QPane.samCheckpointReady()` and `QPane.samCheckpointPath()` let you gate predictor work. Use `QPane.refreshSamFeature()` when checkpoint-related configuration changes need to reinitialize SAM.

For the complete SAM setting list and defaults, see [Configuration Reference](configuration-reference.md).

## Quick Start Recipe
Here is how to wire up a fully functional editor.

```python
import qpane

# 1. Configure
config = qpane.Config().configure(
    mask_autosave_enabled=True,
    mask_autosave_path_template="masks/{image_id}.png",
    sam_device="cpu",
    sam_download_mode="background"
)

# 2. Initialize
viewer = qpane.QPane(config=config, features=("mask", "sam"))

# 3. Wire UI
viewer.maskUndoStackChanged.connect(lambda mid: print(f"Undo stack changed for {mid}"))
viewer.maskSaved.connect(lambda mid, path: print(f"Saved {path}"))

# 4. Activate Tool (ensure image is loaded first!)
# Note: In a real app, do this after loading an image
if viewer.maskFeatureAvailable() and viewer.currentImageID():
    mask_id = viewer.createBlankMask(viewer.currentImage().size())
    viewer.setActiveMaskID(mask_id)
    viewer.setControlMode(qpane.QPane.CONTROL_MODE_SMART_SELECT)
```

## Related Docs
*   [Diagnostics](diagnostics.md): Monitor SAM worker health and cache usage.
*   [Configuration Reference](configuration-reference.md): Full list of mask and SAM settings.

**Continue →** [Diagnostics](diagnostics.md)
