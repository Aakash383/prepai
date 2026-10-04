#!/usr/bin/env python3
"""Tether Interview Coach.

Give it your resume, the job role and the job description. It writes interview
questions for THAT role, listens and watches while you answer (local speech
recognition + webcam), shows quick feedback after every answer, and finishes with
a full review: content, delivery, body language, top fixes and a practice plan.

    python interview.py --resume resume.pdf --role "Backend Engineer" --jd jd.txt
    python interview.py --live      # record a real interview for review afterwards

Keys:  SPACE start/stop answer   N next question   R try the same question again
       S skip question           Q finish and open the review

Privacy: audio stays in memory and is transcribed locally by Whisper; no video is stored.
Only text (resume, job description, transcript, numbers) is sent to the Gemini API.
"""
import argparse
import glob
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime

import cv2
import numpy as np

import coach
import llm
import speech
import tether
from behavior import BehaviorStats
from scoring import Calibrator, FocusScorer, clamp

VID_W, VID_H = tether.VID_W, tether.VID_H
PANEL_W, STRIP_H = 430, 110
FONT = cv2.FONT_HERSHEY_SIMPLEX
GREY, WHITE = (150, 150, 160), (240, 240, 245)
SESSIONS_DIR = "interview_sessions"

DEFAULT_QUESTIONS = [
    dict(q="Tell me about yourself and why you are interested in this role.", type="intro"),
    dict(q="Describe a project you are proud of and your specific contribution.", type="behavioral"),
    dict(q="Tell me about a time you faced a difficult problem and how you solved it.", type="behavioral"),
    dict(q="Which skills from your background are the best match for this job?", type="resume"),
    dict(q="Where do you see gaps in your experience for this role, and how will you close them?", type="gap"),
]


# ============================================================ helpers
def check_versions():
    """Return a message if any file in this folder is an older copy, else None."""
    import inspect
    from scoring import Baseline, Features
    stale = []
    if not hasattr(Baseline(), "face_w") or not hasattr(Features(), "smile"):
        stale.append("scoring.py")
    if "vad" not in inspect.signature(speech.Transcriber.transcribe).parameters:
        stale.append("speech.py")
    if "separated" not in inspect.signature(coach.final_review).parameters:
        stale.append("coach.py")
    if "face_w" not in inspect.getsource(tether.extract_features):
        stale.append("tether.py")
    if stale:
        return ("These files are out of date: " + ", ".join(stale) +
                ". Download ALL files again from the latest version and overwrite the old ones.")
    return None


def safe(text):
    """OpenCV fonts are ASCII-only: replace common Unicode punctuation."""
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'),
                 ("\u2013", "-"), ("\u2014", "-"), ("\u2026", "..."), ("\u2022", "-")):
        text = text.replace(a, b)
    return text.encode("ascii", "ignore").decode()


def wrap(text, width_px, scale=0.5, thick=1):
    lines, cur = [], ""
    for w in safe(text).split():
        trial = (cur + " " + w).strip()
        if cv2.getTextSize(trial, FONT, scale, thick)[0][0] <= width_px:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def read_multiline(prompt):
    print(f"{prompt} (finish with a line containing only END):")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def gather_inputs(args):
    path = args.resume or input("Resume file (.pdf / .docx / .txt): ")
    resume = coach.load_resume(path)
    role = args.role or input("Job role you are interviewing for: ").strip()
    if args.jd and os.path.exists(args.jd):
        with open(args.jd, encoding="utf-8", errors="ignore") as f:
            jd = f.read().strip()
    elif args.jd:
        jd = args.jd.strip()
    else:
        jd = read_multiline("Paste the job description")
    if len(jd) < 40:
        sys.exit("[ERR] The job description looks too short.")
    return resume, role, jd


def aggregate(answers):
    """Duration-weighted overall speech and camera numbers."""
    sp = [a["speech"] for a in answers if a.get("speech") and a["speech"]["words"] > 0]
    bh = [a["behavior"] for a in answers if a.get("behavior")]
    o_sp = o_bh = None
    if sp:
        words = sum(s["words"] for s in sp)
        span = sum(s["words"] / s["wpm"] * 60 for s in sp if s["wpm"] > 0) or 1.0
        fill = sum(s["fillers"] for s in sp)
        tops = {}
        for s in sp:
            for w, c in s["top_fillers"]:
                tops[w] = tops.get(w, 0) + c
        o_sp = dict(duration=sum(s["duration"] for s in sp), words=words, wpm=words / span * 60,
                    latency=sum(s["latency"] for s in sp) / len(sp), fillers=fill,
                    filler_per_min=fill / (span / 60), top_fillers=sorted(tops.items(), key=lambda x: -x[1])[:4],
                    hedges=sum(s["hedges"] for s in sp), long_pauses=sum(s["long_pauses"] for s in sp),
                    longest_pause=max(s["longest_pause"] for s in sp),
                    energy_cv=sum(s["energy_cv"] for s in sp) / len(sp))
    if bh:
        tot = sum(b["seconds"] for b in bh) or 1.0
        wavg = lambda k: sum(b[k] * b["seconds"] for b in bh) / tot
        o_bh = {k: wavg(k) for k in ("face_visible_pct", "eye_contact_pct", "smile_pct", "tension_pct",
                                     "head_motion_dps", "lean_back_pct", "too_close_pct", "blink_rate")}
        o_bh.update(seconds=tot, away_events=sum(b["away_events"] for b in bh),
                    longest_away_s=max(b["longest_away_s"] for b in bh),
                    ec_by_minute=[m for b in bh for m in b["ec_by_minute"]])
    return o_sp, o_bh


def previous_session(role):
    for path in sorted(glob.glob(os.path.join(SESSIONS_DIR, "session_*.json")), reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("role", "").lower() == role.lower() and d.get("mode") != "live":
                score = (d.get("review") or {}).get("overall_score")
                if score is not None:
                    return score
        except Exception:
            continue
    return None


# ============================================================ session logic
class InterviewApp:
    """All interview state and logic. Camera/UI code lives in run()."""

    def __init__(self, args, ctx, role, brief, questions, stt, recorder):
        self.args, self.ctx, self.role, self.brief = args, ctx, role, brief
        self.questions, self.stt, self.recorder = questions, stt, recorder
        self.live = args.live
        self.answers, self.threads = [], []
        self.q_idx, self.attempt = 0, 1
        self.state = "CALIB"
        self.behavior = BehaviorStats()
        self.ans_start = 0.0
        self.final_status = ""
        self.report_path = None
        self.done_at = None

    # ---- state transitions
    def enter_ready(self):
        self.state = "LIVE_READY" if self.live else "READY"
        if not self.live and not self.args.no_voice:
            speech.speak(self.questions[self.q_idx]["q"])

    def start_answer(self):
        self.behavior.reset()
        self.recorder.start()
        self.ans_start = time.monotonic()
        self.state = "LIVE_REC" if self.live else "ANSWERING"

    def stop_answer(self):
        audio = self.recorder.stop()
        q = ({"q": "Live interview", "type": "live"} if self.live else self.questions[self.q_idx])
        rec = dict(q_idx=self.q_idx, attempt=self.attempt, question=q["q"], qtype=q.get("type", ""),
                   duration=len(audio) / speech.SR, behavior=self.behavior.summary(),
                   status="transcribing", transcript="", lines=[], speech=None, eval=None, error=None)
        self.answers.append(rec)
        t = threading.Thread(target=self._process, args=(rec, audio), daemon=True)
        t.start()
        self.threads.append(t)
        self.state = "FEEDBACK" if not self.live else "FINALIZING"
        if self.live:
            self.begin_finalize()

    def _process(self, rec, audio):
        try:
            if len(audio) < speech.SR * 1.5:
                raise ValueError("Recording too short")
            tr = self.stt.transcribe(audio)
            rec.update(transcript=tr["text"], lines=tr["lines"], speech=speech.analyze_speech(tr, audio))
            if not self.live:
                rec["status"] = "evaluating"
                rec["eval"] = coach.evaluate_answer(self.ctx, rec["question"], rec["qtype"], tr["text"],
                                                    rec["speech"], rec["behavior"], rec["attempt"])
            rec["status"] = "done"
        except Exception as e:
            rec["error"], rec["status"] = str(e), "error"

    def next_question(self):
        self.q_idx += 1
        self.attempt = 1
        if self.q_idx >= len(self.questions):
            self.begin_finalize()
        else:
            self.enter_ready()

    def retry_question(self):
        self.attempt += 1
        self.enter_ready()

    def skip_question(self):
        self.next_question()

    def begin_finalize(self):
        if not self.answers:
            self.state = "DONE"
            self.done_at = time.monotonic()
            return
        self.state = "FINALIZING"
        self.final_status = "Finishing transcription..."
        threading.Thread(target=self._finalize, daemon=True).start()

    def _finalize(self):
        for t in self.threads:
            t.join()
        mode = "live" if self.live else "practice"
        usable = [a for a in self.answers if a.get("transcript")]
        o_sp, o_bh = aggregate(self.answers)
        prev = previous_session(self.role) if not self.live else None
        review, review_error = {}, None
        self.final_status = "Writing your review with Gemini..."
        if usable:
            try:
                review = coach.final_review(self.ctx, self.role, mode, usable, o_sp, o_bh, prev)
            except Exception as e:
                review_error = str(e)
        else:
            review_error = "No answer could be transcribed (recording too short or microphone issue)."
        session = dict(version=1, created=datetime.now().isoformat(timespec="seconds"), role=self.role,
                       mode=mode, brief=self.brief, answers=self.answers, overall_speech=o_sp,
                       overall_behavior=o_bh, review=review, review_error=review_error, prev_score=prev)
        try:
            import interview_report
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            stem = os.path.join(SESSIONS_DIR, f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            with open(stem + ".json", "w", encoding="utf-8") as f:
                json.dump(session, f, indent=1, default=str)
            self.report_path = interview_report.save(session, stem + ".html", open_browser=not self.args.no_report)
            print(f"[OK] Review saved: {self.report_path}")
        except Exception as e:
            print(f"[ERR] Could not write the report: {e}")
        self.state = "DONE"
        self.done_at = time.monotonic()

    # ---- text for the panel
    def feedback_lines(self):
        """[(text, colour, scale)] describing the latest answer."""
        if not self.answers:
            return []
        rec = self.answers[-1]
        if rec["status"] == "transcribing":
            return [("Transcribing your answer on this laptop...", GREY, 0.55)]
        if rec["status"] == "error":
            return [("Could not analyse that answer:", tether.COL_BAD, 0.55), (rec["error"] or "", GREY, 0.5)]
        sp, bh = rec["speech"], rec["behavior"]
        L = [(f"{sp['wpm']:.0f} wpm  |  {sp['fillers']} fillers  |  eye contact {bh['eye_contact_pct']:.0f}%",
              WHITE, 0.5)]
        if rec["status"] == "evaluating":
            return L + [("Gemini is reviewing your answer...", GREY, 0.55)]
        ev = rec["eval"] or {}
        score = ev.get("score")
        if isinstance(score, (int, float)):
            prev = [a for a in self.answers[:-1] if a["q_idx"] == rec["q_idx"] and isinstance(
                (a.get("eval") or {}).get("score"), (int, float))]
            tail = f"   ({score - prev[-1]['eval']['score']:+.0f} vs last try)" if prev else ""
            L.insert(0, (f"Answer score: {score:.0f}/100{tail}", tether.score_color(score), 0.8))
        rel = ev.get("relevance") or {}
        if rel.get("verdict"):
            L.append((f"Relevance to the role: {rel['verdict']}", WHITE, 0.5))
        for tip in ev.get("improvements", [])[:3]:
            L.append(("- " + tip, WHITE, 0.5))
        return L

    # ---- camera + UI loop
    def run(self):
        args = self.args
        model = tether.ensure_model(args.model)
        mp, landmarker = tether.make_landmarker(model)
        cap = tether.open_camera(args.camera)
        calib = Calibrator(args.calib)
        boot = time.monotonic()
        last_ts, last_frame, last_hist = -1, boot, boot
        scorer = None
        clock = 0.0
        ec_roll, att_hist = deque(), []
        low_ec_since = quiet_since = None
        att = 100.0
        feats = None
        lean = 1.0
        fatal = None

        while True:
            ok, frame = cap.read()
            if not ok:
                fatal = "Camera stopped delivering frames."
                break
            frame = cv2.flip(frame, 1)
            if frame.shape[1] != VID_W or frame.shape[0] != VID_H:
                frame = cv2.resize(frame, (VID_W, VID_H))
            mono = time.monotonic()
            dt = min(0.25, mono - last_frame)
            last_frame = mono
            ts = max(int((mono - boot) * 1000), last_ts + 1)
            last_ts = ts
            res = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), ts)
            feats = tether.extract_features(res, self.recorder.level)
            clock += dt

            if self.state == "CALIB":
                calib.add(feats, mono)
                if calib.done(mono):
                    base = calib.baseline()
                    scorer = FocusScorer(base)
                    self.behavior = BehaviorStats(base.face_w)
                    self.base_face_w = base.face_w
                    self.state = "PREP"
            if self.state == "PREP":
                if self.stt.ready.is_set() and self.stt.model is None:
                    fatal = f"Speech model failed to load: {self.stt.error}"
                    break
                if self.prep_ready():
                    self.enter_ready()

            out = scorer.update(feats, clock) if scorer else None
            if out:
                att = out["attention"] if feats.face else 0.0
                ec_now = feats.face and out["gaze_s"] >= 60 and out["head_s"] >= 60
                ec_roll.append((mono, ec_now))
                while ec_roll and mono - ec_roll[0][0] > 8:
                    ec_roll.popleft()
                if getattr(self, "base_face_w", 0) > 0 and feats.face_w > 0:
                    lean = feats.face_w / self.base_face_w
                if mono - last_hist >= 1.0:
                    att_hist.append((clock, att))
                    del att_hist[:-200]
                    last_hist = mono
                if self.state in ("ANSWERING", "LIVE_REC"):
                    self.behavior.add(feats, out, dt)
            ec_pct = 100.0 * sum(1 for _, e in ec_roll if e) / len(ec_roll) if ec_roll else 0.0

            hint = ""
            if self.state in ("ANSWERING", "LIVE_REC") and out:
                low_ec_since = (low_ec_since or mono) if ec_pct < 35 and len(ec_roll) > 60 else None
                quiet_since = (quiet_since or mono) if self.recorder.level < 0.008 else None
                if not feats.face:
                    hint = "Stay in frame"
                elif low_ec_since and mono - low_ec_since > 4:
                    hint = "Look at the camera"
                elif lean < 0.88:
                    hint = "Sit up and lean in slightly"
                elif quiet_since and mono - quiet_since > 6 and not self.live:
                    hint = "Start with your main point"

            ui = dict(
                state=self.state, role=self.role, face=feats.face, ec_pct=ec_pct, lean=lean,
                mic=self.recorder.level, hint=hint, hist=att_hist, clock=clock,
                cal_progress=calib.progress(mono) if self.state == "CALIB" else 1.0,
                q_idx=self.q_idx, n_q=len(self.questions), attempt=self.attempt,
                question=self.questions[self.q_idx] if not self.live and self.q_idx < len(self.questions) else None,
                elapsed=(mono - self.ans_start) if self.state in ("ANSWERING", "LIVE_REC") else 0.0,
                feedback=self.feedback_lines() if self.state == "FEEDBACK" else [],
                final_status=self.final_status, live=self.live, report=self.report_path,
                prep_error=getattr(self, "prep_error", None),
            )
            cv2.imshow("Tether Interview Coach", draw_ui(frame, ui))
            key = cv2.waitKey(1) & 0xFF

            if cv2.getWindowProperty("Tether Interview Coach", cv2.WND_PROP_VISIBLE) < 1:
                break
            if self.state == "DONE" and self.done_at and mono - self.done_at > 3:
                break
            if key in (ord("q"), ord("Q"), 27) and self.state in ("READY", "LIVE_READY", "FEEDBACK", "PREP"):
                self.begin_finalize()
            elif key == ord(" "):
                if self.state in ("READY", "LIVE_READY"):
                    self.start_answer()
                elif self.state in ("ANSWERING", "LIVE_REC") and mono - self.ans_start > 2:
                    self.stop_answer()
            elif key in (ord("n"), ord("N")) and self.state == "FEEDBACK":
                self.next_question()
            elif key in (ord("r"), ord("R")) and self.state == "FEEDBACK":
                self.retry_question()
            elif key in (ord("s"), ord("S")) and self.state == "READY":
                self.skip_question()

        cap.release()
        cv2.destroyAllWindows()
        landmarker.close()
        self.recorder.close()
        if fatal:
            print(f"[ERR] {fatal}")

    # set by main(): True once the prep thread finished
    def prep_ready(self):
        return True


# ============================================================ drawing
def meter(img, x, y, w, label, frac, color, value=""):
    tether.put(img, label, (x, y), 0.5, GREY)
    cv2.rectangle(img, (x + 120, y - 12), (x + 120 + w, y + 2), (60, 56, 52), -1)
    cv2.rectangle(img, (x + 120, y - 12), (x + 120 + int(w * clamp(frac)), y + 2), color, -1)
    if value:
        tether.put(img, value, (x + 130 + w, y), 0.45, WHITE)


def draw_ui(frame, ui):
    W, H = VID_W + PANEL_W, VID_H + STRIP_H
    canvas = np.full((H, W, 3), tether.BG, np.uint8)
    canvas[:VID_H, :VID_W] = frame
    px, pw = VID_W + 20, PANEL_W - 40
    st = ui["state"]

    tether.put(canvas, "INTERVIEW COACH", (px, 32), 0.85, WHITE, 2)
    tether.put(canvas, safe(ui["role"])[:46], (px, 54), 0.45, GREY)
    y = 90

    if st == "CALIB":
        for i, t in enumerate(("CALIBRATING", "Sit like you will in the interview.", "Look straight at the CAMERA LENS.")):
            tether.put(canvas, t, (px, y + i * 28), 0.8 if i == 0 else 0.55, (0, 220, 255) if i == 0 else WHITE, 2 if i == 0 else 1)
        cv2.rectangle(canvas, (px, y + 100), (px + pw, y + 116), (70, 65, 60), -1)
        cv2.rectangle(canvas, (px, y + 100), (px + int(pw * ui["cal_progress"]), y + 116), (0, 220, 255), -1)
        if not ui["face"]:
            tether.put(canvas, "No face found yet", (px, y + 150), 0.55, tether.COL_BAD)
    elif st == "PREP":
        tether.put(canvas, "Preparing your interview...", (px, y), 0.7, (0, 220, 255), 2)
        tether.put(canvas, "Reading resume + job description", (px, y + 32), 0.5, GREY)
        tether.put(canvas, "Loading speech model (first run downloads it)", (px, y + 54), 0.5, GREY)
        if ui["prep_error"]:
            for i, l in enumerate(wrap("Could not reach Gemini: " + ui["prep_error"], pw)[:4]):
                tether.put(canvas, l, (px, y + 90 + i * 20), 0.45, tether.COL_BAD)
    elif st in ("READY", "ANSWERING", "FEEDBACK") and ui["question"]:
        q = ui["question"]
        tag = f"Question {ui['q_idx'] + 1} of {ui['n_q']}  -  {q.get('type', '')}"
        if ui["attempt"] > 1:
            tag += f"  -  try {ui['attempt']}"
        tether.put(canvas, safe(tag), (px, y), 0.5, (0, 220, 255))
        for i, l in enumerate(wrap(q["q"], pw, 0.6, 1)[:5]):
            tether.put(canvas, l, (px, y + 30 + i * 24), 0.6, WHITE)
        y2 = y + 30 + 5 * 24 + 14
        if st == "READY":
            tether.put(canvas, "Press SPACE, then answer out loud.", (px, y2), 0.55, tether.COL_GOOD)
            tether.put(canvas, "Press SPACE again when finished.", (px, y2 + 22), 0.5, GREY)
        elif st == "ANSWERING":
            m, s = divmod(int(ui["elapsed"]), 60)
            cv2.circle(canvas, (px + 8, y2 - 5), 8, tether.COL_BAD, -1)
            tether.put(canvas, f"RECORDING {m:02d}:{s:02d}", (px + 24, y2), 0.7, tether.COL_BAD, 2)
            tether.put(canvas, "Aim for 60-120 seconds.", (px, y2 + 24), 0.5, GREY)
        else:
            yy = y2 - 10
            for text, col, sc in ui["feedback"]:
                for l in wrap(text, pw, sc, 2 if sc > 0.7 else 1):
                    yy += int(30 * sc / 0.8) if sc > 0.7 else 19
                    tether.put(canvas, l, (px, yy), sc, col, 2 if sc > 0.7 else 1)
                yy += 4
            tether.put(canvas, "N next    R try again    Q finish", (px, VID_H - 14), 0.5, tether.COL_GOOD)
    elif st in ("LIVE_READY", "LIVE_REC"):
        tether.put(canvas, "LIVE REVIEW MODE", (px, y), 0.7, (0, 220, 255), 2)
        if st == "LIVE_READY":
            for i, l in enumerate(wrap("Press SPACE when your interview starts and SPACE again when it ends. "
                                       "Audio is kept in memory only, then transcribed on this laptop.", pw)):
                tether.put(canvas, l, (px, y + 32 + i * 20), 0.5, WHITE)
        else:
            m, s = divmod(int(ui["elapsed"]), 60)
            cv2.circle(canvas, (px + 8, y + 40), 8, tether.COL_BAD, -1)
            tether.put(canvas, f"RECORDING {m:02d}:{s:02d}", (px + 24, y + 46), 0.7, tether.COL_BAD, 2)
    elif st == "FINALIZING":
        tether.put(canvas, "Building your review...", (px, y), 0.75, (0, 220, 255), 2)
        tether.put(canvas, safe(ui["final_status"]), (px, y + 32), 0.5, GREY)
        tether.put(canvas, "Long interviews can take a few minutes.", (px, y + 54), 0.45, GREY)
    elif st == "DONE":
        tether.put(canvas, "Review ready", (px, y), 0.8, tether.COL_GOOD, 2)
        tether.put(canvas, "Opening in your browser.", (px, y + 30), 0.5, GREY)

    # live meters (not while reading feedback)
    if st in ("READY", "ANSWERING", "LIVE_READY", "LIVE_REC"):
        my = VID_H - 80
        meter(canvas, px, my, 150, "Eye contact", ui["ec_pct"] / 100,
              tether.score_color(ui["ec_pct"] * 1.1), f"{ui['ec_pct']:.0f}%")
        posture = "good" if 0.88 <= ui["lean"] <= 1.2 else ("leaning back" if ui["lean"] < 0.88 else "too close")
        pcol = tether.COL_GOOD if posture == "good" else tether.COL_MID
        meter(canvas, px, my + 26, 150, "Posture", 1.0 if posture == "good" else 0.5, pcol, posture)
        meter(canvas, px, my + 52, 150, "Voice level", ui["mic"] * 15, tether.COL_GOOD)

    # video overlays
    if st not in ("CALIB", "PREP") and not ui["face"]:
        tether.put(canvas, "NO FACE", (20, 50), 1.3, (60, 60, 255), 3, cv2.FONT_HERSHEY_DUPLEX)
    if ui["hint"]:
        cv2.rectangle(canvas, (0, VID_H - 44), (VID_W, VID_H), (30, 130, 200), -1)
        tether.put_centered(canvas, safe(ui["hint"]), VID_W // 2, VID_H - 14, 0.75, (255, 255, 255), 2)
    if st in ("ANSWERING", "LIVE_REC"):
        cv2.rectangle(canvas, (0, 0), (VID_W - 1, VID_H - 1), tether.COL_BAD, 3)

    tether.draw_timeline(canvas, 20, VID_H + 12, W - 20, H - 12, ui["hist"], ui["clock"], [], [])
    tether.put(canvas, "attention / eye contact, last 3 min", (30, VID_H + 28), 0.4, GREY)
    return canvas


# ============================================================ main
def parse_args():
    p = argparse.ArgumentParser(description="Tether Interview Coach")
    p.add_argument("--resume", help="resume file (.pdf, .docx, .txt)")
    p.add_argument("--role", help="job role, e.g. 'Backend Engineer'")
    p.add_argument("--jd", help="job description: a text file path, or the text itself")
    p.add_argument("--questions", type=int, default=6, help="number of practice questions")
    p.add_argument("--live", action="store_true", help="record a real interview and review it afterwards")
    p.add_argument("--whisper", default="base.en", help="speech model: tiny.en, base.en, small.en...")
    p.add_argument("--calib", type=float, default=8.0, help="calibration seconds")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--model", default=os.path.join(tether.HERE, "face_landmarker.task"))
    p.add_argument("--no-voice", action="store_true", help="do not read questions aloud")
    p.add_argument("--no-report", action="store_true", help="save the report but do not open it")
    p.add_argument("--yes", action="store_true", help="skip the live-mode consent prompt")
    return p.parse_args()


def main():
    args = parse_args()
    problem = check_versions()
    if problem:
        sys.exit("[ERR] " + problem)
    if not llm.has_key():
        sys.exit("[ERR] Set your free Gemini key first (aistudio.google.com/apikey):\n"
                 "         set GEMINI_API_KEY=your_key      (Windows)\n"
                 "         export GEMINI_API_KEY=your_key   (Mac/Linux)")
    if args.live and not args.yes:
        print("LIVE MODE records your microphone (kept in memory, transcribed locally, then discarded).\n"
              "If another person will be heard, make sure they know and agree to this before you start.")
        if input("Continue? [y/N] ").strip().lower() != "y":
            sys.exit(0)

    resume, role, jd = gather_inputs(args)
    ctx = coach.build_context(resume, role, jd)
    stt = speech.Transcriber(args.whisper)
    try:
        recorder = speech.Recorder()
    except Exception as e:
        sys.exit(f"[ERR] Cannot open the microphone: {e}")

    prep = {"done": False, "data": None, "error": None}

    def _prep():
        try:
            prep["data"] = coach.prepare(ctx, args.questions)
        except Exception as e:
            prep["error"] = str(e)
        prep["done"] = True

    threading.Thread(target=_prep, daemon=True).start()

    app = InterviewApp(args, ctx, role, None, [], stt, recorder)

    def prep_ready():
        if not prep["done"]:
            return False
        if prep["error"] and not app.questions:
            app.prep_error = prep["error"]
            if args.live:
                return True
            print(f"[WARN] Could not generate tailored questions ({prep['error']}). Using generic ones.")
            app.questions = DEFAULT_QUESTIONS
        elif prep["data"] and not app.questions:
            app.brief = prep["data"]
            app.questions = prep["data"]["questions"]
        return stt.ready.is_set()

    app.prep_ready = prep_ready
    if args.live:
        app.questions = [dict(q="Live interview", type="live")]
    print("[OK] Calibrating... look at the camera lens. Preparing your interview in the background.")
    app.run()


if __name__ == "__main__":
    main()
