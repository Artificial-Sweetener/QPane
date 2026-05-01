**← Previous:** [Catalog and Navigation](catalog-and-navigation.md)

# Interaction Modes

Modes define the relationship between the pointer and the image. Whether you are building a read-only viewer, a mask editor, or an inpainting workflow, the Control Mode determines what happens when a user clicks, drags, or scrolls.

## Switching Modes
Use `QPane.setControlMode` to switch tools. You can check which mode is active with `QPane.getControlMode` or see the full list of registered tools via `QPane.availableControlModes`.

```python
from qpane import QPane

# Switch to Pan/Zoom (default navigation)
viewer.setControlMode(QPane.CONTROL_MODE_PANZOOM)

# Switch to a static cursor (good for read-only states)
viewer.setControlMode(QPane.CONTROL_MODE_CURSOR)
```

> **Heads-up:** `QPane.setControlMode` will ignore requests for mask or selection modes if the catalog is empty (check `QPane.placeholderActive()` to see if the placeholder is currently shown).

### Building a Toggle
QPane doesn't cycle modes automatically, but you can easily build a toggle button.

```python
# Cycle through available modes
modes = viewer.availableControlModes()
current = viewer.getControlMode()

if modes:
    # Find current index and step forward
    next_index = (modes.index(current) + 1) % len(modes)
    viewer.setControlMode(modes[next_index])
```

## Built-in Modes
QPane comes with core navigation modes ready to use. You can refer to them via the `ControlMode` enum or the string constants on `QPane`.

* **Pan/Zoom (`ControlMode.PANZOOM`):** The default. Users drag to pan and scroll to zoom. Double-clicking toggles between "fit" and "1:1" views. Wheel steps snap to 100% when crossing it, so you never skip the native scale. Use `QPane.CONTROL_MODE_PANZOOM` when a toolbar or shortcut should return to normal navigation.
* **Cursor (`ControlMode.CURSOR`):** "Look but don't touch." The viewport stays locked, and drag/scroll events are ignored. Use `QPane.CONTROL_MODE_CURSOR` for read-only states, kiosks, or hosts that handle pointer events outside the viewer.

If you have installed the `mask` or `sam` extras, you will also see `ControlMode.DRAW_BRUSH` and `ControlMode.SMART_SELECT`. Use `QPane.CONTROL_MODE_DRAW_BRUSH` for the raster mask brush and `QPane.CONTROL_MODE_SMART_SELECT` for the SAM box-selection tool. These modes are unavailable when the catalog is empty. See [Masks and SAM](masks-and-sam.md) for details.

## View State
You can control how placeholder and startup image fitting works using `ZoomMode`. `ZoomMode.FIT` keeps the whole image visible, `ZoomMode.LOCKED_ZOOM` keeps the configured zoom value stable for inspection workflows, and `ZoomMode.LOCKED_SIZE` keeps the rendered placeholder size stable when the viewport changes.

Choose `PlaceholderScaleMode` based on what the empty viewer should measure against:

* `PlaceholderScaleMode.AUTO` uses QPane's default placeholder rule for normal host applications.
* `PlaceholderScaleMode.LOGICAL_FIT` fits the logical widget viewport when host chrome is measured in widget coordinates.
* `PlaceholderScaleMode.PHYSICAL_FIT` fits the device-pixel viewport when high-DPI pixel alignment matters.
* `PlaceholderScaleMode.RELATIVE_FIT` scales the placeholder relative to the configured viewport policy for specialized placeholder layouts.

Want to know how deep you are? `QPane.currentZoom` tells you the current multiplier.

## Interaction Rules
* **Persistence:** Modes stick around. If you switch to "Brush" mode and navigate to the next image, you remain in "Brush" mode.
* **Overlays:** Switching modes often changes the cursor and may show or hide overlays (like the brush circle).
* **Validation:** `setControlMode` safely handles missing features (like trying to use Smart Select without SAM installed) by logging a warning and ignoring the request. However, it raises a `ValueError` if passed an unknown mode ID.
* **Event Delivery:** Tools always expose the full Qt event surface via concrete no-op handlers, so dispatch is direct and predictable—override only what you need.
### Comparison Divider Interaction
Split comparison uses the normal viewer modes and belongs to the active composition. QPane owns built-in split-boundary dragging as interaction chrome, not image content. QPane does not paint a divider line or handle; the visible boundary between the base and comparison images is the drag target.

Host controls can call `QPane.setComparisonSplit` directly to move the boundary. Use `QPane.comparisonDividerState` when the host wants to draw its own divider overlay from authoritative geometry. `QPane.setComparisonDividerInteractive` disables or restores built-in dragging, and `QPane.comparisonDividerInteractive` reports the current interaction setting for checkboxes or toolbar state.

> **Pro Tip:** Want the best of both worlds? QPane includes a built-in "hold Space to pan" feature that temporarily switches to `CONTROL_MODE_PANZOOM` while the widget has focus. For a global implementation that works even when focus is elsewhere (like in the demo), see `examples/demonstration/demo_window.py`.

## Related Docs
* [Masks and SAM](masks-and-sam.md): Details on the brush and smart selection tools.
* [Extensibility](extensibility.md): How to register your own custom tools and cursors.
* [Catalog and Navigation](catalog-and-navigation.md): Managing the images you are interacting with.

**Continue →** [Masks and SAM](masks-and-sam.md)
