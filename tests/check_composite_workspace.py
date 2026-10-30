"""Phase 4 integration with both real RAW samples and a full-resolution export."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import cv2
import numpy as np
import tifffile
from PySide6.QtCore import QModelIndex, QPoint, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from viewloom.app import MainWindow
from viewloom.composite import effective_opacity, output_geometry
from viewloom.imaging import fingerprint
from viewloom.project import load_project
from viewloom.qt_runtime import prepare_qt_plugins


ROOT = Path(__file__).resolve().parents[1]


def settle(app, window):
    deadline = time.monotonic() + 90
    while window.pending or window.jobs or window.render_job or window.composite_timer.isActive():
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
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(20)
    sources = [ROOT / "DSC_0150.NEF", ROOT / "leica_d_lux_8_37.dng"]
    if not all(path.is_file() for path in sources):
        raise SystemExit("Optional RAW checks need local photographic fixtures; see docs/DEVELOPMENT.md. No photos are included in this repository.")
    before = [fingerprint(path) for path in sources]
    errors = []
    with tempfile.TemporaryDirectory(prefix="viewloom-export-check-") as directory, \
            patch("viewloom.compositing_ui.QMessageBox.critical", side_effect=lambda *args: errors.append(args[-1])):
        root = Path(directory)
        window.import_paths(sources)
        settle(app, window)
        window.stages.setCurrentIndex(1)
        window.view_mode.setCurrentIndex(1)
        settle(app, window)
        assert "Composite ready" in window.composite_status.text(), window.composite_status.text()
        assert window.stack.currentWidget() is window.composite_view

        # Drag the selected layer directly in composite Weave view, not the flattened image.
        view = window.composite_view
        assert view.editable
        frame = window.current_frame()
        original_positions = {item.id: item.translation for item in window.project.frames}
        start = view.mapFromScene(view.canvas.rect().center())
        end = start + QPoint(25, 10)
        scene_delta = view.mapToScene(end) - view.mapToScene(start)
        dx = scene_delta.x() * window.project.canvas_size[0] / view.canvas.rect().width()
        dy = scene_delta.y() * window.project.canvas_size[1] / view.canvas.rect().height()
        undo_index = window.undo_stack.index()
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end)
        assert view.photo.pos().isNull(), "Do not shift the entire flattened composite"
        assert frame.translation == original_positions[frame.id], "Dragging is temporary until release"
        window._refresh_work_progress()
        assert window.work_progress.isVisible()
        settle(app, window)
        assert view.drag_start is not None, "A preview refresh must not interrupt the drag"
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert abs(frame.translation[0] - (original_positions[frame.id][0] + dx)) < 0.001
        assert abs(frame.translation[1] - (original_positions[frame.id][1] + dy)) < 0.001
        assert all(item.translation == original_positions[item.id] for item in window.project.frames if item.id != frame.id)
        assert window.undo_stack.index() == undo_index + 1
        settle(app, window)
        window.undo_stack.undo()
        settle(app, window)
        assert frame.translation == original_positions[frame.id]
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end)
        QTest.keyClick(view, Qt.Key.Key_Escape)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        settle(app, window)
        assert frame.translation == original_positions[frame.id] and window.composite_drag_offset is None

        # All five blend modes are available and produce usable previews.
        for mode in ("normal", "multiply", "screen", "lighten", "average"):
            window.blend_mode.setCurrentIndex(window.blend_mode.findData(mode))
            settle(app, window)
            assert "Composite ready" in window.composite_status.text(), window.composite_status.text()

        selected = window.current_frame()
        window.blend_fields["opacity"].setValue(40)
        window.develop_fields["exposure"].setValue(0.5)
        assert selected.opacity == 0.4 and selected.exposure == 0.5
        window.undo_stack.undo()
        assert selected.exposure == 0
        window.undo_stack.redo()
        assert selected.exposure == 0.5
        old_order = [frame.id for frame in window.project.frames]
        window._move_layer(1)
        assert [frame.id for frame in window.project.frames] == list(reversed(old_order))
        assert window.project.main_frame_id == old_order[1]
        assert window.current_frame().id == selected.id
        window.undo_stack.undo()
        assert [frame.id for frame in window.project.frames] == old_order

        # Internal drag/drop uses Qt's row-move operation and is undoable.
        assert window.list.model().moveRow(QModelIndex(), 0, QModelIndex(), 2)
        app.processEvents()
        assert [frame.id for frame in window.project.frames] == list(reversed(old_order))
        assert window.current_frame().id == selected.id
        window.undo_stack.undo()
        assert [frame.id for frame in window.project.frames] == old_order

        # Strength targets the top photograph and keeps base opacities intact.
        old_opacities = [frame.opacity for frame in window.project.frames]
        index = window.undo_stack.index()
        window.strength_slider.setSliderDown(True)
        for value in (200, 400, 600):
            window.strength_slider.setValue(value)
        window.strength_slider.setSliderDown(False)
        assert window.undo_stack.index() == index + 1
        assert window.project.strength == 0.6
        assert [frame.opacity for frame in window.project.frames] == old_opacities
        assert abs(effective_opacity(window.project, selected) - 0.76) < 1e-8
        main_id = window.project.main_frame_id
        window.list.setCurrentRow(1)
        assert window.project.main_frame_id == main_id
        window.list.setCurrentItem(window._item(selected.id))
        window.undo_stack.undo()
        assert window.project.strength == 0
        window.undo_stack.redo()
        assert window.project.strength == 0.6
        window.edge_fade_check.setChecked(True)
        assert selected.edge_fade == 0.05
        window.edge_fade_amount.setValue(8)
        assert selected.edge_fade == 0.08
        window.undo_stack.undo()
        assert selected.edge_fade == 0.05
        window.undo_stack.redo()
        assert selected.edge_fade == 0.08
        window.list.setCurrentRow(1)
        window.edge_fade_check.setChecked(True)
        window.edge_fade_amount.setValue(12)
        assert window.current_frame().edge_fade == 0.12
        window.list.setCurrentItem(window._item(selected.id))

        window.include_check.setChecked(False)
        assert not selected.enabled
        window.undo_stack.undo()
        assert selected.enabled
        for control, value in zip(window.crop_fields, (10, 10, 90, 90)):
            control.setValue(value)
        assert window.project.crop == [0.1, 0.1, 0.9, 0.9]
        settle(app, window)
        assert "Composite ready" in window.composite_status.text()
        project_path = root / "blend.viewloom"
        assert window.save_path(project_path)
        loaded = load_project(project_path)
        assert loaded.crop == window.project.crop
        assert loaded.frames[0].opacity == 0.4 and loaded.frames[0].exposure == 0.5
        assert loaded.strength == 0.6 and loaded.main_frame_id == main_id
        assert loaded.frames[0].edge_fade == 0.08
        assert loaded.frames[1].edge_fade == 0.12

        # Export while a preview is still pending; it must wait, then complete.
        destination = root / "composite.tiff"
        window._request_composite()
        assert window.start_export(destination)
        assert not window.centralWidget().isEnabled()
        start_ticks = len(ticks)
        settle(app, window)
        assert not errors, errors
        assert destination.exists()
        assert window.centralWidget().isEnabled() and not window.export_busy
        assert len(ticks) > start_ticks + 5, "GUI must stay responsive during export"
        expected_width, expected_height = output_geometry(window.project)[1]
        with tifffile.TiffFile(destination) as image:
            assert image.pages[0].shape == (expected_height, expected_width, 3)
            assert image.pages[0].dtype == np.dtype("uint16")
            assert image.pages[0].tags[34675].value
        exported = tifffile.memmap(destination)
        preview = window.renderer.preview(window.project)
        reduced = cv2.resize(exported, (preview.shape[1], preview.shape[0]), interpolation=cv2.INTER_AREA)
        difference = float(np.mean(np.abs(reduced.astype(np.float32) / 65535 - preview / 255)))
        assert difference < 0.025, difference
        exported._mmap.close()

        # Rapid changes must eventually display the newest settings, not stale work.
        for value in (15, 85, 65):
            window.blend_fields["opacity"].setValue(value)
            app.processEvents()
        settle(app, window)
        assert selected.opacity == 0.65
        assert "Composite ready" in window.composite_status.text()
        window.grab().save(str(ROOT / "phase4-workspace.png"))
        assert [fingerprint(path) for path in sources] == before
        report = {"status": "passed", "blend_modes": 5, "originals_unchanged": True,
                  "tiff_shape": [expected_height, expected_width, 3], "tiff_dtype": "uint16",
                  "embedded_profile": "sRGB", "preview_export_mean_difference": difference,
                  "gui_responsive_during_export": True, "project_settings_roundtrip": True,
                  "top_layer_is_main": True, "edge_fade_preview_and_export": True,
                  "scope": "macOS Apple Silicon; two supplied RAW samples"}
        (ROOT / "phase4-report.json").write_text(json.dumps(report, indent=2) + "\n")
        window.dirty = False
        window.close()
        window.pool.waitForDone()
        window.render_pool.waitForDone()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
