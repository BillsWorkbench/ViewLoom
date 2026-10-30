"""Integration check using the two supplied samples; creates only temporary projects."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import shutil
import tempfile
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from viewloom.app import MainWindow
from viewloom.imaging import fingerprint
from viewloom.project import load_project
from viewloom.qt_runtime import prepare_qt_plugins


ROOT = Path(__file__).resolve().parents[1]


def settle(app, window):
    deadline = time.monotonic() + 45
    while window.pending or window.jobs:
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Background loading timed out")
        time.sleep(0.01)
    app.processEvents()


def main():
    prepare_qt_plugins()
    app = QApplication([])
    window = MainWindow()
    window.show()
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(10)
    samples = [ROOT / "DSC_0150.NEF", ROOT / "leica_d_lux_8_37.dng"]
    if not all(path.is_file() for path in samples):
        raise SystemExit("Optional RAW checks need local photographic fixtures; see docs/DEVELOPMENT.md. No photos are included in this repository.")
    before = [fingerprint(path) for path in samples]
    with tempfile.TemporaryDirectory(prefix="viewloom-workflow-") as directory:
        root = Path(directory).resolve()
        photos = root / "photos"
        photos.mkdir()
        copies = [Path(shutil.copy2(path, photos)) for path in samples]
        window.import_paths(copies)
        settle(app, window)
        assert len(window.project.frames) == 2
        assert all(value == "Ready" for value in window.states.values()), window.states
        assert len(ticks) > 5, "GUI event loop did not stay responsive"
        window.import_paths(copies)
        assert len(window.project.frames) == 2, "Duplicate imports should be ignored"
        window.list.setCurrentRow(1)
        window.set_reference()
        reference = window.project.reference_id
        path = root / "sample.viewloom"
        assert window.save_path(path)
        assert not window.dirty
        assert load_project(path).reference_id == reference

        # Moving an entire project preserves its relative source references.
        moved = root / "moved"
        moved.mkdir()
        photos.rename(moved / "photos")
        path.rename(moved / path.name)
        path = moved / path.name
        assert window.open_path(path)
        settle(app, window)
        assert all(value == "Ready" for value in window.states.values())
        assert not window.dirty

        # A relocated original is missing until explicitly relinked.
        frame = window.project.frames[0]
        relocated = root / frame.source.name
        frame.source.rename(relocated)
        assert window.open_path(path)
        settle(app, window)
        frame = window.project.frames[0]
        assert window.states[frame.id].startswith("Missing")
        window.relink(frame.id, window.project.frames[1].source)
        settle(app, window)
        assert window.states[frame.id].startswith("Could not load:")
        assert not frame.source.exists(), "Wrong relink must not change source path"
        window.relink(frame.id, relocated)
        settle(app, window)
        assert window.states[frame.id] == "Ready"
        assert frame.source == relocated
        assert window.dirty
        assert window.save_path(path)
        assert load_project(path).frames[0].source == relocated

        # Starting another project during loading must ignore stale results.
        window._replace_project(load_project(path), path)
        window._replace_project(type(window.project)(), None)
        settle(app, window)
        assert not window.project.frames and not window.cache

        # Render a populated workspace for visual review.
        window.open_path(path)
        settle(app, window)
        window.list.setCurrentRow(0)
        app.processEvents()
        window.grab().save(str(ROOT / "phase2-workspace.png"))
        assert [fingerprint(source) for source in samples] == before
        window.dirty = False
        window.close()
        window.pool.waitForDone()
    print("PASS: RAW import, responsive GUI, duplicate handling, reference selection, "
          "save/reopen, relative paths, missing files, verified relinking, stale-job isolation, "
          "and unchanged originals")


if __name__ == "__main__":
    main()
