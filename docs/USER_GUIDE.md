# Viewloom user guide

See [README](../README.md) for installation and current validation status.

## Three steps

The right panel contains **Develop**, **Weave**, and **Export** tabs. Switching
steps retains all project edits. Each step uses lighter gray control groups,
consistent value fields, and scroll-safe controls: the wheel and trackpad scroll
the panel rather than changing values, even when a field is focused.

A bottom-right activity bar appears during import, thumbnail updates, development,
gray sampling, and composite rendering. Preview batches show completed/total
counts; operations without a reliable percentage show an animated busy indicator.
TIFF export retains its percentage bar and Cancel export button. The indicator
hides when background work finishes.

- **Develop** shows the active source photograph without alignment or blending.
- **Weave** offers alignment and blend controls together, including Strength,
  the main-image lock, layer opacity, and edge fade. Composite view shows the
  whole canvas; the alignment overlay remains available in its view selector.
- **Export** shows the final cropped composite, crop controls, output size, and
  16-bit RGB TIFF export. Export still renders from full-resolution originals.

## Develop

Adjust exposure (−10 to +10 EV), contrast, and saturation for the active source.
White balance provides **As shot**, a **Pick neutral gray** tool, and relative
**Warmth** and **Tint** sliders. Warmth is not a calibrated Kelvin temperature
control. White-balance gains are applied to the RAW before demosaicing; exposure,
contrast, and saturation are applied before blending. Contrast and saturation
operate on an sRGB-encoded representation and are converted back to linear light
for compositing. The same development rules are used for preview and export.

For the gray picker, click a neutral midtone patch on the source preview. It
samples a small patch of original sensor data, accounting for image orientation,
and rejects patches with clipped channels or insufficient shadow signal. Picking
a colored object will intentionally neutralize that color; Viewloom cannot know
whether an object in the scene should be gray. Gray-picked settings remain local
to that photograph and never modify the original file.

**Before · As shot** temporarily displays the camera white balance with neutral
development settings. It does not change the stored recipe or undo history.
**Reset selected development** resets development only and is undoable.

Batch development always copies from the **main photograph at the top of the
left list**, regardless of which photograph is being viewed. Use Cmd/Ctrl-click
or Shift-click to select targets, then choose **Apply main adjustments to
selected**. You may select just one target; the main photo does not need to be
selected. **Apply main adjustments to all** applies to every other photograph,
regardless of selection. Both buttons let you choose Exposure, Contrast,
Saturation, and/or White balance. Targets receive one undoable batch edit, and
the main source is left unchanged. Warmth/Tint can be copied
across cameras, but gray-picked sensor gains are copied only to matching camera
models. For a different camera, gray calibration is retained and a message asks
you to pick its gray separately. Alignment, opacity, Strength, and edge fade are
not copied by development syncing.

All affected thumbnails are rebuilt in the background after individual or batch
development edits, including undo/redo; clicking each target is unnecessary.

Projects are now saved as schema version 2. Earlier schema-1 projects open with
neutral defaults for new settings. Older Viewloom builds reject schema 2 rather
than silently discarding development settings.

## Blending and export

Alignment and blending controls share the **Weave** panel. Select a
photograph in the left list to adjust its inclusion or opacity. Blend
mode applies to the whole project. The top of the list is the top layer; use
drag and drop in the left list, or its **Move up / Move down** buttons, to reorder
it. Reordering supports undo/redo. Sliders update live,
and each completed slider drag is one undo step.

**Strength** shifts the composite toward the photograph at the top of the left
list. That top photograph is always the main image; reordering changes it.
At 0%, each layer uses its individual opacity. As strength increases, the main
image's effective opacity rises toward 100% while all other layers fade. At 100%,
only the main image contributes (if included). Individual layer opacity settings
are preserved. Strength and the main image are saved in the project, support
undo/redo, and apply equally to preview and export. Selecting a different layer
for editing does not change the main image.

Changing a blend setting automatically displays the composite. The preview's
Alignment/Composite selector remains available for the reference overlay and
direct image dragging. Dragging also works directly in Weave's composite view:
only the active layer moves, the composite refreshes during the gesture, and
releasing commits one undo step. Escape cancels the move; Space-drag pans the
view. Alignment number controls and sliders can also be used
while viewing the composite, so you can adjust alignment and blending together.

- **Average** uses opacity as a relative weight. Equal weights give equal
  contributions. Reducing all weights equally does not darken the result.
- **Normal** stacks photographs using their opacity.
- **Multiply**, **Screen**, and **Lighten** combine each layer with those below
  it, using opacity and image coverage. Empty regions remain black.

Blending happens in linear sRGB; results can differ from editors that blend
gamma-encoded values. Exposure ranges from −10 to +10 stops and clips each
photograph's values to the working range before blending. Rotated and translated
image boundaries use coverage masks so Average does not darken empty edges.

Crop edges in **Export** are percentages of the original canvas. Left must be
less than Right, and Top less than Bottom. Export preview shows the cropped
result; Weave retains the full canvas. The export size appears below the crop
controls. Scroll the right panel if these controls are below the window edge.

**Fade selected image edges**, in Weave's **Edge fade** group, softens the selected
photograph's own boundary where it overlaps other photographs. Enable it, then
adjust the width slider or percentage field (0–50% of that photograph's shorter
edge). The smooth fade moves, rotates, and scales with the image. Each layer has
its own fade amount, stored non-destructively in the project and included in
undo/redo, composite preview, and TIFF export. It does not change the rectangular
export crop. With Average, fading changes relative contributions where other
images overlap; a lone remaining image retains its color until coverage ends.

Click **Export 16-bit TIFF**, or use **File → Export TIFF** (Cmd+E on macOS).
Export decodes the verified originals at full resolution and writes an uncompressed
16-bit RGB TIFF with an embedded sRGB profile. Remaining transparency is flattened
onto black. Existing destination files are replaced only after the new export is
complete. Cancelling or encountering an error preserves any previous destination.
Cancellation may wait for the current RAW decoding or TIFF-writing step to finish.

Export uses one decoded original at a time, a disk-backed accumulator, and tiled
processing. Temporary disk space is required beside the destination: about
22 bytes per output pixel plus the TIFF itself during writing. For a 24-megapixel
canvas, budget roughly 700 MB of temporary free space. The UI remains responsive;
editing is temporarily disabled while exporting the project snapshot.

Composite previews decode the same originals and use the same color and blending
rules, but reduce source and output resolution for interactive work. Fine edges
can differ slightly from full-resolution export. Preview caches are bounded and
disposable. All processing stays local.

## Manual alignment

Choose one photograph and click **Use as reference**. Select a different
photograph to align against it. Reference selection controls the overlay;
it does not permanently lock that photograph's transforms.

**Lock main image scale and rotation** protects the current top photograph's
scale and rotation while still allowing position changes. Uncheck it to edit
those transforms. The lock follows the top position when layers are reordered,
is saved in the project, and supports undo/redo. Resetting a locked main image
resets its position while preserving its protected scale and rotation.

- Drag the selected photograph to move it. Each completed drag is one undo step.
- With the preview focused, arrow keys move by one full-resolution canvas pixel;
  Shift + arrows move by ten. At fit-to-window zoom, a one-pixel move can be subtle.
- Use **Horizontal**, **Vertical**, **Rotation**, and **Scale** for precise edits.
- Rotation and scale also have live sliders. Rotation spans −180° to +180°;
  scale spans 1% to 400%. Number fields allow values beyond those slider ranges.
  A slider drag is one undo step; sliders and number fields stay synchronized.
- Hold Space and drag to pan. **Fit photograph** returns to the whole canvas.
- Escape cancels a drag before it is committed.
- Use **Reset selected alignment** to restore its initial centered fit.
- Expand **Reference overlay and guides** to adjust the alignment overlay or
  toggle the center guides.
- Use **Edit → Undo/Redo**, or Cmd+Z / Shift+Cmd+Z on macOS.

The canvas is set by the first successfully loaded photograph and saved with the
project. Changing the reference keeps the canvas and existing alignments fixed.
Each photograph starts centered and fitted within that canvas; rotation is
clockwise around its center, and scale is relative to its initial fit. Transformed
content outside the canvas is hidden in the preview but remains in the original.

Overlay strength affects only the alignment guide. It is separate from
per-image blending opacity. Overlay strength, guide visibility,
and view zoom are temporary display settings. Alignment values and reference
selection are saved in the project. Opening a project clears undo history;
removing a photograph also clears the current alignment history. Import/removal
and file relinking are not undoable in this build.

For useful subject alignment, import a sequence of the same subject photographed
from different positions. Sample photographs are not included in this repository.

Source photographs remain external and unchanged. Move a project together with
its source folders to preserve relative links. If an original moves separately,
select it and choose **Relink original**. A replacement must match the saved
fingerprint; import a changed photograph as a new image instead.

Wait for imports to finish before saving. Unreadable imports must be removed or
relinked first. Previews are disposable and held in a bounded memory cache;
reopening a project rebuilds them. No photo data is embedded in project files.

The RAW preview uses the selected white balance, no automatic brightening, linear
sRGB decoding, and an sRGB display curve. It is not intended to reproduce a camera's
JPEG picture style. Camera compatibility has been checked with the two supplied
samples only; other compression settings remain unverified.

