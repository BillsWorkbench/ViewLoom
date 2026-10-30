"""End-to-end development, RAW gray sampling, batch sync, and stage switching."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import time

import cv2
import numpy as np
import tifffile
from PySide6.QtCore import QItemSelectionModel, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from viewloom.app import MainWindow
from viewloom.development import DEVELOP_FIELDS, recipe_key
from viewloom.imaging import fingerprint
from viewloom.project import load_project
from viewloom.qt_runtime import prepare_qt_plugins


ROOT = Path(__file__).resolve().parents[1]


def settle(app, window):
    deadline = time.monotonic() + 90
    while (window.pending or window.jobs or window.render_job or window.composite_timer.isActive() or
           window.develop_job or window.develop_timer.isActive()):
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Background processing timed out")
        time.sleep(0.01)
    app.processEvents()


def main():
    prepare_qt_plugins()
    app = QApplication([])
    window = MainWindow()
    window.show()
    sources = [ROOT / "DSC_0150.NEF", ROOT / "leica_d_lux_8_37.dng"]
    if not all(path.is_file() for path in sources):
        raise SystemExit("Optional RAW checks need local photographic fixtures; see docs/DEVELOPMENT.md. No photos are included in this repository.")
    hashes = [fingerprint(path) for path in sources]
    window.import_paths(sources)
    settle(app, window)
    assert [window.stages.tabText(i) for i in range(3)] == ["Develop", "Weave", "Export"]
    assert window.stack.currentWidget() is window.develop_view
    assert "preview ready" in window.develop_status.text(), window.develop_status.text()
    source = window.current_frame()
    before_pixels = window.develop_view.photo.pixmap().toImage()
    window.develop_fields["exposure"].setValue(0.6)
    window.develop_fields["contrast"].setValue(15)
    window.develop_fields["saturation"].setValue(-20)
    window.develop_fields["warmth"].setValue(8)
    window.develop_fields["tint"].setValue(-3)
    settle(app, window)
    assert window.develop_view.photo.pixmap().toImage() != before_pixels
    recipe = recipe_key(source)
    undo_index = window.undo_stack.index()
    window.before_check.setChecked(True)
    settle(app, window)
    assert window.develop_view.photo.pixmap().toImage() == before_pixels
    assert recipe_key(source) == recipe and window.undo_stack.index() == undo_index
    window.before_check.setChecked(False)
    settle(app, window)

    # Exercise the actual source-view gray-picker gesture on a truck midtone.
    window.gray_button.setChecked(True)
    bounds = window.develop_view.canvas.rect()
    point = window.develop_view.mapFromScene(QPointF(bounds.width() * 0.5, bounds.height() * 0.6))
    QTest.mouseClick(window.develop_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    settle(app, window)
    assert source.wb_multipliers is not None, window.develop_status.text()
    assert "NIKON" in source.wb_camera
    assert source.warmth == 0 and source.tint == 0
    window.undo_stack.undo()
    assert source.wb_multipliers is None and source.warmth == 8
    window.undo_stack.redo()
    assert source.wb_multipliers is not None
    settle(app, window)

    # Select both sources without changing the active copy-from photograph.
    for index in range(window.list.count()):
        window.list.item(index).setSelected(True)
    assert len(window.list.selectedItems()) == 2 and window.current_frame().id == source.id
    target = window.project.frames[1]
    old_thumbnail = window._item(target.id).icon().pixmap(144, 96).toImage()
    index = window.undo_stack.index()
    window.apply_batch_development(DEVELOP_FIELDS)
    assert (window.generation, target.id) in window.pending
    window._refresh_work_progress()
    assert window.work_progress.isVisible()
    assert window.undo_stack.index() == index + 1
    assert target.exposure == source.exposure and target.contrast == source.contrast
    assert target.saturation == source.saturation
    assert target.wb_multipliers is None, "Nikon calibration must not be copied to Leica"
    assert "different cameras" in window.batch_status.text()
    settle(app, window)
    assert window.current_frame().id == source.id
    new_thumbnail = window._item(target.id).icon().pixmap(144, 96).toImage()
    assert old_thumbnail != new_thumbnail, "Batch target thumbnail must update without selecting it"
    window._refresh_work_progress()
    assert not window.work_progress.isVisible()
    window.undo_stack.undo()
    assert target.exposure == 0 and target.contrast == 0
    window.undo_stack.redo()
    settle(app, window)

    # The top/main photo remains the source when viewing a selected target.
    window.list.setCurrentRow(1, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    assert window.current_frame().id == target.id
    assert len(window.list.selectedItems()) == 1 and window.batch_button.isEnabled()
    main_recipe = recipe_key(source)
    window.develop_fields["exposure"].setValue(-0.4)
    window.apply_batch_development(("exposure",))
    assert target.exposure == source.exposure == 0.6
    assert recipe_key(source) == main_recipe
    window.undo_stack.undo()
    assert target.exposure == -0.4
    window.undo_stack.undo()
    assert target.exposure == 0.6

    # Apply-to-all works with no selection and still copies from the top photo.
    window.list.clearSelection()
    assert window.batch_all_button.isEnabled() and not window.batch_button.isEnabled()
    window.change_development({target.id: {"contrast": -30}}, "Change target contrast")
    window.apply_batch_development(("contrast",), all_images=True)
    assert target.contrast == source.contrast == 15
    assert recipe_key(source) == main_recipe
    window.undo_stack.undo()
    assert target.contrast == -30
    window.undo_stack.undo()
    assert target.contrast == 15
    window.list.setCurrentRow(0)
    settle(app, window)

    with tempfile.TemporaryDirectory(prefix="viewloom-develop-check-") as directory:
        root = Path(directory)
        project_path = root / "development.viewloom"
        assert window.save_path(project_path)
        loaded = load_project(project_path)
        assert recipe_key(loaded.frames[0]) == recipe_key(source)
        assert recipe_key(loaded.frames[1]) == recipe_key(target)
        window.grab().save(str(ROOT / "develop-workspace.png"))
        window.stages.setCurrentIndex(1)
        window.view_mode.setCurrentIndex(1)
        settle(app, window)
        assert "Composite ready" in window.composite_status.text()
        assert window.project.main_frame_id == source.id
        window.stages.setCurrentIndex(2)
        for control, value in zip(window.crop_fields, (10, 10, 90, 90)):
            control.setValue(value)
        settle(app, window)
        assert window.stack.currentWidget() is window.composite_view
        destination = root / "developed.tiff"
        assert window.start_export(destination)
        settle(app, window)
        exported = tifffile.memmap(destination)
        preview = window.renderer.preview(window.project)
        reduced = cv2.resize(exported, (preview.shape[1], preview.shape[0]), interpolation=cv2.INTER_AREA)
        difference = float(np.mean(np.abs(reduced.astype(np.float32) / 65535 - preview / 255)))
        assert difference < 0.025, difference
        assert exported.dtype == np.dtype("uint16")
        exported._mmap.close()
        window.stages.setCurrentIndex(0)
        settle(app, window)
        assert recipe_key(source) == recipe_key(loaded.frames[0])
        window._reset_development()
        assert source.wb_multipliers is None and source.exposure == 0 and source.contrast == 0
        window.undo_stack.undo()
        assert recipe_key(source) == recipe_key(loaded.frames[0])
        settle(app, window)
    assert [fingerprint(path) for path in sources] == hashes
    window.dirty = False
    window.close()
    window.pool.waitForDone()
    window.render_pool.waitForDone()
    window.develop_pool.waitForDone()
    print(f"PASS: Develop/Weave/Export, source edits, before/after, RAW gray-picker gesture, "
          f"selective batch sync, mixed-camera protection, undo/reset, persistence, "
          f"and developed 16-bit TIFF; preview/export mean difference={difference:.6f}")


if __name__ == "__main__":
    main()
