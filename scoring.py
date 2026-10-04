"""Tether scoring engine.

Pure logic: no camera, no window, no files. The live app (tether.py) feeds it
one `Features` sample per video frame; the demo generator (report.py) feeds it
synthetic scores. Keeping it separate makes it easy to test and tune.

All times are "session seconds" (a float that does not advance while paused).
"""
import math
from collections import deque
from dataclasses import dataclass

import numpy as np


# --------------------------------------------------------------- helpers
def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ramp(x, lo, hi):
    """0.0 when x <= lo, 1.0 when x >= hi, linear in between."""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return clamp((x - lo) / (hi - lo))


def angdiff(a, b):
    """Smallest signed difference a-b in degrees, in (-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0


def circ_mean(vals):
    r = np.radians(np.asarray(vals, dtype=float))
    return math.degrees(math.atan2(float(np.mean(np.sin(r))), float(np.mean(np.cos(r)))))


# ------------------------------------------------------------- data types
@dataclass
class Features:
    """What the vision layer extracts from one frame."""
    face: bool = False
    yaw: float = 0.0       # head turn, degrees
    pitch: float = 0.0     # head up/down, degrees
    gaze_h: float = 0.0    # eye direction, left/right (blendshape units)
    gaze_v: float = 0.0    # eye direction, up/down
    blink: float = 0.0     # 0 = eyes open, 1 = eyes shut
    jaw: float = 0.0       # 0 = mouth closed, 1 = wide open
    mic: float = 0.0       # ambient loudness (RMS), context only
    smile: float = 0.0     # 0-1 mouth-smile blendshape
    brow: float = 0.0      # 0-1 brow-lowered (furrow) blendshape
    face_w: float = 0.0    # face width as a fraction of the frame (distance/lean proxy)


@dataclass
class Baseline:
    yaw: float = 0.0
    pitch: float = 0.0
    gaze_h: float = 0.0
    gaze_v: float = 0.0
    blink: float = 0.1
    jaw: float = 0.05
    face_w: float = 0.0


class Calibrator:
    """Learns what 'looking at my screen' looks like for this person."""

    def __init__(self, seconds=15.0):
        self.seconds = seconds
        self.samples = []
        self.t_start = None

    def add(self, f, now):
        if not f.face:
            return
        if self.t_start is None:
            self.t_start = now
        if f.blink < 0.5:          # ignore mid-blink frames
            self.samples.append(f)

    def progress(self, now):
        if self.t_start is None:
            return 0.0
        return clamp((now - self.t_start) / self.seconds)

    def done(self, now):
        return (self.t_start is not None
                and now - self.t_start >= self.seconds
                and len(self.samples) >= 30)

    def baseline(self):
        s = self.samples
        med = lambda attr: float(np.median([getattr(x, attr) for x in s]))
        return Baseline(
            yaw=circ_mean([x.yaw for x in s]),
            pitch=circ_mean([x.pitch for x in s]),
            gaze_h=med("gaze_h"), gaze_v=med("gaze_v"),
            blink=med("blink"), jaw=med("jaw"), face_w=med("face_w"),
        )


# ----------------------------------------------------------------- scorer
class FocusScorer:
    """Turns per-frame Features into a smooth 0-100 focus score.

        attention  = 50% gaze on screen + 50% head facing screen
        alertness  = 60% eyes (PERCLOS) + 25% no recent yawn + 15% blink rate
        score      = 70% attention + 30% alertness   (no face => 0)

    Everything is measured relative to the person's own calibration baseline.
    """
    PERCLOS_WINDOW = 20.0   # seconds of eye history

    def __init__(self, baseline):
        self.b = baseline
        self.score = 100.0
        self.last_t = None
        self.start_t = None
        self.closed = deque()          # (t, eyes_closed)
        self.blinks = deque()          # times of completed blinks
        self.close_start = None
        self.yawn_start = None
        self.yawn_fired = False
        self.last_yawn_t = -1e9
        self.gaze_s = 100.0
        self.close_thr = clamp(baseline.blink + 0.35, 0.45, 0.80)
        self.yawn_thr = max(0.45, baseline.jaw + 0.30)

    def update(self, f, now):
        if self.start_t is None:
            self.start_t = now
        dt = 0.033 if self.last_t is None else clamp(now - self.last_t, 0.001, 0.5)
        self.last_t = now

        yawn_event = False
        perclos = 0.0
        blink_rate = 0.0
        gaze_s = head_s = eyes_s = yawn_s = blink_s = 0.0
        yaw_dev = pitch_dev = 0.0

        if f.face:
            b = self.b
            closed = f.blink >= self.close_thr

            # --- eye closure history (PERCLOS) + blink counting
            self.closed.append((now, closed))
            while self.closed and now - self.closed[0][0] > self.PERCLOS_WINDOW:
                self.closed.popleft()
            perclos = sum(1 for _, c in self.closed if c) / len(self.closed)

            run = 0.0
            if closed:
                if self.close_start is None:
                    self.close_start = now
                run = now - self.close_start
            elif self.close_start is not None:
                if 0.04 <= now - self.close_start <= 0.6:
                    self.blinks.append(now)
                self.close_start = None
            while self.blinks and now - self.blinks[0] > 60.0:
                self.blinks.popleft()

            # --- gaze (hold last value while eyes are shut: iris data unreliable)
            if closed:
                # short blink: hold last value; eyes shut 1-3 s+ means not attending
                gaze_s = self.gaze_s * (1.0 - ramp(run, 1.0, 3.0))
            else:
                gdev = math.hypot(f.gaze_h - b.gaze_h, f.gaze_v - b.gaze_v)
                gaze_s = 100.0 * (1.0 - ramp(gdev, 0.12, 0.45))
                self.gaze_s = gaze_s

            # --- head pose (catches looking down at a phone or turning away)
            yaw_dev = abs(angdiff(f.yaw, b.yaw))
            pitch_dev = abs(angdiff(f.pitch, b.pitch))
            head_s = 100.0 * (1.0 - max(ramp(yaw_dev, 12, 38), ramp(pitch_dev, 10, 30)))

            # --- eyes: PERCLOS plus a penalty for one long closure (micro-sleep)
            eyes_s = 100.0 * (1.0 - ramp(perclos, 0.06, 0.30))
            eyes_s = min(eyes_s, 100.0 * (1.0 - ramp(run, 1.0, 3.0)))

            # --- yawn: mouth wide open for >= 1.2 s
            if f.jaw >= self.yawn_thr:
                if self.yawn_start is None:
                    self.yawn_start = now
                if not self.yawn_fired and now - self.yawn_start >= 1.2:
                    self.yawn_fired = True
                    self.last_yawn_t = now
                    yawn_event = True
            else:
                self.yawn_start = None
                self.yawn_fired = False
            yawn_s = 0.0 if now - self.last_yawn_t < 8.0 else 100.0

            # --- blink rate (very high = eye strain / fatigue); needs warm-up
            tracked = now - self.start_t
            blink_rate = len(self.blinks) * 60.0 / max(1.0, min(60.0, tracked))
            blink_s = 100.0 if tracked < 20.0 else 100.0 * (1.0 - ramp(blink_rate, 28, 45))

            attention = 0.5 * gaze_s + 0.5 * head_s
            alertness = 0.6 * eyes_s + 0.25 * yawn_s + 0.15 * blink_s
            raw = 0.7 * attention + 0.3 * alertness
        else:
            self.close_start = None
            self.yawn_start = None
            self.yawn_fired = False
            attention = alertness = raw = 0.0

        # Fall quickly (so nudges feel responsive), rise slowly (no flicker).
        tau = 1.5 if raw < self.score else 3.0
        self.score += (raw - self.score) * (1.0 - math.exp(-dt / tau))
        self.score = clamp(self.score, 0.0, 100.0)

        return dict(
            score=self.score, raw=raw, face=f.face,
            attention=attention, alertness=alertness,
            gaze_s=gaze_s, head_s=head_s, eyes_s=eyes_s,
            yawn_s=yawn_s, blink_s=blink_s,
            perclos=perclos, blink_rate=blink_rate,
            yaw_dev=yaw_dev, pitch_dev=pitch_dev,
            yawn_event=yawn_event,
        )


# ------------------------------------------------------------ drift logic
class DriftTracker:
    """Turns the score stream into events: drifts, nudges, break prompts.

    Events returned by update(): DRIFT_START, NUDGE, DRIFT_END, BREAK_SUGGESTED.
    Hysteresis (different enter/exit levels + hold times) stops flicker.
    """

    def __init__(self, low=50.0, high=65.0, hold_start=4.0, hold_end=2.5,
                 nudge_cooldown=25.0, break_after=25 * 60.0, now=0.0):
        self.low, self.high = low, high
        self.hold_start, self.hold_end = hold_start, hold_end
        self.nudge_cooldown = nudge_cooldown
        self.break_after = break_after
        self.in_drift = False
        self.drifts = 0
        self.below_since = None
        self.above_since = None
        self.last_nudge = -1e9
        self.streak_start = None
        self.work_start = now
        self.last_break_prompt = -1e9
        self.yawn_times = deque()

    def note_yawn(self, now):
        self.yawn_times.append(now)

    def reset_break(self, now):
        self.work_start = now
        self.yawn_times.clear()

    def streak(self, now):
        return 0.0 if self.streak_start is None else now - self.streak_start

    def update(self, score, now):
        ev = []

        # focus streak (enter >= high, survives dips down to 55)
        if score >= self.high:
            if self.streak_start is None:
                self.streak_start = now
        elif score < 55.0:
            self.streak_start = None

        if not self.in_drift:
            if score < self.low:
                if self.below_since is None:
                    self.below_since = now
                if now - self.below_since >= self.hold_start:
                    self.in_drift = True
                    self.drifts += 1
                    self.above_since = None
                    ev.append("DRIFT_START")
                    if now - self.last_nudge >= self.nudge_cooldown:
                        ev.append("NUDGE")
                        self.last_nudge = now
            else:
                self.below_since = None
        else:
            if score >= self.high:
                if self.above_since is None:
                    self.above_since = now
                if now - self.above_since >= self.hold_end:
                    self.in_drift = False
                    self.below_since = None
                    self.above_since = None
                    ev.append("DRIFT_END")
            else:
                self.above_since = None
                if now - self.last_nudge >= self.nudge_cooldown * 1.5:
                    ev.append("NUDGE")
                    self.last_nudge = now

        # break suggestions: long work block, or several yawns in 10 minutes
        while self.yawn_times and now - self.yawn_times[0] > 600.0:
            self.yawn_times.popleft()
        if now - self.last_break_prompt >= 600.0:
            if now - self.work_start >= self.break_after or len(self.yawn_times) >= 3:
                ev.append("BREAK_SUGGESTED")
                self.last_break_prompt = now
        return ev
