"""Interactive alignment canvas. Dragging commits one undoable edit on release."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QFrame, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from .alignment import Pose, affine_coefficients


class PhotoView(QGraphicsView):
    translated = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.canvas = QGraphicsRectItem()
        self.canvas.setPen(QPen(Qt.PenStyle.NoPen))
        self.canvas.setBrush(QColor("#111315"))
        self.canvas.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        self.scene().addItem(self.canvas)
        self.photo = QGraphicsPixmapItem(self.canvas)
        self.reference = QGraphicsPixmapItem(self.canvas)
        self.reference.setZValue(1)
        self.reference.setOpacity(0.5)
        self.photo.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.reference.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor("#111315"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.fit_mode = True
        self.editable = False
        self.guides = True
        self.drag_start = None
        self.space_down = False
        self.context = None

    def show_alignment(self, image, frame, source_size, canvas_size, reference_image=None,
                       reference_frame=None, reference_size=None, editable=False, context=None):
        changed = self.context != context or self.canvas.rect().size() != QRectF(0, 0, *canvas_size).size()
        self.cancel_drag()
        self.context = context
        self.editable = editable
        self.canvas.setRect(0, 0, *canvas_size)
        self.scene().setSceneRect(self.canvas.rect())
        self._place(self.photo, image, frame, source_size, canvas_size)
        if reference_image is not None and reference_frame is not None:
            self._place(self.reference, reference_image, reference_frame, reference_size, canvas_size)
            self.reference.show()
        else:
            self.reference.hide()
        if changed:
            self.fit_image()
        self._cursor()
        self.viewport().update()

    @staticmethod
    def _place(item, image, frame, source_size, canvas_size):
        item.setPixmap(QPixmap.fromImage(image))
        a, b, c, d, tx, ty = affine_coefficients(source_size, canvas_size, Pose.from_frame(frame))
        sx, sy = source_size[0] / image.width(), source_size[1] / image.height()
        item.setTransform(QTransform(a * sx, b * sx, c * sy, d * sy, tx, ty))
        item.setPos(0, 0)

    def clear(self):
        self.cancel_drag()
        self.photo.setPixmap(QPixmap())
        self.reference.setPixmap(QPixmap())
        self.editable = False
        self.context = None

    def set_overlay_strength(self, value):
        self.reference.setOpacity(value / 100)

    def set_guides(self, enabled):
        self.guides = enabled
        self.viewport().update()

    def fit_image(self):
        self.fit_mode = True
        if not self.canvas.rect().isEmpty():
            self.fitInView(self.canvas.rect(), Qt.AspectRatioMode.KeepAspectRatio)

    def actual_size(self):
        self.fit_mode = False
        # Show the selected reduced preview at its pixel density on the canvas.
        factor = self.photo.transform().m11() ** 2 + self.photo.transform().m12() ** 2
        self.resetTransform()
        if factor > 0:
            self.scale(1 / factor ** 0.5, 1 / factor ** 0.5)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit_image()

    def _cursor(self):
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if self.space_down
                         else QGraphicsView.DragMode.NoDrag)
        if not self.space_down:
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor if self.editable
                                      else Qt.CursorShape.ArrowCursor)

    def cancel_drag(self):
        self.drag_start = None
        self.photo.setPos(0, 0)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.editable and not self.space_down:
            point = self.mapToScene(event.position().toPoint())
            if self.canvas.rect().contains(point):
                self.drag_start = point
                self.setFocus()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_start is not None:
            delta = self.mapToScene(event.position().toPoint()) - self.drag_start
            self.photo.setPos(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_start is not None and event.button() == Qt.MouseButton.LeftButton:
            delta = self.mapToScene(event.position().toPoint()) - self.drag_start
            self.cancel_drag()
            if abs(delta.x()) + abs(delta.y()) > 0.001:
                self.translated.emit(delta.x(), delta.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.drag_start is not None:
            self.cancel_drag()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self.space_down = True
            self._cursor()
            event.accept()
            return
        steps = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                 Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if self.editable and event.key() in steps and self.drag_start is None:
            dx, dy = steps[event.key()]
            amount = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self.translated.emit(dx * amount, dy * amount)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space_down = False
            self._cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.space_down = False
        self.cancel_drag()
        self._cursor()
        super().focusOutEvent(event)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if not self.guides or self.photo.pixmap().isNull():
            return
        bounds = self.canvas.rect()
        painter.save()
        painter.setClipRect(bounds)
        pen = QPen(QColor(255, 224, 156, 155), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        center = bounds.center()
        painter.drawLine(QPointF(center.x(), 0), QPointF(center.x(), bounds.height()))
        painter.drawLine(QPointF(0, center.y()), QPointF(bounds.width(), center.y()))
        painter.restore()


class CompositeView(PhotoView):
    """Translate a selected layer through a flattened composite preview."""
    drag_preview = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.full_canvas = (1, 1)
        self.set_guides(False)

    def show_composite(self, image, canvas_size, editable, context):
        changed = self.context != context or self.canvas.rect().width() != image.width() or self.canvas.rect().height() != image.height()
        if self.context != context or not editable:
            self.cancel_drag()
        self.context = context
        self.full_canvas = canvas_size
        self.editable = editable
        self.canvas.setRect(0, 0, image.width(), image.height())
        self.scene().setSceneRect(self.canvas.rect())
        self.photo.setPixmap(QPixmap.fromImage(image))
        self.photo.setTransform(QTransform())
        self.photo.setPos(0, 0)
        self.reference.hide()
        if changed:
            self.fit_image()
        self._cursor()

    def _canvas_delta(self, point):
        delta = point - self.drag_start
        bounds = self.canvas.rect()
        return (delta.x() * self.full_canvas[0] / max(1, bounds.width()),
                delta.y() * self.full_canvas[1] / max(1, bounds.height()))

    def mouseMoveEvent(self, event):
        if self.drag_start is not None:
            self.drag_preview.emit(*self._canvas_delta(self.mapToScene(event.position().toPoint())))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_start is not None and event.button() == Qt.MouseButton.LeftButton:
            dx, dy = self._canvas_delta(self.mapToScene(event.position().toPoint()))
            self.cancel_drag()
            if abs(dx) + abs(dy) > 0.001:
                self.translated.emit(dx, dy)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def cancel_drag(self):
        active = self.drag_start is not None
        super().cancel_drag()
        if active:
            self.drag_preview.emit(0, 0)
