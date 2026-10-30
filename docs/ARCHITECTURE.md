# Architecture

## Separation of responsibilities

| Module | Responsibility |
| --- | --- |
| `app.py` | Main window, project lifecycle, source import jobs, and alignment commands |
| `project.py` | Versioned project data, validation, relative paths, and atomic saves |
| `imaging.py` | Verified RAW reads, preview conversion, and sRGB display encoding |
| `development.py` | Tone/color adjustments, RAW white-balance gains, and gray-patch sampling |
| `develop_ui.py` | Develop controls, source previews, before/after, and batch commands |
| `alignment.py` | Shared affine geometry in full-resolution canvas coordinates |
| `alignment_view.py` | Alignment interaction and composite drag previews |
| `composite.py` | Coverage-aware linear-light blending and tiled TIFF export |
| `compositing_ui.py` | Blend/crop controls, render snapshots, and asynchronous export |
| `work_progress.py` | Bottom-right activity/progress display |
| `scroll_controls.py` | Wheel-event forwarding from editing controls to their panel |
| `ui_groups.py` | Consistent control-group construction |
| `qt_runtime.py` | macOS Qt plugin visibility preparation |

## Processing flow

1. Verify the source fingerprint before reading a known original.
2. Decode oriented RAW data with camera or custom white balance, without automatic brightening.
3. Resample into the fixed canvas using the photograph's alignment transform.
4. Apply exposure, contrast, and saturation to unassociated color values.
5. Combine each layer using coverage, edge fade, opacity, and Strength.
6. Encode the display/export result as sRGB.

White balance operates in the RAW decoder. Exposure operates on decoded linear
RGB. Contrast and saturation use an sRGB-encoded representation and are converted
back to linear RGB before compositing. This is a practical prototype pipeline;
it is not a complete photographic color-management or highlight-recovery system.

## Blending and geometry

The first successfully decoded photograph establishes a fixed canvas. The top
list item is always the main photograph. Strength changes effective layer
opacities without overwriting the saved per-layer opacity values. The reference
photograph independently supplies an alignment overlay.

Alignment uses full-resolution canvas pixels. Each source starts centered and
fitted within the canvas. Image coverage and a smooth optional edge mask prevent
empty transformed regions from darkening an Average blend. Normal, Multiply,
Screen, and Lighten use alpha-aware layer composition. Remaining transparency
is flattened onto black for TIFF output.

Weave previews use the whole canvas. Export previews and output use the saved
crop. Reduced previews use the same recipe and geometry with smaller arrays,
so fine resampling differences from export are expected.

## Background work

Qt thread pools handle source previews, development previews, and composite
rendering. Small GUI updates and undo commands stay on the interface thread.
Workers receive snapshots rather than editing live project state.

Project generations, recipe keys, and render revisions reject stale results.
Repeated changes are coalesced, while cancellation is checked between expensive
steps. All affected thumbnails are queued after batch edits. Preview caches are
bounded; thumbnails remain on their list items even after larger previews are evicted.

During a composite drag, a temporary translation is applied to a render snapshot.
The project changes only on release, producing one undo command. Refreshing the
flattened composite does not cancel the gesture or move all layers together.

## Project format

Schema 2 stores source paths and SHA-256 fingerprints, canvas dimensions,
reference selection, development recipes, transforms, inclusion/opacity, blend
mode, Strength, main transform lock, edge fade, and crop. The main identity is
derived from layer order. Source images and rendered caches are not embedded.

Schema-1 projects remain readable. Older applications reject schema 2 instead
of silently discarding development fields. Saves write and flush a temporary
file, then replace the destination atomically. Missing originals can be relinked
only to matching fingerprints.

## Export memory and storage

Export decodes one original at a time and accumulates in a disk-backed float32
canvas. Small output strips are converted to uint16 and written as TIFF with
an embedded sRGB profile. Temporary files live beside the chosen destination.
The destination is replaced only when the new file is complete; cancellation
or failure preserves the previous destination.

Budget approximately 28 bytes per output pixel for temporary accumulation,
uint16 pixels, and the new TIFF while writing, in addition to any existing output
and source files. Actual RAM also depends on source decoding and caches.
