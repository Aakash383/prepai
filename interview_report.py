"""Interview review report: session dict -> self-contained HTML file."""
import os
import webbrowser
from datetime import datetime

from report import CSS, esc

EXTRA_CSS = """
.hero2{display:flex;gap:28px;align-items:center;flex-wrap:wrap;margin:24px 0 10px}
.pill{display:inline-block;padding:3px 12px;border-radius:99px;border:1px solid var(--line);
font-size:.9rem;background:var(--panel)}
.three{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.card h3{margin:0 0 4px;font-size:1.05rem}.card .num{font-size:1.9rem;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--line);
border-radius:10px;padding:12px 14px}
.tile.good{border-left-color:var(--good)}.tile.ok{border-left-color:var(--mid)}
.tile.bad{border-left-color:var(--bad)}
.tile b{display:block;font-size:1.35rem}.tile span{color:var(--mute);font-size:.85rem}
.fix{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 20px;margin-bottom:12px}
.fix h3{margin:0 0 6px}.fix .drill{margin-top:8px;padding-top:8px;border-top:1px dashed var(--line)}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin-bottom:10px}
summary{cursor:pointer;padding:14px 18px;font-weight:600}
details .in{padding:0 18px 16px}details h4{margin:14px 0 4px}
.chip{display:inline-block;margin:3px 6px 3px 0;padding:2px 10px;border-radius:99px;font-size:.85rem;
border:1px solid var(--line)}.chip.g{border-color:var(--good)}.chip.r{border-color:var(--bad)}
.tr{white-space:pre-wrap;color:var(--mute);font-size:.93rem;max-width:80ch}
.delta.up{color:var(--good)}.delta.down{color:var(--bad)}
"""


def f0(x, d=0):
    try:
        return f"{float(x):.{d}f}"
    except (TypeError, ValueError):
        return "-"


def status(v, good, ok):
    """good/ok are (lo, hi) ranges."""
    if good[0] <= v <= good[1]:
        return "good"
    if ok[0] <= v <= ok[1]:
        return "ok"
    return "bad"


def tile(label, value, note, cls=""):
    return f'<div class="tile {cls}"><b>{esc(value)}</b><span>{esc(label)}<br>{esc(note)}</span></div>'


def metric_tiles(sp, bh):
    t = []
    if bh:
        ec = bh["eye_contact_pct"]
        t.append(tile("Eye contact", f"{ec:.0f}%", "target 55-80%", status(ec, (55, 85), (40, 100))))
        t.append(tile("Head movement", f"{bh['head_motion_dps']:.1f} deg/s", "steady is under 6",
                      status(bh["head_motion_dps"], (0, 6), (0, 12))))
        t.append(tile("Leaning back", f"{bh['lean_back_pct']:.0f}%", "of the time",
                      status(bh["lean_back_pct"], (0, 20), (0, 45))))
        t.append(tile("Looked away 2s+", str(bh["away_events"]), f"longest {bh['longest_away_s']:.0f}s",
                      status(bh["away_events"], (0, 4), (0, 10))))
        t.append(tile("Smiling", f"{bh['smile_pct']:.0f}%", "of the time"))
    if sp:
        t.append(tile("Pace", f"{sp['wpm']:.0f} wpm", "target 120-165", status(sp["wpm"], (120, 165), (100, 185))))
        t.append(tile("Filler words", f"{sp['filler_per_min']:.1f}/min", f"{sp['fillers']} total, aim under 3",
                      status(sp["filler_per_min"], (0, 3), (0, 6))))
        t.append(tile("Hedging", str(sp["hedges"]), "'I think', 'maybe'...", status(sp["hedges"], (0, 3), (0, 8))))
        t.append(tile("Long pauses", str(sp["long_pauses"]), f"longest {sp['longest_pause']:.1f}s"))
    return '<div class="tiles">' + "".join(t) + "</div>"


def ul(items):
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>" if items else ""


def chips(items, cls):
    return "".join(f'<span class="chip {cls}">{esc(i)}</span>' for i in items)


def answer_blocks(answers, mode):
    by_q = {}
    for a in answers:
        by_q.setdefault(a["q_idx"], []).append(a)
    out = []
    for qi in sorted(by_q):
        group = by_q[qi]
        scores = [(g.get("eval") or {}).get("score") for g in group]
        title = esc(group[0]["question"])
        badge = ""
        if len(group) > 1 and scores[0] is not None and scores[-1] is not None:
            d = scores[-1] - scores[0]
            badge = f' <span class="delta {"up" if d >= 0 else "down"}">{d:+.0f} after retry</span>'
        inner = []
        for g in group:
            ev = g.get("eval") or {}
            sc = f"{ev['score']:.0f}/100" if isinstance(ev.get("score"), (int, float)) else "no score"
            inner.append(f'<h4>Attempt {g["attempt"]} &middot; {sc} &middot; {g.get("duration", 0):.0f}s</h4>')
            if g.get("error"):
                inner.append(f'<p>Could not analyse this answer: {esc(g["error"])}</p>')
                continue
            if mode != "live":
                inner.append(f'<div class="tr">{esc(g.get("transcript", ""))}</div>')
            if ev.get("strengths"):
                inner.append("<h4>What worked</h4>" + ul(ev["strengths"]))
            if ev.get("improvements"):
                inner.append("<h4>Improve</h4>" + ul(ev["improvements"]))
            if ev.get("missing_keywords"):
                inner.append("<h4>Job-description terms you could use</h4>" + chips(ev["missing_keywords"], "r"))
            if ev.get("better_answer"):
                inner.append(f'<h4>A stronger version (built only from your own facts)</h4>'
                             f'<p>{esc(ev["better_answer"])}</p>')
        label = f"Q{qi + 1}. {title}" if mode != "live" else "Full interview transcript review"
        out.append(f'<details><summary>{label}{badge}</summary><div class="in">{"".join(inner)}</div></details>')
    return "".join(out)


def build(s):
    r = s.get("review") or {}
    brief = s.get("brief") or {}
    mode = s.get("mode", "practice")
    try:
        when = datetime.fromisoformat(s["created"]).strftime("%A %d %B %Y, %H:%M")
    except (KeyError, ValueError):
        when = ""
    overall = r.get("overall_score")
    big = f0(overall) if overall is not None else "-"
    trend = ""
    if s.get("prev_score") is not None and overall is not None:
        d = overall - s["prev_score"]
        trend = (f'<span class="pill delta {"up" if d >= 0 else "down"}">{d:+.0f} vs last practice '
                 f'({f0(s["prev_score"])})</span>')

    cards = ""
    for key, title in (("content", "Your answers"), ("delivery", "Your delivery"),
                       ("body_language", "Your body language")):
        c = r.get(key) or {}
        cards += (f'<div class="card"><h3>{title}</h3><div class="num">{f0(c.get("score"))}'
                  f'<small>/10</small></div><p>{esc(c.get("summary", ""))}</p></div>')

    fixes = "".join(
        f'<div class="fix"><h3>{esc(i.get("title", ""))}</h3><p>{esc(i.get("detail", ""))}</p>'
        f'<p class="drill"><b>5-minute drill:</b> {esc(i.get("drill", ""))}</p></div>'
        for i in r.get("top_improvements", []))

    cov = r.get("jd_coverage") or {}
    cov_html = (f'<h2>Job description coverage</h2><p>{chips(cov.get("covered", []), "g")}'
                f'{chips(cov.get("missing", []), "r")}</p>'
                '<p class="sub">Green: shown in your answers. Red: important, not shown yet.</p>'
                ) if cov.get("covered") or cov.get("missing") else ""

    qa = ""
    if r.get("qa_breakdown"):
        qa = "<h2>Question by question</h2>" + "".join(
            f'<div class="fix"><h3>{esc(x.get("question", ""))} <span class="pill">'
            f'{f0(x.get("rating"))}/10</span></h3><p>{esc(x.get("feedback", ""))}</p></div>'
            for x in r["qa_breakdown"])

    brief_html = ""
    if brief:
        brief_html = (f'<h2>Role fit before you started</h2><div class="card"><div class="num">'
                      f'{f0(brief.get("fit_score"))}<small>/100 resume-to-role match</small></div>'
                      f'<p>{esc(brief.get("summary", ""))}</p><h4>Lead with</h4>{ul(brief.get("strengths", []))}'
                      f'<h4>Be ready to be probed on</h4>{ul(brief.get("gaps", []))}</div>')

    plan = f'<h2>Your practice plan</h2><ol>{"".join(f"<li>{esc(p)}</li>" for p in r.get("practice_plan", []))}</ol>' \
        if r.get("practice_plan") else ""
    err = f'<p class="coach">The final review could not be generated: {esc(s["review_error"])}</p>' \
        if s.get("review_error") else ""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Interview review</title><style>{CSS}{EXTRA_CSS}</style></head><body><main>
<h1>Interview review</h1>
<p class="sub">{esc(s.get("role", ""))} &middot; {esc(when)} &middot;
{"real interview" if mode == "live" else "practice session"}</p>
<div class="hero2"><div class="big">{big}</div><div>
<span class="pill">{esc(r.get("readiness", ""))}</span> {trend}
<p style="max-width:48ch;margin:8px 0 0">{esc(r.get("headline", ""))}</p></div></div>
{err}<div class="three">{cards}</div>
<h2>The numbers</h2>{metric_tiles(s.get("overall_speech"), s.get("overall_behavior"))}
<p class="sub" style="margin-top:8px">Camera numbers are estimates of observable behaviour, not emotions.
Eye contact means facing and looking toward the camera.</p>
<h2>Fix these first</h2>{fixes}
{ul(r.get("strengths", [])) and "<h2>What you did well</h2>" + ul(r.get("strengths", []))}
{qa}{"<h2>Answer by answer</h2>" + answer_blocks(s.get("answers", []), mode) if mode != "live" else ""}
{cov_html}{plan}{brief_html}
<footer>Privacy: audio is processed in memory and transcribed on this laptop, then discarded. No video is
stored. Transcripts and numbers are saved in the interview_sessions folder on this computer; delete it any time.
Text of your resume, the job description and transcripts is sent to the Anthropic API to produce the feedback.</footer>
</main></body></html>"""


def save(session, path, open_browser=True):
    with open(path, "w", encoding="utf-8") as f:
        f.write(build(session))
    if open_browser:
        webbrowser.open("file://" + os.path.abspath(path))
    return path
