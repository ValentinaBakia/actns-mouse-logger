from dataclasses import dataclass
import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
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
    width: float = 560.0
    height: float = 360.0
    target_size: float = 34.0
    target_hit_size: float = 68.0

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

    def __init__(self, config: DrawingConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config or DrawingConfig()
        self._current_move: DirectedMove | None = None
        self._trial_state = self.WAITING
        self.setMinimumSize(980, 860)
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
        hit_half_size = self._config.target_hit_size / 2
        for anchor_name, point in self._anchor_points().items():
            hit_rect = QRectF(
                point.x() - hit_half_size,
                point.y() - hit_half_size,
                self._config.target_hit_size,
                self._config.target_hit_size,
            )
            if hit_rect.contains(position):
                return anchor_name
        return None

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
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        if self._trial_state == self.ACTIVE:
            self.trial_cancelled.emit()
            self._end_active_trial(self.INCOMPLETE)

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

    def _draw_target(
        self,
        painter: QPainter,
        center: QPointF,
        fill_color: QColor,
        border_color: QColor,
    ) -> None:
        half_size = self._config.target_size / 2
        target_rect = QRectF(
            center.x() - half_size,
            center.y() - half_size,
            self._config.target_size,
            self._config.target_size,
        )
        painter.setPen(QPen(border_color, 3))
        painter.setBrush(fill_color)
        painter.drawRect(target_rect)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f7f5ef"))

        top_left, top_right, bottom_left, bottom_right = self._config.corners
        anchor_points = self._anchor_points()
        self._draw_reference_geometry(painter, top_left, top_right, bottom_left, bottom_right)

        if self._current_move is not None:
            start_point = anchor_points[self._current_move.start_anchor]
            end_point = anchor_points[self._current_move.end_anchor]
            line_color = QColor("#475569")
            if self._trial_state == self.FINISHED:
                line_color = QColor("#16a34a")
            guide_pen = QPen(line_color, 4)
            guide_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(guide_pen)
            painter.drawLine(start_point, end_point)

            start_fill = QColor("#bbf7d0")
            start_border = QColor("#15803d")
            end_fill = QColor("#fecaca")
            end_border = QColor("#b91c1c")
            if self._trial_state == self.FINISHED:
                end_fill = QColor("#bbf7d0")
                end_border = QColor("#15803d")

            self._draw_target(painter, start_point, start_fill, start_border)
            self._draw_target(painter, end_point, end_fill, end_border)
