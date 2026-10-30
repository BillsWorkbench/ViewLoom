"""Exercise alignment gestures and undo history with a synthetic subject."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from dataclasses import asdict
import tempfile

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from viewloom.alignment import Pose
from viewloom.app import MainWindow
from viewloom.project import Frame, Project, load_project
from viewloom.qt_runtime import prepare_qt_plugins


def main():
    prepare_qt_plugins()
    app = QApplication([])
    window = MainWindow()
    window.show()
    window.stages.setCurrentIndex(1)
    with tempfile.TemporaryDirectory(prefix="viewloom-alignment-") as directory:
        root = Path(directory)
        frames = [Frame(root / f"subject-{i}.NEF", sha256="a" * 64) for i in range(2)]
        project = Project(frames=frames, reference_id=frames[0].id, canvas_size=(1200, 800))
        window._replace_project(project, None)
        image = QImage(600, 400, QImage.Format.Format_RGB888)
        image.fill(QColor("#253344"))
        painter = QPainter(image)
        painter.setBrush(QColor("#f6cc7f"))
        painter.drawEllipse(QPointF(300, 200), 40, 80)
        painter.end()
        for frame in frames:
            window.cache[frame.id] = image
            window.dimensions[frame.id] = (1200, 800)
            window.states[frame.id] = "Ready"
            window._update_item(frame)
        window.list.setCurrentRow(1)
        window._refresh_selection()
        app.processEvents()
        assert window.photo_view.reference.isVisible()
        assert window.photo_view.editable
        assert window.save_path(root / "alignment.viewloom")
        assert not window.dirty

        # Keyboard nudges use full-resolution canvas pixels at any view zoom.
        view = window.photo_view
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_Right)
        QTest.keyClick(view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
        assert frames[1].translation == (1, 10)
        assert window.dirty
        window.undo_stack.undo()
        assert frames[1].translation == (1, 0)
        window.undo_stack.undo()
        assert frames[1].translation == (0, 0) and not window.dirty
        window.undo_stack.redo()
        window.undo_stack.redo()
        assert frames[1].translation == (1, 10)

        # One drag makes one command, measured in scene coordinates.
        start = view.mapFromScene(QPointF(600, 400))
        end = start + QPoint(30, -20)
        delta = view.mapToScene(end) - view.mapToScene(start)
        count = window.undo_stack.count()
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert window.undo_stack.count() == count + 1
        assert abs(frames[1].translation[0] - (1 + delta.x())) < 0.001
        assert abs(frames[1].translation[1] - (10 + delta.y())) < 0.001
        window.undo_stack.undo()
        assert frames[1].translation == (1, 10)

        # Numeric rotation and scale use the same undoable pose model.
        view.actual_size()
        zoom = view.transform().m11()
        window.pose_controls["rotation"].setValue(12.5)
        window.pose_controls["scale"].setValue(120)
        assert frames[1].rotation == 12.5 and frames[1].scale == 1.2
        assert view.transform().m11() == zoom, "Adjusting pose must not reset viewing zoom"
        assert window.pose_sliders["rotation"].value() == 125
        assert window.pose_sliders["scale"].value() == 1200

        # A live slider drag synchronizes fields and merges into one undo step.
        slider = window.pose_sliders["rotation"]
        index = window.undo_stack.index()
        slider.setSliderDown(True)
        for value in (150, 180, 200):
            slider.setValue(value)
        slider.setSliderDown(False)
        assert window.undo_stack.index() == index + 1
        assert frames[1].rotation == 20
        assert window.pose_controls["rotation"].value() == 20
        window.undo_stack.undo()
        assert frames[1].rotation == 12.5 and slider.value() == 125
        window.undo_stack.redo()
        assert frames[1].rotation == 20 and slider.value() == 200
        window.undo_stack.undo()
        slider = window.pose_sliders["scale"]
        slider.setSliderDown(True)
        slider.setValue(1300)
        slider.setValue(1500)
        slider.setSliderDown(False)
        assert frames[1].scale == 1.5
        assert window.pose_controls["scale"].value() == 150
        window.undo_stack.undo()
        assert frames[1].scale == 1.2 and slider.value() == 1200
        window.reset_alignment()
        assert Pose.from_frame(frames[1]) == Pose()
        window.undo_stack.undo()
        assert frames[1].rotation == 12.5 and frames[1].scale == 1.2

        # Guides and overlay are viewing aids; changing them does not dirty a saved project.
        destination = root / "alignment.viewloom"
        assert window.save_path(destination)
        saved_pose = Pose.from_frame(frames[1])
        window.overlay_slider.setValue(25)
        assert view.reference.opacity() == 0.25
        window.overlay_check.setChecked(False)
        assert not view.reference.isVisible()
        window.guides_check.setChecked(False)
        assert not view.guides and not window.dirty
        window.overlay_check.setChecked(True)
        window.guides_check.setChecked(True)
        loaded = load_project(destination)
        assert Pose.from_frame(loaded.frames[1]) == saved_pose
        assert loaded.canvas_size == (1200, 800)
        window._replace_project(loaded, destination)
        assert not window.undo_stack.canUndo()
        for frame in loaded.frames:
            window.cache[frame.id] = image
            window.dimensions[frame.id] = (1200, 800)
            window.states[frame.id] = "Ready"
        window.list.setCurrentRow(1)
        window._refresh_selection()
        assert window.pose_controls["rotation"].value() == 12.5
        assert window.pose_controls["scale"].value() == 120

        # Reference selection preserves the canvas; the main lock controls only scale/rotation.
        window.set_reference()
        assert view.editable
        assert all(control.isEnabled() for control in window.pose_sliders.values())
        before = Pose.from_frame(loaded.frames[1])

        # Wheel scrolling moves the panel, including when a value field has focus.
        area = window.panel_wheel_filter.scroll_area
        before_scroll = asdict(window.project)
        undo_index = window.undo_stack.index()
        for control in [*window.blend_fields.values(), window.crop_fields[0],
                        window.pose_controls["rotation"], window.pose_sliders["scale"],
                        window.strength_slider, window.blend_mode, window.edge_fade_amount]:
            area = control.parentWidget()
            while not isinstance(area, QScrollArea):
                area = area.parentWidget()
            window.stages.setCurrentIndex(window.stages.indexOf(area))
            area.ensureWidgetVisible(control)
            control.setFocus()
            app.processEvents()
            bar = area.verticalScrollBar()
            start_scroll = bar.value()
            delta = -120 if start_scroll < bar.maximum() else 120
            position = control.rect().center()
            event = QWheelEvent(QPointF(position), QPointF(control.mapToGlobal(position)),
                                QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(control, event)
            if bar.maximum() > 0:
                assert bar.value() != start_scroll, "Wheel must scroll the panel"
            assert asdict(window.project) == before_scroll, "Wheel changed an edit value"
            assert window.undo_stack.index() == undo_index
        sizes = {(control.width(), control.height()) for control in
                 [*window.blend_fields.values(), *window.crop_fields]}
        assert len(sizes) == 1, f"Value box sizes must match: {sizes}"
        window.stages.setCurrentIndex(1)
        window.view_mode.setCurrentIndex(0)
        assert Pose.from_frame(loaded.frames[1]) == before
        assert loaded.canvas_size == (1200, 800)
        window.undo_stack.undo()
        assert window.project.reference_id == frames[0].id
        assert view.editable and not window.dirty

        window.list.setCurrentRow(0)
        window.pose_controls["rotation"].setValue(20)
        window.pose_controls["scale"].setValue(130)
        window.lock_main_check.setChecked(True)
        assert all(not control.isEnabled() for control in window.pose_sliders.values())
        assert window.pose_controls["x"].isEnabled()
        window.change_pose(Pose(7, 9, 55, 2))
        assert Pose.from_frame(loaded.frames[0]) == Pose(7, 9, 20, 1.3)
        window.undo_stack.undo()
        assert Pose.from_frame(loaded.frames[0]) == Pose(0, 0, 20, 1.3)
        assert window.save_path(destination)
        assert load_project(destination).lock_main_transform
        window._move_layer(1)
        assert window.project.main_frame_id == loaded.frames[0].id
        assert all(control.isEnabled() for control in window.pose_sliders.values())
        window.undo_stack.undo()
        assert not window.pose_sliders["scale"].isEnabled()
        window.list.setCurrentRow(1)
        assert all(control.isEnabled() for control in window.pose_sliders.values())
        before = Pose.from_frame(loaded.frames[1])

        # Escape cancels an in-progress drag without committing an edit.
        start = view.mapFromScene(QPointF(600, 400))
        count = window.undo_stack.count()
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), start + QPoint(12, 12))
        QTest.keyClick(view, Qt.Key.Key_Escape)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(12, 12))
        assert Pose.from_frame(loaded.frames[1]) == before
        assert window.undo_stack.count() == count
        window.grab().save(str(Path(__file__).resolve().parents[1] / "alignment-controls.png"))
        window.dirty = False
        window.close()
    print("PASS: alignment keyboard and drag gestures, canvas geometry, main transform lock, "
          "rotation/scale, reset, undo/redo, clean-save tracking, preserved zoom, "
          "display-only overlay and guides, saved poses, cancelled drags, "
          "scroll-safe controls, and matching value box sizes")


if __name__ == "__main__":
    main()
