#!/usr/bin/env python3
"""Tether report: turns a focus_log_*.csv into a self-contained HTML report.

    python report.py                  # newest focus_log_*.csv in this folder
    python report.py my_log.csv       # a specific log
    python report.py --demo           # build a realistic sample session (backup demo!)
    python report.py --offline        # never call the AI coach

AI coach: set ANTHROPIC_API_KEY (and `pip install anthropic`) to get a written
coaching note from Claude. Only aggregated numbers and app names are sent -
never video, audio or window titles. Without a key, a local rule-based coach
writes the note, so the report always works offline.
"""
import argparse
import csv
import glob
import html
import json
import os
import random
import statistics
import webbrowser
from datetime import datetime, timedelta

HIGH, LOW = 65, 40


# ================================================================ load + analyze
def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append(dict(
                    ts=r["ts"], t=float(r["t"]), score=float(r["score"]),
                    event=(r.get("event") or "").strip(), face=int(float(r.get("face") or 0)),
                    mic=float(r.get("mic") or 0), app=(r.get("app") or "unknown").strip(),
                ))
            except (ValueError, KeyError):
                continue
    return rows


def fmt(sec):
    m, s = divmod(int(max(0, sec)), 60)
    return f"{m}:{s:02d}"


def analyze(rows):
    samples = [r for r in rows if not r["event"]]
    if len(samples) < 5:
        return None
    events = [r for r in rows if r["event"]]
    scores = [r["score"] for r in samples]
    n = len(scores)
    dur = samples[-1]["t"]

    # streaks: start >= 65, continue until < 55
    longest, start = 0.0, None
    for r in samples:
        if start is None:
            if r["score"] >= HIGH:
                start = r["t"]
        elif r["score"] < 55:
            longest = max(longest, r["t"] - start)
            start = None
    if start is not None:
        longest = max(longest, dur - start)

    # drift episodes
    drifts, open_ev = [], None
    for e in events:
        if e["event"] == "DRIFT_START":
            open_ev = e
        elif e["event"] == "DRIFT_END" and open_ev:
            drifts.append(dict(start=open_ev["t"], end=e["t"], app=open_ev["app"]))
            open_ev = None
    if open_ev:
        drifts.append(dict(start=open_ev["t"], end=dur, app=open_ev["app"]))

    # per-app
    by_app = {}
    for r in samples:
        by_app.setdefault(r["app"], []).append(r["score"])
    apps = sorted(((a, statistics.mean(v), len(v)) for a, v in by_app.items() if len(v) >= 10),
                  key=lambda x: -x[2])

    # per-minute series, rolling windows
    buckets = {}
    for r in samples:
        buckets.setdefault(int(r["t"] // 60), []).append(r["score"])
    series = [(m, statistics.mean(v)) for m, v in sorted(buckets.items())]
    w = 5 if len(series) >= 12 else 3 if len(series) >= 6 else 1
    roll = [(series[i][0], statistics.mean(v for _, v in series[i:i + w]))
            for i in range(len(series) - w + 1)]
    best = max(roll, key=lambda x: x[1]) if roll else (0, 0)
    worst = min(roll, key=lambda x: x[1]) if roll else (0, 0)
    fade_min = None
    if len(roll) > 1:
        base = roll[0][1]
        for m, v in roll[1:]:
            if v < base - 15:
                fade_min = m + w // 2
                break

    # noise context
    foc_mic = [r["mic"] for r in samples if r["score"] >= HIGH]
    dis_mic = [r["mic"] for r in samples if r["score"] < LOW]
    noise_ratio = None
    if len(foc_mic) > 20 and len(dis_mic) > 20 and statistics.mean(foc_mic) > 0:
        noise_ratio = statistics.mean(dis_mic) / statistics.mean(foc_mic)

    drift_apps = {}
    for d in drifts:
        drift_apps[d["app"]] = drift_apps.get(d["app"], 0) + 1

    return dict(
        samples=samples, events=events, n=n, dur=dur,
        start_ts=samples[0]["ts"], avg=statistics.mean(scores),
        focused_pct=100 * sum(s >= HIGH for s in scores) / n,
        drifting_pct=100 * sum(LOW <= s < HIGH for s in scores) / n,
        distracted_pct=100 * sum(s < LOW for s in scores) / n,
        longest=longest, drifts=drifts,
        drift_avg=statistics.mean(d["end"] - d["start"] for d in drifts) if drifts else 0.0,
        top_drift_app=max(drift_apps, key=drift_apps.get) if drift_apps else None,
        apps=apps, series=series, best=best, worst=worst, w=w, fade_min=fade_min,
        yawns=[e for e in events if e["event"] == "YAWN"],
        marks=[e for e in events if e["event"] == "MARK"],
        nudges=sum(1 for e in events if e["event"] == "NUDGE"),
        breaks=sum(1 for e in events if e["event"] == "BREAK_SUGGESTED"),
        noise_ratio=noise_ratio,
    )


# ================================================================ coaching
def local_coach(a):
    f = a["focused_pct"]
    tone = ("Strong session" if f >= 75 else
            "Solid session with room to grow" if f >= 50 else "A hard focus day")
    L = [f"{tone}: you were focused {f:.0f}% of the time (average score {a['avg']:.0f}).", "",
         "What I noticed"]
    b0, b1 = a["best"][0], a["best"][0] + a["w"]
    if a["fade_min"] is not None:
        L.append(f"- Focus started fading around minute {a['fade_min']}. "
                 f"Your best stretch was minutes {b0}-{b1}.")
    else:
        L.append(f"- Focus held steady. Your best stretch was minutes {b0}-{b1}.")
    weak = [x for x in a["apps"] if x[2] >= 60]
    if len(weak) >= 2:
        w = min(weak, key=lambda x: x[1])
        L.append(f"- {w[0]} was your weakest context (average {w[1]:.0f} over {w[2] / 60:.0f} min).")
    if a["drifts"]:
        extra = f", most often starting in {a['top_drift_app']}" if a["top_drift_app"] else ""
        L.append(f"- {len(a['drifts'])} drift episodes lasting {a['drift_avg']:.0f}s on average{extra}.")
    if len(a["yawns"]) >= 2:
        L.append(f"- {len(a['yawns'])} yawns, a sign of fatigue rather than distraction.")
    if a["noise_ratio"] and a["noise_ratio"] > 1.5:
        L.append("- Your environment was noticeably louder whenever you drifted.")
    L += ["", "Try next session"]
    brk = a["fade_min"] if a["fade_min"] else 25
    L.append(f"- Schedule a 5-minute break just before minute {max(10, brk - 3)}, before focus fades.")
    if a["top_drift_app"]:
        L.append(f"- Close or mute {a['top_drift_app']} during deep-work blocks.")
    L.append(f"- Beat today's longest streak of {fmt(a['longest'])} by one minute.")
    return "\n".join(L)


PROMPT = """You are a concise, encouraging focus coach. Below is one person's focus-session data
(scores are 0-100; >=65 focused, 40-65 drifting, <40 distracted). Write under 200 words:
1) one headline sentence, 2) a short 'What I noticed' list of 3 specific, data-backed observations
with timestamps, 3) a short 'Try next session' list of 3 concrete experiments.
Use plain text and '- ' bullets. Use only the data given; do not invent causes.

DATA:
"""


def summary_for_ai(a):
    return dict(
        duration_min=round(a["dur"] / 60, 1), average_score=round(a["avg"]),
        focused_pct=round(a["focused_pct"]), drifting_pct=round(a["drifting_pct"]),
        distracted_pct=round(a["distracted_pct"]), longest_streak=fmt(a["longest"]),
        drift_episodes=[dict(at=fmt(d["start"]), seconds=round(d["end"] - d["start"]), app=d["app"])
                        for d in a["drifts"][:12]],
        per_minute_avg=[round(v) for _, v in a["series"]],
        apps=[dict(app=x[0], avg=round(x[1]), minutes=round(x[2] / 60, 1)) for x in a["apps"][:6]],
        yawn_times=[fmt(e["t"]) for e in a["yawns"]],
        marked_moments=[dict(at=fmt(e["t"]), score=round(e["score"]), app=e["app"]) for e in a["marks"]],
        fade_minute=a["fade_min"], best_window_start_min=a["best"][0], window_len_min=a["w"],
        louder_when_distracted_ratio=round(a["noise_ratio"], 2) if a["noise_ratio"] else None,
    )


def ai_coach(a, offline=False):
    if offline or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model=os.environ.get("TETHER_MODEL", "claude-sonnet-5-5"), max_tokens=700,
            messages=[{"role": "user", "content": PROMPT + json.dumps(summary_for_ai(a))}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        return text or None
    except Exception as e:
        print(f"[WARN] AI coach unavailable ({e}); using the local coach.")
        return None


# ================================================================ HTML
CSS = """
:root{--bg:#f3f1ec;--panel:#fbfaf7;--ink:#17212b;--mute:#5d6975;--line:#d9d5cc;
--good:#1f9d6b;--mid:#d98a10;--bad:#d64545;--mark:#1a82b3}
@media (prefers-color-scheme:dark){:root{--bg:#12171d;--panel:#1a2129;--ink:#e8ecef;--mute:#98a3ad;
--line:#2b353f;--good:#3ecf8e;--mid:#f5b942;--bad:#ef6a6a;--mark:#4cc9f0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:40px 20px 64px}
h1{font:600 clamp(2rem,5vw,3.2rem)/1.1 "Iowan Old Style",Palatino,Georgia,serif;margin:0 0 6px}
h2{font:600 1.35rem/1.2 "Iowan Old Style",Palatino,Georgia,serif;margin:44px 0 14px}
.sub{color:var(--mute);margin:0}
.hero{display:flex;gap:28px;align-items:flex-end;flex-wrap:wrap;margin:26px 0 8px}
.big{font:600 clamp(4rem,12vw,7rem)/0.9 "Iowan Old Style",Palatino,Georgia,serif}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:22px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi b{display:block;font-size:1.5rem;font-weight:600}.kpi span{color:var(--mute);font-size:.9rem}
.chart{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.chart svg{display:block;width:100%;height:auto}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--mute);font-size:.88rem;margin-top:10px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.coach{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--mark);
border-radius:10px;padding:20px 24px;max-width:70ch}
.coach h4{margin:18px 0 6px;font-size:1rem}.coach h4:first-child{margin-top:0}
.coach p{margin:0 0 10px}.coach ul{margin:0 0 6px;padding-left:20px}.coach li{margin:4px 0}
.badge{display:inline-block;font-size:.8rem;color:var(--mute);margin-bottom:10px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);
border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);font-size:.95rem}
th{color:var(--mute);font-weight:500}tr:last-child td{border-bottom:0}
.bar{height:8px;border-radius:4px;background:var(--line);min-width:120px}
.bar i{display:block;height:100%;border-radius:4px}
.scroll{overflow-x:auto}
footer{margin-top:48px;color:var(--mute);font-size:.88rem;max-width:70ch}
"""


def band(s):
    return "var(--good)" if s >= HIGH else "var(--mid)" if s >= LOW else "var(--bad)"


def esc(x):
    return html.escape(str(x))


def timeline_svg(a):
    W, H, L, R, T, B = 920, 290, 44, 14, 16, 34
    pw, ph = W - L - R, H - T - B
    dur = max(a["dur"], 1.0)
    X = lambda t: L + pw * t / dur
    Y = lambda s: T + ph * (1 - s / 100)
    S = a["samples"]
    N = min(240, len(S))
    size = len(S) / N
    pts = []
    for i in range(N):
        chunk = S[int(i * size):max(int((i + 1) * size), int(i * size) + 1)]
        pts.append((statistics.mean(r["t"] for r in chunk), statistics.mean(r["score"] for r in chunk)))
    line = " L".join(f"{X(t):.1f},{Y(s):.1f}" for t, s in pts)
    area = f"M{X(pts[0][0]):.1f},{Y(0):.1f} L{line} L{X(pts[-1][0]):.1f},{Y(0):.1f} Z"

    o = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Focus score over time">',
         '<defs><linearGradient id="g" gradientUnits="userSpaceOnUse" '
         f'x1="0" y1="{Y(100):.1f}" x2="0" y2="{Y(0):.1f}">'
         '<stop offset="0" stop-color="var(--good)"/><stop offset=".32" stop-color="var(--good)"/>'
         '<stop offset=".42" stop-color="var(--mid)"/><stop offset=".55" stop-color="var(--mid)"/>'
         '<stop offset=".66" stop-color="var(--bad)"/><stop offset="1" stop-color="var(--bad)"/>'
         '</linearGradient></defs>']
    for lvl in (0, LOW, HIGH, 100):
        o.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(lvl):.1f}" y2="{Y(lvl):.1f}" '
                 f'stroke="var(--line)" stroke-dasharray="{"" if lvl in (0, 100) else "4 4"}"/>')
        o.append(f'<text x="{L - 8}" y="{Y(lvl) + 4:.1f}" text-anchor="end" font-size="11" '
                 f'fill="var(--mute)">{lvl}</text>')
    for d in a["drifts"]:
        o.append(f'<rect x="{X(d["start"]):.1f}" y="{T}" width="{max(2, X(d["end"]) - X(d["start"])):.1f}" '
                 f'height="{ph}" fill="var(--bad)" opacity=".13"/>')
    o.append(f'<path d="{area}" fill="url(#g)" opacity=".12"/>')
    o.append(f'<path d="M{line}" fill="none" stroke="url(#g)" stroke-width="2.6" '
             'stroke-linejoin="round" stroke-linecap="round"/>')
    for e in a["marks"]:
        x = X(e["t"])
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{T + ph}" stroke="var(--mark)" '
                 'stroke-dasharray="3 3"/>'
                 f'<text x="{x + 4:.1f}" y="{T + 12}" font-size="11" fill="var(--mark)">mark {fmt(e["t"])}</text>')
    for e in a["yawns"]:
        x = X(e["t"])
        o.append(f'<path d="M{x - 5:.1f},{T + ph + 1} L{x + 5:.1f},{T + ph + 1} L{x:.1f},{T + ph - 9} Z" '
                 'fill="var(--mid)"/>')
    mins = dur / 60
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60) if mins / s <= 9), 60)
    m = 0
    while m * 60 <= dur:
        o.append(f'<text x="{X(m * 60):.1f}" y="{H - 10}" text-anchor="middle" font-size="11" '
                 f'fill="var(--mute)">{m}m</text>')
        m += step
    o.append("</svg>")
    return "".join(o)


def render_coach(text):
    out, in_ul = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ", "\u2022 ")):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{esc(s[2:].strip())}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if not s:
            continue
        if s.startswith("#") or (len(s) <= 28 and s[-1] not in ".!?:"):
            out.append(f"<h4>{esc(s.lstrip('# ').strip())}</h4>")
        else:
            out.append(f"<p>{esc(s)}</p>")
    if in_ul:
        out.append("</ul>")
    return "".join(out)


def build_html(a, coach_text, source):
    try:
        when = datetime.fromisoformat(a["start_ts"]).strftime("%A %d %B %Y, %H:%M")
    except ValueError:
        when = a["start_ts"]
    kpis = [
        (f"{a['focused_pct']:.0f}%", "of time focused"),
        (fmt(a["longest"]), "longest focus streak"),
        (str(len(a["drifts"])), f"drift episodes (avg {a['drift_avg']:.0f}s)"),
        (str(len(a["yawns"])), "yawns"),
        (str(a["nudges"]), "nudges sent"),
        (f"{a['distracted_pct']:.0f}%", "of time distracted"),
    ]
    kp = "".join(f'<div class="kpi"><b>{v}</b><span>{esc(k)}</span></div>' for v, k in kpis)

    apps = "".join(
        f'<tr><td>{esc(n)}</td><td>{sec / 60:.1f} min</td>'
        f'<td><div class="bar"><i style="width:{avg:.0f}%;background:{band(avg)}"></i></div></td>'
        f'<td>{avg:.0f}</td></tr>' for n, avg, sec in a["apps"][:8])
    apps_html = (f'<h2>Where your focus went</h2><div class="scroll"><table><tr><th>App</th><th>Time</th>'
                 f'<th>Average focus</th><th></th></tr>{apps}</table></div>') if apps and \
        any(x[0] not in ("-", "unknown") for x in a["apps"]) else ""

    marks = "".join(f'<tr><td>{fmt(e["t"])}</td><td>{e["score"]:.0f}</td><td>{esc(e["app"])}</td></tr>'
                    for e in a["marks"])
    marks_html = (f'<h2>Moments you marked</h2><div class="scroll"><table><tr><th>When</th><th>Score</th>'
                  f'<th>App</th></tr>{marks}</table></div>') if marks else ""

    drift_rows = "".join(
        f'<tr><td>{fmt(d["start"])}</td><td>{d["end"] - d["start"]:.0f}s</td><td>{esc(d["app"])}</td></tr>'
        for d in a["drifts"][:12])
    drift_html = (f'<h2>Drift episodes</h2><div class="scroll"><table><tr><th>Started</th><th>Lasted</th>'
                  f'<th>App when it began</th></tr>{drift_rows}</table></div>') if drift_rows else ""

    badge = ("Written by Claude from your aggregated session numbers" if source == "ai"
             else "Written by Tether's local coach (offline)")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tether session report</title><style>{CSS}</style></head><body><main>
<h1>Your focus session</h1><p class="sub">{esc(when)} &middot; {a['dur'] / 60:.0f} minutes</p>
<div class="hero"><div class="big">{a['avg']:.0f}</div>
<p class="sub">average focus score out of 100.<br>Best stretch: minutes {a['best'][0]}&ndash;{a['best'][0] + a['w']}
&middot; Lowest: minutes {a['worst'][0]}&ndash;{a['worst'][0] + a['w']}</p></div>
<div class="kpis">{kp}</div>
<h2>Focus over time</h2><div class="chart">{timeline_svg(a)}</div>
<div class="legend"><span><i style="background:var(--good)"></i>Focused 65+</span>
<span><i style="background:var(--mid)"></i>Drifting 40&ndash;65</span>
<span><i style="background:var(--bad)"></i>Distracted</span>
<span><i style="background:var(--bad);opacity:.25"></i>Drift episode</span>
<span><i style="background:var(--mark)"></i>Marked moment</span>
<span><i style="background:var(--mid);clip-path:polygon(50% 0,100% 100%,0 100%)"></i>Yawn</span></div>
<h2>Your coach</h2><div class="coach"><div class="badge">{badge}</div>{render_coach(coach_text)}</div>
{apps_html}{drift_html}{marks_html}
<footer>Privacy: no video or images were saved, no face recognition is used, the microphone is
reduced to a loudness number, and window titles are reduced to app names. All scoring ran on this
laptop.</footer></main></body></html>"""


def generate(csv_path, open_browser=True, offline=False, out_path=None):
    a = analyze(load(csv_path))
    if a is None:
        raise ValueError("Not enough data in the log (need at least a few seconds).")
    text = ai_coach(a, offline)
    source = "ai" if text else "local"
    text = text or local_coach(a)
    out_path = out_path or os.path.splitext(csv_path)[0] + "_report.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_html(a, text, source))
    print(f"[OK] Report: {out_path} ({'AI coach' if source == 'ai' else 'local coach'})")
    if open_browser:
        webbrowser.open("file://" + os.path.abspath(out_path))
    return out_path


# ================================================================ demo data
def make_demo(path, minutes=32, seed=7):
    """Writes a realistic 32-minute session using the real DriftTracker."""
    from scoring import DriftTracker, clamp
    rnd = random.Random(seed)
    plan = [(0, 9, "Visual Studio Code", 90), (9, 12, "Google Chrome", 76), (12, 14, "WhatsApp", 38),
            (14, 24, "Visual Studio Code", 82), (24, 27, "YouTube", 30),
            (27, minutes, "Visual Studio Code", 68)]
    marks, yawn_at = {9 * 60 + 50, 15 * 60 + 20}, {19 * 60 + 10, 22 * 60 + 40, 28 * 60 + 5}
    start = datetime.now() - timedelta(minutes=minutes)
    tracker, score, yawns = DriftTracker(now=0.0), 95.0, 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "t", "score", "event", "face", "attention", "alertness", "gaze", "head",
                    "eyes", "perclos", "blink_rate", "yawns", "mic", "app"])
        for sec in range(minutes * 60):
            mnt = sec / 60
            app, base = next((a, b) for s, e, a, b in plan if s <= mnt < e)
            target = base - 0.45 * mnt + rnd.gauss(0, 6)
            score = clamp(score + (target - score) * 0.12, 0.0, 100.0)
            mic = (0.05 if app in ("WhatsApp", "YouTube") else 0.015) + rnd.random() * 0.01
            ts = (start + timedelta(seconds=sec)).isoformat(timespec="seconds")

            def row(ev=""):
                w.writerow([ts, sec, int(score), ev, 1 if score > 12 else 0, int(score), int(score),
                            int(score), int(score), 90, 0.03, 16, yawns, round(mic, 4), app])
            row()
            if sec in marks:
                row("MARK")
            if sec in yawn_at:
                yawns += 1
                tracker.note_yawn(sec)
                row("YAWN")
            for ev in tracker.update(score, sec):
                row(ev)
    return path


# ================================================================ cli
def main():
    p = argparse.ArgumentParser(description="Build a Tether HTML report")
    p.add_argument("csv", nargs="?", help="focus_log_*.csv (default: newest in this folder)")
    p.add_argument("--demo", action="store_true", help="generate a sample session and report")
    p.add_argument("--offline", action="store_true", help="skip the AI coach")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.add_argument("--out", help="output html path")
    args = p.parse_args()

    if args.demo:
        path = make_demo("demo_session.csv")
        print(f"[OK] Wrote sample log {path}")
    else:
        path = args.csv or max(glob.glob("focus_log_*.csv"), key=os.path.getmtime, default=None)
        if not path:
            raise SystemExit("No focus_log_*.csv found. Run tether.py first, or use --demo.")
    generate(path, open_browser=not args.no_open, offline=args.offline, out_path=args.out)


if __name__ == "__main__":
    main()
