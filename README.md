# Viewloom

**Multiple perspectives. One image.**

After seeing Pep Ventosa's *In the Round* photographs, I decided to vibe code
some software to help create that kind of image. I call it **Viewloom**.

Viewloom is a Python + PySide6 desktop application for developing, aligning,
and blending photographs taken around a subject. It keeps the original RAW
files untouched and saves your adjustments in an editable project.

## Current status

**Version 0.5.3 — early working prototype.** Tested on an Apple Silicon Mac with
Python 3.14.10, including one Nikon Z5 II NEF and one Leica D-LUX 8 DNG sample.
Other camera settings and RAW compression variants still need testing.

macOS and Windows share the same source code. Windows installation and desktop
behavior still require validation. This repository contains source code, not a
signed standalone application or Windows installer. No sample photographs are
included.

## The workflow

| Step | What you can do |
| --- | --- |
| **Develop** | Adjust exposure, contrast, saturation, and white balance. Pick a neutral gray patch, compare with As Shot, or copy the main photo's adjustments to selected photos or all photos. |
| **Weave** | Move, rotate, and scale photographs. Blend with Average, Normal, Multiply, Screen, or Lighten. Adjust opacity, Strength, and edge fade. |
| **Export** | Set the final crop and export a full-resolution 16-bit RGB TIFF with an embedded sRGB profile. |

The top photograph in the left list is always the **main image**. Drag photos
to change their layer order. Strength increases the main image's contribution
while fading the others, preserving their individual opacity settings.

Projects are non-destructive. Alignment, development, blend settings, crop, and
source references are saved in `.viewloom` files. Undo/redo supports development,
batch adjustments, alignment, blending, and layer ordering. Original photographs
remain external to the project.

## Run from source

Use standard **Python 3.14**. The tested interpreter is 3.14.10; do not replace
Apple's system Python. Homebrew is not required. The pinned Qt package requires
macOS 14 or later. Use a 64-bit Python installation on Windows.

### macOS

Open Terminal in the extracted repository folder:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m viewloom
```

If Python.org's interpreter is not on your path, use
`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14` for the first
command. After setup, double-click `Launch Viewloom.command` to launch.

### Windows PowerShell

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m viewloom
```

After setup, `Launch Viewloom.cmd` uses that environment. These Windows
instructions are provided for validation; a Windows release has not been tested.

## Start a project

1. Import a sequence of NEF or DNG photographs of the same subject.
2. Develop the top/main photo. Select target photos and copy its adjustments
   with **Apply main adjustments to selected** or **Apply main adjustments to all**.
3. In Weave, select each photograph and align it against the reference or drag
   it directly in the composite. Adjust blending, Strength, and edge fade.
4. Save the `.viewloom` project, then crop and export in the Export step.

For the full control reference, see the [user guide](docs/USER_GUIDE.md).

## Testing and contributing

```sh
python -m unittest discover -s tests -v
python -m tests.check_alignment_ui
```

Use the isolated environment's Python for these commands. The unit suite and
synthetic interface check do not require photographs. Additional RAW integration
checks use private local fixtures; see [development notes](docs/DEVELOPMENT.md).

GitHub Actions is configured to run unit checks, the synthetic interface check,
and a wheel build on macOS and Windows. The workflow has not yet been run in the
new GitHub repository. No build badges or Windows validation claims are implied.

- [User guide](docs/USER_GUIDE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Development and tests](docs/DEVELOPMENT.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Roadmap](ROADMAP.md)
- [Third-party dependencies](THIRD_PARTY_NOTICES.md)
- [GitHub upload guide](docs/GITHUB_UPLOAD.md)

## License and inspiration

Viewloom's original code and documentation are licensed under the
[MIT License](LICENSE). Copyright © 2026 Bill's Workbench.

Pep Ventosa's photographic work inspired this project. Viewloom is an independent
project and is not affiliated with or endorsed by Pep Ventosa, Nikon, or Leica.
No third-party photographs are included.
