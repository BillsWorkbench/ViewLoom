"""Desktop workspace with non-destructive manual subject alignment."""
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
import sys
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColorSpace, QIcon, QImage, QKeySequence, QPixmap, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSlider, QSplitter, QStackedWidget, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from .imaging import decode_preview
from .project import Frame, Project, load_project, save_project
from .qt_runtime import prepare_qt_plugins
from .alignment import Pose
from .alignment_view import CompositeView, PhotoView
from . import __version__
from .compositing_ui import CompositeWorkspace
from .ui_groups import control_group
from .scroll_controls import PanelWheelFilter
from .development import recipe_key
from .develop_ui import DevelopView, DevelopmentWorkspace
from .work_progress import WorkProgress


RAW_FILTER = "RAW photographs (*.nef *.NEF *.dng *.DNG)"
PROJECT_FILTER = "Viewloom project (*.viewloom)"


class JobSignals(QObject):
    result = Signal(str, str, object, object, str)
    finished = Signal(str)


class PreviewJob(QRunnable):
    def __init__(self, job_id, generation, frame, source, cancellation):
        super().__init__()
        self.job_id = job_id
        self.generation = generation
        self.frame_id = frame.id
        self.expected = frame.sha256
        self.frame = deepcopy(frame)
        self.source = source
        self.cancellation = cancellation
        self.signals = JobSignals()

    def run(self):
        try:
            if self.cancellation.is_set():
                return
            preview = decode_preview(self.source, self.expected, frame=self.frame)
            if not self.cancellation.is_set():
                self.signals.result.emit(self.generation, self.frame_id, self.source, preview, "")
        except Exception as error:
            if not self.cancellation.is_set():
                message = str(error).strip() or type(error).__name__
                self.signals.result.emit(self.generation, self.frame_id, self.source, None, message)
        finally:
            self.signals.finished.emit(self.job_id)


class PoseCommand(QUndoCommand):
    def __init__(self, window, frame_id, before, after, label, merge_key=None):
        super().__init__(label)
        self.window, self.frame_id = window, frame_id
        self.before, self.after = before, after
        self.merge_key = merge_key

    def id(self):
        return 1 if self.merge_key is not None else -1

    def mergeWith(self, other):
        if (not isinstance(other, PoseCommand) or self.merge_key != other.merge_key
                or self.frame_id != other.frame_id):
            return False
        self.after = other.after
        self.setObsolete(self.before == self.after)
        return True

    def redo(self):
        self.window._apply_pose(self.frame_id, self.after)

    def undo(self):
        self.window._apply_pose(self.frame_id, self.before)


class ReferenceCommand(QUndoCommand):
    def __init__(self, window, before, after):
        super().__init__("Change reference")
        self.window, self.before, self.after = window, before, after

    def redo(self):
        self.window._apply_reference(self.after)

    def undo(self):
        self.window._apply_reference(self.before)


class MainWindow(WorkProgress, DevelopmentWorkspace, CompositeWorkspace, QMainWindow):
    def __init__(self):
        super().__init__()
        self.project = Project()
        self.project_path = None
        self.dirty = False
        self.structural_dirty = False
        self.undo_stack = QUndoStack(self)
        self.undo_stack.indexChanged.connect(self._sync_dirty)
        self.generation = str(uuid4())
        self.cancellation = Event()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.jobs = {}
        self.pending = set()
        self.cache = OrderedDict()
        self.states = {}
        self.dimensions = {}
        self.slider_gesture = None
        self._init_compositing()
        self._init_development()
        self.resize(1340, 820)
        self.setMinimumSize(900, 580)
        self._build_ui()
        self._init_work_progress()
        self._update_title()
        self._refresh_selection()

    def _action(self, text, callback, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut is not None:
            action.setShortcut(shortcut)
        return action

    def _build_ui(self):
        file_menu = self.menuBar().addMenu("&File")
        toolbar = QToolBar("Project")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        actions = [
            self._action("New project", self.new_project, QKeySequence.StandardKey.New),
            self._action("Open…", self.open_project, QKeySequence.StandardKey.Open),
            self._action("Save", self.save, QKeySequence.StandardKey.Save),
            self._action("Save as…", self.save_as, QKeySequence.StandardKey.SaveAs),
            self._action("Import photographs…", self.import_dialog, "Ctrl+I"),
        ]
        for action in actions:
            file_menu.addAction(action)
            toolbar.addAction(action)
        self.export_action = self._action("Export TIFF…", self.export_dialog, "Ctrl+E")
        file_menu.addAction(self.export_action)
        toolbar.addAction(self.export_action)
        edit_menu = self.menuBar().addMenu("&Edit")
        self.undo_action = self.undo_stack.createUndoAction(self, "Undo")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = self.undo_stack.createRedoAction(self, "Redo")
        self.redo_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Redo))
        for action in (self.undo_action, self.redo_action):
            edit_menu.addAction(action)
            toolbar.addAction(action)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(18, 14, 18, 14)
        heading = QLabel("Viewloom")
        heading.setObjectName("brand")
        layout.addWidget(heading)
        subtitle = QLabel("Multiple perspectives. One image.")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        splitter = QSplitter()
        layout.addWidget(splitter, 1)
        library = QWidget()
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(0, 12, 12, 0)
        self.count_label = QLabel("PHOTOGRAPHS · 0")
        library_layout.addWidget(self.count_label)
        self.list = QListWidget()
        self.list.setIconSize(QSize(144, 96))
        self.list.setSpacing(4)
        self.list.setMinimumWidth(210)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._refresh_batch_count)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setDropIndicatorShown(True)
        self.list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._layers_dropped))
        self.list.setToolTip("Drag photographs to reorder layers. The top photograph is the top layer.")
        self.list.currentItemChanged.connect(self._refresh_selection)
        library_layout.addWidget(self.list, 1)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_selected)
        library_layout.addWidget(self.remove_button)
        splitter.addWidget(library)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 12, 0, 0)
        self.preview_title = QLabel("Your photographs, ready to weave")
        view_header = QHBoxLayout()
        view_header.addWidget(self.preview_title, 1)
        self.view_mode = QComboBox()
        self.view_mode.addItems(["Alignment", "Composite"])
        self.view_mode.currentIndexChanged.connect(self._view_mode_changed)
        view_header.addWidget(self.view_mode)
        center_layout.addLayout(view_header)
        self.stack = QStackedWidget()
        self.placeholder = QLabel("Import NEF or DNG photographs to begin.")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setObjectName("placeholder")
        self.photo_view = PhotoView()
        self.photo_view.translated.connect(self.translate_selected)
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.photo_view)
        self.composite_view = CompositeView()
        self.composite_view.translated.connect(self.translate_selected)
        self.composite_view.drag_preview.connect(self._composite_drag_preview)
        self.composite_view.set_guides(False)
        self.stack.addWidget(self.composite_view)
        self.develop_view = DevelopView()
        self.develop_view.gray_picked.connect(self._gray_clicked)
        self.stack.addWidget(self.develop_view)
        center_layout.addWidget(self.stack, 1)
        preview_tools = QHBoxLayout()
        fit = QPushButton("Fit photograph")
        fit.clicked.connect(lambda: self._active_view().fit_image())
        preview_tools.addWidget(fit)
        pixels = QPushButton("Preview pixels 1:1")
        pixels.setToolTip("Shows the reduced preview at its own pixel size, not full RAW resolution.")
        pixels.clicked.connect(lambda: self._active_view().actual_size())
        preview_tools.addWidget(pixels)
        preview_tools.addStretch()
        center_layout.addLayout(preview_tools)
        self.preview_note = QLabel("Drag to align · arrows: 1 pixel · Shift + arrows: 10 pixels · hold Space to pan")
        self.preview_note.setObjectName("muted")
        self.preview_note.setWordWrap(True)
        center_layout.addWidget(self.preview_note)
        splitter.addWidget(center)

        inspector = QWidget()
        inspector_root = QVBoxLayout(inspector)
        inspector_root.setContentsMargins(14, 12, 0, 0)
        inspector_root.setSpacing(14)
        photograph_box, inspector_layout = control_group("Photograph")
        inspector_root.addWidget(photograph_box)
        self.details = QLabel("Select a photograph to inspect it.")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inspector_layout.addWidget(self.details)
        self.reference_button = QPushButton("Use as reference")
        self.reference_button.clicked.connect(self.set_reference)
        photograph_actions = QHBoxLayout()
        photograph_actions.addWidget(self.reference_button)
        self.relink_button = QPushButton("Relink original…")
        self.relink_button.clicked.connect(self.relink_dialog)
        photograph_actions.addWidget(self.relink_button)
        inspector_layout.addLayout(photograph_actions)
        alignment_box, inspector_layout = control_group("Alignment")
        inspector_root.addWidget(alignment_box)
        self.alignment_hint = QLabel("Select a photograph to align.")
        self.alignment_hint.setWordWrap(True)
        self.alignment_hint.setObjectName("muted")
        inspector_layout.addWidget(self.alignment_hint)
        self.lock_main_check = QCheckBox("Lock main image scale and rotation")
        self.lock_main_check.toggled.connect(lambda value: self._set_setting(
            None, "lock_main_transform", value, "Lock / unlock main image"))
        inspector_layout.addWidget(self.lock_main_check)
        form = QFormLayout()
        self.pose_controls = {}
        self.pose_sliders = {}
        for key, label, low, high, step, suffix in (
            ("x", "Horizontal", -1e9, 1e9, 1, " px"),
            ("y", "Vertical", -1e9, 1e9, 1, " px"),
            ("rotation", "Rotation", -360000, 360000, 0.1, "°"),
            ("scale", "Scale", 0.0001, 1e8, 1, "%"),
        ):
            control = QDoubleSpinBox()
            control.setRange(low, high)
            control.setDecimals(4 if key == "scale" else 2)
            control.setSingleStep(step)
            control.setSuffix(suffix)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda value, field=key: self._pose_control_changed(field, value))
            self.pose_controls[key] = control
            if key in ("rotation", "scale"):
                form.addRow(label, control)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(-1800, 1800) if key == "rotation" else slider.setRange(10, 4000)
                slider.setSingleStep(1)
                slider.setPageStep(100)
                slider.setAccessibleName(label + " slider")
                slider.setToolTip("−180° to +180°; the number field allows larger rotations."
                                  if key == "rotation" else
                                  "1% to 400%; the number field allows values beyond this range.")
                slider.sliderPressed.connect(lambda: self._start_pose_slider())
                slider.sliderReleased.connect(self._finish_pose_slider)
                slider.valueChanged.connect(lambda value, field=key: self._pose_slider_changed(field, value))
                self.pose_sliders[key] = slider
                form.addRow(slider)
            else:
                form.addRow(label, control)
        inspector_layout.addLayout(form)
        self.reset_button = QPushButton("Reset selected alignment")
        self.reset_button.clicked.connect(self.reset_alignment)
        inspector_layout.addWidget(self.reset_button)
        guides_toggle = QPushButton("Reference overlay and guides ▸")
        guides_toggle.setCheckable(True)
        inspector_layout.addWidget(guides_toggle)
        guides_widget = QWidget()
        guides_layout = QVBoxLayout(guides_widget)
        guides_layout.setContentsMargins(0, 0, 0, 0)
        guides_widget.hide()
        guides_toggle.toggled.connect(guides_widget.setVisible)
        guides_toggle.toggled.connect(lambda checked: guides_toggle.setText(
            "Reference overlay and guides ▾" if checked else "Reference overlay and guides ▸"))
        inspector_layout.addWidget(guides_widget)
        self.overlay_check = QCheckBox("Reference overlay")
        self.overlay_check.setChecked(True)
        self.overlay_check.toggled.connect(self._refresh_selection)
        guides_layout.addWidget(self.overlay_check)
        self.overlay_label = QLabel("Overlay strength · 50%")
        guides_layout.addWidget(self.overlay_label)
        self.overlay_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_slider.setRange(0, 100)
        self.overlay_slider.setValue(50)
        self.overlay_slider.valueChanged.connect(self._overlay_strength_changed)
        self.overlay_slider.setToolTip("Reference overlay for alignment only; it does not change the final composite.")
        guides_layout.addWidget(self.overlay_slider)
        self.guides_check = QCheckBox("Center guides")
        self.guides_check.setChecked(True)
        self.guides_check.toggled.connect(self.photo_view.set_guides)
        guides_layout.addWidget(self.guides_check)
        inspector_root.addWidget(self._build_composite_controls())
        library_layout.insertWidget(2, self.layer_order_controls)
        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        inspector_scroll.setMinimumWidth(360)
        inspector_scroll.setWidget(inspector)
        self.panel_wheel_filter = PanelWheelFilter(inspector_scroll)
        self.stages = QTabWidget()
        self.stages.setMinimumWidth(390)
        for title, panel in (("Develop", self._build_develop_controls()), ("Weave", inspector),
                             ("Export", self.export_panel)):
            if title == "Weave":
                area = inspector_scroll
            else:
                area = QScrollArea()
                area.setWidgetResizable(True)
                area.setFrameShape(QFrame.Shape.NoFrame)
                area.setWidget(panel)
                area.wheel_filter = PanelWheelFilter(area)
            area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            self.stages.addTab(area, title)
        self.stages.currentChanged.connect(self._stage_changed)
        self.view_mode.hide()
        splitter.addWidget(self.stages)
        splitter.setSizes([245, 650, 405])
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(container)
        self.statusBar().showMessage("Ready · import photographs or open a project")
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #202326; color: #ece8e1; }
            QMenuBar, QMenu, QToolBar { background: #292d31; }
            QToolBar { spacing: 8px; padding: 9px; border: none; }
            QToolButton, QPushButton { background: #343a40; border: 1px solid #4a5056;
                border-radius: 5px; padding: 7px 10px; }
            QToolButton:hover, QPushButton:hover { background: #444b52; }
            QPushButton:disabled { color: #777d82; border-color: #343a40; }
            QListWidget { background: #191c1f; border: 1px solid #343a40; border-radius: 6px; }
            QListWidget::item { padding: 7px; border-radius: 4px; }
            QListWidget::item:selected { background: #4a4437; color: #fff1ce; }
            QLabel#brand { font-size: 28px; font-weight: 600; }
            QLabel#muted { color: #aeb3b7; }
            QLabel#placeholder { background: #151719; color: #b6babe; border-radius: 6px; padding: 24px; }
            QStatusBar { color: #b6babe; }
            QSplitter::handle { background: #343a40; width: 1px; }
            QGroupBox#controlGroup { background: #2d3237; border: 1px solid #454c53;
                border-radius: 8px; margin-top: 9px; font-weight: 600; }
            QGroupBox#controlGroup::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QGroupBox#controlGroup QWidget { background: transparent; font-weight: normal; }
            QGroupBox#controlGroup QDoubleSpinBox, QGroupBox#controlGroup QComboBox {
                background: #202529; border: 1px solid #525960; border-radius: 4px; padding: 3px; }
            QGroupBox#controlGroup QPushButton { background: #3a4249; }
            QGroupBox#controlGroup QPushButton:hover { background: #49525b; }
            QCheckBox::indicator { width: 14px; height: 14px; background: #202529;
                border: 1px solid #8d99a2; border-radius: 3px; }
            QCheckBox::indicator:checked { background: #202529; border-color: #8d99a2;
                image: url("__CHECK_ICON__"); }
            QCheckBox::indicator:disabled { background: #363c42; border-color: #5b6269; }
            QSlider::groove:horizontal { height: 5px; background: #181d21; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #86a7ba; border-radius: 2px; }
            QSlider::handle:horizontal { background: #cbd3d9; border: 1px solid #8d99a2;
                width: 12px; margin: -5px 0; border-radius: 3px; }
            QSlider::handle:horizontal:disabled { background: #656e76; }
            QProgressBar { background: #202529; color: #ece8e1; border: 1px solid #525960;
                border-radius: 4px; text-align: center; }
            QProgressBar::chunk { background: #58788b; border-radius: 3px; }
        """.replace("__CHECK_ICON__", (Path(__file__).parent / "assets" / "check.svg").as_posix()))

    def current_frame(self):
        item = self.list.currentItem()
        return self.project.find(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _update_title(self):
        name = self.project_path.stem if self.project_path else "Untitled project"
        self.setWindowTitle(f"{'● ' if self.dirty else ''}{name} — Viewloom {__version__}")
        self.count_label.setText(f"PHOTOGRAPHS · {len(self.project.frames)}")

    def _mark_dirty(self):
        self.structural_dirty = True
        self._sync_dirty()

    def _sync_dirty(self, *_):
        self.dirty = self.structural_dirty or not self.undo_stack.isClean()
        self._update_title()
        if hasattr(self, "composite_timer"):
            self._schedule_composite()

    def _item(self, frame_id):
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == frame_id:
                return item
        return None

    def _update_item(self, frame):
        item = self._item(frame.id)
        if item is None:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, frame.id)
            item.setSizeHint(QSize(200, 118))
            self.list.addItem(item)
        state = self.states.get(frame.id, "Waiting…")
        state += f" · {frame.opacity:.0%}" if frame.enabled else " · Excluded"
        reference = " · Reference" if frame.id == self.project.reference_id else ""
        main = "Main · " if frame.id == self.project.main_frame_id else ""
        item.setText(f"{main}{frame.source.name}{reference}\n{state}")
        item.setToolTip(str(frame.source))

    def _show_message(self, message):
        self.placeholder.setText(message)
        self.stack.setCurrentWidget(self.placeholder)

    def _can_align(self, frame):
        return (frame is not None and frame.sha256 is not None and
                not self.states.get(frame.id, "").startswith(("Missing", "Could not")))

    def _refresh_selection(self, *_):
        frame = self.current_frame()
        self._sync_blend_controls(frame)
        self._refresh_develop_controls(frame)
        busy = frame is not None and (self.generation, frame.id) in self.pending
        self.remove_button.setEnabled(frame is not None)
        self.reference_button.setEnabled(frame is not None and frame.id != self.project.reference_id)
        self.relink_button.setEnabled(frame is not None and not busy)
        editable = self._can_align(frame)
        composite_editable = editable and self.stages.currentIndex() == 1
        if self.composite_view.context is not None and self.composite_view.context != (
                self.generation, frame.id if frame else None, self.stages.currentIndex()):
            self.composite_view.cancel_drag()
        self.composite_view.editable = composite_editable
        self.composite_view._cursor()
        main_locked = frame is not None and frame.id == self.project.main_frame_id and self.project.lock_main_transform
        for key, control in self.pose_controls.items():
            control.blockSignals(True)
            pose = Pose.from_frame(frame) if frame else Pose()
            control.setValue(getattr(pose, key) * (100 if key == "scale" else 1))
            control.blockSignals(False)
            control.setEnabled(editable and not (main_locked and key in {"rotation", "scale"}))
            if key in self.pose_sliders:
                slider = self.pose_sliders[key]
                slider.blockSignals(True)
                slider.setValue(round(getattr(pose, key) * (1000 if key == "scale" else 10)))
                slider.blockSignals(False)
                slider.setEnabled(editable and not main_locked)
        can_reset = ((pose.x != 0 or pose.y != 0) if main_locked else pose != Pose())
        self.reset_button.setEnabled(editable and can_reset)
        if frame is None:
            self.alignment_hint.setText("Select a photograph to align.")
        elif main_locked:
            self.alignment_hint.setText("Main scale and rotation are locked. Position can still be adjusted.")
        elif editable:
            self.alignment_hint.setText("Drag the photograph or use these controls. Position uses canvas pixels.")
        else:
            self.alignment_hint.setText("Load or relink this photograph before aligning it.")
        if not editable:
            self.photo_view.cancel_drag()
            self.photo_view.editable = False
        if frame is None:
            self.preview_title.setText("Your photographs, ready to weave")
            self.details.setText("Select a photograph to inspect it.")
            self._show_message("Import NEF or DNG photographs to begin.")
            if self.view_mode.currentIndex() == 1:
                self._schedule_composite()
            return
        self.preview_title.setText(frame.source.name)
        dimensions = self.dimensions.get(frame.id)
        size = f"{dimensions[0]:,} × {dimensions[1]:,} pixels\n" if dimensions else ""
        reference = "Reference photograph\n" if frame.id == self.project.reference_id else ""
        self.details.setText(f"{reference}{size}{self.states.get(frame.id, 'Waiting…')}")
        self.details.setToolTip(str(frame.source))
        if self.stages.currentIndex() == 0:
            self.preview_note.setText("Develop the selected source photograph · gray picker samples original RAW data.")
            self._schedule_develop()
            return
        if self.view_mode.currentIndex() == 1:
            self._schedule_composite()
            return
        if frame.id in self.cache:
            self.cache.move_to_end(frame.id)
            reference = self.project.find(self.project.reference_id)
            reference_image = None
            if self.overlay_check.isChecked() and reference and reference.id != frame.id:
                reference_image = self.cache.get(reference.id)
                if reference_image is None and self.states.get(reference.id) == "Ready":
                    self._enqueue(reference)
                if reference_image is None:
                    self.alignment_hint.setText("Reference preview unavailable; load or relink it to use the overlay.")
            canvas = self.project.canvas_size or self.dimensions[frame.id]
            self.photo_view.show_alignment(
                self.cache[frame.id], frame, self.dimensions[frame.id], canvas,
                reference_image, reference, self.dimensions.get(reference.id) if reference else None,
                editable=editable, context=(self.generation, frame.id))
            self.stack.setCurrentWidget(self.photo_view)
        elif self.states.get(frame.id) == "Ready":
            self._show_message("Loading preview…")
            self._enqueue(frame)
        else:
            self._show_message(self.states.get(frame.id, "Loading preview…"))

    def _enqueue(self, frame, source=None):
        key = (self.generation, frame.id)
        if key in self.pending:
            return
        if not self.pending:
            self.preview_work_total = 0
            self.preview_work_done = 0
        self.preview_work_total += 1
        self.pending.add(key)
        self.states[frame.id] = "Loading preview…"
        self._update_item(frame)
        job_id = str(uuid4())
        job = PreviewJob(job_id, self.generation, frame, source or frame.source, self.cancellation)
        job.signals.result.connect(self._loaded)
        job.signals.finished.connect(self._job_finished)
        self.jobs[job_id] = job
        self.pool.start(job)
        self._update_status()
        self._refresh_work_progress()

    def _loaded(self, generation, frame_id, source, preview, error):
        if generation != self.generation:
            return
        self.preview_work_done += 1
        self.pending.discard((generation, frame_id))
        frame = self.project.find(frame_id)
        if frame is None:
            self._update_status()
            return
        if preview is not None and preview.recipe != recipe_key(frame):
            self._enqueue(frame)
            return
        if error:
            self.states[frame_id] = "Could not load: " + error
            self.cache.pop(frame_id, None)
        else:
            if frame.source != source or frame.sha256 != preview.sha256:
                frame.source = source
                frame.sha256 = preview.sha256
                self._mark_dirty()
            height, width, _ = preview.rgb.shape
            image = QImage(preview.rgb.data, width, height, preview.rgb.strides[0],
                           QImage.Format.Format_RGB888).copy()
            image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
            self.cache[frame_id] = image
            self.cache.move_to_end(frame_id)
            while len(self.cache) > 12:
                current = self.current_frame()
                protected = {frame_id, self.project.reference_id, current.id if current else None}
                victim = next(key for key in self.cache if key not in protected)
                del self.cache[victim]
            self.dimensions[frame_id] = (preview.width, preview.height)
            if self.project.canvas_size is None:
                self.project.canvas_size = (preview.width, preview.height)
                self._mark_dirty()
            self.states[frame_id] = "Ready"
            item = self._item(frame_id)
            item.setIcon(QIcon(QPixmap.fromImage(image).scaled(
                144, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        self._update_item(frame)
        self._update_status()
        current = self.current_frame()
        if current and (current.id == frame_id or self.project.reference_id == frame_id):
            self._refresh_selection()

    def _job_finished(self, job_id):
        self.jobs.pop(job_id, None)

    def _update_status(self):
        loading = sum(generation == self.generation for generation, _ in self.pending)
        errors = sum(state.startswith(("Missing", "Could not")) for state in self.states.values())
        text = f"{len(self.project.frames)} photographs"
        if loading:
            text += f" · {loading} loading in background"
        if errors:
            text += f" · {errors} need attention (select to inspect or relink)"
        if not loading and not errors:
            text += " · Ready"
        self.statusBar().showMessage(text)

    def import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import photographs", "", RAW_FILTER)
        self.import_paths([Path(path) for path in paths])

    def import_paths(self, paths):
        known = {frame.source for frame in self.project.frames}
        first = None
        for path in paths:
            path = path.resolve()
            if path in known:
                continue
            known.add(path)
            frame = Frame(source=path)
            self.project.frames.append(frame)
            if self.project.reference_id is None:
                self.project.reference_id = frame.id
            self._update_item(frame)
            self._enqueue(frame)
            first = first or frame.id
        if first:
            self._mark_dirty()
            self.list.setCurrentItem(self._item(first))
        self._update_status()

    def set_reference(self):
        frame = self.current_frame()
        if frame and frame.id != self.project.reference_id:
            self.undo_stack.push(ReferenceCommand(self, self.project.reference_id, frame.id))

    def _apply_reference(self, frame_id):
        self.project.reference_id = frame_id
        for photograph in self.project.frames:
            self._update_item(photograph)
        self._refresh_selection()

    def _apply_pose(self, frame_id, pose):
        frame = self.project.find(frame_id)
        if frame:
            pose.apply(frame)
            current = self.current_frame()
            if current and (current.id == frame_id or self.project.reference_id == frame_id):
                self._refresh_selection()

    def change_pose(self, pose, label="Align photograph", merge_key=None):
        frame = self.current_frame()
        if not self._can_align(frame):
            return
        before = Pose.from_frame(frame)
        if frame.id == self.project.main_frame_id and self.project.lock_main_transform:
            pose = Pose(pose.x, pose.y, before.rotation, before.scale)
        if pose != before:
            self.undo_stack.push(PoseCommand(self, frame.id, before, pose, label, merge_key))

    def _start_pose_slider(self):
        self.slider_gesture = str(uuid4())

    def _finish_pose_slider(self):
        self.slider_gesture = None

    def _pose_slider_changed(self, field, value):
        from dataclasses import replace
        frame = self.current_frame()
        if frame is None:
            return
        amount = value / (1000 if field == "scale" else 10)
        self.change_pose(replace(Pose.from_frame(frame), **{field: amount}),
                         "Adjust " + field, self.slider_gesture)

    def _pose_control_changed(self, field, value):
        frame = self.current_frame()
        if frame is None:
            return
        from dataclasses import replace
        pose = Pose.from_frame(frame)
        value = value / 100 if field == "scale" else value
        self.change_pose(replace(pose, **{field: value}), "Adjust " + field)

    def translate_selected(self, dx, dy):
        frame = self.current_frame()
        if frame:
            pose = Pose.from_frame(frame)
            self.change_pose(Pose(pose.x + dx, pose.y + dy, pose.rotation, pose.scale), "Move photograph")

    def reset_alignment(self):
        self.change_pose(Pose(), "Reset alignment")

    def _overlay_strength_changed(self, value):
        self.overlay_label.setText(f"Overlay strength · {value}%")
        self.photo_view.set_overlay_strength(value)

    def remove_selected(self):
        frame = self.current_frame()
        if frame is None:
            return
        self.project.frames.remove(frame)
        self.cache.pop(frame.id, None)
        self.states.pop(frame.id, None)
        self.dimensions.pop(frame.id, None)
        if self.project.reference_id == frame.id:
            self.project.reference_id = self.project.frames[0].id if self.project.frames else None
        self.list.takeItem(self.list.currentRow())
        for photograph in self.project.frames:
            self._update_item(photograph)
        self._mark_dirty()
        self.undo_stack.clear()
        self._refresh_selection()
        self._update_status()

    def relink_dialog(self):
        frame = self.current_frame()
        if frame is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Locate the unchanged original", "", RAW_FILTER)
        if path:
            self.relink(frame.id, Path(path))

    def relink(self, frame_id, path):
        frame = self.project.find(frame_id)
        if frame:
            self._enqueue(frame, path.resolve())
            self._refresh_selection()

    def _can_discard(self):
        if not self.dirty:
            return True
        answer = QMessageBox.warning(
            self, "Save your project?", "This project has unsaved changes.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
            QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _replace_project(self, project, path):
        self._cancel_compositing()
        self._cancel_development()
        self.cancellation.set()
        self.cancellation = Event()
        self.generation = str(uuid4())
        self.pending.clear()
        self.project = project
        self.project_path = path
        self.structural_dirty = False
        self.undo_stack.clear()
        self.dirty = False
        self.cache.clear()
        self.states.clear()
        self.dimensions.clear()
        self.list.clear()
        self.photo_view.clear()
        self.composite_view.clear()
        self.develop_view.clear()
        self._develop_display_id = None
        for frame in project.frames:
            if frame.source.is_file():
                self._update_item(frame)
                self._enqueue(frame)
            else:
                self.states[frame.id] = "Missing original — use Relink original…"
                self._update_item(frame)
        if project.frames:
            self.list.setCurrentRow(0)
        self._update_title()
        self._refresh_selection()
        self._update_status()

    def new_project(self):
        if self._can_discard():
            self._replace_project(Project(), None)

    def open_project(self):
        if not self._can_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open Viewloom project", "", PROJECT_FILTER)
        if path:
            self.open_path(Path(path))

    def open_path(self, path):
        try:
            project = load_project(path.resolve())
        except (OSError, ValueError, TypeError, KeyError) as error:
            QMessageBox.critical(self, "Could not open project", str(error))
            return False
        self._replace_project(project, path.resolve())
        return True

    def save(self):
        if self.project_path is None:
            return self.save_as()
        return self.save_path(self.project_path)

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Viewloom project", str(self.project_path or "Untitled.viewloom"), PROJECT_FILTER)
        if not path:
            return False
        destination = Path(path)
        if destination.suffix.lower() != ".viewloom":
            destination = destination.with_suffix(destination.suffix + ".viewloom")
        return self.save_path(destination)

    def save_path(self, destination):
        if any(frame.sha256 is None for frame in self.project.frames):
            QMessageBox.information(self, "Photographs are not ready",
                                    "Wait for imports to finish before saving. Remove any unreadable "
                                    "photographs, or relink them to valid originals.")
            return False
        try:
            save_project(self.project, destination)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Could not save project", str(error))
            return False
        self.project_path = destination.resolve()
        self.structural_dirty = False
        self.undo_stack.setClean()
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {destination.name} · originals remain untouched")
        return True

    def closeEvent(self, event):
        if self._can_discard():
            self.cancellation.set()
            self._cancel_compositing()
            self._cancel_development()
            self.work_timer.stop()
            event.accept()
        else:
            event.ignore()


def main():
    try:
        prepare_qt_plugins()
    except OSError as error:
        print(f"Viewloom could not prepare its graphics plugins: {error}", file=sys.stderr)
        return 1
    app = QApplication(sys.argv)
    app.setApplicationName("Viewloom")
    app.setOrganizationName("Viewloom")
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        window.open_path(Path(sys.argv[1]))
    return app.exec()
