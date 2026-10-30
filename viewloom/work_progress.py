"""A shared bottom-right activity indicator for background image processing."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QProgressBar


class WorkProgress:
    def _init_work_progress(self):
        self.preview_work_total = 0
        self.preview_work_done = 0
        self.work_label = QLabel()
        self.work_progress = QProgressBar()
        self.work_progress.setFixedWidth(140)
        self.work_progress.setTextVisible(True)
        self.statusBar().addPermanentWidget(self.work_label)
        self.statusBar().addPermanentWidget(self.work_progress)
        self.work_label.hide()
        self.work_progress.hide()
        self.work_timer = QTimer(self)
        self.work_timer.setInterval(80)
        self.work_timer.timeout.connect(self._refresh_work_progress)
        self.work_timer.start()

    def _refresh_work_progress(self):
        if self.export_busy:
            self.work_label.hide()
            self.work_progress.hide()
            return
        loading = sum(generation == self.generation for generation, _ in self.pending)
        developing = ((self.develop_job is not None and self.develop_job.generation == self.generation) or
                      self.develop_timer.isActive())
        rendering = ((self.render_job is not None and self.render_job.generation == self.generation) or
                     self.composite_timer.isActive())
        if not (loading or developing or rendering):
            self.work_label.hide()
            self.work_progress.hide()
            return
        self.work_label.show()
        self.work_progress.show()
        if loading:
            self.work_label.setText("Updating previews")
            self.work_progress.setRange(0, max(1, self.preview_work_total))
            self.work_progress.setValue(self.preview_work_done)
            self.work_progress.setFormat("%v / %m")
        else:
            self.work_label.setText("Sampling gray" if developing and self.develop_job and self.develop_job.gray
                                    else "Developing preview" if developing else "Rendering composite")
            self.work_progress.setRange(0, 0)
        tasks = []
        if loading:
            tasks.append(f"{loading} photograph previews remaining")
        if developing:
            tasks.append("Developing source preview")
        if rendering:
            tasks.append("Rendering composite")
        self.work_progress.setToolTip(" · ".join(tasks))
