**← Previous:** [Masks and SAM](masks-and-sam.md)

# Diagnostics & Debugging

## See the Brain at Work
QPane does a lot of heavy lifting in the background—prefetching tiles, managing memory budgets, and running neural networks. The **Diagnostics Overlay** lets you see that brain at work. Turn it on when you’re tuning performance, debugging stutter, or just curious about how your cache configuration is behaving.

## Quick Start
You can toggle the overlay at any time, even before the window is visible, using `QPane.setDiagnosticsOverlayEnabled`. To check the current state, call `QPane.diagnosticsOverlayEnabled`.

```python
# Enable the HUD during setup
viewer.setDiagnosticsOverlayEnabled(True)

# Check visibility
is_visible = viewer.diagnosticsOverlayEnabled()
```

> **Heads-up:** The overlay draws directly on top of your content. It’s great for development, but you’ll likely want to hide it for end users.

## The HUD
When enabled, the overlay always shows a few "base" rows at the top:
*   **Paint:** How long the last render took.
*   **Zoom:** Current zoom level and pixel ratio.
*   **Smooth Zoom FPS:** Target frame rate for zoom animations, with "(Fallback)" when the monitor refresh rate is unavailable or disabled.
*   **Smooth Zoom Frame:** Effective frame interval and whether the last zoom burst used the burst path.
*   **Cache:** Total memory usage against the budget.
*   **Swap:** Latency of the last navigation event.
*   **Pyramid Level:** Which resolution tier is currently active.

> Rows only appear for features that are installed. For example, SAM rows stay hidden until a SAM manager is attached; mask rows hide when masking is disabled. The overlay surfaces whatever data is available without requiring host-side wiring.

Below these, you can enable specific **Domains** to drill down into subsystems.

## Drill Down (Domains)
Diagnostics are organized into domains so you can focus on what matters. Use `qpane.DiagnosticsDomain` members (or their string values) to toggle them.

### Memory & Resources
*   **Cache (`DiagnosticsDomain.CACHE`):** Drill down into memory usage.
    *   *What to look for:* While the HUD tracks your total budget, enabling this domain adds `Tiles` and `Pyramids` rows to break down exactly which subsystems are consuming that memory.
*   **Swap (`DiagnosticsDomain.SWAP`):** Monitor data flow.
    *   *What to look for:* Dig deeper into the navigation latency shown in the HUD. `Prefetch` tracks background loading, while `Swap|Renderer` reveals scroll miss rates to help tune your prefetch settings.

### Performance & Health
*   **Executor (`DiagnosticsDomain.EXECUTOR`):** Check background workers.
    *   *What to look for:* `Queued` tasks and `Threads` usage. If `Executor|Rejections` climbs, you might be hitting concurrency limits.
*   **Retry (`DiagnosticsDomain.RETRY`):** Spot network or disk flakes.
    *   *What to look for:* Active retries for tiles or pyramids. A high count here usually means I/O trouble.

### Feature Tools
*   **Masks (`DiagnosticsDomain.MASK`):** Debug editing workflows.
    *   *What to look for:* `Mask Autosave` status (pending saves), `Mask Jobs` (generation queues), and `Mask|Brush` details.
*   **SAM (`DiagnosticsDomain.SAM`):** Inspect the AI engine.
    *   *What to look for:* `SAM|State` tells you if the predictor is `Ready` or warming up. `SAM|Cache` shows how many embeddings are kept in memory (per device/checkpoint path).

## Programmatic Control
You can manage these domains from your code. This is useful for building a "Developer Menu" in your host app.

*   **List available domains:** `QPane.diagnosticsDomains()` returns the list of valid strings.
*   **Check state:** `QPane.diagnosticsDomainEnabled(domain)` returns `True` if active.
*   **Toggle:** `QPane.setDiagnosticsDomainEnabled(domain, enabled)` turns a section on or off.

```python
from qpane import DiagnosticsDomain

# Enable the cache view programmatically
viewer.setDiagnosticsDomainEnabled(DiagnosticsDomain.CACHE, True)

# Or use the string ID
if "swap" in viewer.diagnosticsDomains():
    viewer.setDiagnosticsDomainEnabled("swap", True)
```

> **Pro Tip:** `setDiagnosticsDomainEnabled` raises a `ValueError` if you pass an unknown domain. Use the enum or check `diagnosticsDomains()` to stay safe.

### Understanding the Data
The overlay displays `DiagnosticRecord` items—simple label/value pairs. If you're writing a custom provider, this is the format you'll return.

## Stay in Sync
If you build a UI for these toggles, listen to QPane's signals to keep your checkboxes accurate.

*   `QPane.diagnosticsOverlayToggled`: Emits `bool` when the main overlay is shown/hidden.
*   `QPane.diagnosticsDomainToggled`: Emits `(domain: str, enabled: bool)` when a specific section changes.

```python
# Keep your menu in sync
def on_domain_toggled(domain, enabled):
    print(f"Domain {domain} is now {'on' if enabled else 'off'}")

viewer.diagnosticsDomainToggled.connect(on_domain_toggled)
```

## Related Docs
*   [Configuration](configuration.md): Enable diagnostics by default in your `Config`.
*   [Masks and SAM](masks-and-sam.md): See how AI features surface in the overlay.
*   [Extensibility](extensibility.md): Learn how to add your own custom overlays.

**Continue →** [Extensibility](extensibility.md)
