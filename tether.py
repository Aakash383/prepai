#!/usr/bin/env python3
"""Tether - a personal focus coach that runs entirely on your laptop.

Webcam + microphone level + active app  ->  live focus score, gauge needle,
gentle nudges, and a session report that explains when and why you drifted.

Privacy: no video or audio is stored. The mic is reduced to a loudness number,
and window titles are reduced to the app name. Everything runs on-device
(the optional AI coach receives only aggregated numbers).

Keys:  SPACE pause   M mark moment   C recalibrate   B took a break
       N toggle sound   Q quit (opens the report)
"""
import argparse
import csv
import math
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

import cv2
import numpy as np

from scoring import Calibrator, DriftTracker, Features, FocusScorer, clamp

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
HERE = os.path.dirname(os.path.abspath(__file__))

VID_W, VID_H = 640, 480
PANEL_W, STRIP_H = 320, 120
COL_GOOD, COL_MID, COL_BAD = (100, 210, 90), (0, 190, 240), (80, 80, 240)  # BGR
BG = (28, 24, 22)
FONT = cv2.FONT_HERSHEY_SIMPLEX


# ================================================================ setup
def parse_args():
    p = argparse.ArgumentParser(description="Tether personal focus coach")
    p.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    p.add_argument("--model", default=os.path.join(HERE, "face_landmarker.task"),
                   help="path to face_landmarker.task (auto-downloaded if missing)")
    p.add_argument("--calib", type=float, default=15.0, help="calibration seconds")
    p.add_argument("--no-mic", action="store_true", help="disable microphone level")
    p.add_argument("--no-window", action="store_true", help="do not log the active app")
    p.add_argument("--no-sound", action="store_true", help="start with sound nudges off")
    p.add_argument("--no-report", action="store_true", help="skip report at exit")
    p.add_argument("--offline", action="store_true", help="never call the AI coach")
    return p.parse_args()


def ensure_model(path):
    if os.path.exists(path):
        return path
    legacy = r"C:\Users\aakas\Desktop\face_landmarker.task"
    if os.path.exists(legacy):
        return legacy
    print(f"[..] Downloading face model to {path} (one time, ~4 MB)")
    try:
        urllib.request.urlretrieve(MODEL_URL, path)
    except Exception as e:
        sys.exit(f"[ERR] Could not download the model ({e}).\n"
                 f"     Download it manually from:\n     {MODEL_URL}\n"
                 f"     and pass --model PATH")
    return path


def make_landmarker(model_path):
    import mediapipe as mp
    opts = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_faces=1,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return mp, mp.tasks.vision.FaceLandmarker.create_from_options(opts)


def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if platform.system() == "Windows" \
        else cv2.VideoCapture(index)
    if not cap.isOpened():
        sys.exit("[ERR] Cannot open the camera. Close other apps using it, "
                 "or try --camera 1.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, VID_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, VID_H)
    return cap


# ================================================================ sensors
class Mic:
    """Keeps only a loudness number (RMS). No audio is recorded or stored."""

    def __init__(self, enabled):
        self.level = 0.0
        self.active = False
        self._stream = None
        if not enabled:
            return
        try:
            import sounddevice as sd

            def cb(indata, frames, t, status):
                self.level = float(np.sqrt(np.mean(indata ** 2)))

            self._stream = sd.InputStream(channels=1, samplerate=16000,
                                          blocksize=1600, callback=cb)
            self._stream.start()
            self.active = True
            print("[OK] Microphone level active (no audio stored)")
        except Exception as e:
            print(f"[WARN] Mic unavailable, continuing without it: {e}")

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass


def active_app(disabled):
    """Active window reduced to an app name, e.g. 'main.py - Visual Studio Code' -> 'Visual Studio Code'."""
    if disabled:
        return "-"
    try:
        import pygetwindow as gw
        win = gw.getActiveWindow()
        title = win.title if win else ""
    except Exception:
        return "unknown"
    if not title:
        return "unknown"
    for sep in (" - ", " \u2014 ", " | "):
        if sep in title:
            title = title.split(sep)[-1]
    return title.strip()[:40] or "unknown"


def _beep_sync():
    try:
        if platform.system() == "Windows":
            import winsound
            winsound.Beep(880, 140)
            winsound.Beep(660, 200)
            return
        if platform.system() == "Darwin":
            subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    except Exception:
        pass
    print("\a", end="", flush=True)


def nudge_sound():
    threading.Thread(target=_beep_sync, daemon=True).start()   # never block video


def extract_features(res, mic_level):
    """MediaPipe result -> Features (head pose, gaze, eyes, mouth)."""
    if not (res.face_landmarks and res.face_blendshapes
            and res.facial_transformation_matrixes):
        return Features(face=False, mic=mic_level)
    bs = {c.category_name: c.score for c in res.face_blendshapes[0]}
    g = bs.get

    R = np.array(res.facial_transformation_matrixes[0], dtype=float)[:3, :3]
    R = R / np.linalg.norm(R, axis=0)                      # strip scale
    yaw = math.degrees(math.atan2(-R[2, 0], math.hypot(R[2, 1], R[2, 2])))
    pitch = math.degrees(math.atan2(R[2, 1], R[2, 2]))

    gaze_h = ((g("eyeLookOutLeft", 0) - g("eyeLookInLeft", 0)) +
              (g("eyeLookInRight", 0) - g("eyeLookOutRight", 0))) / 2
    gaze_v = ((g("eyeLookUpLeft", 0) - g("eyeLookDownLeft", 0)) +
              (g("eyeLookUpRight", 0) - g("eyeLookDownRight", 0))) / 2
    # interview-coach extras: smile, brow tension, face size (lean/distance proxy)
    smile = (g("mouthSmileLeft", 0) + g("mouthSmileRight", 0)) / 2
    brow = max((g("browDownLeft", 0) + g("browDownRight", 0)) / 2,
               g("browInnerUp", 0))
    lm = res.face_landmarks[0]
    face_w = abs(lm[454].x - lm[234].x)          # temple to temple, 0-1 of frame
    return Features(
        face=True, yaw=yaw, pitch=pitch, gaze_h=gaze_h, gaze_v=gaze_v,
        blink=(g("eyeBlinkLeft", 0) + g("eyeBlinkRight", 0)) / 2,
        jaw=g("jawOpen", 0), mic=mic_level, smile=smile, brow=brow, face_w=face_w,
    )


# ================================================================ logging
class Logger:
    HEADER = ["ts", "t", "score", "event", "face", "attention", "alertness", "gaze",
              "head", "eyes", "perclos", "blink_rate", "yawns", "mic", "app"]

    def __init__(self, path):
        self.path = path
        self.f = open(path, "w", newline="", encoding="utf-8")
        self.w = csv.writer(self.f)
        self.w.writerow(self.HEADER)

    def row(self, st, out, yawns, mic, app, event=""):
        self.w.writerow([
            datetime.now().isoformat(timespec="seconds"), round(st, 1),
            int(out["score"]), event, int(out["face"]), int(out["attention"]),
            int(out["alertness"]), int(out["gaze_s"]), int(out["head_s"]),
            int(out["eyes_s"]), round(out["perclos"], 3), round(out["blink_rate"], 1),
            yawns, round(mic, 4), app,
        ])
        self.f.flush()

    def close(self):
        self.f.close()


# ================================================================ drawing
def put(img, text, org, scale=0.55, color=(210, 210, 215), thick=1, font=FONT):
    cv2.putText(img, text, org, font, scale, color, thick, cv2.LINE_AA)


def put_centered(img, text, cx, y, scale, color, thick=1, font=FONT):
    (w, _), _ = cv2.getTextSize(text, font, scale, thick)
    put(img, text, (int(cx - w / 2), y), scale, color, thick, font)


def fmt_t(sec):
    m, s = divmod(int(max(0, sec)), 60)
    return f"{m:02d}:{s:02d}"


def score_color(s):
    return COL_GOOD if s >= 65 else COL_MID if s >= 40 else COL_BAD


def draw_gauge(img, cx, cy, r, value):
    """Half-circle dial with a moving needle: red / amber / green zones."""
    for a0, a1, col in ((180, 252, COL_BAD), (252, 297, COL_MID), (297, 360, COL_GOOD)):
        cv2.ellipse(img, (cx, cy), (r, r), 0, a0, a1, col, 14, cv2.LINE_AA)
    ang = math.radians(180 + 1.8 * clamp(value, 0, 100))
    tip = (int(cx + (r - 6) * math.cos(ang)), int(cy + (r - 6) * math.sin(ang)))
    cv2.line(img, (cx, cy), tip, (245, 245, 245), 4, cv2.LINE_AA)
    cv2.circle(img, (cx, cy), 9, (245, 245, 245), -1, cv2.LINE_AA)
    put(img, "0", (cx - r - 4, cy + 22), 0.45, (150, 150, 160))
    put(img, "100", (cx + r - 20, cy + 22), 0.45, (150, 150, 160))


def draw_timeline(img, x0, y0, x1, y1, hist, now_t, marks, drifts, span=180.0):
    cv2.rectangle(img, (x0, y0), (x1, y1), (40, 35, 32), -1)
    for lvl in (40, 65):
        y = int(y1 - (y1 - y0) * lvl / 100)
        cv2.line(img, (x0, y), (x1, y), (75, 70, 66), 1)
    xs = lambda t: int(x1 - (now_t - t) / span * (x1 - x0))
    ys = lambda s: int(y1 - (y1 - y0) * clamp(s, 0, 100) / 100)
    for t in drifts:
        if now_t - t <= span:
            cv2.line(img, (xs(t), y0), (xs(t), y0 + 10), COL_BAD, 2)
    for t in marks:
        if now_t - t <= span:
            cv2.line(img, (xs(t), y0), (xs(t), y1), (240, 230, 90), 1)
    pts = [(xs(t), ys(s), s) for t, s in hist if now_t - t <= span]
    for (xa, ya, _), (xb, yb, sb) in zip(pts, pts[1:]):
        cv2.line(img, (xa, ya), (xb, yb), score_color(sb), 2, cv2.LINE_AA)
    put(img, "last 3 min", (x0 + 6, y1 - 6), 0.4, (150, 150, 160))


def draw_ui(frame, ui):
    W, H = VID_W + PANEL_W, VID_H + STRIP_H
    canvas = np.full((H, W, 3), BG, np.uint8)
    canvas[:VID_H, :VID_W] = frame
    px = VID_W
    cx = px + PANEL_W // 2

    put(canvas, "TETHER", (px + 20, 36), 0.95, (245, 245, 245), 2)
    put(canvas, "personal focus coach", (px + 20, 58), 0.45, (150, 150, 160))

    if ui["calibrating"]:
        put_centered(canvas, "CALIBRATING", cx, 170, 0.9, (0, 220, 255), 2)
        put_centered(canvas, "Sit naturally and", cx, 205, 0.55, (210, 210, 215))
        put_centered(canvas, "look at your screen", cx, 228, 0.55, (210, 210, 215))
        bx0, bx1, by = px + 30, px + PANEL_W - 30, 260
        cv2.rectangle(canvas, (bx0, by), (bx1, by + 16), (70, 65, 60), -1)
        cv2.rectangle(canvas, (bx0, by),
                      (bx0 + int((bx1 - bx0) * ui["cal_progress"]), by + 16), (0, 220, 255), -1)
        if not ui["face"]:
            put_centered(canvas, "No face found yet", cx, 310, 0.55, COL_BAD)
    else:
        score = ui["score"]
        draw_gauge(canvas, cx, 215, 112, ui["needle"])
        put_centered(canvas, str(int(score)), cx, 292, 1.9, score_color(score), 3,
                     cv2.FONT_HERSHEY_DUPLEX)
        state_col = (170, 170, 175) if ui["paused"] else score_color(score)
        put_centered(canvas, ui["state"], cx, 322, 0.8, state_col, 2)
        rows = [("Session", fmt_t(ui["elapsed"])),
                ("Focused", f"{ui['focus_pct']:.0f}%"),
                ("Streak", fmt_t(ui["streak"])),
                ("Drifts / Yawns", f"{ui['drifts']} / {ui['yawns']}")]
        for i, (k, v) in enumerate(rows):
            y = 358 + i * 24
            put(canvas, k, (px + 24, y), 0.5, (150, 150, 160))
            put(canvas, v, (px + 190, y), 0.55, (235, 235, 240), 1)

    put(canvas, "SPACE pause  M mark  C recal", (px + 20, VID_H - 30), 0.4, (125, 125, 135))
    put(canvas, "B break  N sound  Q quit", (px + 20, VID_H - 12), 0.4, (125, 125, 135))

    # video overlays
    if not ui["calibrating"] and not ui["face"] and not ui["paused"]:
        put(canvas, "NO FACE", (20, 50), 1.3, (60, 60, 255), 3, cv2.FONT_HERSHEY_DUPLEX)
    if ui["flash"]:
        cv2.rectangle(canvas, (0, 0), (VID_W - 1, VID_H - 1), COL_BAD, 10)
        put_centered(canvas, "EYES UP", VID_W // 2, VID_H // 2, 2.2, (255, 255, 255), 4,
                     cv2.FONT_HERSHEY_DUPLEX)
    if ui["marked"]:
        put_centered(canvas, "MARKED", VID_W // 2, 90, 1.5, (240, 230, 90), 3,
                     cv2.FONT_HERSHEY_DUPLEX)
    if ui["break_msg"]:
        cv2.rectangle(canvas, (0, VID_H - 44), (VID_W, VID_H), (30, 130, 200), -1)
        put_centered(canvas, "Time for a 5-minute break - press B when you're back",
                     VID_W // 2, VID_H - 16, 0.65, (255, 255, 255), 2)
    if ui["paused"]:
        put_centered(canvas, "PAUSED", VID_W // 2, VID_H // 2, 1.8, (230, 230, 230), 3,
                     cv2.FONT_HERSHEY_DUPLEX)

    draw_timeline(canvas, 20, VID_H + 12, W - 20, H - 12, ui["hist"], ui["elapsed"],
                  ui["marks"], ui["drift_marks"])
    return canvas


# ================================================================ main
def main():
    args = parse_args()
    model_path = ensure_model(args.model)
    mp, landmarker = make_landmarker(model_path)
    mic = Mic(not args.no_mic)
    cap = open_camera(args.camera)
    logger = Logger(f"focus_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    print(f"[OK] Logging to {logger.path}")
    print("[OK] Calibrating - sit normally and look at the screen...")

    boot = time.monotonic()
    last_ts = -1
    calib, calibrating, cal_began = Calibrator(args.calib), True, boot
    scorer = tracker = None
    t0 = None
    paused, pause_start, paused_total = False, 0.0, 0.0
    sound = not args.no_sound

    out = dict(score=100.0, face=False, attention=0, alertness=0, gaze_s=0, head_s=0,
               eyes_s=0, perclos=0.0, blink_rate=0.0)
    needle = 100.0
    hist, marks, drift_marks = [], [], []
    yawns_total = 0
    sec_total = sec_focus = 0.0
    last_log = last_frame = boot
    flash_until = marked_until = break_until = 0.0
    app = "unknown"
    st = 0.0
    face_now = False
    window_name = "Tether"

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[ERR] Camera stopped delivering frames.")
            break
        frame = cv2.flip(frame, 1)
        if frame.shape[1] != VID_W or frame.shape[0] != VID_H:
            frame = cv2.resize(frame, (VID_W, VID_H))
        mono = time.monotonic()
        dt = min(0.5, mono - last_frame)
        last_frame = mono

        if not paused:
            ts = max(int((mono - boot) * 1000), last_ts + 1)   # strictly increasing
            last_ts = ts
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
            feats = extract_features(res, mic.level)
            face_now = feats.face

            if calibrating:
                calib.add(feats, mono)
                if calib.done(mono):
                    scorer = FocusScorer(calib.baseline())
                    if t0 is None:
                        t0, paused_total = mono, 0.0
                        tracker = DriftTracker(now=0.0)
                    else:                       # recalibration: don't count it as work time
                        paused_total += mono - cal_began
                    calibrating = False
                    print("[OK] Calibrated. Session started.")
            else:
                st = mono - t0 - paused_total
                out = scorer.update(feats, st)
                score = out["score"]
                sec_total += dt
                if score >= 65:
                    sec_focus += dt

                events = []
                if out["yawn_event"]:
                    yawns_total += 1
                    tracker.note_yawn(st)
                    events.append("YAWN")
                events += tracker.update(score, st)
                for ev in events:
                    if ev == "NUDGE":
                        flash_until = mono + 1.5
                        if sound:
                            nudge_sound()
                    elif ev == "DRIFT_START":
                        drift_marks.append(st)
                    elif ev == "BREAK_SUGGESTED":
                        break_until = mono + 12
                    logger.row(st, out, yawns_total, mic.level, app, ev)

                if mono - last_log >= 1.0:
                    app = active_app(args.no_window)
                    logger.row(st, out, yawns_total, mic.level, app)
                    hist.append((st, score))
                    del hist[:-200]
                    last_log = mono

        needle += (out["score"] - needle) * 0.2           # smooth needle motion

        if paused:
            state = "PAUSED"
        elif calibrating:
            state = ""
        elif tracker.in_drift or out["score"] < 40:
            state = "REFOCUS"
        elif out["score"] < 65:
            state = "DRIFTING"
        else:
            state = "FOCUSED"

        ui = dict(
            calibrating=calibrating, cal_progress=calib.progress(mono) if calibrating else 1.0,
            face=face_now, score=out["score"], needle=needle, state=state, paused=paused,
            elapsed=st, focus_pct=(100 * sec_focus / sec_total) if sec_total else 100.0,
            streak=tracker.streak(st) if tracker else 0.0,
            drifts=tracker.drifts if tracker else 0, yawns=yawns_total,
            flash=mono < flash_until, marked=mono < marked_until,
            break_msg=mono < break_until, hist=hist, marks=marks, drift_marks=drift_marks,
        )
        cv2.imshow(window_name, draw_ui(frame, ui))

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), ord("Q"), 27):
            break
        if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
            break
        if key == ord(" ") and not calibrating:
            paused = not paused
            if paused:
                pause_start = mono
            else:
                paused_total += mono - pause_start
        elif key in (ord("m"), ord("M")) and not calibrating:
            marked_until = mono + 1.0
            marks.append(st)
            logger.row(st, out, yawns_total, mic.level, app, "MARK")
        elif key in (ord("c"), ord("C")):
            calib, calibrating, cal_began = Calibrator(args.calib), True, mono
            paused = False
        elif key in (ord("b"), ord("B")) and tracker:
            tracker.reset_break(st)
            break_until = 0.0
        elif key in (ord("n"), ord("N")):
            sound = not sound
            print(f"[OK] Sound nudges {'on' if sound else 'off'}")

    if tracker and tracker.in_drift:
        logger.row(st, out, yawns_total, mic.level, app, "DRIFT_END")
    cap.release()
    cv2.destroyAllWindows()
    landmarker.close()
    mic.close()
    logger.close()
    print(f"[DONE] Saved {logger.path}")

    if not args.no_report and t0 is not None:
        try:
            import report
            report.generate(logger.path, open_browser=True, offline=args.offline)
        except Exception as e:
            print(f"[WARN] Could not build the report: {e}\n"
                  f"       Run: python report.py {logger.path}")


if __name__ == "__main__":
    main()
