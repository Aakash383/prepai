"""Real-interview mode: records quietly in the background, reviews afterwards.

While your Meet / Zoom / Teams call runs, Tether captures:
  * YOUR microphone                -> your answers          ("You")
  * the call audio (system output) -> the interviewer       ("Interviewer")   [Windows loopback]
  * your webcam (if the camera is free) -> eye contact, posture, head movement

Because the two audio sources are separate, questions and answers are matched
automatically. Afterwards every answer is checked for relevance to the job,
delivery and body language, and a full review is written.

Nothing is stored except the final transcript and numbers: audio stays in memory.
Use headphones, otherwise the interviewer's voice leaks into your microphone.
"""
import difflib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

import coach
import speech
import tether
from behavior import BehaviorStats
from interview import SESSIONS_DIR, aggregate
from scoring import Calibrator, FocusScorer

SR = speech.SR
MIN_ANSWER_WORDS = 15
MAX_ANSWERS = 12


def rms(a):
    return float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0


class TimedBuffer:
    """Stores 16 kHz mono audio as int16, placed on a wall-clock timeline (gaps become silence)."""

    def __init__(self):
        self.chunks, self.n = [], 0

    def add(self, x, t_rel):
        start = int(t_rel * SR) - len(x)
        if start > self.n:
            self.chunks.append(np.zeros(start - self.n, np.int16))
            self.n = start
        arr = np.clip(x * 32767.0, -32768, 32767).astype(np.int16)
        self.chunks.append(arr)
        self.n += len(arr)

    def to_float(self, total_samples):
        data = np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.int16)
        if len(data) < total_samples:
            data = np.concatenate([data, np.zeros(total_samples - len(data), np.int16)])
        return data.astype(np.float32) / 32768.0


# ---------------------------------------------------------------- dialogue
def is_echo(mine, others):
    """True if one of my segments is just the interviewer's voice leaking into my mic."""
    s, e, _, text = mine
    for os_, oe, _, otext in others:
        overlap = min(e, oe) - max(s, os_)
        if overlap > 0.5 * min(e - s, oe - os_) and \
                difflib.SequenceMatcher(None, text.lower(), otext.lower()).ratio() > 0.5:
            return True
    return False


def build_turns(tr_you, tr_int):
    others = [(s, e, "Interviewer", t) for s, e, t in tr_int["segs"] if t]
    mine = [(s, e, "You", t) for s, e, t in tr_you["segs"] if t]
    mine = [m for m in mine if not is_echo(m, others)]
    turns = []
    for s, e, who, t in sorted(others + mine):
        if turns and turns[-1]["speaker"] == who and s - turns[-1]["end"] < 3.5:
            turns[-1]["end"] = e
            turns[-1]["text"] += " " + t
        else:
            turns.append(dict(speaker=who, start=s, end=e, text=t))
    return turns


def pair_turns(turns):
    """Interviewer turn(s) followed by a substantive answer from you."""
    pairs, pending = [], []
    for t in turns:
        if t["speaker"] == "Interviewer":
            pending.append(t)
        elif len(t["text"].split()) >= MIN_ANSWER_WORDS:
            q = " ".join(p["text"] for p in pending)[-700:] or "(No question detected before this answer)"
            pairs.append(dict(question=q, answer=t, q_end=pending[-1]["end"] if pending else None))
            pending = []
    return pairs


# ---------------------------------------------------------------- session
class LiveSession:
    CAL_SECONDS = 10.0

    def __init__(self, ctx, role, stt, use_camera=True, camera_index=0, model_path=None,
                 open_report=True):
        self.ctx, self.role, self.stt = ctx, role, stt
        self.use_camera, self.camera_index = use_camera, camera_index
        self.model_path = model_path or os.path.join(tether.HERE, "face_landmarker.task")
        self.open_report = open_report
        self.state, self.status, self.error = "idle", "Ready", None
        self.mic_level = self.sys_level = 0.0
        self.camera_note = "Camera: off" if not use_camera else "Camera: starting..."
        self.audio_note = "Call audio: starting..."
        self.has_camera = self.has_loopback = False
        self.t0 = None
        self.report_path = None
        self.mic, self.sys = TimedBuffer(), TimedBuffer()
        self.records = []              # (t, Features, gaze_s, head_s, blink_rate)
        self.base_face_w = 0.0
        self._stop, self._cal_done = threading.Event(), threading.Event()
        self._threads, self._mic_stream = [], None

    # ---- public
    def elapsed(self):
        return time.monotonic() - self.t0 if self.t0 and self.state == "recording" else 0.0

    def start(self):
        self.state = "calibrating"
        self.status = "Calibrating: look at the call window the way you normally will..."
        try:
            self._open_mic()
        except Exception as e:
            self.state, self.error = "error", f"Cannot open the microphone: {e}"
            return
        for fn in (self._loopback, self._camera, self._begin):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        if self.state == "calibrating":
            self._stop.set()
            self._close_mic()
            self.state, self.status = "idle", "Cancelled"
        elif self.state == "recording":
            self.state, self.status = "processing", "Stopping..."
            self._stop.set()
            threading.Thread(target=self._process, daemon=True).start()

    # ---- capture
    def _open_mic(self):
        import sounddevice as sd

        def cb(indata, frames, t, status):
            x = indata[:, 0]
            self.mic_level = float(np.sqrt(np.mean(x ** 2)))
            if self.state == "recording":
                self.mic.add(x, time.monotonic() - self.t0)

        self._mic_stream = sd.InputStream(channels=1, samplerate=SR, blocksize=1600,
                                          dtype="float32", callback=cb)
        self._mic_stream.start()

    def _close_mic(self):
        try:
            if self._mic_stream:
                self._mic_stream.stop()
                self._mic_stream.close()
        except Exception:
            pass

    def _loopback(self):
        try:
            import soundcard as sc
            spk = sc.default_speaker()
            lb = sc.get_microphone(id=str(spk.name), include_loopback=True)
            with lb.recorder(samplerate=48000, channels=2) as rec:
                self.has_loopback = True
                self.audio_note = f"Call audio: capturing '{spk.name}'"
                while not self._stop.is_set():
                    data = rec.record(numframes=4800)                    # 0.1 s
                    mono = data.mean(axis=1)
                    mono = mono[: len(mono) // 3 * 3].reshape(-1, 3).mean(axis=1)   # 48k -> 16k
                    self.sys_level = float(np.sqrt(np.mean(mono ** 2)))
                    if self.state == "recording":
                        self.sys.add(mono.astype(np.float32), time.monotonic() - self.t0)
        except Exception as e:
            self.has_loopback = False
            self.audio_note = ("Call audio not available on this computer (mic only). "
                               f"Reason: {str(e)[:80]}")

    def _camera(self):
        cap = lm = None
        try:
            if not self.use_camera:
                self._cal_done.set()
                return
            import cv2
            cap = (cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW) if os.name == "nt"
                   else cv2.VideoCapture(self.camera_index))
            if not cap.isOpened() or not cap.read()[0]:
                self.camera_note = "Camera busy or unavailable: audio-only review"
                self._cal_done.set()
                return
            try:
                model = tether.ensure_model(self.model_path)
            except SystemExit as e:
                self.camera_note = f"Face model download failed: audio-only ({e})"
                self._cal_done.set()
                return
            mp, lm = tether.make_landmarker(model)
            calib, scorer = Calibrator(self.CAL_SECONDS), None
            boot, last, last_ts = time.monotonic(), 0.0, -1
            self.camera_note = "Camera: calibrating..."
            while not self._stop.is_set():
                if not cap.grab():
                    break
                mono = time.monotonic()
                if mono - last < 0.16:                       # ~6 fps is plenty
                    continue
                last = mono
                ok, frame = cap.retrieve()
                if not ok:
                    continue
                ts = max(int((mono - boot) * 1000), last_ts + 1)
                last_ts = ts
                res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                   data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), ts)
                f = tether.extract_features(res, 0.0)
                if scorer is None:
                    calib.add(f, mono)
                    if calib.done(mono):
                        base = calib.baseline()
                        scorer, self.base_face_w = FocusScorer(base), base.face_w
                        self.has_camera = True
                        self.camera_note = "Camera: analysing body language"
                        self._cal_done.set()
                else:
                    out = scorer.update(f, mono - boot)
                    if self.state == "recording":
                        self.records.append((time.monotonic() - self.t0, f, out["gaze_s"],
                                             out["head_s"], out["blink_rate"]))
        except Exception as e:
            self.camera_note = f"Camera error: audio-only ({str(e)[:60]})"
            self._cal_done.set()
        finally:
            if cap is not None:
                cap.release()
            if lm is not None:
                lm.close()

    def _begin(self):
        if not self._cal_done.wait(timeout=60):
            self.camera_note = "Could not find your face to calibrate: audio-only review"
        if self._stop.is_set() or self.state != "calibrating":
            return
        self.t0 = time.monotonic()
        self.state = "recording"
        self.status = "Recording. You can minimize this window and join your call."

    # ---- analysis
    def _transcribe(self, audio):
        if rms(audio) < 0.0015:
            return dict(text="", lines=[], words=[], segs=[])
        return self.stt.transcribe(audio, vad=True)

    def _window_behavior(self, a, b):
        recs = [r for r in self.records if a <= r[0] <= b]
        if len(recs) < 5:
            return None
        bs, prev = BehaviorStats(self.base_face_w), recs[0][0] - 0.16
        for t, f, g, h, br in recs:
            bs.add(f, dict(gaze_s=g, head_s=h, blink_rate=br), t - prev)
            prev = t
        return bs.summary()

    def _process(self):
        try:
            self._close_mic()
            total = max(self.mic.n, self.sys.n)
            if total / SR < 20:
                raise ValueError("The recording was shorter than 20 seconds.")
            mic_a, sys_a = self.mic.to_float(total), self.sys.to_float(total)
            separated = self.has_loopback and rms(sys_a) > 0.001

            self.status = "Transcribing your voice (runs on this laptop)..."
            tr_you = self._transcribe(mic_a)
            if not tr_you["text"]:
                raise ValueError("No speech was detected on your microphone. Check which "
                                 "microphone Windows is using.")
            tr_int = None
            if separated:
                self.status = "Transcribing the interviewer..."
                tr_int = self._transcribe(sys_a)
                separated = bool(tr_int["segs"])

            if separated:
                turns = build_turns(tr_you, tr_int)
                answers = self._answers_from_pairs(pair_turns(turns), tr_you, mic_a)
                dialogue = [[t["start"], t["speaker"], t["text"]] for t in turns]
                if not answers:
                    raise ValueError("No full answers were detected (answers need about 15+ words).")
                self.status = "Reviewing your answers with Claude..."
                self._evaluate(answers)
            else:
                answers = [dict(q_idx=0, attempt=1, question="Live interview", qtype="live",
                                duration=total / SR, behavior=self._window_behavior(0, 1e9),
                                transcript=tr_you["text"], lines=tr_you["lines"],
                                speech=speech.analyze_speech(tr_you, mic_a), eval=None,
                                status="done", error=None, t_start=0.0)]
                dialogue = None

            o_sp, o_bh = aggregate(answers)
            self.status = "Writing your review with Claude..."
            review, review_error = {}, None
            try:
                review = coach.final_review(self.ctx, self.role, "live", answers, o_sp, o_bh,
                                            None, separated=separated)
            except Exception as e:
                review_error = str(e)
            session = dict(version=2, created=datetime.now().isoformat(timespec="seconds"),
                           role=self.role, mode="live", separated=separated, brief=None,
                           answers=answers, dialogue=dialogue, overall_speech=o_sp,
                           overall_behavior=o_bh, review=review, review_error=review_error,
                           prev_score=None, audio_note=self.audio_note, camera_note=self.camera_note)
            import interview_report
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            stem = os.path.join(SESSIONS_DIR, f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            with open(stem + ".json", "w", encoding="utf-8") as f:
                json.dump(session, f, indent=1, default=str)
            self.report_path = interview_report.save(session, stem + ".html",
                                                     open_browser=self.open_report)
            self.state, self.status = "done", "Review ready."
        except Exception as e:
            self.state, self.error, self.status = "error", str(e), "Something went wrong"

    def _answers_from_pairs(self, pairs, tr_you, mic_a):
        if len(pairs) > MAX_ANSWERS:                       # keep the longest answers, in order
            keep = sorted(sorted(pairs, key=lambda p: -len(p["answer"]["text"]))[:MAX_ANSWERS],
                          key=lambda p: p["answer"]["start"])
            pairs = keep
        answers = []
        for i, p in enumerate(pairs):
            a = p["answer"]
            words = [(w, s - a["start"], e - a["start"]) for w, s, e in tr_you["words"]
                     if s >= a["start"] - 0.2 and e <= a["end"] + 0.2]
            if not words:
                continue
            audio = mic_a[int(a["start"] * SR):int(a["end"] * SR)]
            win = dict(text=a["text"], words=words, lines=[], segs=[])
            sp = speech.analyze_speech(win, audio)
            sp["duration"] = a["end"] - a["start"]
            if p["q_end"] is not None:
                sp["latency"] = max(0.0, a["start"] - p["q_end"])    # real response delay
            answers.append(dict(
                q_idx=len(answers), attempt=1, question=p["question"], qtype="live",
                duration=a["end"] - a["start"], t_start=a["start"], transcript=a["text"], lines=[],
                speech=sp, behavior=self._window_behavior(a["start"], a["end"]), eval=None,
                status="evaluating", error=None))
        return answers

    def _evaluate(self, answers):
        done = [0]

        def one(rec):
            try:
                rec["eval"] = coach.evaluate_answer(self.ctx, rec["question"], "live", rec["transcript"],
                                                    rec["speech"], rec["behavior"], 1)
                rec["status"] = "done"
            except Exception as e:
                rec["error"], rec["status"] = str(e), "error"
            done[0] += 1
            self.status = f"Reviewing your answers with Claude ({done[0]}/{len(answers)})..."

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, answers))
