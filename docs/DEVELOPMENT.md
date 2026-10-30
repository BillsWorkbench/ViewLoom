# Development and validation

## Environment

Use standard Python 3.14 in `.venv`; do not modify system Python. See the
[README setup](../README.md#run-from-source) for macOS and Windows commands.
For editable development, run `python -m pip install -e .` with the isolated
environment's Python.

`pyproject.toml` pins the direct dependencies used by the prototype. The
`requirements-macos-arm64.txt` file records the original macOS dependency
environment and includes transitive Qt packages. Do not treat it as a validated
Windows lockfile.

## Checks requiring no photographs

```sh
python -m unittest discover -s tests -v
python -m tests.check_alignment_ui
```

Unit checks cover project persistence/validation, interrupted saves, alignment
geometry, development math, white-balance parameters, known blend values,
coverage/alpha, edge fade, crop, TIFF encoding/profile, and cancelled/failed exports.

The synthetic interface check uses generated shapes. It checks mouse/key
alignment, undo/redo, main transform locking, preserved zoom, guides, matching
value-box sizes, and scroll-safe controls. It sets `QT_QPA_PLATFORM=offscreen`
internally and writes an ignored screenshot.

## Optional RAW integration checks

The original integration checks use local fixture filenames:

- `DSC_0150.NEF`: the Nikon Z5 II sample used during development.
- `leica_d_lux_8_37.dng`: the Leica D-LUX 8 sample used during development.

These photographs are not included or redistributable through this package.
Place permitted local fixtures in the repository root if you have the originals.
If substituting different images, update the fixture paths and gray-picker test
coordinates to a usable neutral midtone in your photograph.

```sh
python -m tests.check_workspace
python -m tests.check_composite_workspace
python -m tests.check_develop_workspace
```

The checks cover RAW import, source preservation, source relocation/relinking,
all blend modes, composite dragging, background thumbnail updates, progress,
before/after, gray-picking, selected/all batch syncing, persistence, and full-size
16-bit TIFF export. They create temporary copies/exports and ignored local
screenshots/reports. They are intentionally excluded from hosted CI because
the photographic fixtures are private and absent from the repository.

## GitHub Actions

The included workflow installs Python 3.14, installs the package, runs unit and
synthetic interface checks, and builds a wheel on macOS and Windows. It uses
read-only repository permissions and no custom secrets. It does not publish
releases or upload photographs. The workflow is provided for first execution
after upload; Windows checks have not been claimed as passing locally.

## Local package checks

```sh
python -m pip wheel --no-deps . --wheel-dir dist
```

This builds a Python source wheel, not a standalone desktop executable. The SVG
checkbox asset is declared as package data. Source installation reads README
metadata and includes the MIT license.

## Known limits

- Camera support is verified only for the two original sample files.
- Windows desktop behavior and clean-machine installers remain unvalidated.
- Warmth is a relative control, not calibrated Kelvin temperature.
- Gray-picked sensor gains are not copied across different camera models.
- No automatic subject alignment, subject masks, lens profiles, or advanced highlight recovery.
- Imports, removals, and relinking are not undoable; removal clears alignment history.
- Opening a project clears undo history; raw files remain external references.
- Preview/export sampling can differ at fine edges.
- Cancellation waits for a RAW decode or TIFF write already in progress.

On some managed macOS workspaces, hidden flags on Qt plugin files prevented
plugin discovery. Startup clears only the hidden flag within the active Python
environment's Qt plugin folder. It does not change system Python or other flags.
