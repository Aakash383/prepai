"""Speech tools for the interview coach.

* Recorder     - keeps mic audio in memory only while you answer (never written to disk)
* Transcriber  - local Whisper (faster-whisper); audio never leaves the laptop
* analyze_speech - pace, filler words, hedges, pauses, response delay, vocal variation
* speak        - reads the interviewer's question aloud (optional, offline)
"""
import re
import threading

import numpy as np

SR = 16000
# Whisper tends to delete "um"/"uh". This prompt nudges it to keep them.
WHISPER_PROMPT = "Umm, so, uh, I think, like, you know, I worked on, hmm, basically the project was..."

SOFT_FILLERS = ("like", "you know", "i mean", "basically", "actually", "literally",
                "sort of", "kind of")
HEDGES = ("i think", "maybe", "i guess", "not sure", "probably", "i hope", "perhaps",
          "i feel like")
HARD_RE = re.compile(r"\b(?:um+|uh+|erm?|hmm+|mm+)\b")


class Recorder:
    """Always-on mic stream (for the level meter) that only stores audio while recording."""

    def __init__(self):
        import sounddevice as sd
        self.level = 0.0
        self._rec = False
        self._buf = []

        def cb(indata, frames, t, status):
            x = indata[:, 0]
            self.level = float(np.sqrt(np.mean(x ** 2)))
            if self._rec:
                self._buf.append(x.copy())

        self._stream = sd.InputStream(channels=1, samplerate=SR, blocksize=1600,
                                      dtype="float32", callback=cb)
        self._stream.start()

    def start(self):
        self._buf = []
        self._rec = True

    def stop(self):
        self._rec = False
        buf, self._buf = self._buf, []
        return np.concatenate(buf) if buf else np.zeros(0, np.float32)

    def close(self):
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class Transcriber:
    """Loads Whisper in the background so startup is not blocked."""

    def __init__(self, model_name="base.en"):
        self.model = None
        self.error = None
        self.ready = threading.Event()
        threading.Thread(target=self._load, args=(model_name,), daemon=True).start()

    def _load(self, name):
        try:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(name, device="cpu", compute_type="int8")
        except Exception as e:
            self.error = str(e)
        finally:
            self.ready.set()

    def transcribe(self, audio, vad=False):
        self.ready.wait()
        if self.model is None:
            raise RuntimeError(f"Speech model unavailable: {self.error}")
        extra = dict(vad_filter=True, vad_parameters=dict(min_silence_duration_ms=600)) if vad else {}
        segs, _ = self.model.transcribe(
            audio, language="en", word_timestamps=True, beam_size=3,
            condition_on_previous_text=False, initial_prompt=None if vad else WHISPER_PROMPT, **extra)
        words, lines, spans = [], [], []
        for s in segs:
            lines.append((float(s.start), s.text.strip()))
            spans.append((float(s.start), float(s.end), s.text.strip()))
            for w in (s.words or []):
                words.append((w.word.strip(), float(w.start), float(w.end)))
        return dict(text=" ".join(t for _, t in lines).strip(), lines=lines, words=words, segs=spans)


def analyze_speech(tr, audio):
    """Delivery metrics from a transcript (with word timestamps) and the raw audio."""
    words = tr["words"]
    n = len(words)
    dur = len(audio) / SR
    base = dict(duration=dur, words=n, wpm=0.0, latency=dur, fillers=0, filler_per_min=0.0,
                top_fillers=[], hedges=0, long_pauses=0, longest_pause=0.0, energy_cv=0.0)
    if n == 0:
        return base

    text = " " + tr["text"].lower() + " "
    first, last = words[0][1], words[-1][2]
    span = max(last - first, 1.0)
    gaps = [words[i + 1][1] - words[i][2] for i in range(n - 1)]

    counts = {}
    hard = len(HARD_RE.findall(text))
    if hard:
        counts["um/uh"] = hard
    for f in SOFT_FILLERS:
        c = len(re.findall(rf"\b{f}\b", text))
        if c:
            counts[f] = c
    total = sum(counts.values())

    # vocal variation: how much loudness changes while speaking (low = flat delivery)
    cv = 0.0
    if len(audio) > SR:
        frames = len(audio) // 1600
        rms = np.sqrt(np.mean(audio[:frames * 1600].reshape(frames, 1600) ** 2, axis=1))
        thr = max(0.008, 0.25 * float(np.percentile(rms, 90)))
        voiced = rms[rms > thr]
        if len(voiced) > 10 and voiced.mean() > 0:
            cv = float(voiced.std() / voiced.mean())

    base.update(
        wpm=n / span * 60.0, latency=float(first), fillers=total,
        filler_per_min=total / (span / 60.0),
        top_fillers=sorted(counts.items(), key=lambda x: -x[1])[:4],
        hedges=sum(len(re.findall(rf"\b{h}\b", text)) for h in HEDGES),
        long_pauses=sum(1 for g in gaps if g >= 1.2),
        longest_pause=float(max(gaps, default=0.0)), energy_cv=cv,
    )
    return base


def speak(text):
    """Read text aloud with the OS voice (pyttsx3). Silent no-op if unavailable."""
    def run():
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", 170)
            engine.say(text)
            engine.runAndWait()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()
