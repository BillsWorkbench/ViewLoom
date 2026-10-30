"""Develop step: source previews, gray picker, and selective batch syncing."""
from copy import deepcopy
from pathlib import Path
from threading import Event
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColorSpace, QIcon, QImage, QPixmap, QUndoCommand
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from .alignment_view import PhotoView
from .composite import Cancelled, Renderer, check_cancel
from .development import DEVELOP_FIELDS, camera_identity, develop_pixels, pick_gray
from .imaging import linear_to_srgb
from .project import Frame
from .ui_groups import control_group


class DevelopView(PhotoView):
    gray_picked = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.picking = False
        self.set_guides(False)

    def set_picker(self, enabled):
        self.picking = enabled
        self._cursor()

    def _cursor(self):
        super()._cursor()
        if getattr(self, "picking", False):
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event):
        if self.picking and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            bounds = self.canvas.rect()
            if bounds.contains(point):
                self.gray_picked.emit(point.x() / bounds.width(), point.y() / bounds.height())
            event.accept()
            return
        super().mousePressEvent(event)


class DevelopmentCommand(QUndoCommand):
    def __init__(self, window, changes, label, merge_key=None):
        super().__init__(label)
        self.window, self.after, self.merge_key = window, deepcopy(changes), merge_key
        self.before = {identity: {field: deepcopy(getattr(window.project.find(identity), field))
                                 for field in fields} for identity, fields in changes.items()}

    def id(self):
        return 3 if self.merge_key else -1

    def mergeWith(self, other):
        if (not isinstance(other, DevelopmentCommand) or self.merge_key != other.merge_key or
                self.before.keys() != other.before.keys() or
                any(self.before[key].keys() != other.before[key].keys() for key in self.before)):
            return False
        self.after = deepcopy(other.after)
        self.setObsolete(self.before == self.after)
        return True

    def redo(self):
        self.window._apply_development(self.after)

    def undo(self):
        self.window._apply_development(self.before)


class DevelopSignals(QObject):
    result = Signal(str, str, int, object, str)
    finished = Signal(str)


class DevelopJob(QRunnable):
    def __init__(self, renderer, generation, revision, frame, before, gray=None):
        super().__init__()
        self.id = str(uuid4())
        self.renderer, self.generation, self.revision = renderer, generation, revision
        self.frame, self.before, self.gray = deepcopy(frame), before, gray
        self.cancellation = Event()
        self.signals = DevelopSignals()

    def run(self):
        try:
            check_cancel(self.cancellation)
            if self.gray is not None:
                result = pick_gray(self.frame.source, self.frame.sha256, *self.gray)
            else:
                frame = self.frame
                if self.before:
                    frame = Frame(frame.source, sha256=frame.sha256)
                linear, _ = self.renderer._source(frame, True, self.cancellation)
                result = np.ascontiguousarray(np.rint(linear_to_srgb(develop_pixels(linear, frame)) * 255).astype(np.uint8))
            check_cancel(self.cancellation)
            self.signals.result.emit(self.generation, self.frame.id, self.revision, result, "")
        except Cancelled:
            pass
        except Exception as error:
            self.signals.result.emit(self.generation, self.frame.id, self.revision, None,
                                     str(error).strip() or type(error).__name__)
        finally:
            self.signals.finished.emit(self.id)


class DevelopmentWorkspace:
    def _init_development(self):
        self.develop_renderer = Renderer()
        self.develop_pool = QThreadPool(self)
        self.develop_pool.setMaxThreadCount(1)
        self.develop_job = None
        self.develop_revision = 0
        self.develop_queued = False
        self.pending_gray = None
        self.develop_gesture = None
        self._develop_selected_id = None
        self._develop_display_id = None
        self.develop_timer = QTimer(self)
        self.develop_timer.setSingleShot(True)
        self.develop_timer.timeout.connect(self._request_develop)

    def _build_develop_controls(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 0, 0)
        layout.setSpacing(14)
        selected_box, selected_layout = control_group("Selected photograph")
        layout.addWidget(selected_box)
        self.develop_name = QLabel("Import a photograph to begin.")
        self.develop_name.setWordWrap(True)
        selected_layout.addWidget(self.develop_name)
        self.before_check = QCheckBox("Before · As shot")
        self.before_check.toggled.connect(self._before_changed)
        selected_layout.addWidget(self.before_check)
        self.develop_fields, self.develop_sliders = {}, {}
        for title, fields in (("Tone & color", ("exposure", "contrast", "saturation")),
                              ("White balance", ("warmth", "tint"))):
            box, controls_layout = control_group(title)
            layout.addWidget(box)
            if title == "White balance":
                self.wb_status = QLabel("As shot")
                controls_layout.addWidget(self.wb_status)
                actions = QHBoxLayout()
                self.gray_button = QPushButton("Pick neutral gray")
                self.gray_button.setCheckable(True)
                self.gray_button.toggled.connect(self.develop_view.set_picker)
                actions.addWidget(self.gray_button)
                self.as_shot_button = QPushButton("As shot")
                self.as_shot_button.clicked.connect(self._as_shot)
                actions.addWidget(self.as_shot_button)
                controls_layout.addLayout(actions)
                note = QLabel("Pick a neutral midtone in this source photograph. Warmth and tint are relative adjustments.")
                note.setWordWrap(True)
                note.setObjectName("muted")
                controls_layout.addWidget(note)
            for field in fields:
                row = QHBoxLayout()
                row.setSpacing(6)
                label = QLabel(field.title())
                label.setFixedWidth(80)
                row.addWidget(label)
                spin = QDoubleSpinBox()
                spin.setFixedHeight(30)
                spin.setRange(-10, 10) if field == "exposure" else spin.setRange(-100, 100)
                spin.setDecimals(1)
                spin.setSingleStep(0.1 if field == "exposure" else 1)
                spin.setSuffix(" EV" if field == "exposure" else "")
                spin.setKeyboardTracking(False)
                spin.valueChanged.connect(lambda value, key=field: self._develop_value(key, value))
                row.addWidget(spin, 1)
                controls_layout.addLayout(row)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(-100, 100)
                slider.setAccessibleName("Develop " + field)
                slider.sliderPressed.connect(lambda: setattr(self, "develop_gesture", str(uuid4())))
                slider.sliderReleased.connect(lambda: setattr(self, "develop_gesture", None))
                slider.valueChanged.connect(lambda value, key=field:
                    self._develop_value(key, value / 10 if key == "exposure" else value, self.develop_gesture))
                controls_layout.addWidget(slider)
                self.develop_fields[field], self.develop_sliders[field] = spin, slider
        batch_box, batch_layout = control_group("Batch development")
        layout.addWidget(batch_box)
        self.batch_count = QLabel()
        self.batch_count.setWordWrap(True)
        batch_layout.addWidget(self.batch_count)
        self.batch_button = QPushButton("Apply main adjustments to selected…")
        self.batch_button.clicked.connect(lambda: self._batch_dialog(False))
        batch_layout.addWidget(self.batch_button)
        self.batch_all_button = QPushButton("Apply main adjustments to all…")
        self.batch_all_button.clicked.connect(lambda: self._batch_dialog(True))
        batch_layout.addWidget(self.batch_all_button)
        self.batch_status = QLabel()
        self.batch_status.setWordWrap(True)
        batch_layout.addWidget(self.batch_status)
        self.reset_develop_button = QPushButton("Reset selected development")
        self.reset_develop_button.clicked.connect(self._reset_development)
        batch_layout.addWidget(self.reset_develop_button)
        self.develop_status = QLabel()
        self.develop_status.setWordWrap(True)
        self.develop_status.setObjectName("muted")
        layout.addWidget(self.develop_status)
        layout.addStretch()
        return root

    def _develop_value(self, field, value, merge_key=None):
        frame = self.current_frame()
        if frame:
            self.change_development({frame.id: {field: value}}, "Adjust " + field, merge_key)

    def change_development(self, changes, label, merge_key=None):
        actual = {identity: values for identity, values in changes.items()
                  if self.project.find(identity) and any(getattr(self.project.find(identity), key) != value
                                                        for key, value in values.items())}
        if actual:
            self.undo_stack.push(DevelopmentCommand(self, actual, label, merge_key))

    def _apply_development(self, changes):
        updated = []
        for identity, values in changes.items():
            frame = self.project.find(identity)
            if frame:
                for field, value in values.items():
                    setattr(frame, field, deepcopy(value))
                self.cache.pop(identity, None)
                updated.append(frame)
        for frame in updated:
            self._enqueue(frame)
        self._refresh_selection()

    def _reset_development(self):
        frame = self.current_frame()
        if frame:
            defaults = Frame(frame.source)
            self.change_development({frame.id: {key: getattr(defaults, key) for key in DEVELOP_FIELDS}}, "Reset development")

    def _as_shot(self):
        frame = self.current_frame()
        if frame:
            self.change_development({frame.id: {"warmth": 0, "tint": 0, "wb_multipliers": None, "wb_camera": ""}},
                                    "Reset white balance")

    def _before_changed(self, before):
        if before:
            self.gray_button.setChecked(False)
        self._refresh_develop_controls(self.current_frame())
        self._schedule_develop()

    def _refresh_batch_count(self):
        if not hasattr(self, "batch_count"):
            return
        main = self.project.frames[0] if self.project.frames else None
        selected = self._batch_targets(False)
        name = main.source.name if main else "none"
        self.batch_count.setText(f"Source: main photo · {name}\n{len(selected)} selected targets. "
                                "Use Cmd/Ctrl-click or Shift-click to select photos.")
        self.batch_button.setEnabled(main is not None and main.sha256 is not None and bool(selected))
        self.batch_all_button.setEnabled(main is not None and main.sha256 is not None and len(self.project.frames) > 1)

    def _refresh_develop_controls(self, frame):
        if not hasattr(self, "develop_fields"):
            return
        identity = frame.id if frame else None
        if identity != self._develop_selected_id:
            self._develop_selected_id = identity
            self.pending_gray = None
            self.gray_button.setChecked(False)
            self.before_check.blockSignals(True)
            self.before_check.setChecked(False)
            self.before_check.blockSignals(False)
            if hasattr(self, "stages") and self.stages.currentIndex() == 0:
                self._show_message("Developing selected photograph…" if frame else "Import photographs to begin.")
        enabled = frame is not None and frame.sha256 is not None
        self.develop_name.setText(frame.source.name if frame else "Import a photograph to begin.")
        self.before_check.setEnabled(enabled)
        for key, spin in self.develop_fields.items():
            value = getattr(frame, key) if frame else 0
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
            spin.setEnabled(enabled and not self.before_check.isChecked())
            slider = self.develop_sliders[key]
            slider.blockSignals(True)
            slider.setValue(round(value * 10 if key == "exposure" else value))
            slider.blockSignals(False)
            slider.setEnabled(spin.isEnabled())
        self.gray_button.setEnabled(enabled and self._develop_display_id == identity and not self.before_check.isChecked())
        self.as_shot_button.setEnabled(enabled and not self.before_check.isChecked())
        self.reset_develop_button.setEnabled(enabled)
        self.wb_status.setText("Gray-picked base" if frame and frame.wb_multipliers else "As-shot base")
        self._refresh_batch_count()

    def _schedule_develop(self):
        self.develop_revision += 1
        if hasattr(self, "stages") and self.stages.currentIndex() == 0:
            self.develop_timer.start(150)

    def _request_develop(self):
        if self.stages.currentIndex() != 0:
            return
        frame = self.current_frame()
        if frame is None or frame.sha256 is None:
            return
        if self.develop_job:
            self.develop_job.cancellation.set()
            self.develop_queued = True
            return
        gray = self.pending_gray
        self.pending_gray = None
        self.develop_queued = False
        job = DevelopJob(self.develop_renderer, self.generation, self.develop_revision, frame,
                         self.before_check.isChecked(), gray)
        job.signals.result.connect(self._develop_result)
        job.signals.finished.connect(self._develop_finished)
        self.develop_job = job
        self.develop_status.setText("Reading neutral patch…" if gray else "Developing preview…")
        self.develop_pool.start(job)

    def _gray_clicked(self, x, y):
        self.gray_button.setChecked(False)
        self.pending_gray = (x, y)
        self._schedule_develop()

    def _develop_result(self, generation, frame_id, revision, result, error):
        frame = self.current_frame()
        if (generation != self.generation or revision != self.develop_revision or frame is None or
                frame.id != frame_id or self.stages.currentIndex() != 0):
            return
        if error:
            self.develop_status.setText(error)
            if not self.develop_job.gray:
                self._show_message("Develop preview unavailable\n\n" + error)
            return
        if isinstance(result, dict):
            self.change_development({frame_id: result}, "Pick gray white balance")
            self._schedule_develop()
            return
        height, width, _ = result.shape
        image = QImage(result.data, width, height, result.strides[0], QImage.Format.Format_RGB888).copy()
        image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
        if not self.before_check.isChecked():
            self.cache[frame_id] = image
            self.cache.move_to_end(frame_id)
            while len(self.cache) > 12:
                victim = next(key for key in self.cache if key not in {frame_id, self.project.reference_id})
                del self.cache[victim]
            item = self._item(frame_id)
            if item:
                item.setIcon(QIcon(QPixmap.fromImage(image).scaled(144, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                                                  Qt.TransformationMode.SmoothTransformation)))
        self.develop_view.show_alignment(image, Frame(Path("develop")), (width, height),
                                         (width, height), context=(generation, frame_id))
        self._develop_display_id = frame_id
        self.gray_button.setEnabled(not self.before_check.isChecked())
        self.stack.setCurrentWidget(self.develop_view)
        self.preview_title.setText(("Before · " if self.before_check.isChecked() else "Developed · ") + frame.source.name)
        self.develop_status.setText("As shot · temporary before view" if self.before_check.isChecked() else "Development preview ready")

    def _stage_changed(self, index):
        self._cancel_development()
        self.gray_button.setChecked(False)
        self.view_mode.setVisible(index == 1)
        if index == 2:
            self.view_mode.setCurrentIndex(1)
        if index == 0:
            self.preview_note.setText("Develop the selected source photograph · gray picker samples original RAW data.")
        elif index == 2:
            self.preview_note.setText("Final crop · 16-bit RGB TIFF / sRGB · originals remain unchanged.")
        else:
            self._view_mode_changed()
        self._refresh_selection()

    def _develop_finished(self, identity):
        if self.develop_job and self.develop_job.id == identity:
            self.develop_job = None
            if self.develop_queued or self.pending_gray is not None:
                self.develop_timer.start(0)

    def _cancel_development(self):
        self.develop_timer.stop()
        self.develop_queued = False
        self.pending_gray = None
        if self.develop_job:
            self.develop_job.cancellation.set()

    def _batch_targets(self, all_images):
        main_id = self.project.main_frame_id
        candidates = (self.project.frames if all_images else
                      [self.project.find(item.data(Qt.ItemDataRole.UserRole)) for item in self.list.selectedItems()])
        return [frame for frame in candidates if frame is not None and frame.id != main_id]

    def apply_batch_development(self, fields, all_images=False):
        source = self.project.frames[0] if self.project.frames else None
        if source is None:
            return
        targets = self._batch_targets(all_images)
        changes, skipped = {}, 0
        for target in targets:
            if target is None or target.id == source.id:
                continue
            values = {field: deepcopy(getattr(source, field)) for field in fields}
            if "wb_multipliers" in values and source.wb_multipliers is not None:
                identity = camera_identity(target.source)
                if not source.wb_camera or identity != source.wb_camera:
                    values.pop("wb_multipliers", None)
                    values.pop("wb_camera", None)
                    skipped += 1
            if values:
                changes[target.id] = values
        self.change_development(changes, "Apply main development to all" if all_images else "Apply main development to selected")
        self.batch_status.setText(f"Applied main photo adjustments to {len(changes)} photographs." +
            (f" Gray-picked calibration skipped for {skipped} photographs from different cameras; pick their gray separately." if skipped else ""))

    def _batch_dialog(self, all_images=False):
        source = self.project.frames[0] if self.project.frames else None
        targets = self._batch_targets(all_images)
        if source is None or not targets:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Apply main adjustments to all" if all_images else "Apply main adjustments to selected")
        layout = QVBoxLayout(dialog)
        description = QLabel(f"Copy from main photo {source.source.name} to {len(targets)} "
                             f"{'other' if all_images else 'selected'} photographs. "
                             "Choose which settings to apply:")
        description.setTextFormat(Qt.TextFormat.PlainText)
        description.setWordWrap(True)
        layout.addWidget(description)
        options = {}
        for title, fields in (("Exposure", ("exposure",)), ("Contrast", ("contrast",)),
                              ("Saturation", ("saturation",)),
                              ("White balance", ("warmth", "tint", "wb_multipliers", "wb_camera"))):
            check = QCheckBox(title)
            check.setChecked(True)
            layout.addWidget(check)
            options[check] = fields
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_batch_development([field for check, fields in options.items() if check.isChecked() for field in fields],
                                         all_images=all_images)
