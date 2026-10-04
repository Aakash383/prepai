"""Body-language statistics for the interview coach.

Everything here is an *estimate from a webcam* of observable behaviour
(where the face points, how much the head moves, how close you sit).
It does not claim to read emotions or personality.
"""
from collections import defaultdict

from scoring import angdiff

EYE_CONTACT_MIN = 60.0     # gaze and head scores both must be at least this
MOTION_DEADBAND = 0.15     # degrees; ignore landmark jitter after smoothing


class BehaviorStats:
    def __init__(self, base_face_w=0.0):
        self.base_face_w = base_face_w
        self.reset()

    def reset(self):
        self.total = 0.0
        self.face_t = self.ec_t = self.smile_t = self.tense_t = 0.0
        self.back_t = self.close_t = 0.0
        self.motion = 0.0
        self.ema = None
        self.away_run = 0.0
        self.away_events = 0
        self.longest_away = 0.0
        self.blink_rate = 0.0
        self.by_minute = defaultdict(lambda: [0.0, 0.0])   # minute -> [seconds, eye-contact seconds]

    def _end_away(self):
        if self.away_run >= 2.0:
            self.away_events += 1
            self.longest_away = max(self.longest_away, self.away_run)
        self.away_run = 0.0

    def add(self, f, out, dt):
        dt = min(dt, 0.25)
        bucket = self.by_minute[int(self.total // 60)]
        self.total += dt
        bucket[0] += dt

        eye_contact = f.face and out["gaze_s"] >= EYE_CONTACT_MIN and out["head_s"] >= EYE_CONTACT_MIN
        if eye_contact:
            self.ec_t += dt
            bucket[1] += dt
            self._end_away()
        else:
            self.away_run += dt

        if not f.face:
            self.ema = None
            return
        self.face_t += dt
        self.blink_rate = out["blink_rate"]
        if f.smile >= 0.35:
            self.smile_t += dt
        if f.brow >= 0.5:
            self.tense_t += dt

        # head motion from a smoothed pose track
        if self.ema is None:
            self.ema = [f.yaw, f.pitch]
        else:
            ny = self.ema[0] + 0.3 * angdiff(f.yaw, self.ema[0])
            np_ = self.ema[1] + 0.3 * angdiff(f.pitch, self.ema[1])
            step = abs(angdiff(ny, self.ema[0])) + abs(angdiff(np_, self.ema[1]))
            if step > MOTION_DEADBAND:
                self.motion += step
            self.ema = [ny, np_]

        # posture: face size relative to calibration (smaller = leaning back/slouching)
        if self.base_face_w > 0 and f.face_w > 0:
            ratio = f.face_w / self.base_face_w
            if ratio < 0.88:
                self.back_t += dt
            elif ratio > 1.2:
                self.close_t += dt

    def summary(self):
        self._end_away()
        t = max(self.total, 1e-6)
        ft = max(self.face_t, 1e-6)
        minutes = [100.0 * ec / s if s > 0 else 0.0
                   for _, (s, ec) in sorted(self.by_minute.items())]
        return dict(
            seconds=self.total,
            face_visible_pct=100.0 * self.face_t / t,
            eye_contact_pct=100.0 * self.ec_t / t,
            smile_pct=100.0 * self.smile_t / ft,
            tension_pct=100.0 * self.tense_t / ft,
            head_motion_dps=self.motion / ft,
            lean_back_pct=100.0 * self.back_t / ft,
            too_close_pct=100.0 * self.close_t / ft,
            blink_rate=self.blink_rate,
            away_events=self.away_events,
            longest_away_s=self.longest_away,
            ec_by_minute=minutes,
        )
