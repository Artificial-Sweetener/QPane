# Getting Started

## Welcome to QPane
QPane is a PySide6 widget for fast, CPU-first tiled image viewing. It slots into your Qt app without leaning on the GPU, handling large images and smooth zooming right out of the box.

## Install and Import
Install the core package to get the viewer. If you need advanced tools like mask editing or Segment Anything (SAM), grab the extras now or add them later.

```bash
# Core viewer only
pip install qpane

# Optional extras
pip install "qpane[mask]"      # For mask editing
pip install "qpane[mask,sam]"  # For AI segmentation
pip install "qpane[full]"      # Installs all current and future extras
```

```python
from qpane import QPane
```

## Spin up the Widget
Create the `QPane` widget after your `QApplication` is ready. You can request optional features like masking during initialization.

```python
import sys
from PySide6.QtWidgets import QApplication
from qpane import QPane

app = QApplication(sys.argv)

# Basic viewer
viewer = QPane()

# Viewer with mask editing tools enabled
# viewer = QPane(features=("mask",))

# Viewer with mask editing + Segment Anything
# viewer = QPane(features=("mask", "sam"))
```

> **Heads-up:** If you request a feature but lack its dependencies (e.g., asking for `"sam"` without `torch` installed), QPane logs a warning and initializes safely without it. You can check `viewer.installedFeatures` at runtime to see what's available.  
> **SAM note:** SAM checkpoints can be downloaded automatically or provided by your app.
> See [Masks and SAM](masks-and-sam.md) for the full checkpoint and download details.

## Build Your Catalog
QPane organizes images into a linear catalog so users can navigate without you managing the index. Use `QPane.imageMapFromLists` to build this catalog from your data.

```python
from PySide6.QtGui import QImage
from qpane import QPane

# 1. Prepare your data
images = [QImage("example.png")]
paths = ["example.png"]

# 2. Build the map (raises ValueError if list lengths don't match)
image_map = QPane.imageMapFromLists(images, paths=paths)

# 3. Load it into the viewer
current_id = next(iter(image_map))
viewer.setImagesByID(image_map, current_id=current_id)
```

> **Pro Tip:** `imageMapFromLists` generates UUIDs for you if you don't provide them. Pass your own persistent UUIDs when your host needs stable identity across catalog rebuilds or app restarts.

## Inspect State
Once images are loaded, you can query the viewer for the current state. These properties are read-only; use the catalog methods to make changes.

* **Check for content:** `QPane.hasImages` returns `True` if the catalog isn't empty.
* **Get the active item:** `QPane.currentImageID` gives you the UUID, while `QPane.currentImage` returns the `QImage` or `None` and `QPane.currentImagePath` returns the path or `None`.
* **See the full list:** `QPane.imageIDs` returns the ordered list of all loaded image UUIDs.

## React to Navigation
Don't poll the viewer to see what's visible. Instead, connect to `QPane.catalogSelectionChanged`. This signal fires whenever the active image changes—whether the user clicked "Next", pressed a key, or you called `setImagesByID`.

```python
def on_image_changed(image_id):
    if image_id is None:
        print("Gallery is empty")
        return
    print(f"Now viewing image {image_id}")

viewer.catalogSelectionChanged.connect(on_image_changed)
```

> **Why this matters:** This signal is the heartbeat of your integration. Use it to update window titles, enable/disable "Next" buttons, or refresh sidebars.

## Next Steps
You have a running viewer. Now you can customize its behavior or add power-user features.

* **Refine the feel:** [Configuration](configuration.md) covers zoom limits, drag behavior, and defaults.
* **Manage the list:** [Catalog and Navigation](catalog-and-navigation.md) explains how to add/remove images and handle large sets.
* **Debug your app:** [Diagnostics](diagnostics.md) shows you how to peek at memory usage and tile generation.

**Continue →** [Configuration](configuration.md)
