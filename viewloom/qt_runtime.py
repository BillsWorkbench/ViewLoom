"""Prepare the local Qt runtime before its first plugin discovery."""
import os
from pathlib import Path
import stat
import sys

import PySide6


def prepare_qt_plugins():
    """Qt skips files carrying macOS's hidden flag during plugin discovery.

    Some managed workspaces reapply that flag to dependency files between runs.
    Remove only UF_HIDDEN, only from this interpreter's Qt plugin installation.
    Other flags and all system installations remain unchanged.
    """
    if sys.platform != "darwin":
        return
    plugins = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
    for path in [plugins, *plugins.rglob("*")]:
        flags = path.stat().st_flags
        if flags & stat.UF_HIDDEN:
            os.chflags(path, flags & ~stat.UF_HIDDEN)
