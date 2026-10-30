"""Keep panel scrolling from changing image-editing controls."""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QComboBox, QSlider, QWidget
from PySide6.QtCore import Qt


class PanelWheelFilter(QObject):
    def __init__(self, scroll_area):
        super().__init__(scroll_area)
        self.scroll_area = scroll_area
        for control in scroll_area.widget().findChildren(QWidget):
            if isinstance(control, (QAbstractSpinBox, QComboBox, QSlider)):
                control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                control.installEventFilter(self)
                if isinstance(control, QAbstractSpinBox):
                    control.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        if isinstance(watched, QComboBox) and watched.view().isVisible():
            return False
        viewport = self.scroll_area.viewport()
        local = viewport.mapFromGlobal(event.globalPosition().toPoint())
        forwarded = QWheelEvent(local, event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                                event.source(), event.pointingDevice())
        QApplication.sendEvent(viewport, forwarded)
        event.accept()
        return True
