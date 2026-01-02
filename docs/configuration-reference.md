**← Previous:** [Configuration](configuration.md)

# Configuration Reference

A complete, annotated config sample using default values. Fields are grouped by area; adjust them via `Config.configure(...)` before passing the snapshot to `QPane`. All values stay JSON-serializable, and feature slices (mask/SAM) are active only when those features are installed.

Enum-backed strings match the public types (`qpane.CacheMode`, `qpane.PlaceholderScaleMode`, `qpane.ZoomMode`, `qpane.DiagnosticsDomain`), and `Config.configure` is strict: it validates keys and normalizes enums, raising an error immediately if you pass an unknown field (like a typo) or an invalid value. The demo config UI is the fastest way to experiment live—most settings apply immediately, though SAM checkpoint changes (blocking/disabled) require a restart.


```python
config = {
    "cache": {  # Cache budgeting and prefetch tuning for tiles/pyramids/masks/predictors.
        "mode": "auto",  # CacheMode: auto uses system headroom, hard uses budget_mb.
        "headroom_percent": 0.1,  # Auto mode: keep this fraction of total RAM free.
        "headroom_cap_mb": 4096,  # Auto mode: cap reserved headroom in MB.
        "budget_mb": None,  # Hard mode budget in MB; None -> default 1024, ignored in auto.
        "weights": {  # Relative weights used to split the active cache budget.
            "tiles": 22.0,  # Weight for tile cache share.
            "pyramids": 18.0,  # Weight for pyramid cache share.
            "masks": 50.0,  # Weight for mask overlay cache share.
            "predictors": 10.0,  # Weight for SAM predictor cache share.
        },
        "prefetch": {  # Neighbor prefetch depths for background warmup.
            "pyramids": 2,  # Neighbor pyramid prefetch depth; 0 disables, -1 unlimited.
            "tiles": 2,  # Neighbor tile prefetch depth; 0 disables, -1 unlimited.
            "masks": -1,  # Neighbor mask prefetch depth; 0 disables, -1 unlimited.
            "predictors": 0,  # Neighbor predictor prefetch depth; 0 disables, -1 unlimited.
            "tiles_per_neighbor": 4,  # Max tiles to prefetch per neighbor image.
        },
        "tiles": {  # Tile cache budget override.
            "mb": -1,  # Per-bucket budget in MB; negative/None uses weighted budget.
        },
        "pyramids": {  # Pyramid cache budget override.
            "mb": -1,  # Per-bucket budget in MB; negative/None uses weighted budget.
        },
        "masks": {  # Mask cache budget override.
            "mb": -1,  # Per-bucket budget in MB; negative/None uses weighted budget.
        },
        "predictors": {  # SAM predictor cache budget override.
            "mb": -1,  # Per-bucket budget in MB; negative/None uses weighted budget.
        },
    },
    "placeholder": {  # Placeholder image and interaction policy when catalog is empty.
        "source": None,  # Path or Qt resource; None or "" disables the placeholder image.
        "panzoom_enabled": False,  # When True, pan/zoom is unlocked and panzoom tool is selected.
        "drag_out_enabled": False,  # Allow drag-out while placeholder is shown.
        # Zoom policy: one of "fit", "locked_zoom", or "locked_size".
        "zoom_mode": "fit",  # Selects which zoom/size rule is applied.
        "locked_zoom": None,  # Fixed zoom factor; ignored unless zoom_mode="locked_zoom".
        "locked_size": None,  # (width, height) target; ignored unless zoom_mode="locked_size".
        # Scaling policy for size-based modes: one of "auto", "logical_fit", "physical_fit", "relative_fit".
        "scale_mode": "auto",  # Controls how display sizes are interpreted.
        "display_size": None,  # (width, height) used by fit/relative; ignored when None.
        "min_display_size": None,  # (width, height) minimum clamp; ignored when None.
        "max_display_size": None,  # (width, height) maximum clamp; ignored when None.
        "scale_factor": 1.0,  # Multiplier for relative_fit; ignored by other scale modes.
    },
    # --- Viewport & Rendering ---
    "tile_size": 1024,  # Tile edge length in pixels for cache/rendering.
    "tile_overlap": 8,  # Overlap in pixels between tiles to avoid seams.
    "min_view_size_px": 128,  # Smallest pyramid/view size (px) before downsampling stops.
    "canvas_expansion_factor": 1.4,  # Pan margin multiplier (>1 lets you pan past edges).
    "safe_min_zoom": 0.001,  # Absolute minimum zoom clamp, regardless of content.
    "drag_out_enabled": True,  # Global gate for drag-out (disabled if False).
    "normalize_zoom_on_screen_change": False,  # Rebase zoom/pan when screen DPR changes.
    "normalize_zoom_for_one_to_one": False,  # Also rebase when in 1:1 zoom mode.
    "smooth_zoom_enabled": True,  # Animate wheel and double-click zoom transitions.
    "smooth_zoom_duration_ms": 80,  # Normal zoom animation duration.
    "smooth_zoom_burst_duration_ms": 20,  # Duration used for rapid wheel bursts.
    "smooth_zoom_burst_threshold_ms": 25,  # Burst window for rapid wheel ticks.
    "smooth_zoom_use_display_fps": True,  # Prefer monitor refresh rate for animation cadence.
    "smooth_zoom_fallback_fps": 60.0,  # Fallback FPS when display refresh is unavailable.

    # --- Masks & Tools ---
    "default_brush_size": 30,  # Default brush size in pixels.
    "brush_scroll_increment": 5,  # Brush size delta per scroll tick.
    "mask_undo_limit": 20,  # Max undo steps retained per mask.
    "smart_select_min_size": 5,  # Minimum selection size (px) for smart-select tool.
    "mask_border_enabled": False,  # Draw mask borders (uses OpenCV if available).
    "mask_prefetch_enabled": True,  # Allow background mask prefetch on navigation.
    "mask_autosave_enabled": False,  # Enable autosave for masks.
    "mask_autosave_on_creation": True,  # Create blank mask files at mask creation.
    "mask_autosave_debounce_ms": 2000,  # Delay after last change before autosave.
    "mask_autosave_path_template": "./saved_masks/{image_name}-{mask_id}.png",  # Uses {image_name}, {mask_id}.

    # --- Diagnostics ---
    "diagnostics_overlay_enabled": False,  # Master switch for diagnostics overlay.
    "diagnostics_domains_enabled": (),  # Enabled domains (DiagnosticsDomain strings/values).
    "draw_tile_grid": False,  # Debug overlay for tile boundaries.

    # --- SAM (AI Features) ---
    "sam_device": "cpu",  # SAM device string (e.g., "cpu", "cuda", "mps" when available).
    "sam_model_path": None,  # Local checkpoint path; overrides download when set.
    "sam_model_url": "https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt",  # Download URL when path not set.
    "sam_model_hash": None,  # Optional SHA-256 checksum; use "default" for the built-in hash.
    "sam_download_mode": "background",  # One of "blocking", "background", "disabled".
    "sam_prefetch_depth": None,  # Predictor prefetch depth; None inherits cache.prefetch.predictors.
    "sam_cache_limit": 1,  # Max cached SAM predictors/embeddings kept in RAM.

    # --- Concurrency ---
    "concurrency": {  # Executor tuning (threads, priorities, and limits).
        "max_workers": 2,  # Max worker threads in the background executor.
        "category_priorities": {  # Higher numbers run sooner within the queue.
            "pyramid": 20,
            "tiles": 30,
            "io": 10,
            "sam": 5,
            "maintenance": 0,
        },
        "category_limits": {  # Max concurrent tasks per category.
            "pyramid": 2,
        },
        "device_limits": {  # Per-device caps for categories (e.g., sam on cpu/cuda).
            "cpu": {
                "sam": 2,
            },
            "cuda": {
                "sam": 1,
            },
        },
        "max_pending_total": None,  # Global pending-queue cap; None is unlimited.
        "pending_limits": {  # Per-category pending-queue caps.
        },
    },
}
```

## Related Docs
Pair this reference with the narrative guide in [Configuration](configuration.md) to understand when to choose each setting, see [Masks and SAM](masks-and-sam.md) for feature-specific behavior, and check [Diagnostics](diagnostics.md) to interpret the live overlay after you tweak these values.

### Diagnostics Domains Example
Seed diagnostics domains with enum members for autocomplete and central validation; pass `.value` when you need the canonical strings.

```python
import qpane

config = qpane.Config().configure(
    diagnostics_domains_enabled=[
        qpane.DiagnosticsDomain.CACHE.value,
        qpane.DiagnosticsDomain.SWAP.value,
    ]
)
```

**Continue →** [Catalog and Navigation](catalog-and-navigation.md)
