**← Previous:** [Diagnostics](diagnostics.md)

# Extensibility

QPane isn't just a viewer; it's a canvas. Whether you need to draw a scale bar, change the cursor based on keyboard modifiers, or build a completely new interaction mode (like a "measurement tape"), QPane exposes the same hooks it uses internally. This means your extensions feel native, performant, and integrated.

## 1. Overlays: Draw on Top
Want to add a watermark, a grid, or a "Zoom: 100%" label? Overlays let you paint directly onto the canvas after the content stack is rendered but before the tool layer.

* **Register:** `QPane.registerOverlay(name, draw_fn)` adds host chrome to the overlay stack after image content renders.
* **Remove:** `QPane.unregisterOverlay(name)` removes a named overlay during cleanup without disturbing other callbacks.

Your `draw_fn` receives a `QPainter` and an `OverlayState` object. The painter operates in **Widget Space** (physical pixels), so `0,0` is the top-left corner of the viewer widget.

Use the overlay state for the job your chrome is doing:

* `OverlayState.qpane_rect` anchors HUD elements to the viewer bounds when the overlay is positioned like normal widget chrome.
* `OverlayState.zoom` lets text or stroke sizes follow the current view.
* `OverlayState.transform` maps image coordinates into widget coordinates for image-anchored artwork.
* `OverlayState.current_pan` is available when host chrome follows pan state directly.
* `OverlayState.physical_viewport_rect` gives device-pixel-aware bounds for overlays that need crisp alignment on high-DPI screens.
* `OverlayState.source_image` is the resolved base catalog raster for this overlay pass, not a flattened export of rendered masks or public overlays.

```python
from PySide6.QtCore import Qt

def draw_hud(painter, state):
    # 'state.qpane_rect' is the viewport in widget pixels.
    # We use it to anchor text to the top-left corner.
    rect = state.qpane_rect.adjusted(10, 10, -10, -10)
    painter.setPen(Qt.yellow)
    painter.drawText(rect, Qt.AlignTop | Qt.AlignLeft, f"Zoom: {state.zoom:.2f}x")

# Add it to the stack
viewer.registerOverlay("zoom_hud", draw_hud)

# Trigger a repaint so it shows up immediately
viewer.update()
```

> **Pro Tip:** Registration order determines draw order. If you register "grid" then "labels", the labels will draw on top of the grid.

Overlay hooks may be suspended during navigation or activation workflows so visual layers stay consistent with the active image. Most hosts only need to register callbacks and let QPane manage the pause, but these methods are available when a host coordinates async UI around navigation:

* `QPane.overlaysSuspended()` reports whether overlay drawing is currently paused before the host coordinates related UI work.
* `QPane.overlaysResumePending()` reports whether QPane is waiting for activation work before drawing resumes.
* `QPane.resumeOverlays()` allows drawing to resume on the next paint.
* `QPane.resumeOverlaysAndUpdate()` resumes drawing and schedules a repaint when the host needs the overlay result visible immediately.
* `QPane.maybeResumeOverlays()` resumes only when pending activation work has completed.
* `QPane.contentOverlays()` returns a read-only snapshot of registered content overlays for host diagnostics or cleanup.

Use `QPane.registerOverlay()` and `QPane.unregisterOverlay()` to change content overlay registration.

### Scene Overlays
Use scene overlays when the host needs chrome tied to active layered scene composition layers rather than the base image. Register them with `QPane.registerSceneOverlay(name, draw_fn)` and remove them with `QPane.unregisterSceneOverlay(name)`.

`QPane.sceneOverlays()` returns a read-only snapshot of registered scene overlays. Use `QPane.registerSceneOverlay()` and `QPane.unregisterSceneOverlay()` to change scene overlay registration.

Scene overlay callbacks receive a `QPaneSceneOverlayState`. The painter uses widget logical coordinates.

Treat scene overlay state as the prepared view model for the current scene pass:

* `QPaneSceneOverlayState.zoom` mirrors the current view scale for stroke and text sizing.
* `QPaneSceneOverlayState.qpane_rect` anchors scene-level chrome to the widget.
* `QPaneSceneOverlayState.physical_viewport_rect` is available when scene chrome needs device-pixel alignment.
* `QPaneSceneOverlayState.composition_id`, `QPaneSceneOverlayState.scene_id`, and `QPaneSceneOverlayState.scene_bounds` identify the active scene being annotated.
* `QPaneSceneOverlayState.layers` is the ordered collection the overlay iterates when drawing per-layer chrome.

Each `QPaneSceneOverlayLayer` describes one rendered layer. Use the geometry fields first, then layer identity when deciding labels or host behavior:

* `QPaneSceneOverlayLayer.panel_bounds` gives the widget-space rectangle for labels and outlines.
* `QPaneSceneOverlayLayer.transform` maps source pixels into widget coordinates.
* `QPaneSceneOverlayLayer.source_size` reports the resolved raster size for source-pixel math.
* `QPaneSceneOverlayLayer.placement` provides the scene rectangle for scene-coordinate labels.
* `QPaneSceneOverlayLayer.visible` lets the overlay skip hidden layers instead of drawing stale labels or outlines.
* `QPaneSceneOverlayLayer.layer_id`, `QPaneSceneOverlayLayer.image_id`, `QPaneSceneOverlayLayer.role`, and `QPaneSceneOverlayLayer.metadata` connect the overlay back to host scene data.

```python
from PySide6.QtCore import Qt

def draw_scene_labels(painter, state):
    painter.setPen(Qt.white)
    for layer in state.layers:
        painter.drawText(layer.panel_bounds.adjusted(8, 8, -8, -8), layer.role)

viewer.registerSceneOverlay("scene_labels", draw_scene_labels)
```

Scene overlays are observational. They do not render image pixels and they do not own selection or navigation behavior.

## 2. Cursors: Context-Aware Feedback
Static cursors are boring. QPane uses **Cursor Providers**-functions that decide what the cursor should look like based on the current state. This lets you show a "forbidden" sign when hovering over invalid areas or a "crosshair" only when a specific key is held.

* **Register:** `QPane.registerCursorProvider(mode, provider)` attaches cursor logic to one control mode.
* **Remove:** `QPane.unregisterCursorProvider(mode)` detaches that provider and refreshes the cursor if needed.

The provider function is called whenever the mouse moves or state changes. If it returns `None`, QPane falls back to the default cursor for that mode.

```python
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

def smart_cursor(qpane):
    # Show a crosshair only if we are zoomed in past 100%
    if qpane.currentZoom() > 1.0:
        return QCursor(Qt.CrossCursor)
    return None  # Fallback to standard arrow

viewer.registerCursorProvider("inspect_mode", smart_cursor)
```

## 3. Custom Tools: Take Control
Tools define how the viewer responds to input. While QPane comes with `panzoom`, `cursor`, and `brush` modes, you can register your own to handle clicks, drags, and key presses exactly how you want.

* **Register:** `QPane.registerTool(mode, factory, *, on_connect=None, on_disconnect=None)` installs a custom mode and optional signal wiring callbacks.
* **Remove:** `QPane.unregisterTool(mode)` removes a custom mode after the host switches away from it.

A "Tool" is just a class that receives events (like `mousePressEvent`). You register a `factory` (a function that creates your tool) so QPane can spin it up when the mode activates.

> **Heads-up:** You cannot unregister the currently active tool. Always switch the viewer to a safe mode (like `QPane.CONTROL_MODE_PANZOOM`) before removing your custom tool.

### Tool Lifecycle & Events
Tool entry points live on `ExtensionTool`. `ExtensionTool.activate` runs when QPane switches into the tool, and `ExtensionTool.deactivate` runs when the tool is left so the host can clear transient state.

For pointer workflows, override the methods that match the interaction:

* Start a drag or click action in `ExtensionTool.mousePressEvent` when the pointer first commits to the tool workflow.
* Update drag or hover state in `ExtensionTool.mouseMoveEvent` as the pointer moves through the viewer.
* Finish the action in `ExtensionTool.mouseReleaseEvent` when the tool should commit or cancel pointer state.
* Treat `ExtensionTool.mouseDoubleClickEvent` as a separate command only when double-click has a special meaning.
* Use `ExtensionTool.wheelEvent` for wheel or trackpad gestures that belong to the custom mode.
* Use `ExtensionTool.enterEvent` when the pointer enters the widget and the tool needs hover setup.
* Use `ExtensionTool.leaveEvent` when the pointer leaves the widget and transient hover state should clear.
* Use `ExtensionTool.keyPressEvent` when keyboard modifiers change how the active tool behaves.
* Use `ExtensionTool.keyReleaseEvent` when releasing a modifier should restore the normal tool behavior.
* Use `ExtensionTool.draw_overlay` for active-tool chrome and `ExtensionTool.getCursor` when the tool itself supplies a cursor.

`ExtensionTool` ships with concrete no-op implementations for every handler, so you only need to override the events you care about. Overrides must follow the expected signatures exactly.

### Tool Signals
Emit requests through `ExtensionTool.signals`, which is an `ExtensionToolSignals` instance owned by the tool. Signals keep custom tools from reaching into QPane internals:

* `ExtensionToolSignals.pan_requested` asks QPane to pan without the tool mutating viewport internals.
* `ExtensionToolSignals.zoom_requested` asks QPane to zoom around an anchor point using the normal viewport rules.
* `ExtensionToolSignals.repaint_overlay_requested` schedules another paint when tool chrome changes outside the normal paint loop.
* `ExtensionToolSignals.cursor_update_requested` refreshes the cursor when state changed outside a normal mouse move.

## Putting it Together: The "Lens" Tool
Let's build a "Lens" feature: a custom mode where the mouse drives a magnifying glass overlay. This combines all three extension points.

```python
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QCursor, QColor
from qpane import ExtensionTool

# 1. The Tool: Tracks mouse position
# Inherit from ExtensionTool and override only the events you need.
class LensTool(ExtensionTool):
    def __init__(self, qpane):
        super().__init__()  # Initialize signal bus
        self.qpane = qpane

    def mouseMoveEvent(self, event):
        self.qpane.update()  # Request redraw so the lens follows the mouse

    # Optional overrides (base class already provides no-op handlers)
    def mousePressEvent(self, event): pass
    def mouseReleaseEvent(self, event): pass
    def wheelEvent(self, event): pass

# 2. The Overlay: Draws the circle
def draw_lens(painter, state):
    # Only draw when our mode is active
    if viewer.getControlMode() != "lens":
        return

    # Get the cursor position in widget coordinates
    cursor_pos = viewer.mapFromGlobal(QCursor.pos())
    
    # Draw a yellow circle at the mouse position
    painter.setPen(Qt.yellow)
    painter.drawEllipse(cursor_pos, 50, 50)

# 3. The Cursor: Hides the default pointer so the lens is clear
def lens_cursor(qpane):
    return QCursor(Qt.BlankCursor)

# 4. Wire it up
# Note: The factory must be a callable that returns the tool instance.
viewer.registerTool("lens", lambda: LensTool(viewer))
viewer.registerOverlay("lens_visual", draw_lens)
viewer.registerCursorProvider("lens", lens_cursor)

# Activate!
viewer.setControlMode("lens")
```

Tool actions are requested by emitting signals on `self.signals`. For example, emit `repaint_overlay_requested` to force an overlay redraw or `pan_requested`/`zoom_requested` to ask QPane to move the viewport. The signal hub is `ExtensionToolSignals`, exposed as `self.signals` on every `ExtensionTool`.

> **Try it Live:** The QPane demo includes a playground for these hooks. Run `python examples/demo.py` and check out the **Hooks** menu.
>
> *Note: The demo playground uses a simplified helper to let you edit just the drawing logic live. When building a permanent tool, use the full `ExtensionTool` structure shown above.*

## Rules of the Road
To keep your extensions playing nicely with the rest of QPane:

1. **Unregister Safely:** `unregisterOverlay` and friends are idempotent—they won't crash if the item is already gone. It's safe to call them in your cleanup code even if you aren't sure they were registered.
2. **Watch Your Coordinates:**
    * **Painters** in overlays use **Widget Space** (physical pixels).
    * **`state.qpane_rect`** is in **Widget Space**.
    * Use `qpane.panelHitTest(pos)` if you need to convert mouse clicks to image pixels.
3. **Performance Matters:** Overlays run on the main render loop. Keep your `draw_fn` fast—avoid loading files or heavy math inside the draw call.

## Related Docs
* [Interaction Modes](interaction-modes.md): Learn about the built-in tools you can switch to.
* [Catalog and Navigation](catalog-and-navigation.md): Understand how your tools interact with image loading.

**Continue →** [API Reference](api-reference.md)
