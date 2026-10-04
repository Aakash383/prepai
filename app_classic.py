#!/usr/bin/env python3
"""Tether Interview Coach - classic desktop window (Tk).   Run:  python app_classic.py
(The modern web UI is app.py)

Two modes:
  * Practice with AI        - Gemini asks questions for your role, you answer, instant feedback
  * Real interview (Meet/Zoom) - records quietly in the background, full review afterwards
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)                                  # sessions + model files live next to the code
sys.path.insert(0, HERE)
SETTINGS = os.path.join(os.path.expanduser("~"), ".tether_settings.json")

import coach  # noqa: E402
import llm  # noqa: E402
import speech  # noqa: E402

BG, CARD, INK, MUTE = "#14181f", "#1d232d", "#e9edf2", "#97a3b1"
GOOD, WARN, BAD, ACC = "#3ecf8e", "#f5b942", "#ef6a6a", "#4cc9f0"
FONT = "Segoe UI"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Tether Interview Coach")
        self.geometry("820x760")
        self.minsize(760, 680)
        self.configure(bg=BG)
        self.session = None
        self.stt = None
        self.stt_name = None
        self.cfg = self._load_settings()
        self._style()
        self._build_home()
        self._build_live()
        self.show("home")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(300, self.poll)

    # ------------------------------------------------------------ settings
    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self):
        data = dict(resume=self.var_resume.get(), role=self.var_role.get(),
                    jd=self.txt_jd.get("1.0", "end").strip(), whisper=self.var_whisper.get())
        if self.var_remember.get() and self.var_key.get().strip():
            data["api_key"] = self.var_key.get().strip()
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    # ------------------------------------------------------------ styling
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=INK, font=(FONT, 10))
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG, foreground=INK)
        s.configure("Card.TLabel", background=CARD, foreground=INK)
        s.configure("Mute.TLabel", background=BG, foreground=MUTE)
        s.configure("Head.TLabel", font=(FONT, 20, "bold"))
        s.configure("H2.TLabel", font=(FONT, 12, "bold"))
        s.configure("TButton", padding=8, background="#2a3340", foreground=INK, borderwidth=0)
        s.map("TButton", background=[("active", "#34404f"), ("disabled", "#20262f")],
              foreground=[("disabled", "#66707c")])
        s.configure("Big.TButton", font=(FONT, 13, "bold"), padding=(18, 18))
        s.configure("Go.TButton", background="#1f9d6b", foreground="white", font=(FONT, 11, "bold"))
        s.map("Go.TButton", background=[("active", "#27b47c"), ("disabled", "#20262f")])
        s.configure("Stop.TButton", background="#c0463f", foreground="white", font=(FONT, 11, "bold"))
        s.map("Stop.TButton", background=[("active", "#d9534b"), ("disabled", "#20262f")])
        s.configure("TCheckbutton", background=BG, foreground=INK)
        s.configure("Card.TCheckbutton", background=CARD, foreground=INK)
        s.configure("TEntry", fieldbackground="#222a35", foreground=INK, insertcolor=INK, padding=5)
        s.configure("TCombobox", fieldbackground="#222a35", foreground=INK, background="#2a3340",
                    arrowcolor=INK, padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", "#222a35")], foreground=[("readonly", INK)],
              selectbackground=[("readonly", "#222a35")], selectforeground=[("readonly", INK)])
        self.option_add("*TCombobox*Listbox.background", "#222a35")
        self.option_add("*TCombobox*Listbox.foreground", INK)
        s.configure("Horizontal.TProgressbar", troughcolor="#222a35", background=GOOD, thickness=10)

    # ------------------------------------------------------------ home
    def _build_home(self):
        f = self.home = ttk.Frame(self, padding=24)
        ttk.Label(f, text="Tether Interview Coach", style="Head.TLabel").pack(anchor="w")
        ttk.Label(f, text="Practice with AI, or let it quietly review your real interview.",
                  style="Mute.TLabel").pack(anchor="w", pady=(0, 14))

        card = ttk.Frame(f, style="Card.TFrame", padding=16)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        self.var_resume = tk.StringVar(value=self.cfg.get("resume", ""))
        self.var_role = tk.StringVar(value=self.cfg.get("role", ""))
        self.var_whisper = tk.StringVar(value=self.cfg.get("whisper", "base.en"))
        self.var_key = tk.StringVar(value=self.cfg.get("api_key", ""))
        self.var_remember = tk.BooleanVar(value=bool(self.cfg.get("api_key")))

        ttk.Label(card, text="Resume", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(card, textvariable=self.var_resume).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(card, text="Browse...", command=self.pick_resume).grid(row=0, column=2)

        ttk.Label(card, text="Job role", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(card, textvariable=self.var_role).grid(row=1, column=1, columnspan=2, sticky="ew", padx=8)

        ttk.Label(card, text="Job description", style="Card.TLabel").grid(row=2, column=0, sticky="nw", pady=4)
        box = ttk.Frame(card)
        box.grid(row=2, column=1, columnspan=2, sticky="ew", padx=8)
        self.txt_jd = tk.Text(box, height=9, wrap="word", bg="#222a35", fg=INK, insertbackground=INK,
                              relief="flat", font=(FONT, 10), padx=8, pady=6)
        sb = ttk.Scrollbar(box, command=self.txt_jd.yview)
        self.txt_jd.configure(yscrollcommand=sb.set)
        self.txt_jd.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.txt_jd.insert("1.0", self.cfg.get("jd", ""))

        ttk.Label(card, text="Speech model", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(card, textvariable=self.var_whisper, values=("tiny.en", "base.en", "small.en"),
                     state="readonly", width=12).grid(row=3, column=1, sticky="w", padx=8)

        ttk.Label(card, text="Gemini API key", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(card, textvariable=self.var_key, show="\u2022").grid(row=4, column=1, columnspan=2,
                                                                         sticky="ew", padx=8)
        env = "found in your environment" if llm.has_key() else \
            "free key: aistudio.google.com/apikey (starts with AIza)"
        ttk.Label(card, text=env, style="Mute.TLabel", background=CARD).grid(row=5, column=1, sticky="w", padx=8)
        ttk.Checkbutton(card, text="Remember the key on this computer (saved as plain text in your user folder)",
                        variable=self.var_remember, style="Card.TCheckbutton").grid(
            row=6, column=1, columnspan=2, sticky="w", padx=8, pady=(2, 0))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=20)
        row.columnconfigure((0, 1), weight=1, uniform="a")
        ttk.Button(row, text="Practice with AI\nInstant feedback", style="Big.TButton",
                   command=self.start_practice).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(row, text="Real interview\nMeet / Zoom, in background",
                   style="Big.TButton", command=self.open_live).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.lbl_home = ttk.Label(f, text="", style="Mute.TLabel", wraplength=740)
        self.lbl_home.pack(anchor="w")

    def pick_resume(self):
        p = filedialog.askopenfilename(title="Choose your resume",
                                       filetypes=[("Resume", "*.pdf *.docx *.txt *.md"), ("All files", "*.*")])
        if p:
            self.var_resume.set(p)

    def collect(self):
        """Validate the setup form. Returns (resume_text, role, jd) or None."""
        key = self.var_key.get().strip() or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            messagebox.showerror("API key", "Paste your Gemini API key first (free at aistudio.google.com/apikey).")
            return None
        llm.set_key(key)
        role, jd = self.var_role.get().strip(), self.txt_jd.get("1.0", "end").strip()
        if not role:
            messagebox.showerror("Job role", "Enter the job role.")
            return None
        if len(jd) < 40:
            messagebox.showerror("Job description", "Paste the job description (at least a few lines).")
            return None
        try:
            resume = coach.load_resume(self.var_resume.get())
        except Exception as e:
            messagebox.showerror("Resume", str(e))
            return None
        self._save_settings()
        return resume, role, jd

    def _stale_files(self):
        try:
            import interview
            return interview.check_versions()
        except Exception as e:
            return f"Could not load the project files ({e}). Make sure ALL files are in one folder."

    # ------------------------------------------------------------ practice
    def start_practice(self):
        data = self.collect()
        if not data:
            return
        resume, role, jd = data
        problem = self._stale_files()
        if problem:
            messagebox.showerror("Old files", problem)
            return
        jd_file = os.path.join(tempfile.gettempdir(), "tether_jd.txt")
        with open(jd_file, "w", encoding="utf-8") as f:
            f.write(jd)
        cmd = [sys.executable, os.path.join(HERE, "interview.py"), "--resume", self.var_resume.get().strip().strip('"'),
               "--role", role, "--jd", jd_file, "--whisper", self.var_whisper.get()]
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        try:
            subprocess.Popen(cmd, cwd=HERE, env=os.environ.copy(), creationflags=flags)
            self.lbl_home.config(text="Practice window is opening (the first run downloads the speech model). "
                                      "Keys: SPACE answer, N next, R retry, Q finish.")
        except Exception as e:
            messagebox.showerror("Could not start", str(e))

    # ------------------------------------------------------------ live page
    def _build_live(self):
        f = self.live = ttk.Frame(self, padding=24)
        ttk.Label(f, text="Real interview mode", style="Head.TLabel").pack(anchor="w")
        ttk.Label(f, text="Records in the background. You get the review after the call.",
                  style="Mute.TLabel").pack(anchor="w", pady=(0, 12))

        tips = ttk.Frame(f, style="Card.TFrame", padding=14)
        tips.pack(fill="x")
        for t in ("1.  Wear headphones, so the interviewer's voice does not leak into your microphone.",
                  "2.  Press Start in the waiting room. Look at the call window normally for 10 seconds.",
                  "3.  Minimize this window and join the call. Do nothing else; it never pops up.",
                  "4.  When the interview ends, press Stop. The review opens in your browser."):
            ttk.Label(tips, text=t, style="Card.TLabel", wraplength=720).pack(anchor="w", pady=1)

        self.var_cam = tk.BooleanVar(value=True)
        self.var_consent = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Analyse my body language with the webcam (skipped automatically if the camera is busy)",
                        variable=self.var_cam).pack(anchor="w", pady=(14, 2))
        ttk.Checkbutton(f, text="The interviewer knows I am recording audio for my own review, or recording is "
                                "allowed here.", variable=self.var_consent, command=self._sync_buttons).pack(anchor="w")

        st = ttk.Frame(f, style="Card.TFrame", padding=16)
        st.pack(fill="x", pady=16)
        self.lbl_state = ttk.Label(st, text="Ready", style="Card.TLabel", font=(FONT, 14, "bold"), wraplength=700)
        self.lbl_state.pack(anchor="w")
        self.lbl_time = ttk.Label(st, text="00:00", style="Card.TLabel", font=(FONT, 28, "bold"))
        self.lbl_time.pack(anchor="w", pady=4)
        for name in ("mic", "sys"):
            r = ttk.Frame(st, style="Card.TFrame")
            r.pack(fill="x", pady=2)
            ttk.Label(r, text="Your microphone" if name == "mic" else "Call audio", style="Card.TLabel",
                      width=16).pack(side="left")
            bar = ttk.Progressbar(r, maximum=100)
            bar.pack(side="left", fill="x", expand=True)
            setattr(self, f"bar_{name}", bar)
        self.lbl_audio = ttk.Label(st, text="", style="Card.TLabel", foreground=MUTE, wraplength=700)
        self.lbl_audio.pack(anchor="w", pady=(8, 0))
        self.lbl_cam = ttk.Label(st, text="", style="Card.TLabel", foreground=MUTE, wraplength=700)
        self.lbl_cam.pack(anchor="w")

        btns = ttk.Frame(f)
        btns.pack(fill="x")
        self.btn_start = ttk.Button(btns, text="Start", style="Go.TButton", command=self.live_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="Stop and get my review", style="Stop.TButton", command=self.live_stop)
        self.btn_stop.pack(side="left", padx=10)
        self.btn_report = ttk.Button(btns, text="Open report", command=self.open_report)
        self.btn_report.pack(side="left")
        self.btn_back = ttk.Button(btns, text="Back", command=self.live_back)
        self.btn_back.pack(side="right")
        self._sync_buttons()

    def open_live(self):
        data = self.collect()
        if not data:
            return
        self.live_data = data
        name = self.var_whisper.get()
        if self.stt is None or self.stt_name != name:       # start loading Whisper now
            self.stt, self.stt_name = speech.Transcriber(name), name
        self.show("live")

    def live_start(self):
        problem = self._stale_files()
        if problem:
            messagebox.showerror("Old files", problem)
            return
        import live
        resume, role, jd = self.live_data
        ctx = coach.build_context(resume, role, jd)
        self.session = live.LiveSession(ctx, role, self.stt, use_camera=self.var_cam.get())
        self.session.start()
        self._sync_buttons()

    def live_stop(self):
        if self.session:
            self.session.stop()

    def live_back(self):
        if self.session and self.session.state in ("calibrating", "recording", "processing"):
            messagebox.showinfo("Busy", "Stop the session first.")
            return
        self.show("home")

    def open_report(self):
        if self.session and self.session.report_path:
            webbrowser.open("file://" + os.path.abspath(self.session.report_path))

    def _sync_buttons(self):
        st = self.session.state if self.session else "idle"
        busy = st in ("calibrating", "recording", "processing")
        self.btn_start.state(["!disabled"] if (self.var_consent.get() and not busy) else ["disabled"])
        self.btn_stop.state(["!disabled"] if st in ("calibrating", "recording") else ["disabled"])
        self.btn_report.state(["!disabled"] if (self.session and self.session.report_path) else ["disabled"])
        self.btn_back.state(["disabled"] if busy else ["!disabled"])

    # ------------------------------------------------------------ plumbing
    def show(self, name):
        for fr in (self.home, self.live):
            fr.pack_forget()
        (self.home if name == "home" else self.live).pack(fill="both", expand=True)

    def poll(self):
        s = self.session
        if s:
            colour = {"recording": BAD, "calibrating": WARN, "processing": ACC, "done": GOOD,
                      "error": BAD}.get(s.state, INK)
            text = s.status if s.state != "error" else f"{s.status}: {s.error}"
            self.lbl_state.config(text=text, foreground=colour)
            e = s.elapsed()
            self.lbl_time.config(text=f"{int(e // 60):02d}:{int(e % 60):02d}")
            self.bar_mic["value"] = min(100, s.mic_level * 1500)
            self.bar_sys["value"] = min(100, s.sys_level * 1500)
            self.lbl_audio.config(text=s.audio_note)
            self.lbl_cam.config(text=s.camera_note)
            self._sync_buttons()
        self.after(250, self.poll)

    def on_close(self):
        if self.session and self.session.state in ("recording", "calibrating", "processing"):
            if not messagebox.askyesno("Quit", "A session is running. Quit and lose it?"):
                return
        self._save_settings()
        os._exit(0)


if __name__ == "__main__":
    App().mainloop()
