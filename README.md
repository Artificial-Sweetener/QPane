<p align="center">
  <img src="assets/logos/logo-black.png#gh-light-mode-only" alt="QPane" width="320">
  <img src="assets/logos/logo-white.png#gh-dark-mode-only" alt="QPane" width="320">
</p>

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE) [![semantic-release](https://img.shields.io/badge/semantic--release-angular-e10079?logo=semantic-release)](https://github.com/semantic-release/semantic-release) [![PyPI](https://img.shields.io/pypi/v/qpane.svg)](https://pypi.org/project/qpane/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/) [![PySide6](https://img.shields.io/badge/PySide6-6.7.3%2B-41CD52?logo=qt&logoColor=white)](https://pyside.org) [![OpenCV optional](https://img.shields.io/badge/OpenCV-optional%204.9%2B-5C3EE8?logo=opencv&logoColor=white)](https://opencv.org/) [![PyTorch optional](https://img.shields.io/badge/PyTorch-optional%202.1%2B-EE4C2C?logo=pytorch&logoColor=white)](https://pytorch.org/)

**QPane** is a high-performance, **open-source (GPLv3)** image viewer and raster canvas for PySide6.

It bridges the gap between a raw `QGraphicsView` and a full-blown image editor, providing a drop-in widget for **interactive workflows**—specifically those involving high-resolution image inspection, dataset curation, and precise masking.

Whether you are building a simple photo viewer or a mission-critical imaging system, QPane adapts to your resource constraints.

## Highlights
*   **Drop-in PySide6 Widget:** A production-ready image viewer you can add to any layout in a few lines of code.
*   **True FOSS:** Distributed under GPLv3 to ensure it remains free for everyone. No pro versions, no hidden costs.
*   **CPU-First Performance:** Renders massive images smoothly using system RAM, ensuring responsiveness on any hardware—from laptops to workstations.
*   **Fluid Pan & Zoom Navigation:** Silky smooth zooming, panning, and tiling out of the box.
*   **Modular Dependencies:** A "pay-for-what-you-use" design means you never install heavy libraries (like Torch or OpenCV) unless you need them.
*   **Advanced Raster Masking:** Features a full 8-bit masking engine with brush tools, undo/redo, and optional **Segment Anything (SAM)** integration.
*   **Native High-DPI Support:** Automatically adapts to different monitor pixel densities and OS zoom levels for crisp rendering anywhere.

<p align="center">
  <img src="assets/videos/zoom.gif" alt="QPane zoom demo" width="852" height="480">
</p>
<blockquote>
  <p>Deep-zoom navigation on a high-resolution Hubble composite. Note the cursor-anchored zooming and fluid responsiveness even at extreme magnification.</p>
  <p><strong>Credit:</strong> <em>"Hubble's Spectacular Wide View of the Universe"</em> (NASA, ESA, G. Illingworth and D. Magee (University of California, Santa Cruz), K. Whitaker (University of Connecticut), R. Bouwens (Leiden University), P. Oesch (University of Geneva), and the Hubble Legacy Field team).</p>
  <p><strong>Source:</strong> <a href="https://esahubble.org/images/heic1909a/">ESA/Hubble Original</a></p>
</blockquote>

## Installation

QPane uses a "pay-for-what-you-use" model. Don't need AI? Don't install torch

```bash
# Core viewer only (lightweight)
pip install qpane

# With Masking tools (adds OpenCV)
pip install "qpane[mask]"

# With AI driven segment masking (adds Torch + SAM; requires everything from "mask")
pip install "qpane[mask,sam]"
```

## The Gap in the Qt Ecosystem

If you are building a Python GUI that needs to display images, you typically face a dilemma between two built-in widgets, neither of which is quite right for the job:

### 1. The `QLabel` Trap
It's easy to use (`setPixmap`), but it's static. You get no zooming, no panning, and no coordinate system. It's a picture frame, not a tool.

### 2. The `QGraphicsView` Reality
`QGraphicsView` is the standard recommendation for custom viewports, but it is a low-level building block, not a complete solution. It provides a scene graph, but it doesn't give you a modern image viewing experience out of the box.

To build a production-grade viewer with `QGraphicsView`, you inevitably end up writing the same complex infrastructure:
*   **Interaction Logic:** Implementing anchored zooming and smooth panning.
*   **Coordinate Systems:** Mapping mouse events from the view to the scene to the image pixels for precise tool handling.
*   **Performance Tuning:** Managing threading and tiling to keep the UI responsive when the image gets large.

**QPane is that infrastructure.** It encapsulates the hundreds of hours of specialized engineering required to turn a raw Qt widget into a professional image viewer.

## The Engine: CPU-First & Raster-Optimized

QPane rejects the modern "GPU-brute-force" approach in favor of deterministic, CPU-friendly optimizations reminiscent of high-performance 2D engines from the 90s.

I originally built QPane for my **Stable Diffusion frontend**, where the GPU is already at 100% load running inference. I needed a viewer that wouldn't fight the AI model for VRAM. This architecture makes QPane ideal for **any resource-constrained environment**, from scientific imaging on office laptops to embedded systems with limited graphics acceleration.

### 1. The Raster Pipeline
QPane behaves less like a scene graph and more like a map engine. Instead of rendering the image, QPane renders the *viewport*.
*   **Software Tiling:** Large images are sliced into small CPU-resident tiles. Instead of a heavy scene graph, QPane calculates tile visibility using raw coordinate math, eliminating the overhead of managing thousands of `QGraphicsItem` objects.
*   **Viewport Culling:** Only the pixels currently visible on screen are processed. You can load a 5GB satellite scan, and QPane will only render the 1920x1080 pixels needed for your monitor.
*   **Threaded Pyramids:** Background workers generate downsampled versions of your image. When you zoom out, QPane instantly swaps to a lower-resolution tier. This happens in a thread pool, ensuring the UI thread never stutters while loading a 100MB image.
*   **Bit-Blit Scrolling:** When you pan, QPane doesn't redraw the screen. It shifts the existing pixel buffer and only renders the newly exposed "damage strips" at the edges. This keeps scrolling silky smooth even at high resolutions.

### 2. Smart Memory Management
QPane counts every byte of every tile. By default, it dynamically adjusts its cache based on system memory pressure, but can be locked to a strict budget for deterministic performance.

*   **Auto Mode (Consumer Friendly):** Uses `psutil` to monitor available RAM. "Use what's free, but leave 10% headroom for the OS." Ideal for general-purpose viewers or apps running alongside other heavy software.
*   **Hard Mode (Dedicated Resources):** Locks QPane to a specific memory budget (e.g., 4GB). "Take 4GB of RAM and keep as many tiles in memory as possible." Ideal for dedicated imaging systems or kiosk applications where the viewer is the primary task.

```python
# Configure for a dedicated system with 4GB cache
conf = Config()
conf.cache.mode = "hard"
conf.cache.budget_mb = 4096
```

## Key Features

### 1. Advanced Viewing Capabilities
*   **Linked Views:** Perfect for "Before/After" workflows. Group multiple images into a **Linked Group**, and panning/zooming one image synchronizes the view state across the entire group.
*   **Catalog System:** QPane manages the list of images for you. Images are tracked by stable UUIDs, allowing you to swap file paths or buffers without breaking selection state. Includes built-in `next()`, `previous()`, and `setImagesByID()` methods.
*   **High-DPI Ready:** QPane detects the pixel density of the monitor it's on and renders at the native resolution. Drag the window between monitors with different OS zoom levels, and QPane instantly rebuilds its render buffers to match the new pixel density without stuttering.

### 2. The Raster Canvas (Masks & SAM)
QPane doesn't just have to be a viewer; it can be an interactive canvas, too. It supports a full **8-bit raster masking system** layered on top of your image.
*   **Layer Stack:** Multiple mask layers with configurable color and opacity.
*   **Brush Tool:** A circular brush for manual pixel-level editing.
*   **Smart Select (SAM):** Integrated support for the **Segment Anything Model**. Drag a box, and QPane runs the AI inference to generate a high-quality mask-all on the CPU. Powered by [MobileSAM](https://github.com/ChaoningZhang/MobileSAM).
*   **Undo/Redo:** A robust, per-image undo stack that tracks pixel changes.

<p align="center">
  <img src="assets/videos/smartselect.gif" alt="QPane Smart Select demo" width="852" height="480">
</p>
<blockquote>
  <p>AI-assisted masking with the Smart Select tool. A simple drag-selection triggers the SAM inference to snap to object boundaries.</p>
  <p><strong>Credit:</strong> <em>"reckless thoughts abide"</em> by Aurelie Curie. Used with permission and gratitude.</p>
  <p><strong>Source:</strong> <a href="https://www.aureliecurie.net">Artist Website</a> | <a href="https://www.flickr.com/photos/_aurelie_/7440016548/in/album-72157668497689075">Flickr Original</a></p>
</blockquote>

## Developer Experience

QPane is designed to be the library I wish I had. It uses a **Facade Pattern** to hide complexity. You don't interact with tile managers or thread pools directly; you just instantiate the `QPane` widget.

*   **Native Qt Feel:** It's a `QWidget`. Add it to a layout, connect signals (`catalogSelectionChanged`, `maskSaved`), and it just works.
*   **Immutable Config:** No global state spaghetti. Create a `Config` object, set your preferences (cache size, keybindings), and pass it in.
*   **Diagnostics HUD:** Easy to wire into your app. Bind your preferred shortcut to toggle the overlay and see memory usage, render times, and worker queues.
*   **Lazy Loading:** Importing `qpane` is instant. Heavy dependencies like `cv2` (for masking) `torch` (for SAM) are only imported when you actually use those features.

<p align="center">
  <img src="assets/videos/diagnostics.gif" alt="QPane diagnostics overlay demo" width="852" height="480">
</p>
<blockquote>
  <p>Real-time performance monitoring. The diagnostics overlay visualizes memory usage, render latency, and the active tile grid to help debug resource constraints.</p>
  <p><strong>Credit:</strong> <em>"Woman Holding a Balance"</em> by Johannes Vermeer, courtesy National Gallery of Art.</p>
  <p><strong>Source:</strong> <a href="https://www.nga.gov/artworks/1236-woman-holding-balance">National Gallery of Art</a></p>
</blockquote>

## Try the Demo

The package includes a comprehensive demo application that lets you test the performance and features (including the AI tools) without writing any code. The demo launcher bootstraps a dedicated virtual environment and installs all necessary dependencies, so a working Python install is all you need to get started.

```bash
# Run the interactive launcher
python -m examples.demo
```

## Usage

```python
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from PySide6.QtGui import QImage
from qpane import QPane

# 1. Initialize
# Pass features=("mask", "sam") to enable advanced tools.
widget = QPane(features=("mask", "sam"))

# 2. Load Data
images = [QImage("scan_001.tif"), QImage("scan_002.tif")]
# QPane.imageMapFromLists generates UUIDs if you don't provide them,
# but passing your own IDs is recommended for stable tracking.
ids = ["uuid-1", "uuid-2"]
mapping = QPane.imageMapFromLists(images, ids=ids)

# Load the map and select the first image
widget.setImagesByID(mapping, current_id=ids[0])

# 3. Connect Signals
widget.catalogSelectionChanged.connect(lambda iid: print(f"Now viewing {iid}"))
widget.maskSaved.connect(lambda mid, path: print(f"Saved mask to {path}"))
```

## Documentation

*   **[Getting Started](docs/getting-started.md):** A step-by-step guide to your first integration.
*   **[Configuration](docs/configuration.md):** Learn how to tune the cache, thread pool, and interaction behavior.
*   **[Configuration Reference](docs/configuration-reference.md):** The complete list of every field and default value.
*   **[Catalog and Navigation](docs/catalog-and-navigation.md):** Managing image lists and linked views.
*   **[Interaction Modes](docs/interaction-modes.md):** Switching between pan/zoom, cursor, and custom tools.
*   **[Masks and SAM](docs/masks-and-sam.md):** Deep dive into manual painting and AI-powered masking workflows.
*   **[Diagnostics](docs/diagnostics.md):** How to observe runtime behavior and debug performance.
*   **[Extensibility](docs/extensibility.md):** Registering custom overlays, cursors, and tools.
*   **[API Reference](docs/api-reference.md):** A fast, linked index to the QPane facade.

## License & Philosophy

QPane is **Free and Open Source Software (FOSS)**, distributed under the **GNU General Public License v3.0**.

I believe that robust UI infrastructure should be a public good, not a proprietary product. QPane is designed to be the standard, high-performance viewer for the PySide6 ecosystem. The GPL ensures it remains free forever, and that any optimizations or fixes made to the core engine are shared back to benefit the next developer.

## From the Developer 💖

I hope QPane saves you the months of headache I spent figuring out efficient tiling and threading! If you'd like to support my work or see what else I'm up to, here are a few links:

- **Buy Me a Coffee**: You can help fuel more projects like this at my [Ko-fi page](https://ko-fi.com/artificial_sweetener).
- **My Website & Socials**: See my art, poetry, and other dev updates at [artificialsweetener.ai](https://artificialsweetener.ai).
- **If you like this project**, it would mean a lot to me if you gave me a star here on Github!! ⭐
