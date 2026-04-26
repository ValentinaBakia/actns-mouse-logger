import random
import time
from PySide6.QtGui import QGuiApplication, QShortcut, QKeySequence

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QGridLayout,
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
from collections import defaultdict

# Main application window.
# This file owns the high-level app flow:
# 1. collect subject name
# 2. start a session
# 3. show one move at a time
# 4. react to canvas events
# 5. forward logging events into the recorder
class MainWindow(QMainWindow):
    COMPLETION_FLASH_MS = 250
    RETRY_DELAY_MS = 400

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mouse Logger")
        self.setStyleSheet("QMainWindow { background-color: #ffffff; }")

        # SHORTCUT: Allows you to quit the application by pressing "Esc"
        self._quit_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._quit_shortcut.activated.connect(self.close)

        #1. Get the screen size (now we use the entire available screen)
        screen = QGuiApplication.primaryScreen()
        screen_geom = screen.geometry()
        win_w = screen_geom.width()
        win_h = screen_geom.height()

        self._current_move: DirectedMove | None = None
        self._session_started = False
        self._audio_ready_for_moves = False
        self._pending_moves: list[DirectedMove] = []
        self._recorder = SessionRecorder()
        self._next_move_timer = QTimer(self)
        self._next_move_timer.setSingleShot(True)
        self._next_move_timer.timeout.connect(self._advance_session)

        canvas_area_w = float(win_w)
        canvas_area_h = float(win_h)
        # Keep the task area almost edge-to-edge, but leave a tiny safety
        # margin so the corner squares stay fully visible.
        #edge_inset = max(4.0, min(canvas_area_w, canvas_area_h) * 0.004)
        edge_inset = 0.0

        self._canvas = DrawingCanvas(
            DrawingConfig(
                left=edge_inset,
                top=edge_inset,
                # These dimensions describe the usable drawing region, not a
                # fixed design-time rectangle, so the canvas stays responsive.
                width=canvas_area_w - (edge_inset * 2),
                height=canvas_area_h - (edge_inset * 2),
            )
        )

        self._canvas.setMinimumSize(100, 100)
        self.setMinimumSize(640, 480)

        # --- Initialize Label and Input ---
        self._move_label = QLabel()
        self._move_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._move_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #0f172a;")
        self._audio_banner = QLabel()
        self._audio_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._audio_banner.setWordWrap(True)
        self._audio_banner.setStyleSheet(self._banner_style("#fef3c7", "#92400e", "#f59e0b"))
        self._audio_banner.hide()
        self._state_label = QLabel()
        self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._recording_label = QLabel()
        self._recording_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._session_label = QLabel()
        self._session_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stop_button = QPushButton("STOP")
        self._stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_button.setStyleSheet(self._badge_button_style("#dc2626", "#ffffff"))
        self._stop_button.clicked.connect(self._stop_session)

        self._subject_label = QLabel("Subject name")
        self._subject_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self._subject_input = QLineEdit()
        self._subject_input.setPlaceholderText("Enter subject name")
        self._subject_input.setStyleSheet("padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px; background: white; color: black;")
        self._subject_input.returnPressed.connect(self._start_session)
        
        self._start_button = QPushButton("GO")
        self._start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_button.setStyleSheet(
            "QPushButton {"
            "min-width: 110px;"
            "padding: 8px 16px;"
            "font-weight: 700;"
            "border: 1px solid #0f172a;"
            "border-radius: 6px;"
            "background-color: #0f172a;"
            "color: #ffffff;"
            "}"
            "QPushButton:hover {"
            "background-color: #1e293b;"
            "}"
            "QPushButton:pressed {"
            "background-color: #334155;"
            "}"
        )
        self._start_button.setFixedHeight(self._subject_input.sizeHint().height())
        self._start_button.clicked.connect(self._start_session)

        # --- Event Connections ---
        self._canvas.state_changed.connect(self._update_state_text)
        self._canvas.trial_started.connect(self._handle_trial_started)
        self._canvas.sample_recorded.connect(self._handle_sample_recorded)
        self._canvas.trial_finished.connect(self._handle_trial_finished)
        self._canvas.trial_cancelled.connect(self._handle_trial_cancelled)
        self._recorder.audio_status_changed.connect(self._update_audio_banner)

        # --- LAYOUT HEADER ---
        status_block = QWidget()
        status_block.setStyleSheet("background: transparent;")
        status_block_layout = QVBoxLayout(status_block)
        status_block_layout.setContentsMargins(0, 10, 0, 8)
        status_block_layout.setSpacing(4)
        status_block_layout.addWidget(self._move_label)
        status_block_layout.addWidget(self._audio_banner)

        badges_layout = QHBoxLayout()
        badges_layout.setContentsMargins(0, 0, 0, 0)
        badges_layout.setSpacing(8)
        badges_layout.addWidget(self._state_label)
        badges_layout.addWidget(self._recording_label)
        badges_layout.addWidget(self._session_label)
        badges_layout.addWidget(self._stop_button)
        status_block_layout.addLayout(badges_layout)

        # --- LAYOUT SESSION ---
        self._session_widget = QWidget()
        self._session_widget.setStyleSheet("background: #f7f5ef;")
        session_layout = QGridLayout()
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(0)
        session_layout.addWidget(self._canvas, 0, 0)
        session_layout.addWidget(
            status_block,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
        )
        self._session_widget.setLayout(session_layout)
        self._session_widget.hide()
        self._canvas.setEnabled(False)

        # --- LAYOUT SETUP (Initial Screen) ---
        setup_inner_container = QWidget()
        # Keep the setup step narrow and centered so the session screen can
        # remain visually simple once data collection starts.
        setup_inner_container.setFixedWidth(520)
        setup_inner_layout = QVBoxLayout(setup_inner_container)
        setup_input_row = QHBoxLayout()
        setup_input_row.setContentsMargins(0, 0, 0, 0)
        setup_input_row.setSpacing(8)
        setup_input_row.addWidget(self._subject_input, 1)
        setup_input_row.addWidget(self._start_button)
        setup_inner_layout.addWidget(self._subject_label)
        setup_inner_layout.addLayout(setup_input_row)

        self._setup_widget = QWidget()
        setup_layout = QVBoxLayout()
        setup_layout.addWidget(setup_inner_container, alignment=Qt.AlignmentFlag.AlignCenter)
        self._setup_widget.setLayout(setup_layout)

        # --- ROOT LAYOUT ---
        container = QWidget()
        root_layout = QVBoxLayout()
        root_layout.addWidget(self._setup_widget)
        root_layout.addWidget(self._session_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        container.setLayout(root_layout)
        self.setCentralWidget(container)

        self._update_session_text()
        self._update_state_text("waiting")

        #3. OPENING THE WINDOW IN ABSOLUTE FULL SCREEN
        self.showFullScreen()

    def load_next_trial(self) -> None:
        self._next_move_timer.stop()
        if not self._session_started:
            return

        
        if not self._pending_moves:
            # Generate a new Eulerian Cycle
            if self._current_move:
                # If we're in the middle of a session, we start from where the last movement ended.
                start_node = self._current_move.end_anchor
            else:
                # If it's the very first movement ever, we choose a node at random
                start_node = random.choice(DIRECTED_MOVES).start_anchor
                
            self._pending_moves = self._generate_eulerian_circuit(start_node)

        move = self._pending_moves.pop()
        
        self._set_current_move(move)

    def _generate_eulerian_circuit(self, start_node: str) -> list[DirectedMove]:
        # 1. Create the "Graph Adjacency List"
        # Dictionary that maps: Starting Node -> List of Possible Moves
        adj = defaultdict(list)
        for move in DIRECTED_MOVES:
            adj[move.start_anchor].append(move)

        # 2. We shuffle the edges exiting each node.
        # This ensures that the generated Eulerian path is always random!
        for node in adj:
            random.shuffle(adj[node])

        circuit_edges = []

        #3. Hierholzer Algorithm via Depth-First Search (DFS)
        def euler_dfs(u: str):
            while adj[u]:
                edge = adj[u].pop()
                euler_dfs(edge.end_anchor)
                circuit_edges.append(edge)

        euler_dfs(start_node)
        
        return circuit_edges
        
    def _set_current_move(self, move: DirectedMove | None) -> None:
        self._current_move = move
        # The move label was simplified to the raw direction so participants
        # see only the essential cue, without extra wording.
        self._move_label.setText("" if move is None else move.label)
        self._canvas.set_current_move(move)

    def _start_session(self) -> None:
        subject_name = self._subject_input.text().strip()
        if not subject_name:
            self._subject_input.setFocus()
            return

        self._pending_moves.clear()
        self._set_current_move(None)
        self._recorder.start_session(subject_id=subject_name, start_timestamp=time.time())
        self._session_started = True
        self._setup_widget.hide()
        self._session_widget.show()
        self._update_audio_banner(self._recorder.audio_status, self._recorder.audio_status_message)
        self._update_session_text()

    def _stop_session(self) -> None:
        if not self._session_started:
            return

        self._next_move_timer.stop()
        self._pending_moves.clear()
        self._session_started = False
        self._audio_ready_for_moves = False
        self._set_current_move(None)
        self._recorder.finish_session(time.time())
        self._canvas.setEnabled(False)
        self._session_label.setText("Recorded 0")
        self._session_label.setStyleSheet(self._badge_style("#ecfccb", "#3f6212"))
        self._sync_stop_button_size()
        self._audio_banner.hide()
        self._audio_banner.clear()
        self._setup_widget.show()
        self._session_widget.hide()
        self._subject_input.clear()
        self._subject_input.setFocus()

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
        if not self._session_started or not self._audio_ready_for_moves:
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
        self._sync_stop_button_size()

    @staticmethod
    def _banner_style(background: str, foreground: str, border: str) -> str:
        return (
            "font-size: 12px;"
            "font-weight: 600;"
            "padding: 8px 12px;"
            "border-radius: 10px;"
            f"background-color: {background};"
            f"color: {foreground};"
            f"border: 1px solid {border};"
        )

    @staticmethod
    def _badge_style(background: str, foreground: str) -> str:
        return (
            # These compact pill badges keep recording/session state visible
            # without competing too much with the movement targets.
            "font-size: 11px;"
            "font-weight: 600;"
            "padding: 4px 8px;"
            "border-radius: 999px;"
            f"background-color: {background};"
            f"color: {foreground};"
        )

    @staticmethod
    def _badge_button_style(background: str, foreground: str) -> str:
        return (
            "QPushButton {"
            "font-size: 11px;"
            "font-weight: 600;"
            "padding: 4px 8px;"
            "border: none;"
            "border-radius: 999px;"
            f"background-color: {background};"
            f"color: {foreground};"
            "}"
            "QPushButton:hover {"
            "background-color: #b91c1c;"
            "}"
            "QPushButton:pressed {"
            "background-color: #991b1b;"
            "}"
        )

    def _sync_stop_button_size(self) -> None:
        badge_size = self._session_label.sizeHint()
        self._stop_button.setFixedWidth(max(56, badge_size.width()))
        self._stop_button.setFixedHeight(max(24, badge_size.height()))

    def _update_audio_banner(self, status: str, message: str) -> None:
        self._update_audio_gate(status)
        show_warning = self._session_started and status == "warning" and bool(message)
        if show_warning:
            self._audio_banner.setText(message)
            self._audio_banner.show()
        else:
            self._audio_banner.hide()
            self._audio_banner.clear()
        self._update_state_text(self._canvas.trial_state)

    def _update_audio_gate(self, status: str) -> None:
        audio_ready = self._session_started and status == "ok"
        if audio_ready == self._audio_ready_for_moves:
            return

        self._audio_ready_for_moves = audio_ready
        self._canvas.setEnabled(audio_ready)

        if audio_ready:
            if self._current_move is None:
                self.load_next_trial()
            return

        self._next_move_timer.stop()
        if self._session_started:
            self._pending_moves.clear()
            self._recorder.cancel_trial()
        self._set_current_move(None)

    def _state_presentation(self, state: str) -> tuple[str, str, str, str]:
        if state == "active":
            return (
                "State ACTIVE",
                self._badge_style("#dcfce7", "#166534"),
                *self._recording_presentation(),
            )
        if state == "finished":
            return (
                "Recorded",
                self._badge_style("#dcfce7", "#166534"),
                *self._recording_presentation(),
            )
        if state == "invalid":
            return (
                "State INVALID",
                self._badge_style("#fee2e2", "#b91c1c"),
                *self._recording_presentation(),
            )
        if state == "incomplete":
            return (
                "State INCOMPLETE",
                self._badge_style("#fef3c7", "#92400e"),
                *self._recording_presentation(),
            )
        return (
            "State WAITING",
            self._badge_style("#f1f5f9", "#475569"),
            *self._recording_presentation(),
        )

    def _recording_presentation(self) -> tuple[str, str]:
        status = self._recorder.audio_status
        if status == "ok":
            return (
                "Audio ON",
                self._badge_style("#16a34a", "#ffffff"),
            )
        if status == "starting":
            return (
                "Audio STARTING",
                self._badge_style("#fef3c7", "#92400e"),
            )
        return (
            "Audio OFF",
            self._badge_style("#fee2e2", "#b91c1c"),
        )

    def closeEvent(self, event) -> None:
        if self._session_started:
            self._recorder.finish_session(time.time())
        super().closeEvent(event)
