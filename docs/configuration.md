**← Previous:** [Getting Started](getting-started.md)

# Configuration

## The Config Object
QPane works out of the box with defaults that balance performance and usability. It manages memory automatically and enables standard interactions. If you like, though, you can take full control using the `Config` object—whether you need to enforce strict memory limits or customize the empty placeholder.

`Config` is your control panel. It captures runtime settings like cache budgets, tiling choices, and feature flags. It behaves like a snapshot: you create it, configure it, and pass it to the `QPane` constructor. The viewer keeps its own copy, so you can reuse your config object for other windows without side effects.

### Create and Clone
Start with a default config, then branch it for different windows or user modes. Use `Config.copy()` to create an independent snapshot. This deep-copies nested structures (like cache weights) so you can tweak one config without affecting others. Use `Config.as_dict()` when a settings panel or debug log needs the current snapshot as plain JSON-serializable values.

```python
from qpane import Config

# Start fresh
base_config = Config()

# Branch for a specific use case
kiosk_config = base_config.copy()

# Debugging tip: Dump raw values to see what's set
# (This uses Config.as_dict under the hood)
print(kiosk_config.as_dict())
```

> **Pro Tip:** Always `.copy()` your base config before handing it to a new QPane. This keeps your defaults clean and prevents one window's settings from leaking into another.

## Applying Changes
### Update a Live Viewer
`QPane.applySettings` replaces the active configuration and reconfigures services. This is a "heavy" operation that may trigger repaints or cache rebuilds, so use it for mode switches rather than per-frame updates. In the demo, SAM checkpoint settings apply live only in background mode; blocking/disabled changes are queued until a restart.

You can inspect the current state via `QPane.settings` (read-only) or check `QPane.installedFeatures` to see which requested features (like "mask" or "sam") are actually available.

```python
from qpane import QPane

viewer = QPane(config=kiosk_config)

# Later, switch modes live:
viewer.applySettings(diagnostics_overlay_enabled=True)
```

### Merge Settings
Use `Config.configure` to layer changes onto a snapshot. It accepts a mapping or keyword arguments and merges them intelligently.

This method "fails fast": if you pass an unknown key (like `cahce_mode` instead of `cache`), it raises a `ValueError` immediately. It also normalizes enums, so you can pass `qpane.CacheMode.HARD` or just `"hard"`.

```python
# Layer user preferences onto your base config
user_prefs = {
    "diagnostics_overlay_enabled": True,
    "mask_undo_limit": 50,
}

config = base_config.copy().configure(user_prefs)
```

## Managing Memory (Cache)
QPane's caching engine balances performance against memory usage. You can choose between two strategies depending on your environment.

### Choosing a Strategy (`CacheMode`)
* **Auto Mode (`CacheMode.AUTO`):** The default "good citizen." It monitors system RAM (via `psutil`) and evicts tiles to keep `headroom_percent` free.
    * *Best for:* Desktop apps where QPane is the primary focus.
    * *Heads-up:* If `psutil` is missing, this falls back to a hard 1GB limit.
* **Hard Mode (`CacheMode.HARD`):** A strict budget. QPane will never use more than `budget_mb`, ignoring system pressure.
    * *Best for:* Kiosks, Docker containers, or sidebars where QPane must stay in its lane.

### Fine-Tuning
You can further tune how the budget is spent using `weights` (relative priority) and `prefetch` (background loading depth).

```python
config.configure(
    cache={
        # Strategy: strict 2GB limit
        "mode": "hard",
        "budget_mb": 2048,
        
        # Priorities: Spend 50% of budget on masks
        "weights": {
            "tiles": 20.0,
            "pyramids": 20.0,
            "masks": 50.0,
            "predictors": 10.0,
        },
        # Background work: Load 2 levels of neighbors
        "prefetch": {
            "pyramids": 2,
            "tiles": 2,
            "masks": 0,     # Disable mask prefetch
            "predictors": 0,
        },
    }
)
```

## Empty States (Placeholder)
When your catalog is empty (e.g., after `clearImages`), QPane is **blank** by default. You can configure a **Placeholder** to display a helpful landing page or a static logo instead.

Key settings include:
* `source`: Path to an image file (or `None` for default text).
* `panzoom_enabled`: Allow users to move the placeholder (great for "Drop images here" diagrams).
* `zoom_mode`: Control interaction behavior (`fit`, `locked_zoom`, `locked_size`).
* `scale_mode`: Control how the image is sized (`PlaceholderScaleMode`).
    * `PlaceholderScaleMode.LOGICAL_FIT`: Fits nicely within the widget bounds.
    * `PlaceholderScaleMode.PHYSICAL_FIT`: Uses raw device pixels.
    * `PlaceholderScaleMode.RELATIVE_FIT`: Scales relative to the window size.
    * `PlaceholderScaleMode.AUTO`: The default smart behavior.
* `display_size` / `min_display_size`: Enforce size constraints.

```python
config.configure(
    placeholder={
        "source": "assets/logo.png",
        "panzoom_enabled": False,  # Lock it in place
        "scale_mode": "logical_fit",
    }
)
```

> **Heads-up:** Tools like Masks and SAM are disabled while the placeholder is active.

## Smooth Zoom
Smooth Zoom animates the visual transition between zoom levels without changing the underlying zoom steps. It keeps wheel and double-click navigation feeling snappy while smoothing the motion in between. By default, QPane targets the display refresh rate when available and falls back to a configurable FPS if it cannot detect the monitor.

The key options are:
* `smooth_zoom_enabled`: Master switch for animated zoom.
* `smooth_zoom_duration_ms`: Duration for normal wheel/double-click transitions.
* `smooth_zoom_burst_duration_ms`: Shorter duration used during rapid wheel bursts.
* `smooth_zoom_burst_threshold_ms`: How close wheel ticks must be to count as a burst.
* `smooth_zoom_use_display_fps`: Prefer the monitor refresh rate when True.
* `smooth_zoom_fallback_fps`: The FPS used when display detection is unavailable, or when the display rate is disabled.

If the selected duration is shorter than a single frame at the chosen FPS, QPane applies the zoom immediately instead of animating. This keeps zoom responsive even with very low FPS targets.

```python
config.configure(
    smooth_zoom_enabled=True,
    smooth_zoom_duration_ms=80,
    smooth_zoom_burst_duration_ms=20,
    smooth_zoom_burst_threshold_ms=25,
    smooth_zoom_use_display_fps=True,
    smooth_zoom_fallback_fps=60.0,
)
```

## Concurrency
QPane uses a sophisticated background executor to keep the UI responsive. By default, it runs with two worker threads (`max_workers=2`). This ensures that background tasks (like prefetching the next image or running SAM) never block the primary thread responsible for decoding visible tiles.

While the concurrency engine allows fine-grained control (priorities, category limits, device caps), you typically only need to adjust `max_workers` if you are working with truly massive images or running on older hardware where extra threads can help compensate for slower single-core performance.

All keys are optional:
* `max_workers`: Global cap on worker threads.
* `category_priorities`: Which tasks run first (higher `int` = sooner).
* `category_limits`: Max concurrent tasks per category (e.g., limit "sam" to 1).
* `device_limits`: Per-device caps (e.g., limit CUDA usage).

```python
concurrency = {
    "max_workers": 4,  # Bump threads for massive datasets
    "category_priorities": {
        "tiles": 30,   # Load visible tiles first
        "io": 10,      # Then load files
        "sam": 5,      # Run AI last
    },
}
```

## Diagnostics & Discovery
### Diagnostics Domains
You can enable specific diagnostic overlays to debug behavior. `Config.configure` accepts `qpane.DiagnosticsDomain` enums or strings.

```python
config.configure(
    diagnostics_domains_enabled=[
        "cache",  # Visualize memory usage
        "swap",   # See tile swapping
    ]
)
```

### Feature Discovery
If you are building a UI that exposes settings for optional features (like SAM), use `Config.feature_descriptors`. It returns the schema and validators for installed features, allowing you to build dynamic settings pages without hard-coding fields.

## Related Docs
* [Configuration Reference](configuration-reference.md): The complete list of every field and default value.
* [Masks and SAM](masks-and-sam.md): Feature-specific settings.
* [Diagnostics](diagnostics.md): How to observe runtime behavior.
* [Catalog and Navigation](catalog-and-navigation.md): Managing the image list.

**Continue →** [Configuration Reference](configuration-reference.md)
