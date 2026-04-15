from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re

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
            "end_timestamp": end_timestamp,
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


class SessionRecorder:
    def __init__(self) -> None:
        self.session_data: dict[str, object] | None = None
        self.output_dir = Path("output")
        self._active_trial: ActiveTrial | None = None
        self._next_trial_id = 1

    def start_session(self, subject_id: str, start_timestamp: float | None = None) -> dict[str, object]:
        # A session is the top-level container persisted to JSON.
        started_at = datetime.fromtimestamp(start_timestamp or datetime.now().timestamp())
        self.session_data = {
            "session_id": build_session_id(subject_id, started_at),
            "subject_id": subject_id,
            "session_start_timestamp": started_at.timestamp(),
            "session_end_timestamp": None,
            "trials": [],
        }
        self._active_trial = None
        self._next_trial_id = 1
        self._write_session_json()
        return self.session_data

    def finish_session(self, end_timestamp: float) -> None:
        if self.session_data is None:
            return
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
