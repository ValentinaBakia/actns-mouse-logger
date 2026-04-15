# Mouse Trajectory Ground-Truth Collector

A local desktop Python app for collecting ground-truth mouse trajectory data for a school research project.

## Purpose

This tool is used to collect labeled mouse movement data together with precise timestamps, so the data can later be synchronized with smartphone audio recordings.

The app shows predefined mouse movements on screen and records the actual cursor trajectory while the user performs them.

The final goal is to align (either via a clap before recording or using the timestamps):

- laptop-side mouse movement logs
- smartphone audio recordings

so that each movement can later be matched to the corresponding audio segment.

## Main idea

The app displays one movement at a time on a rectangle-based layout.

Each movement has:

- a start anchor
- an end anchor
- a highlighted intended path

When the user performs the movement correctly:

- the mouse trajectory is recorded
- timestamps are stored
- the completed movement is added to the session log
- a new movement appears automatically

Only successfully completed movements are stored.

## Supported movements

The app uses 12 directed movements:

1. TL -> TR
2. TR -> TL
3. BL -> BR
4. BR -> BL
5. TL -> BL
6. BL -> TL
7. TR -> BR
8. BR -> TR
9. TL -> BR
10. BR -> TL
11. TR -> BL
12. BL -> TR

## Data collected

For each completed move, the app stores:

- subject_id
- trial_id
- movement_label
- start_anchor
- end_anchor
- start_timestamp
- end_timestamp
- start_mouse_position
- end_mouse_position
- samples

Each sample contains:

- timestamp
- x
- y
  
The app uses event-based logging: cursor samples are stored when the mouse moves during an active trial, rather than at a fixed periodic rate.

## Session output

The app stores data as one session object containing:

- session_id
- subject_id
- session_start_timestamp
- session_end_timestamp
- trials

The session is exported to a JSON file.

## Run locally

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

python3 main.py
