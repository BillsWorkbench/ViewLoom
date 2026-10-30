"""Blend controls, asynchronous composite previews, and cancellable export."""
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColorSpace, QImage, QUndoCommand
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QListView, QMessageBox, QProgressBar, QPushButton,
                               QSlider, QStyleFactory, QToolBar, QVBoxLayout, QWidget)
from threading import Event
from uuid import uuid4

from .composite import Cancelled, Renderer, effective_opacity, output_geometry
from .project import Frame
from .ui_groups import control_group


class SettingCommand(QUndoCommand):
    def __init__(self, window, frame_id, field, before, after, label, merge_key=None):
        super().__init__(label)
        self.window, self.frame_id, self.field = window, frame_id, field
        self.before, self.after, self.merge_key = before, after, merge_key

    def id(self):
        return 2 if self.merge_key else -1

    def mergeWith(self, other):
        if (not isinstance(other, SettingCommand) or self.merge_key != other.merge_key or
                self.frame_id != other.frame_id or self.field != other.field):
            return False
        self.after = other.after
        self.setObsolete(self.before == self.after)
        return True

    def redo(self):
        self.window._apply_setting(self.frame_id, self.field, self.after)

    def undo(self):
        self.window._apply_setting(self.frame_id, self.field, self.before)


class RenderSignals(QObject):
    result = Signal(str, int, object, str)
    progress = Signal(float, str)
    done = Signal(str)


class RenderJob(QRunnable):
    def __init__(self, renderer, project, revision, generation, destination=None):
        super().__init__()
        self.id = str(uuid4())
        self.renderer, self.project = renderer, project
        self.revision, self.generation, self.destination = revision, generation, destination
        self.cancellation = Event()
        self.signals = RenderSignals()

    def run(self):
        try:
            if self.destination:
                result = self.renderer.export(self.project, self.destination, self.cancellation,
                                               lambda value, text: self.signals.progress.emit(value, text))
            else:
                result = self.renderer.preview(self.project, self.cancellation)
            self.signals.result.emit(self.generation, self.revision, result, "")
        except Cancelled:
            self.signals.result.emit(self.generation, self.revision, None, "Cancelled")
        except Exception as error:
            self.signals.result.emit(self.generation, self.revision, None,
                                     str(error).strip() or type(error).__name__)
        finally:
            self.signals.done.emit(self.id)


class CompositeWorkspace:
    def _init_compositing(self):
        self.renderer = Renderer()
        self.render_pool = QThreadPool(self)
        self.render_pool.setMaxThreadCount(1)
        self.render_job = None
        self.composite_revision = 0
        self.composite_queued = False
        self.composite_timer = QTimer(self)
        self.composite_timer.setSingleShot(True)
        self.composite_timer.setInterval(150)
        self.composite_timer.timeout.connect(self._request_composite)
        self.blend_gesture = None
        self.pending_export = None
        self.export_busy = False
        self.composite_drag_offset = None

    def _build_composite_controls(self):
        widget = QWidget()
        root_layout = QVBoxLayout(widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(14)
        balance_box, layout = control_group("Blend & strength")
        root_layout.addWidget(balance_box)
        self.main_image = QLabel("Main image · top layer")
        self.main_image.setWordWrap(True)
        layout.addWidget(self.main_image)
        self.strength_label = QLabel("Strength · 0%")
        layout.addWidget(self.strength_label)
        self.strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.strength_slider.setRange(0, 1000)
        self.strength_slider.setAccessibleName("Strength")
        self.strength_slider.sliderPressed.connect(self._start_blend_slider)
        self.strength_slider.sliderReleased.connect(lambda: setattr(self, "blend_gesture", None))
        self.strength_slider.valueChanged.connect(lambda value:
            self._set_setting(None, "strength", value / 1000, "Adjust strength", self.blend_gesture))
        layout.addWidget(self.strength_slider)
        strength_note = QLabel("0%: layer balance · 100%: main image only")
        strength_note.setWordWrap(True)
        strength_note.setObjectName("muted")
        layout.addWidget(strength_note)
        self.blend_mode = QComboBox()
        popup_style = QStyleFactory.create("Fusion")
        popup_style.setParent(self.blend_mode)
        self.blend_mode.setStyle(popup_style)
        popup = QListView()
        popup.setStyleSheet("""
            QListView { background: #252b30; color: #ece8e1; border: 1px solid #66727c;
                outline: none; padding: 4px; selection-background-color: #505b64;
                selection-color: #fff1ce; }
            QListView::item { background: #252b30; color: #ece8e1;
                padding: 7px 12px; min-height: 20px; border-radius: 3px; }
            QListView::item:selected, QListView::item:hover { background: #505b64; color: #fff1ce; }
        """)
        self.blend_mode.setView(popup)
        for mode in ("average", "normal", "multiply", "screen", "lighten"):
            self.blend_mode.addItem(mode.title(), mode)
        self.blend_mode.currentIndexChanged.connect(
            lambda: self._set_setting(None, "blend_mode", self.blend_mode.currentData(), "Change blend mode"))
        layout.addWidget(QLabel("Blend mode"))
        layout.addWidget(self.blend_mode)
        layer_box, layout = control_group("Selected layer")
        root_layout.addWidget(layer_box)
        self.include_check = QCheckBox("Include selected photograph")
        self.include_check.toggled.connect(
            lambda value: self._selected_setting("enabled", value, "Include / exclude photograph"))
        layout.addWidget(self.include_check)
        self.blend_fields = {}
        self.blend_sliders = {}
        for key, label, low, high, multiplier, suffix in (
            ("opacity", "Opacity", 0, 100, 100, "%"),
        ):
            row = QHBoxLayout()
            row.setSpacing(6)
            label_widget = QLabel(label)
            label_widget.setFixedWidth(80)
            row.addWidget(label_widget)
            control = QDoubleSpinBox()
            control.setFixedHeight(30)
            control.setRange(low, high)
            control.setDecimals(1)
            control.setSingleStep(1 if key == "opacity" else 0.1)
            control.setSuffix(suffix)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda value, field=key, factor=multiplier:
                                         self._selected_setting(field, value / factor, "Adjust " + field))
            row.addWidget(control, 1)
            layout.addLayout(row)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(int(low * 10), int(high * 10))
            slider.setAccessibleName(label + " slider")
            slider.sliderPressed.connect(self._start_blend_slider)
            slider.sliderReleased.connect(lambda: setattr(self, "blend_gesture", None))
            slider.valueChanged.connect(lambda value, field=key, factor=multiplier:
                                        self._selected_setting(field, value / (10 * factor),
                                                               "Adjust " + field, self.blend_gesture))
            layout.addWidget(slider)
            self.blend_fields[key], self.blend_sliders[key] = control, slider
        self.effective_opacity_label = QLabel()
        layout.addWidget(self.effective_opacity_label)
        self.layer_order_controls, layer_layout = control_group("Layer order")
        order_note = QLabel("Drag to reorder · top = top layer")
        order_note.setObjectName("muted")
        layer_layout.addWidget(order_note)
        order_row = QHBoxLayout()
        self.layer_up = QPushButton("Move up")
        self.layer_up.clicked.connect(lambda: self._move_layer(-1))
        self.layer_down = QPushButton("Move down")
        self.layer_down.clicked.connect(lambda: self._move_layer(1))
        order_row.addWidget(self.layer_up)
        order_row.addWidget(self.layer_down)
        layer_layout.addLayout(order_row)
        note = QLabel("Average uses opacity as a relative contribution. Other modes use stacked layers. "
                      "Uncovered canvas is black.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self.export_panel = QWidget()
        export_root = QVBoxLayout(self.export_panel)
        export_root.setContentsMargins(14, 12, 0, 0)
        export_root.setSpacing(14)
        crop_box, layout = control_group("Final crop")
        export_root.addWidget(crop_box)
        layout.addWidget(QLabel("Crop · percent of full canvas"))
        crop_form = QFormLayout()
        crop_form.setHorizontalSpacing(6)
        crop_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.crop_fields = []
        for index, label in enumerate(("Left", "Top", "Right", "Bottom")):
            control = QDoubleSpinBox()
            control.setFixedHeight(30)
            control.setRange(0, 100)
            control.setDecimals(2)
            control.setSingleStep(1)
            control.setSuffix("%")
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda value, edge=index: self._change_crop(edge, value / 100))
            label_widget = QLabel(label)
            label_widget.setFixedWidth(80)
            crop_form.addRow(label_widget, control)
            self.crop_fields.append(control)
        layout.addLayout(crop_form)
        reset_crop = QPushButton("Reset crop")
        reset_crop.clicked.connect(lambda: self._set_setting(None, "crop", None, "Reset crop"))
        layout.addWidget(reset_crop)
        fade_box, layout = control_group("Edge fade")
        root_layout.addWidget(fade_box)
        self.edge_fade_check = QCheckBox("Fade selected image edges")
        self.edge_fade_check.toggled.connect(lambda enabled: self._selected_setting(
            "edge_fade", 0.05 if enabled else 0, "Toggle edge fade"))
        layout.addWidget(self.edge_fade_check)
        self.edge_fade_amount = QDoubleSpinBox()
        self.edge_fade_amount.setRange(0, 50)
        self.edge_fade_amount.setDecimals(1)
        self.edge_fade_amount.setSuffix("%")
        self.edge_fade_amount.setKeyboardTracking(False)
        self.edge_fade_amount.valueChanged.connect(lambda value:
            self._selected_setting("edge_fade", value / 100, "Adjust edge fade"))
        layout.addWidget(self.edge_fade_amount)
        self.edge_fade_slider = QSlider(Qt.Orientation.Horizontal)
        self.edge_fade_slider.setRange(0, 500)
        self.edge_fade_slider.setAccessibleName("Edge fade width")
        self.edge_fade_slider.sliderPressed.connect(self._start_blend_slider)
        self.edge_fade_slider.sliderReleased.connect(lambda: setattr(self, "blend_gesture", None))
        self.edge_fade_slider.valueChanged.connect(lambda value: self._selected_setting(
            "edge_fade", value / 1000, "Adjust edge fade", self.blend_gesture))
        layout.addWidget(self.edge_fade_slider)
        fade_note = QLabel("Softens the selected photograph's own boundary to blend it into the layers below. "
                           "Width is a percentage of its shorter edge.")
        fade_note.setWordWrap(True)
        fade_note.setObjectName("muted")
        layout.addWidget(fade_note)
        export_box, layout = control_group("Export")
        export_root.addWidget(export_box)
        self.export_size = QLabel()
        layout.addWidget(self.export_size)
        self.export_button = QPushButton("Export 16-bit TIFF…")
        self.export_button.clicked.connect(self.export_dialog)
        layout.addWidget(self.export_button)
        self.composite_status = QLabel("Choose Composite view to see the blend.")
        self.composite_status.setWordWrap(True)
        self.composite_status.setObjectName("muted")
        layout.addWidget(self.composite_status)
        root_layout.addStretch()
        export_root.addStretch()
        self.export_progress = QProgressBar()
        self.export_progress.setFixedWidth(240)
        self.export_progress.hide()
        self.cancel_export = QPushButton("Cancel export")
        self.cancel_export.clicked.connect(self._cancel_export)
        self.cancel_export.hide()
        self.statusBar().addPermanentWidget(self.export_progress)
        self.statusBar().addPermanentWidget(self.cancel_export)
        return widget

    def _start_blend_slider(self):
        self.blend_gesture = str(uuid4())

    def _selected_setting(self, field, value, label, merge_key=None):
        frame = self.current_frame()
        if frame:
            self._set_setting(frame.id, field, value, label, merge_key)

    def _set_setting(self, frame_id, field, after, label, merge_key=None):
        target = self.project.find(frame_id) if frame_id else self.project
        if target is None:
            return
        before = tuple(f.id for f in self.project.frames) if field == "order" else getattr(target, field)
        if before != after:
            self.undo_stack.push(SettingCommand(self, frame_id, field, before, after, label, merge_key))
            if field not in {"order", "lock_main_transform"}:
                self.view_mode.setCurrentIndex(1)

    def _layers_dropped(self):
        identities = tuple(self.list.item(index).data(Qt.ItemDataRole.UserRole)
                           for index in range(self.list.count()))
        if set(identities) == {frame.id for frame in self.project.frames}:
            self._set_setting(None, "order", identities, "Reorder layers")

    def _apply_setting(self, frame_id, field, value):
        target = self.project.find(frame_id) if frame_id else self.project
        if target is None:
            return
        if field == "order":
            current = self.current_frame()
            frames = {frame.id: frame for frame in self.project.frames}
            self.project.frames = [frames[identity] for identity in value]
            items = {frame.id: self._item(frame.id) for frame in self.project.frames}
            self.list.blockSignals(True)
            while self.list.count():
                self.list.takeItem(0)
            for frame in self.project.frames:
                self.list.addItem(items[frame.id])
            if current:
                self.list.setCurrentItem(items[current.id])
            self.list.blockSignals(False)
            for frame in self.project.frames:
                self._update_item(frame)
        else:
            setattr(target, field, value)
        if frame_id:
            self._update_item(target)
        self._refresh_selection()

    def _move_layer(self, offset):
        frame = self.current_frame()
        if frame is None:
            return
        identities = [item.id for item in self.project.frames]
        index = identities.index(frame.id)
        other = index + offset
        if 0 <= other < len(identities):
            identities[index], identities[other] = identities[other], identities[index]
            self._set_setting(None, "order", tuple(identities), "Reorder layers")

    def _change_crop(self, edge, value):
        crop = list(self.project.crop or [0, 0, 1, 1])
        crop[edge] = value
        if crop[0] >= crop[2] or crop[1] >= crop[3]:
            self.statusBar().showMessage("Crop left/top must be smaller than right/bottom.")
            self._sync_blend_controls(self.current_frame())
            return
        self._set_setting(None, "crop", None if crop == [0, 0, 1, 1] else crop, "Adjust crop")

    def _sync_blend_controls(self, frame):
        if not hasattr(self, "blend_mode"):
            return
        self.blend_mode.blockSignals(True)
        self.blend_mode.setCurrentIndex(self.blend_mode.findData(self.project.blend_mode))
        self.blend_mode.blockSignals(False)
        main = self.project.frames[0] if self.project.frames else None
        self.main_image.setText(f"Main image · {main.source.name}" if main else "Main image · top layer")
        self.main_image.setToolTip("The top photograph in the left panel is always the main image.")
        self.lock_main_check.blockSignals(True)
        self.lock_main_check.setChecked(self.project.lock_main_transform)
        self.lock_main_check.blockSignals(False)
        fade = frame.edge_fade if frame else 0
        for control, value in ((self.edge_fade_check, fade > 0),
                               (self.edge_fade_amount, fade * 100),
                               (self.edge_fade_slider, round(fade * 1000))):
            control.blockSignals(True)
            if control is self.edge_fade_check:
                control.setChecked(value)
            else:
                control.setValue(value)
            control.blockSignals(False)
            control.setEnabled(frame is not None)
        self.strength_slider.blockSignals(True)
        self.strength_slider.setValue(round(self.project.strength * 1000))
        self.strength_slider.blockSignals(False)
        self.strength_slider.setEnabled(bool(self.project.frames))
        self.strength_label.setText(f"Strength · {self.project.strength:.0%}")
        self.effective_opacity_label.setText(f"Effective opacity · {effective_opacity(self.project, frame):.1%}"
                                             if frame and frame.enabled else "Selected layer excluded")
        self.include_check.blockSignals(True)
        self.include_check.setChecked(frame.enabled if frame else False)
        self.include_check.blockSignals(False)
        self.include_check.setEnabled(frame is not None)
        for key, control in self.blend_fields.items():
            value = getattr(frame, key) if frame else (1 if key == "opacity" else 0)
            multiplier = 100 if key == "opacity" else 1
            control.blockSignals(True)
            control.setValue(value * multiplier)
            control.blockSignals(False)
            control.setEnabled(frame is not None)
            slider = self.blend_sliders[key]
            slider.blockSignals(True)
            slider.setValue(round(value * multiplier * 10))
            slider.blockSignals(False)
            slider.setEnabled(frame is not None)
        index = self.project.frames.index(frame) if frame else -1
        self.layer_up.setEnabled(index > 0)
        self.layer_down.setEnabled(0 <= index < len(self.project.frames) - 1)
        for control, value in zip(self.crop_fields, self.project.crop or [0, 0, 1, 1]):
            control.blockSignals(True)
            control.setValue(value * 100)
            control.blockSignals(False)
        if self.project.canvas_size:
            _, (width, height) = output_geometry(self.project)
            self.export_size.setText(f"Export · {width:,} × {height:,} pixels · 16-bit RGB / sRGB")
        else:
            self.export_size.setText("Load photographs to establish the canvas.")
        self.export_button.setEnabled(self.project.canvas_size is not None and
                                      any(f.enabled and effective_opacity(self.project, f) > 0 for f in self.project.frames))
        self.export_action.setEnabled(self.export_button.isEnabled())

    def _active_view(self):
        if hasattr(self, "stages") and self.stages.currentIndex() == 0:
            return self.develop_view
        return self.composite_view if self.view_mode.currentIndex() == 1 else self.photo_view

    def _view_mode_changed(self, *_):
        if self.view_mode.currentIndex() == 0:
            self.composite_view.cancel_drag()
        self.preview_note.setText("Composite · reduced preview · full-resolution originals are used for export."
                                 if self.view_mode.currentIndex() == 1 else
                                 "Drag to align · arrows: 1 pixel · Shift + arrows: 10 pixels · hold Space to pan")
        self._refresh_selection()

    def _composite_drag_preview(self, dx, dy):
        frame = self.current_frame()
        if self.stages.currentIndex() != 1 or not self._can_align(frame):
            self.composite_drag_offset = None
            return
        self.composite_drag_offset = (frame.id, dx, dy) if dx or dy else None
        if not dx and not dy:
            self._schedule_composite()
        elif self.render_job is not None:
            self.composite_queued = True
        elif not self.composite_timer.isActive():
            self._schedule_composite()
            self.composite_timer.start(30)

    def _schedule_composite(self):
        self.composite_revision += 1
        if (not hasattr(self, "view_mode") or self.view_mode.currentIndex() != 1 or
                hasattr(self, "stages") and self.stages.currentIndex() == 0):
            return
        self.composite_status.setText("Updating composite…")
        self.preview_title.setText("Composite · updating…")
        self.composite_timer.start(150)

    def _request_composite(self):
        if self.view_mode.currentIndex() != 1 or self.stages.currentIndex() == 0:
            return
        if self.render_job is not None:
            self.composite_queued = True
            if self.render_job.destination is None and self.composite_view.drag_start is None:
                self.render_job.cancellation.set()
            return
        if not self.project.canvas_size:
            self._show_message("Import photographs to preview a composite.")
            return
        self.composite_queued = False
        snapshot = deepcopy(self.project)
        if self.stages.currentIndex() == 1:
            snapshot.crop = None
            if self.composite_drag_offset:
                identity, dx, dy = self.composite_drag_offset
                frame = snapshot.find(identity)
                if frame:
                    frame.translation = (frame.translation[0] + dx, frame.translation[1] + dy)
        self._start_render(snapshot)

    def _start_render(self, project, destination=None):
        job = RenderJob(self.renderer, project, self.composite_revision, self.generation, destination)
        job.signals.result.connect(self._render_result)
        job.signals.progress.connect(self._export_progress)
        job.signals.done.connect(self._render_finished)
        self.render_job = job
        self.render_pool.start(job)

    def _render_result(self, generation, revision, result, error):
        job = self.render_job
        if job is None or generation != self.generation:
            return
        if job.destination:
            if error:
                self.statusBar().showMessage("Export cancelled; existing files were preserved." if error == "Cancelled"
                                            else "Export failed; existing files were preserved.")
                if error != "Cancelled":
                    QMessageBox.critical(self, "Could not export TIFF", error)
            else:
                self.statusBar().showMessage(f"Exported {job.destination.name} · {result[0]:,} × {result[1]:,} · 16-bit RGB")
                self.composite_status.setText(f"Exported {job.destination.name}")
            return
        if (revision != self.composite_revision or self.view_mode.currentIndex() != 1 or
                self.stages.currentIndex() == 0):
            return
        if error:
            if error != "Cancelled":
                self.composite_status.setText(error)
                self._show_message("Composite unavailable\n\n" + error)
            return
        height, width, _ = result.shape
        image = QImage(result.data, width, height, result.strides[0], QImage.Format.Format_RGB888).copy()
        image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
        frame = self.current_frame()
        self.composite_view.show_composite(image, self.project.canvas_size,
                                           self.stages.currentIndex() == 1 and self._can_align(frame),
                                           context=(self.generation, frame.id if frame else None, self.stages.currentIndex()))
        self.composite_view.set_guides(False)
        self.stack.setCurrentWidget(self.composite_view)
        self.preview_title.setText("Composite · " + self.project.blend_mode.title())
        self.composite_status.setText("Composite ready · reduced preview; export renders from full-resolution originals.")

    def _render_finished(self, identity):
        if self.render_job is None or identity != self.render_job.id:
            return
        exporting = self.render_job.destination is not None
        self.render_job = None
        if self.pending_export is not None:
            project, destination = self.pending_export
            self.pending_export = None
            self._start_render(project, destination)
            return
        if self.export_busy:
            self.composite_queued = self.composite_queued or self.view_mode.currentIndex() == 1
            if not exporting:
                self.statusBar().showMessage("Export cancelled; existing files were preserved.")
            self.export_busy = False
            self.centralWidget().setEnabled(True)
            self.menuBar().setEnabled(True)
            for toolbar in self.findChildren(QToolBar):
                toolbar.setEnabled(True)
            self.export_progress.hide()
            self.cancel_export.hide()
        if self.composite_queued:
            self.composite_timer.start(0)

    def export_dialog(self):
        if self.render_job and self.render_job.destination:
            return
        self.stages.setCurrentIndex(2)
        suggested = self.project_path.with_suffix(".tiff") if self.project_path else Path("Viewloom composite.tiff")
        path, _ = QFileDialog.getSaveFileName(self, "Export 16-bit TIFF", str(suggested), "TIFF image (*.tif *.tiff)")
        if path:
            destination = Path(path)
            if destination.suffix.lower() not in {".tif", ".tiff"}:
                destination = destination.with_suffix(destination.suffix + ".tiff")
            self.start_export(destination)

    def start_export(self, destination):
        self.export_busy = True
        if self.render_job is not None:
            if self.render_job.destination:
                return False
            self.render_job.cancellation.set()
            # Export starts after the current preview releases its worker.
            self.pending_export = (deepcopy(self.project), Path(destination))
        else:
            self.pending_export = None
            self._start_render(deepcopy(self.project), Path(destination))
        self.composite_timer.stop()
        self.centralWidget().setEnabled(False)
        self.menuBar().setEnabled(False)
        for toolbar in self.findChildren(QToolBar):
            toolbar.setEnabled(False)
        self.export_progress.setValue(0)
        self.cancel_export.setEnabled(True)
        self.export_progress.show()
        self.cancel_export.show()
        return True

    def _export_progress(self, value, text):
        self.export_progress.setValue(round(value * 100))
        self.statusBar().showMessage(text)

    def _cancel_export(self):
        self.cancel_export.setEnabled(False)
        self.statusBar().showMessage("Cancelling export…")
        if getattr(self, "pending_export", None):
            self.pending_export = None
            self.composite_queued = True
        if self.render_job:
            self.render_job.cancellation.set()

    def _cancel_compositing(self):
        self.composite_drag_offset = None
        self.composite_timer.stop()
        self.composite_queued = False
        self.pending_export = None
        if self.render_job:
            self.render_job.cancellation.set()
