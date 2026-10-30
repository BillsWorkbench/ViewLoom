# Changelog

These entries summarize the working prototype's local development history.
They are not claims of previously published GitHub releases.

## 0.5.3 — 2026-10-30

- Replaced orange selected-checkbox fills with a white checkmark on a dark background.
- Included the SVG checkmark in Python package data.

## 0.5.2 — 2026-10-30

- Rebuild all affected thumbnails after development and batch edits, including undo/redo.
- Added a bottom-right background-work indicator with preview counts and busy animation.
- Enabled selected-layer dragging directly in Weave's composite view.
- Kept live drag previews temporary until release; each completed drag is one undo step.

## 0.5.1 — 2026-10-30

- Batch development always copies from the top/main photograph.
- Added separate apply-to-selected and apply-to-all actions.
- Enabled applying to a single target without selecting the main source.

## 0.5.0 — 2026-10-30

- Organized the right panel into Develop, Weave, and Export steps.
- Added per-image contrast, saturation, RAW white balance, Warmth/Tint, and a gray picker.
- Added temporary As-shot comparison, reset, and selective batch development.
- Protected mixed-camera batches from copying camera-specific gray-picked gains.
- Added project schema 2 with backward loading of schema 1.

## 0.4.1–0.4.4 — 2026-10-30

- Added Strength, main-image behavior, drag-and-drop layer order, and main transform locking.
- Added per-layer edge fade and lighter gray control groups.
- Matched dropdown styling to the dark theme.
- Standardized numeric fields and prevented wheel/trackpad scrolling from editing values.

## 0.4.0 — 2026-10-30

- Added five blend modes, opacity, exposure, image inclusion, and layer ordering.
- Added cropped composite previews and full-resolution 16-bit RGB TIFF export with sRGB ICC data.
- Added disk-backed, tiled rendering, cancellation, and atomic destination replacement.

## 0.3.0–0.3.2 — 2026-10-30

- Added manual translation, rotation, scale, reference overlays, guides, and undo/redo.
- Added and widened rotation/scale sliders.

## 0.2.0 — 2026-10-30

- Added the PySide6 workspace, background RAW import, thumbnails, and image previews.
- Added versioned projects, relative source paths, fingerprint verification, and relinking.
