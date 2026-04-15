import random
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from canvas import DrawingCanvas, DrawingConfig
from movements import DIRECTED_MOVES, DirectedMove
from recorder import SessionRecorder


# Main application window.
# This file owns the high-level app flow:
# 1. collect subject name
# 2. start a session
# 3. show one move at a time
# 4. react to canvas events
# 5. forward logging events into the recorder
class MainWindow(QMainWindow):
    COMPLETION_FLASH_MS = 700
    RETRY_DELAY_MS = 400

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mouse Logger")
        self.resize(900, 650)
        self._current_move: DirectedMove | None = None
        self._session_started = False
        self._recorder = SessionRecorder()
        self._next_move_timer = QTimer(self)
        self._next_move_timer.setSingleShot(True)
        self._next_move_timer.timeout.connect(self._advance_session)

        self._canvas = DrawingCanvas(
            DrawingConfig(
                left=180.0,
                top=120.0,
                width=500.0,
                height=320.0,
                anchor_radius=8.0,
            )
        )
        self._move_label = QLabel()
        self._move_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._move_label.setStyleSheet("font-size: 16px; font-weight: 600; color: #0f172a;")
        self._state_label = QLabel()
        self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._recording_label = QLabel()
        self._recording_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._session_label = QLabel()
        self._session_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._subject_label = QLabel("Subject name")
        self._subject_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subject_input = QLineEdit()
        self._subject_input.setPlaceholderText("Enter subject name")
        self._subject_input.returnPressed.connect(self._start_session)
        self._start_button = QPushButton("Start Session")
        self._start_button.clicked.connect(self._start_session)

        self._canvas.state_changed.connect(self._update_state_text)
        self._canvas.trial_started.connect(self._handle_trial_started)
        self._canvas.sample_recorded.connect(self._handle_sample_recorded)
        self._canvas.trial_finished.connect(self._handle_trial_finished)
        self._canvas.trial_cancelled.connect(self._handle_trial_cancelled)

        header_layout = QHBoxLayout()
        header_layout.addWidget(self._move_label, stretch=1)
        header_layout.addWidget(self._state_label, stretch=1)
        header_layout.addWidget(self._recording_label, stretch=1)
        header_layout.addWidget(self._session_label, stretch=1)

        self._session_widget = QWidget()
        session_layout = QVBoxLayout()
        session_layout.addLayout(header_layout)
        session_layout.addWidget(self._canvas, stretch=1)
        self._session_widget.setLayout(session_layout)
        self._session_widget.hide()

        self._setup_widget = QWidget()
        setup_layout = QVBoxLayout()
        setup_layout.addStretch(1)
        setup_layout.addWidget(self._subject_label)
        setup_layout.addWidget(self._subject_input)
        setup_layout.addWidget(self._start_button)
        setup_layout.addStretch(1)
        self._setup_widget.setLayout(setup_layout)

        container = QWidget()
        root_layout = QVBoxLayout()
        root_layout.addWidget(self._setup_widget)
        root_layout.addWidget(self._session_widget)
        container.setLayout(root_layout)
        self.setCentralWidget(container)

        self._update_session_text()
        self._update_state_text("waiting")

    def load_next_trial(self) -> None:
        self._next_move_timer.stop()
        if not self._session_started:
            return
        move = random.choice(DIRECTED_MOVES)
        self._set_current_move(move)

    def _set_current_move(self, move: DirectedMove) -> None:
        self._current_move = move
        self._move_label.setText(f"Move {move.label}")
        self._canvas.set_current_move(move)

    def _start_session(self) -> None:
        subject_name = self._subject_input.text().strip()
        if not subject_name:
            self._subject_input.setFocus()
            return

        self._recorder.start_session(subject_id=subject_name, start_timestamp=time.time())
        self._session_started = True
        self._setup_widget.hide()
        self._session_widget.show()
        self._update_session_text()
        self.load_next_trial()

    def _handle_trial_started(self, move: DirectedMove, timestamp: float) -> None:
        # Event-based logging bridge: the canvas emits lifecycle signals and
        # the window forwards them into the recorder.
        self._recorder.start_trial(move, timestamp)

    def _handle_sample_recorded(self, timestamp: float, x: float, y: float) -> None:
        self._recorder.record_sample(timestamp, x, y)

    def _handle_trial_finished(self, timestamp: float) -> None:
        self._recorder.finish_trial(timestamp)
        self._update_session_text()
        self._next_move_timer.start(self.COMPLETION_FLASH_MS)

    def _handle_trial_cancelled(self) -> None:
        self._recorder.cancel_trial()

    def _update_state_text(self, state: str) -> None:
        state_text, state_style, recording_text, recording_style = self._state_presentation(state)
        self._state_label.setText(state_text)
        self._state_label.setStyleSheet(state_style)
        self._recording_label.setText(recording_text)
        self._recording_label.setStyleSheet(recording_style)
        if self._session_started and state in {"invalid", "incomplete"}:
            self._next_move_timer.start(self.RETRY_DELAY_MS)

    def _advance_session(self) -> None:
        if not self._session_started:
            return
        if self._current_move is None:
            self.load_next_trial()
            return
        state = self._canvas.trial_state
        if state == "finished":
            self.load_next_trial()
            return
        if state in {"invalid", "incomplete"}:
            self._set_current_move(self._current_move)

    def _update_session_text(self) -> None:
        session_data = self._recorder.session_data
        if session_data is None:
            self._session_label.setText("Recorded 0")
        else:
            trials = session_data["trials"]
            assert isinstance(trials, list)
            self._session_label.setText(f"Recorded {len(trials)}")
        self._session_label.setStyleSheet(self._badge_style("#ecfccb", "#3f6212"))

    @staticmethod
    def _badge_style(background: str, foreground: str) -> str:
        return (
            "font-size: 14px;"
            "font-weight: 600;"
            "padding: 6px 12px;"
            "border-radius: 10px;"
            f"background-color: {background};"
            f"color: {foreground};"
        )

    def _state_presentation(self, state: str) -> tuple[str, str, str, str]:
        if state == "active":
            return (
                "State ACTIVE",
                self._badge_style("#dcfce7", "#166534"),
                "Recording ON",
                self._badge_style("#16a34a", "#ffffff"),
            )
        if state == "finished":
            return (
                "Recorded",
                self._badge_style("#dcfce7", "#166534"),
                "Recording OFF",
                self._badge_style("#e2e8f0", "#334155"),
            )
        if state == "invalid":
            return (
                "State INVALID",
                self._badge_style("#fee2e2", "#b91c1c"),
                "Recording OFF",
                self._badge_style("#e2e8f0", "#334155"),
            )
        if state == "incomplete":
            return (
                "State INCOMPLETE",
                self._badge_style("#fef3c7", "#92400e"),
                "Recording OFF",
                self._badge_style("#e2e8f0", "#334155"),
            )
        return (
            "State WAITING",
            self._badge_style("#f1f5f9", "#475569"),
            "Recording OFF",
            self._badge_style("#e2e8f0", "#334155"),
        )

    def closeEvent(self, event) -> None:
        if self._session_started:
            self._recorder.finish_session(time.time())
        super().closeEvent(event)
