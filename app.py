#!/usr/bin/env python3
"""Tether Interview Coach - modern web UI.   Run:  python app.py

Opens a beautiful local web app (served only on your own computer, 127.0.0.1) where you
add your resume, role and job description, then either

  * Practice with AI          - Gemini asks questions for your role, you answer, instant feedback
  * Real interview (Meet/Zoom) - records quietly in the background, full review afterwards

Everything runs locally; only text (resume, job description, transcripts, numbers) goes to the Gemini API.
The old Tk window is still available as:  python app_classic.py
"""
import glob
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)                                  # sessions + model files live next to the code
sys.path.insert(0, HERE)

import coach  # noqa: E402
import llm  # noqa: E402

SETTINGS = os.path.join(os.path.expanduser("~"), ".tether_settings.json")
SESSIONS_DIR = "interview_sessions"
UPLOADS = os.path.join(tempfile.gettempdir(), "tether_uploads")
MAX_UPLOAD = 15 * 1024 * 1024
SESSION_RE = re.compile(r"^session_\d{8}_\d{6}$")


class State:
    """Everything the web UI can touch, guarded by one lock."""
    lock = threading.RLock()
    cfg = {}
    session = None          # live.LiveSession
    stt = None
    stt_name = None
    live_data = None        # (resume, role, jd)
    proc = None             # practice subprocess


S = State()


# ------------------------------------------------------------------ settings
def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(**kw):
    """Merge into the saved settings; a value of None removes that key."""
    S.cfg.update(kw)
    for k in [k for k, v in S.cfg.items() if v is None]:
        S.cfg.pop(k)
    try:
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(S.cfg, f)
    except Exception:
        pass


# ------------------------------------------------------------------ helpers
def sessions_list():
    out = []
    for p in sorted(glob.glob(os.path.join(SESSIONS_DIR, "session_*.json")), reverse=True)[:40]:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        r = d.get("review") or {}
        out.append(dict(id=os.path.basename(p)[:-5], created=d.get("created", ""), role=d.get("role", ""),
                        mode=d.get("mode", "practice"), score=r.get("overall_score"),
                        readiness=r.get("readiness", ""), headline=r.get("headline", ""),
                        answers=len(d.get("answers", []))))
    return out


def render_report(stem):
    """Always render from the JSON so older sessions also get the newest design."""
    import interview_report
    with open(os.path.join(SESSIONS_DIR, stem + ".json"), encoding="utf-8") as f:
        return interview_report.build(json.load(f))


def fail(msg, field=None):
    return dict(ok=False, error=msg, field=field)


def collect(body):
    """Validate the setup form the same way the old Tk window did. Returns (resume, role, jd) or an error dict."""
    key = (body.get("key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return fail("Add your free Gemini API key first.", "key")
    llm.set_key(key)
    role, jd = (body.get("role") or "").strip(), (body.get("jd") or "").strip()
    if not role:
        return fail("Enter the job role you are interviewing for.", "role")
    if len(jd) < 40:
        return fail("Paste the job description (at least a few lines).", "jd")
    path = (body.get("resume_path") or "").strip()
    if not path:
        return fail("Upload your resume first.", "resume")
    try:
        resume = coach.load_resume(path)
    except Exception as e:                       # noqa: BLE001
        return fail(str(e), "resume")
    save_settings(resume=path, role=role, jd=jd, whisper=body.get("whisper") or "base.en",
                  api_key=key if body.get("remember") else None)
    return resume, role, jd


def stale_files():
    try:
        import interview
        return interview.check_versions()
    except Exception as e:                       # noqa: BLE001
        return f"Could not load the project files ({e}). Make sure ALL files are in one folder and the requirements are installed."


# ------------------------------------------------------------------ actions
def api_config():
    c = S.cfg
    path = c.get("resume", "")
    return dict(has_key=llm.has_key(), remembered=bool(c.get("api_key")), role=c.get("role", ""), jd=c.get("jd", ""),
                whisper=c.get("whisper", "base.en"),
                resume=dict(path=path, name=os.path.basename(path)) if path and os.path.exists(path) else None,
                model=llm.MAIN_MODEL)


def api_upload(name, data):
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(name or "resume.pdf"))[:80] or "resume.pdf"
    if os.path.splitext(safe)[1].lower() not in (".pdf", ".docx", ".txt", ".md"):
        return fail("Please upload a PDF, DOCX, TXT or MD file.", "resume")
    os.makedirs(UPLOADS, exist_ok=True)
    path = os.path.join(UPLOADS, f"{int(time.time())}_{safe}")
    with open(path, "wb") as f:
        f.write(data)
    try:
        text = coach.load_resume(path)
    except Exception as e:                       # noqa: BLE001
        os.remove(path)
        return fail(str(e), "resume")
    return dict(ok=True, path=path, name=safe, words=len(text.split()))


def api_practice(body):
    got = collect(body)
    if isinstance(got, dict):
        return got
    _, role, jd = got
    problem = stale_files()
    if problem:
        return fail(problem)
    with S.lock:
        if S.proc and S.proc.poll() is None:
            return fail("A practice window is already open. Finish it first (press Q inside it).")
        jd_file = os.path.join(tempfile.gettempdir(), "tether_jd.txt")
        with open(jd_file, "w", encoding="utf-8") as f:
            f.write(jd)
        cmd = [sys.executable, os.path.join(HERE, "interview.py"), "--resume", body["resume_path"], "--role", role,
               "--jd", jd_file, "--whisper", body.get("whisper") or "base.en"]
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        try:
            S.proc = subprocess.Popen(cmd, cwd=HERE, env=os.environ.copy(), creationflags=flags)
        except Exception as e:                   # noqa: BLE001
            return fail(f"Could not start the practice window: {e}")
    return dict(ok=True)


def api_practice_state():
    p = S.proc
    if p is None:
        return dict(running=False, code=None)
    code = p.poll()
    return dict(running=code is None, code=code)


def api_live_prepare(body):
    got = collect(body)
    if isinstance(got, dict):
        return got
    with S.lock:
        S.live_data = got
        name = body.get("whisper") or "base.en"
        if S.stt is None or S.stt_name != name:          # start loading Whisper now
            import speech
            S.stt, S.stt_name = speech.Transcriber(name), name
    return dict(ok=True)


def api_live_start(body):
    if not body.get("consent"):
        return fail("Please confirm the recording consent first.")
    problem = stale_files()
    if problem:
        return fail(problem)
    with S.lock:
        if not S.live_data:
            return fail("Fill in the setup first.")
        if S.session and S.session.state in ("calibrating", "recording", "processing"):
            return fail("A session is already running.")
        import live
        resume, role, jd = S.live_data
        ctx = coach.build_context(resume, role, jd)
        S.session = live.LiveSession(ctx, role, S.stt, use_camera=bool(body.get("camera", True)), open_report=False)
        S.session.start()
    return dict(ok=True)


def api_live_stop():
    with S.lock:
        if S.session:
            S.session.stop()
    return dict(ok=True)


def api_live_state():
    s = S.session
    stt = S.stt
    base = dict(stt="none" if stt is None else ("error" if stt.error else ("ready" if stt.ready.is_set() else "loading")))
    if not s:
        return dict(state="idle", status="Ready", error=None, elapsed=0, mic=0, sys=0, audio_note="", camera_note="",
                    report=None, **base)
    rp = os.path.basename(s.report_path)[:-5] if s.report_path else None
    return dict(state=s.state, status=s.status, error=s.error, elapsed=round(s.elapsed(), 1),
                mic=min(100, s.mic_level * 1500), sys=min(100, s.sys_level * 1500),
                audio_note=s.audio_note, camera_note=s.camera_note, report=rp, **base)


def api_key_test(body):
    key = (body.get("key") or "").strip()
    if not key:
        return fail("Paste a key first.")
    ok, msg = llm.check_key(key)
    return dict(ok=ok, message=msg)


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    server_version = "Tether"

    def log_message(self, *a):          # keep the console clean
        pass

    # --- plumbing
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self):
        """Only answer our own page on 127.0.0.1 (blocks DNS-rebinding and other websites)."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, dict(ok=False, error="forbidden"))
            return False
        if self.command == "POST" and self.headers.get("X-Tether") != "1":
            self._send(403, dict(ok=False, error="forbidden"))
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("File too large (max 15 MB)")
        return self.rfile.read(n)

    # --- routes
    def do_GET(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "ui", "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/config":
                return self._send(200, api_config())
            if u.path == "/api/sessions":
                return self._send(200, sessions_list())
            if u.path == "/api/live/state":
                return self._send(200, api_live_state())
            if u.path == "/api/practice/state":
                return self._send(200, api_practice_state())
            m = re.match(r"^/reports/(session_\d{8}_\d{6})\.html$", u.path)
            if m and SESSION_RE.match(m.group(1)):
                return self._send(200, render_report(m.group(1)).encode("utf-8"), "text/html; charset=utf-8")
            self._send(404, dict(ok=False, error="not found"))
        except FileNotFoundError:
            self._send(404, dict(ok=False, error="not found"))
        except Exception as e:                   # noqa: BLE001
            self._send(500, dict(ok=False, error=str(e)))

    def do_POST(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        try:
            if u.path == "/api/upload":
                name = parse_qs(u.query).get("name", ["resume.pdf"])[0]
                return self._send(200, api_upload(name, self._body()))
            body = json.loads(self._body() or b"{}")
            routes = {"/api/key/test": api_key_test, "/api/practice": api_practice,
                      "/api/live/prepare": api_live_prepare, "/api/live/start": api_live_start}
            if u.path in routes:
                return self._send(200, routes[u.path](body))
            if u.path == "/api/live/stop":
                return self._send(200, api_live_stop())
            self._send(404, dict(ok=False, error="not found"))
        except Exception as e:                   # noqa: BLE001
            self._send(500, dict(ok=False, error=str(e)))


def free_port(preferred=8765):
    for p in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No free port")


def main():
    S.cfg = load_settings()
    if S.cfg.get("api_key") and not llm.has_key():
        llm.set_key(S.cfg["api_key"])
    port = free_port(int(os.environ.get("TETHER_PORT", "8765")))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Tether Interview Coach is running at {url}\n  (press Ctrl+C here to quit)\n")
    if not os.environ.get("TETHER_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if S.session and S.session.state in ("recording", "calibrating", "processing"):
            print("A live session was running and has been discarded.")
        os._exit(0)


if __name__ == "__main__":
    main()
