from dataclasses import dataclass
import math
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
    left: float = 0.0
    top: float = 0.0
    width: float = 1200.0
    height: float = 760.0
    # These values make the corner targets scale with screen/canvas size
    # while staying within a visually stable min/max range.
    target_size_ratio: float = 0.055
    target_min_size: float = 42.0
    target_max_size: float = 70.0
    # The hitbox stays larger than the visible square so users can move
    # naturally without needing pixel-perfect precision.
    target_hit_scale: float = 1.8

    @property
    def rect(self) -> QRectF:
        return QRectF(self.left, self.top, self.width, self.height)

    def target_size_for_rect(self, rect: QRectF | None = None) -> float:
        base_rect = rect or self.rect
        # Use screen-relative sizing, but clamp it so targets look similar
        # across smaller and larger displays.
        raw_size = min(base_rect.width(), base_rect.height()) * self.target_size_ratio
        return max(self.target_min_size, min(raw_size, self.target_max_size))

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
        self.setMinimumSize(420, 320)
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

    def _active_rect(self) -> QRectF:
        # The active drawing region is derived from the real widget size so
        # target placement stays correct after resizing/fullscreen changes.
        inset_x = self._config.left
        inset_y = self._config.top
        return QRectF(
            inset_x,
            inset_y,
            max(0.0, self.width() - (inset_x * 2)),
            max(0.0, self.height() - (inset_y * 2)),
        )

    def _anchor_points(self) -> dict[str, QPointF]:
        rect = self._active_rect()
        target_size = self._config.target_size_for_rect(rect)
        # Anchor points represent the center of each visible corner square.
        top_left, top_right, bottom_left, bottom_right = (
            QPointF(rect.left() + (target_size / 2), rect.top() + (target_size / 2)),
            QPointF(rect.right() - (target_size / 2), rect.top() + (target_size / 2)),
            QPointF(rect.left() + (target_size / 2), rect.bottom() - (target_size / 2)),
            QPointF(rect.right() - (target_size / 2), rect.bottom() - (target_size / 2)),
        )
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

    def _target_rect(
        self,
        anchor_name: str,
        size: float,
        rect: QRectF | None = None,
    ) -> QRectF:
        # Draw the actual target as a plain square snapped to one canvas corner.
        rect = rect or self._active_rect()
        if anchor_name == "TL":
            return QRectF(rect.left(), rect.top(), size, size)
        if anchor_name == "TR":
            return QRectF(rect.right() - size, rect.top(), size, size)
        if anchor_name == "BL":
            return QRectF(rect.left(), rect.bottom() - size, size, size)
        return QRectF(rect.right() - size, rect.bottom() - size, size, size)

    def _activation_zone_rect(self, anchor_name: str, rect: QRectF | None = None) -> QRectF:
        rect = rect or self._active_rect()
        visible_size = self._config.target_size_for_rect(rect)
        # The invisible activation zone is intentionally larger than the drawn
        # square so the experiment feels guided, but not overly precise.
        hit_size = max(visible_size * self._config.target_hit_scale, visible_size + 22.0)
        return self._target_rect(anchor_name, hit_size, rect)

    def _corner_hit(self, position: QPointF) -> str | None:
        active_rect = self._active_rect()
        for anchor_name, point in self._anchor_points().items():
            del point
            target_rect = self._activation_zone_rect(anchor_name, active_rect)
            if target_rect.contains(position):
                return anchor_name
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._current_move is None:
            super().mouseMoveEvent(event)
            return

        position = event.position()
        hit_anchor = self._corner_hit(position)

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

    def _draw_target(
        self,
        painter: QPainter,
        anchor_name: str,
        color: QColor,
    ) -> None:
        active_rect = self._active_rect()
        # Visible targets are intentionally simple: solid sharp-edged squares.
        target_rect = self._target_rect(
            anchor_name,
            self._config.target_size_for_rect(active_rect),
            active_rect,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRect(target_rect)

    def _draw_reference_diagonals(self, painter: QPainter) -> None:
        # Keep the old diagonal guidance, but in a very light style so it does
        # not compete with the start/end targets.
        anchor_points = self._anchor_points()
        trail_pen = QPen(QColor(148, 163, 184, 90), 1.2)
        painter.setPen(trail_pen)
        painter.drawLine(anchor_points["TL"], anchor_points["BR"])
        painter.drawLine(anchor_points["TR"], anchor_points["BL"])

    def _label_rect(self, anchor_name: str) -> tuple[QRectF, Qt.AlignmentFlag]:
        rect = self._active_rect()
        target_size = self._config.target_size_for_rect(rect)
        # Labels are positioned relative to target size so they stay readable
        # without drifting too far away on large or small screens.
        width = max(84.0, target_size * 1.7)
        height = 22.0
        inset_x = target_size + 14.0
        inset_y = 12.0

        if anchor_name == "TL":
            return QRectF(rect.left() + inset_x, rect.top() + inset_y, width, height), Qt.AlignmentFlag.AlignLeft
        if anchor_name == "TR":
            return QRectF(rect.right() - width - inset_x, rect.top() + inset_y, width, height), Qt.AlignmentFlag.AlignRight
        if anchor_name == "BL":
            return QRectF(rect.left() + inset_x, rect.bottom() - height - inset_y, width, height), Qt.AlignmentFlag.AlignLeft
        return QRectF(rect.right() - width - inset_x, rect.bottom() - height - inset_y, width, height), Qt.AlignmentFlag.AlignRight

    def _draw_direction_arrow(self, painter: QPainter) -> None:
        if self._current_move is None:
            return

        # This small arrow gives first-time users a quick direction cue without
        # drawing the full intended path on the screen.
        anchor_points = self._anchor_points()
        start_point = anchor_points[self._current_move.start_anchor]
        end_point = anchor_points[self._current_move.end_anchor]

        dx = end_point.x() - start_point.x()
        dy = end_point.y() - start_point.y()
        distance = math.hypot(dx, dy)
        if distance == 0:
            return

        ux = dx / distance
        uy = dy / distance

        inward_offsets = {
            "TL": QPointF(1.0, 1.0),
            "TR": QPointF(-1.0, 1.0),
            "BL": QPointF(1.0, -1.0),
            "BR": QPointF(-1.0, -1.0),
        }
        inward = inward_offsets[self._current_move.start_anchor]
        target_size = self._config.target_size_for_rect(self._active_rect())
        inward_scale = target_size * 0.35
        start_offset = target_size * 0.65
        arrow_length = min(96.0, distance * 0.16)

        arrow_start = QPointF(
            start_point.x() + (inward.x() * inward_scale) + (ux * start_offset),
            start_point.y() + (inward.y() * inward_scale) + (uy * start_offset),
        )
        arrow_end = QPointF(
            arrow_start.x() + (ux * arrow_length),
            arrow_start.y() + (uy * arrow_length),
        )

        arrow_pen = QPen(QColor(15, 23, 42, 155), 2.0)
        arrow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arrow_pen)
        painter.drawLine(arrow_start, arrow_end)

        head_length = 13.0
        head_width = 8.0
        base_x = arrow_end.x() - (ux * head_length)
        base_y = arrow_end.y() - (uy * head_length)
        perp_x = -uy
        perp_y = ux
        left_point = QPointF(base_x + (perp_x * head_width), base_y + (perp_y * head_width))
        right_point = QPointF(base_x - (perp_x * head_width), base_y - (perp_y * head_width))
        painter.drawLine(arrow_end, left_point)
        painter.drawLine(arrow_end, right_point)

    def _draw_target_labels(self, painter: QPainter, start_color: QColor, end_color: QColor) -> None:
        if self._current_move is None:
            return

        # Explicit START/END labels make the task easier to explain in studies
        # with many participants.
        painter.setPen(start_color)
        start_rect, start_alignment = self._label_rect(self._current_move.start_anchor)
        painter.drawText(start_rect, start_alignment | Qt.AlignmentFlag.AlignVCenter, "START")

        painter.setPen(end_color)
        end_rect, end_alignment = self._label_rect(self._current_move.end_anchor)
        painter.drawText(end_rect, end_alignment | Qt.AlignmentFlag.AlignVCenter, "END")

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f7f5ef"))
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)

        anchor_points = self._anchor_points()
        self._draw_reference_diagonals(painter)

        # Keep inactive corners neutral and only emphasize the current start
        # and destination targets. After success, the destination flashes green.
        neutral_color = QColor("#141414")
        start_color = QColor("#22c55e")
        end_color = QColor("#ef4444")
        success_color = QColor("#22c55e")

        if self._current_move is not None:
            for anchor_name in anchor_points:
                color = neutral_color
                if anchor_name == self._current_move.start_anchor:
                    color = start_color
                elif anchor_name == self._current_move.end_anchor:
                    color = end_color
                if self._trial_state == self.FINISHED and anchor_name == self._current_move.end_anchor:
                    color = success_color
                self._draw_target(painter, anchor_name, color)

        label_end_color = success_color if self._trial_state == self.FINISHED else end_color
        self._draw_direction_arrow(painter)
        self._draw_target_labels(painter, start_color, label_end_color)
