# Tether - a personal focus coach (laptop only)

Two apps, one engine:

1. **Focus coach** (`tether.py`) - watches *you* (webcam), hears how loud your room is (mic level only), sees which app you are in, and:
   - shows a live **focus score** with a moving gauge needle,
   - **nudges** you the moment you drift (flash + sound),
   - suggests **breaks** (25 min of work, or 3 yawns in 10 min),
   - writes a **session report** that shows when focus peaked, when it fell, and what was going on.
2. **Interview coach** (`interview.py`) - give it your resume, the role and the job description. Claude writes questions for THAT job, listens (local Whisper) and watches (webcam) while you answer, scores every answer, and ends with a full review: content, delivery, body language, top fixes and a practice plan. `--live` mode reviews a real interview afterwards.

No extra hardware. No video or audio is ever saved.

## Setup (once)

```bash
pip install -r requirements.txt
python tether.py      # focus coach
```

The face model (~4 MB) downloads automatically on first run.
Optional AI coach: `set ANTHROPIC_API_KEY=your_key` (Windows) / `export ANTHROPIC_API_KEY=...` (Mac/Linux).

### Interview coach

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # required for this mode
python interview.py --resume resume.pdf --role "Backend Engineer" --jd jd.txt
python interview.py --live              # review a real interview afterwards
```

Speech is transcribed locally by Whisper (first run downloads the model; pick a bigger one with `--whisper small.en`). Keys: SPACE start/stop answer, N next question, R retry, S skip, Q finish and open the review. Sessions are saved to `interview_sessions/` so you can track progress per role. Models can be overridden with `TETHER_MODEL` (deep review) and `TETHER_FAST_MODEL` (per-answer feedback).

## Using it

| Key | Action |
|---|---|
| SPACE | pause / resume |
| M | mark a moment (shows up in the report) |
| C | recalibrate (e.g. you changed seats) |
| B | "I took my break" (resets the break timer) |
| N | sound nudges on / off |
| Q | quit and open the report |

First 15 seconds: sit naturally and look at your screen. Tether learns *your* normal gaze, head angle and eye openness, so it works for glasses, different seating, etc.

Flags: `--camera 1`, `--calib 10`, `--no-mic`, `--no-window`, `--no-sound`, `--offline`, `--no-report`.

## Files

| File | Job |
|---|---|
| `tether.py` | live focus app: camera, calibration, gauge HUD, nudges, CSV log |
| `scoring.py` | the scoring + drift logic (pure Python, easy to tune/test) |
| `report.py` | builds the focus HTML report; `python report.py --demo` makes a sample |
| `interview.py` | live interview coach: camera + mic, questions, per-answer feedback |
| `behavior.py` | body-language stats for the interview coach (eye contact, posture, motion) |
| `speech.py` | mic recorder, local Whisper transcription, pace/filler/pause analysis |
| `coach.py` | Claude calls: question generation, per-answer scoring, final review |
| `interview_report.py` | builds the interview-review HTML report |
| `focus_log_*.csv` | one row per second plus events (created each session) |
| `interview_sessions/` | saved practice sessions + reviews (created by interview.py) |

## How the score works

```
attention = 50% gaze on screen + 50% head facing screen
alertness = 60% eyes open (PERCLOS) + 25% no recent yawn + 15% blink rate
score     = 70% attention + 30% alertness        (no face = 0)
```

Everything is measured against your own calibration. The score falls fast and rises slowly so the needle does not flicker.
A **drift** starts when the score stays under 50 for 4 s and ends when it is back over 65 for 2.5 s.

Signals: eye-gaze and blink blendshapes, 3D head pose (yaw and pitch, so looking down at a phone is caught), mouth opening for yawns, eye-closure fraction over 20 s (PERCLOS, a standard drowsiness measure).

## Demo plan (3 minutes)

1. **0:00** Problem: "Everyone knows they lose focus. Nobody knows when or why."
2. **0:20** Run Tether live. Calibrate (do it before the judges arrive, or use `--calib 8`).
3. **0:40** Work normally, then look at your phone: needle drops, screen flashes EYES UP, sound plays. Look back: it recovers.
4. **1:20** Hand the laptop to a judge for 30 seconds. Let them try to break it.
5. **1:50** Press Q. The report opens: timeline, drift episodes, app breakdown, coach note.
6. **2:30** Privacy + impact: on-device, no video stored, only a loudness number and app names. For students, remote workers, ADHD support, interview practice.

**Backup if the webcam fails on stage:** `python report.py --demo` opens a realistic sample report instantly. Record a screen capture of a good live run beforehand.

## Likely judge questions

- *Does it send my face anywhere?* No. MediaPipe runs locally; frames are never saved.
- *Is it accurate?* It is a calibrated estimate from proven signals, not mind reading. State this honestly.
- *Why a score and not a lock-out app?* Coaching beats blocking: it builds awareness and shows you your own patterns.

## Troubleshooting

- **Camera will not open:** close Zoom/Teams/browser tabs using it, or try `--camera 1`.
- **Score low even when focused:** press C and recalibrate looking at the screen's centre; check your face is well lit from the front.
- **No app names in the report:** `pygetwindow` works on Windows only; elsewhere apps show as `unknown`.
- **Wrong model path error:** delete `face_landmarker.task` and run again to re-download.

## Ideas if you have extra time

- Presenter mode: use mic pace and loudness to flag when *your speech* loses energy.
- Weekly trends across sessions (all logs are plain CSV).
- Pomodoro mode with a configurable work/break cycle.
