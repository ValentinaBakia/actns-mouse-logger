from dataclasses import dataclass
import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from movements import DirectedMove


# Canvas layer for interaction and visual guidance.
# This widget does not build session objects directly. Instead, it emits
# signals describing trial lifecycle events, and the window/recorder handles
# logging from those events.

@dataclass(frozen=True)
class DrawingConfig:
    left: float = 120.0
    top: float = 90.0
    width: float = 420.0
    height: float = 280.0
    anchor_radius: float = 7.0

    @property
    def rect(self) -> QRectF:
        return QRectF(self.left, self.top, self.width, self.height)

    @property
    def corners(self) -> tuple[QPointF, QPointF, QPointF, QPointF]:
        rect = self.rect
        return (
            rect.topLeft(),
            rect.topRight(),
            rect.bottomLeft(),
            rect.bottomRight(),
        )


class DrawingCanvas(QWidget):
    state_changed = Signal(str)
    # Event-based logging signals:
    # - `trial_started`: a valid trial begins once the cursor enters the
    #   correct start anchor.
    # - `sample_recorded`: a mouse sample captured during the active portion.
    # - `trial_finished`: a successful trial ended at the correct destination.
    # - `trial_cancelled`: active trial was abandoned or invalidated.
    trial_started = Signal(object, float)
    sample_recorded = Signal(float, float, float)
    trial_cancelled = Signal()
    trial_finished = Signal(float)
    WAITING = "waiting"
    ACTIVE = "active"
    FINISHED = "finished"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"
    DESTINATION_ARROW_LENGTH = 28.0
    DESTINATION_ARROW_WIDTH = 12.0

    def __init__(self, config: DrawingConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config or DrawingConfig()
        self._current_move: DirectedMove | None = None
        self._trial_state = self.WAITING
        self.setMinimumSize(700, 500)
        self.setAutoFillBackground(True)
        self.setMouseTracking(True)

    def set_current_move(self, move: DirectedMove | None) -> None:
        # Reset the per-trial interaction state whenever a new move is shown.
        self.cancel_active_trial()
        self._current_move = move
        self._trial_state = self.WAITING
        self.state_changed.emit(self._trial_state)
        self.update()

    @property
    def trial_state(self) -> str:
        return self._trial_state

    def cancel_active_trial(self) -> None:
        if self._trial_state == self.ACTIVE:
            self.trial_cancelled.emit()
            self._end_active_trial(self.INCOMPLETE)

    def _anchor_points(self) -> dict[str, QPointF]:
        top_left, top_right, bottom_left, bottom_right = self._config.corners
        return {
            "TL": top_left,
            "TR": top_right,
            "BL": bottom_left,
            "BR": bottom_right,
        }

    def _set_trial_state(self, state: str) -> None:
        if self._trial_state == state:
            return
        self._trial_state = state
        self.state_changed.emit(state)
        self.update()

    def _end_active_trial(self, end_state: str) -> None:
        self._set_trial_state(end_state)
        if end_state == self.FINISHED:
            self.trial_finished.emit(time.time())

    def _anchor_hit(self, position: QPointF) -> str | None:
        for anchor_name, point in self._anchor_points().items():
            if self._distance_squared(position, point) <= self._config.anchor_radius ** 2:
                return anchor_name
        return None

    @staticmethod
    def _distance_squared(first: QPointF, second: QPointF) -> float:
        delta_x = first.x() - second.x()
        delta_y = first.y() - second.y()
        return delta_x * delta_x + delta_y * delta_y

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._current_move is None:
            super().mouseMoveEvent(event)
            return

        position = event.position()
        hit_anchor = self._anchor_hit(position)

        if self._trial_state == self.WAITING:
            if hit_anchor == self._current_move.start_anchor:
                timestamp = time.time()
                # Logging begins here: no samples are emitted before the user
                # actually enters the correct start anchor.
                self.trial_started.emit(self._current_move, timestamp)
                self.sample_recorded.emit(timestamp, position.x(), position.y())
                self._set_trial_state(self.ACTIVE)
            elif hit_anchor is not None:
                self._set_trial_state(self.INVALID)
            super().mouseMoveEvent(event)
            return

        if self._trial_state != self.ACTIVE:
            super().mouseMoveEvent(event)
            return

        timestamp = time.time()
        # During ACTIVE, every mouse move produces one logged sample.
        self.sample_recorded.emit(timestamp, position.x(), position.y())
        if hit_anchor == self._current_move.end_anchor:
            self._end_active_trial(self.FINISHED)
        elif hit_anchor is not None and hit_anchor != self._current_move.start_anchor:
            self.trial_cancelled.emit()
            self._end_active_trial(self.INVALID)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        if self._trial_state == self.ACTIVE:
            self.trial_cancelled.emit()
            self._end_active_trial(self.INCOMPLETE)

    def _draw_direction_arrow(self, painter: QPainter, start_point: QPointF, end_point: QPointF) -> None:
        arrow_color = QColor("#16a34a") if self._trial_state == self.FINISHED else QColor("#dc2626")
        dx = end_point.x() - start_point.x()
        dy = end_point.y() - start_point.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            return

        unit_x = dx / length
        unit_y = dy / length
        arrow_length = self.DESTINATION_ARROW_LENGTH
        arrow_width = self.DESTINATION_ARROW_WIDTH
        base = QPointF(end_point.x() - unit_x * arrow_length, end_point.y() - unit_y * arrow_length)
        perpendicular = QPointF(-unit_y, unit_x)
        left = QPointF(
            base.x() + perpendicular.x() * arrow_width,
            base.y() + perpendicular.y() * arrow_width,
        )
        right = QPointF(
            base.x() - perpendicular.x() * arrow_width,
            base.y() - perpendicular.y() * arrow_width,
        )
        painter.setBrush(arrow_color)
        painter.drawPolygon(QPolygonF([end_point, left, right]))

    def _draw_reference_geometry(
        self,
        painter: QPainter,
        top_left: QPointF,
        top_right: QPointF,
        bottom_left: QPointF,
        bottom_right: QPointF,
    ) -> None:
        subtle_pen = QPen(QColor("#cbd5e1"), 2)
        subtle_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        subtle_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(subtle_pen)
        painter.drawLine(top_left, top_right)
        painter.drawLine(top_right, bottom_right)
        painter.drawLine(bottom_right, bottom_left)
        painter.drawLine(bottom_left, top_left)
        painter.drawLine(top_left, bottom_right)
        painter.drawLine(top_right, bottom_left)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f7f5ef"))

        config = self._config
        top_left, top_right, bottom_left, bottom_right = config.corners
        anchor_points = self._anchor_points()

        self._draw_reference_geometry(painter, top_left, top_right, bottom_left, bottom_right)

        if self._current_move is not None:
            start_point = anchor_points[self._current_move.start_anchor]
            end_point = anchor_points[self._current_move.end_anchor]
            path_color = QColor("#334155")
            path_width = 4
            if self._trial_state == self.ACTIVE:
                path_width = 5
            elif self._trial_state == self.FINISHED:
                path_color = QColor("#16a34a")
                path_width = 5

            active_pen = QPen(path_color, path_width)
            active_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(active_pen)
            painter.drawLine(start_point, end_point)
            self._draw_direction_arrow(painter, start_point, end_point)

        painter.setPen(Qt.PenStyle.NoPen)
        radius = config.anchor_radius + 2.0
        diameter = radius * 2
        for anchor_name, point in anchor_points.items():
            if self._trial_state == self.FINISHED and self._current_move is not None:
                if anchor_name == self._current_move.end_anchor:
                    continue
                painter.setBrush(QColor("#16a34a"))
            elif self._current_move is not None and anchor_name == self._current_move.start_anchor:
                painter.setBrush(QColor("#16a34a"))
            elif self._current_move is not None and anchor_name == self._current_move.end_anchor:
                continue
            else:
                painter.setBrush(QColor("#0f766e"))
            painter.drawEllipse(point.x() - radius, point.y() - radius, diameter, diameter)
