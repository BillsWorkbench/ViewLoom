# Third-party dependencies

The MIT license in this repository covers Viewloom's original source and
documentation. It does not replace the licenses of the libraries Viewloom uses.
The source package contains no third-party binaries or photographic samples.

| Dependency | Purpose | Upstream |
| --- | --- | --- |
| PySide6 / Qt | Desktop interface and image display | https://doc.qt.io/qtforpython-6/ |
| rawpy / LibRaw | RAW decoding and camera metadata | https://github.com/letmaik/rawpy and https://www.libraw.org/ |
| NumPy | Image arrays | https://numpy.org/ |
| OpenCV | Resampling and alignment transforms | https://opencv.org/ |
| Pillow / Little CMS | Color profile creation and preview resizing | https://python-pillow.org/ |
| tifffile | TIFF writing and metadata inspection | https://github.com/cgohlke/tifffile |

Dependency versions are recorded in `pyproject.toml`. The macOS environment
snapshot is `requirements-macos-arm64.txt`; it is not a cross-platform lockfile.
Consult each installed distribution's license files and upstream licensing
documentation before distributing bundled executable applications. That review
is part of the planned desktop-distribution work.
