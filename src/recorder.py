from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from PySide6.QtCore import QCoreApplication, QMicrophonePermission, QObject, QTimer, Qt, QUrl, Signal
from PySide6.QtMultimedia import (
    QAudioInput,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaFormat,
    QMediaRecorder,
)

from movements import DirectedMove


# Recorder layer for session/trial data.
# This module owns the in-memory session object and keeps the JSON export
# exactly aligned with that same structure.

@dataclass(frozen=True)
class TrialSample:
    timestamp: float
    x: float
    y: float

    def to_dict(self) -> dict[str, float]:
        return {
            "timestamp": self.timestamp,
            "x": self.x,
            "y": self.y,
        }


@dataclass
class ActiveTrial:
    trial_id: int
    movement_label: str
    start_anchor: str
    end_anchor: str
    start_timestamp: float
    samples: list[TrialSample]

    def to_completed_dict(self, end_timestamp: float) -> dict[str, object]:
        # The first and last recorded samples define the start/end mouse
        # positions requested in the final stored trial object.
        first_sample = self.samples[0]
        last_sample = self.samples[-1]
        return {
            "trial_id": self.trial_id,
            "movement_label": self.movement_label,
            "start_anchor": self.start_anchor,
            "end_anchor": self.end_anchor,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": end_timestamp + 0.15,
            "start_mouse_position": {
                "x": first_sample.x,
                "y": first_sample.y,
            },
            "end_mouse_position": {
                "x": last_sample.x,
                "y": last_sample.y,
            },
            "samples": [sample.to_dict() for sample in self.samples],
        }


def sanitize_subject_name(subject_name: str) -> str:
    lowered = subject_name.strip().lower()
    normalized = re.sub(r"\s+", "_", lowered)
    safe = re.sub(r"[^a-z0-9_-]", "_", normalized)
    collapsed = re.sub(r"_+", "_", safe).strip("_")
    return collapsed or "subject"


def build_session_id(subject_name: str, started_at: datetime) -> str:
    safe_subject = sanitize_subject_name(subject_name)
    return f"session_{safe_subject}_{started_at:%Y-%m-%d_%H-%M-%S}"


class SessionRecorder(QObject):
    audio_status_changed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.session_data: dict[str, object] | None = None

        base_dir = Path(__file__).parent.parent.resolve()
        self.output_dir = base_dir / "output"

        self._active_trial: ActiveTrial | None = None
        self._next_trial_id = 1
        self._session_active = False
        self._audio_status = "idle"
        self._audio_status_message = ""
        self._audio_start_attempt = 0
        self._pending_audio_filename: str | None = None
        self._pending_audio_filepath: Path | None = None

        self._capture_session = QMediaCaptureSession()
        self._audio_input = QAudioInput()
        self._capture_session.setAudioInput(self._audio_input)

        self._audio_recorder = QMediaRecorder()
        self._capture_session.setRecorder(self._audio_recorder)
        self._media_devices = QMediaDevices()

        self._audio_recorder.errorOccurred.connect(self._handle_audio_error)
        self._audio_recorder.recorderStateChanged.connect(self._handle_recorder_state_changed)
        self._media_devices.audioInputsChanged.connect(self._handle_audio_inputs_changed)

        self._audio_extension = self._configure_audio_recording()

    @property
    def audio_status(self) -> str:
        return self._audio_status

    @property
    def audio_status_message(self) -> str:
        return self._audio_status_message

    def start_session(self, subject_id: str, start_timestamp: float | None = None) -> dict[str, object]:
        # A session is the top-level container persisted to JSON.
        started_at = datetime.fromtimestamp(start_timestamp or datetime.now().timestamp())
        session_id = build_session_id(subject_id, started_at)

        self.output_dir.mkdir(parents=True, exist_ok=True)

        audio_filename = f"{session_id}.{self._audio_extension}"
        audio_filepath = self.output_dir / audio_filename

        self._session_active = True
        self._active_trial = None
        self._next_trial_id = 1
        self._pending_audio_filename = audio_filename
        self._pending_audio_filepath = audio_filepath

        self.session_data = {
            "session_id": session_id,
            "subject_id": subject_id,
            "session_start_timestamp": started_at.timestamp(),
            "audio_file": None,
            "audio_start_timestamp": None,
            "audio_status": "starting",
            "audio_status_detail": None,
            "session_end_timestamp": None,
            "trials": [],
        }

        self._set_audio_status("starting", "")
        permission_status = self._microphone_permission_status()
        if permission_status == Qt.PermissionStatus.Denied:
            self._set_audio_status(
                "warning",
                "Microphone permission is turned off in system settings. Enable it for this app to record audio.",
            )
        elif permission_status == Qt.PermissionStatus.Undetermined:
            self._request_microphone_permission()
        else:
            self._begin_audio_recording()
        self._write_session_json()
        return self.session_data

    def finish_session(self, end_timestamp: float) -> None:
        if self.session_data is None:
            return

        self._session_active = False
        if self._audio_recorder.recorderState() == QMediaRecorder.RecorderState.RecordingState:
            self._audio_recorder.stop()

        self.session_data["session_end_timestamp"] = end_timestamp
        self._write_session_json()

    def start_trial(self, move: DirectedMove, timestamp: float) -> None:
        # Called only after the canvas emits "trial_started", which happens
        # when the cursor enters the correct start anchor.
        if self.session_data is None:
            return
        self._active_trial = ActiveTrial(
            trial_id=self._next_trial_id,
            movement_label=move.label,
            start_anchor=move.start_anchor,
            end_anchor=move.end_anchor,
            start_timestamp=timestamp,
            samples=[],
        )

    def record_sample(self, timestamp: float, x: float, y: float) -> None:
        # Samples are ignored unless a valid active trial already exists.
        if self._active_trial is None:
            return
        self._active_trial.samples.append(TrialSample(timestamp=timestamp, x=x, y=y))

    def finish_trial(self, timestamp: float) -> dict[str, object] | None:
        # Only successfully completed trials are converted into stored data.
        if self.session_data is None or self._active_trial is None or not self._active_trial.samples:
            return None

        completed_trial = self._active_trial.to_completed_dict(end_timestamp=timestamp)
        trials = self.session_data["trials"]
        assert isinstance(trials, list)
        trials.append(completed_trial)
        self.session_data["session_end_timestamp"] = timestamp
        self._active_trial = None
        self._next_trial_id += 1
        self._write_session_json()
        return completed_trial

    def cancel_trial(self) -> None:
        # Invalid/incomplete trials are discarded instead of being stored.
        self._active_trial = None

    def session_file_path(self) -> Path | None:
        if self.session_data is None:
            return None
        session_id = self.session_data["session_id"]
        assert isinstance(session_id, str)
        return self.output_dir / f"{session_id}.json"

    def _write_session_json(self) -> None:
        if self.session_data is None:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.session_file_path()
        if file_path is None:
            return
        file_path.write_text(json.dumps(self.session_data, indent=2), encoding="utf-8")

    def _set_audio_status(self, status: str, message: str) -> None:
        if status == self._audio_status and message == self._audio_status_message:
            return

        self._audio_status = status
        self._audio_status_message = message

        if self.session_data is not None:
            self.session_data["audio_status"] = status
            self.session_data["audio_status_detail"] = message or None
            self._write_session_json()

        self.audio_status_changed.emit(status, message)

    def _microphone_permission_status(self) -> Qt.PermissionStatus | None:
        app = QCoreApplication.instance()
        if app is None:
            return None
        return app.checkPermission(QMicrophonePermission())

    def _request_microphone_permission(self) -> None:
        app = QCoreApplication.instance()
        if app is None:
            self._set_audio_status(
                "warning",
                "Audio recording unavailable. The app could not check microphone permission.",
            )
            return
        app.requestPermission(QMicrophonePermission(), self, self._handle_microphone_permission_result)

    def _handle_microphone_permission_result(self, *_args: object) -> None:
        if not self._session_active or self.session_data is None:
            return

        permission_status = self._microphone_permission_status()
        if permission_status == Qt.PermissionStatus.Granted:
            self._begin_audio_recording()
            return

        if permission_status == Qt.PermissionStatus.Denied:
            self._set_audio_status(
                "warning",
                "Microphone permission is turned off in system settings. Enable it for this app to record audio.",
            )
            return

        self._set_audio_status(
            "warning",
            "Audio recording unavailable. Microphone permission was not granted.",
        )

    def _begin_audio_recording(self) -> None:
        if self.session_data is None or self._pending_audio_filename is None or self._pending_audio_filepath is None:
            return
        if not self._warn_if_audio_input_missing():
            return

        self.session_data["audio_file"] = self._pending_audio_filename
        self.session_data["audio_start_timestamp"] = datetime.now().timestamp()
        self._audio_recorder.setOutputLocation(QUrl.fromLocalFile(str(self._pending_audio_filepath)))
        self._set_audio_status("starting", "")
        self._audio_recorder.record()
        self._audio_start_attempt += 1
        attempt_id = self._audio_start_attempt
        QTimer.singleShot(400, lambda: self._verify_audio_recording_started(attempt_id))

    def _warn_if_audio_input_missing(self) -> None:
        if QMediaDevices.audioInputs():
            return True
        self._set_audio_status(
            "warning",
            "Audio recording unavailable. No microphone input device is available.",
        )
        return False

    def _verify_audio_recording_started(self, attempt_id: int) -> None:
        if not self._session_active or attempt_id != self._audio_start_attempt:
            return
        if self._audio_recorder.recorderState() == QMediaRecorder.RecorderState.RecordingState:
            return
        if self._audio_status == "warning":
            return

        error_message = self._audio_recorder.errorString().strip()
        message = error_message or (
            "Audio recording did not start. Check microphone permissions or the selected input device."
        )
        self._set_audio_status("warning", message)

    def _handle_audio_error(self, error: QMediaRecorder.Error, error_string: str) -> None:
        if error == QMediaRecorder.Error.NoError or not self._session_active:
            return

        message = error_string.strip() or (
            "Audio recording failed. Check microphone permissions or the selected input device."
        )
        self._set_audio_status("warning", message)

    def _handle_recorder_state_changed(self, state: QMediaRecorder.RecorderState) -> None:
        if not self._session_active:
            return
        if state == QMediaRecorder.RecorderState.RecordingState:
            self._set_audio_status("ok", "")

    def _handle_audio_inputs_changed(self) -> None:
        if not self._session_active:
            return
        if not QMediaDevices.audioInputs():
            self._set_audio_status(
                "warning",
                "Audio recording unavailable. No microphone input device is available.",
            )
            return
        if self._audio_recorder.recorderState() == QMediaRecorder.RecorderState.RecordingState:
            self._set_audio_status("ok", "")

    def _configure_audio_recording(self) -> str:
        # 1. First try real WAV
        wav_format = QMediaFormat()
        wav_format.setFileFormat(QMediaFormat.FileFormat.Wave)

        if wav_format.isSupported(QMediaFormat.ConversionMode.Encode):
            self._audio_recorder.setMediaFormat(wav_format)
            return "wav"

        # 2. Fallback to MP4 if WAV is not supported
        mp4_format = QMediaFormat()
        mp4_format.setFileFormat(QMediaFormat.FileFormat.MPEG4)

        if mp4_format.isSupported(QMediaFormat.ConversionMode.Encode):
            self._audio_recorder.setMediaFormat(mp4_format)
            return "mp4"

        # 3. Last fallback: let Qt choose anything supported
        self._audio_recorder.setMediaFormat(QMediaFormat())
        return "audio"
