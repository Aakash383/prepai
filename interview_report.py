"""Interview review report: session dict -> one self-contained, animated HTML file.

Dark "aurora" design with an animated readiness ring, skills radar, range gauges,
eye-contact timeline, per-question score bars, JD coverage and a practice checklist.
No external libraries are needed (fonts fall back to the system font when offline).
"""
import math
import os
import webbrowser
from datetime import datetime

from report import esc

CSS = r"""
:root{--bg:#070a12;--bg2:#0c1220;--card:rgba(255,255,255,.045);--card2:rgba(255,255,255,.07);
--line:rgba(255,255,255,.09);--ink:#eef2fb;--mute:#97a3bd;--dim:#66728c;
--good:#34d399;--mid:#fbbf24;--bad:#fb7185;--acc:#7c9cff;--acc2:#b388ff;--acc3:#38e1ff}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI",system-ui,-apple-system,sans-serif;
line-height:1.55;-webkit-font-smoothing:antialiased;overflow-x:hidden}
.aurora{position:fixed;inset:0;z-index:-1;overflow:hidden;background:radial-gradient(1200px 700px at 80% -10%,#141b3a 0%,transparent 60%),var(--bg)}
.aurora i{position:absolute;border-radius:50%;filter:blur(90px);opacity:.5;animation:float 18s ease-in-out infinite}
.aurora i:nth-child(1){width:520px;height:520px;background:#4f46e5;left:-120px;top:-100px}
.aurora i:nth-child(2){width:460px;height:460px;background:#0ea5e9;right:-100px;top:25%;animation-delay:-6s;opacity:.32}
.aurora i:nth-child(3){width:520px;height:520px;background:#a855f7;left:25%;bottom:-220px;animation-delay:-11s;opacity:.3}
@keyframes float{50%{transform:translate(40px,-30px) scale(1.08)}}
.grid{position:fixed;inset:0;z-index:-1;background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:56px 56px;mask-image:radial-gradient(ellipse at 50% 0%,#000 0%,transparent 70%);opacity:.35}
header.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;padding:12px 28px;
backdrop-filter:blur(16px);background:rgba(7,10,18,.65);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:.2px}
.logo{width:28px;height:28px;border-radius:9px;background:linear-gradient(135deg,var(--acc),var(--acc2));display:grid;place-items:center;
box-shadow:0 6px 24px rgba(124,156,255,.45)}
.logo svg{width:16px;height:16px}
.top .sp{flex:1}.top .meta{color:var(--mute);font-size:.88rem}
.btn{cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:8px 14px;border-radius:10px;font:inherit;font-size:.86rem;
transition:.2s}.btn:hover{background:var(--card2);transform:translateY(-1px)}
main{max-width:1120px;margin:0 auto;padding:34px 24px 80px}
.reveal{opacity:0;transform:translateY(22px);transition:opacity .8s cubic-bezier(.2,.7,.2,1),transform .8s cubic-bezier(.2,.7,.2,1)}
.reveal.in{opacity:1;transform:none}
h2{font-size:1.35rem;margin:54px 0 16px;display:flex;align-items:center;gap:10px;letter-spacing:-.2px}
h2 .n{font-size:.72rem;color:var(--acc);background:rgba(124,156,255,.12);border:1px solid rgba(124,156,255,.3);padding:2px 8px;border-radius:99px}
.sub{color:var(--mute);font-size:.9rem}
.glass{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:22px 24px;backdrop-filter:blur(14px);
box-shadow:0 20px 50px -25px rgba(0,0,0,.7),inset 0 1px 0 rgba(255,255,255,.05)}
.hero{display:grid;grid-template-columns:auto 1fr auto;gap:36px;align-items:center;padding:34px 38px;position:relative;overflow:hidden}
.hero:before{content:"";position:absolute;inset:-1px;border-radius:20px;padding:1px;background:linear-gradient(135deg,rgba(124,156,255,.6),transparent 40%,rgba(179,136,255,.5));
-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none}
.ring{position:relative;width:210px;height:210px}
.ring svg{transform:rotate(-90deg);width:100%;height:100%}
.ring .track{stroke:rgba(255,255,255,.08)}
.ring .bar{stroke-linecap:round;stroke-dasharray:var(--c);stroke-dashoffset:var(--c);transition:stroke-dashoffset 1.8s cubic-bezier(.2,.8,.2,1) .2s}
.ring.in .bar{stroke-dashoffset:var(--off)}
.ring .num{position:absolute;inset:0;display:grid;place-content:center;text-align:center}
.ring .num b{font-size:3.8rem;line-height:1;font-weight:800;letter-spacing:-2px;background:linear-gradient(180deg,#fff,#b9c4e0);-webkit-background-clip:text;background-clip:text;color:transparent}
.ring .num span{color:var(--mute);font-size:.8rem;letter-spacing:.14em;text-transform:uppercase;margin-top:4px}
.badge{display:inline-flex;align-items:center;gap:8px;padding:6px 14px;border-radius:99px;font-weight:600;font-size:.86rem;border:1px solid}
.badge:before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 12px currentColor;animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.4}}
.b-good{color:var(--good);background:rgba(52,211,153,.1);border-color:rgba(52,211,153,.35)}
.b-mid{color:var(--mid);background:rgba(251,191,36,.1);border-color:rgba(251,191,36,.35)}
.b-bad{color:var(--bad);background:rgba(251,113,133,.1);border-color:rgba(251,113,133,.35)}
.headline{font-size:1.45rem;line-height:1.35;font-weight:600;margin:14px 0 8px;letter-spacing:-.3px;max-width:40ch}
.delta{font-weight:700}.delta.up{color:var(--good)}.delta.down{color:var(--bad)}
.pill{display:inline-block;padding:3px 12px;border-radius:99px;border:1px solid var(--line);font-size:.82rem;background:var(--card);color:var(--mute)}
.radar{width:310px;height:auto;overflow:visible}.radar .area{fill:rgba(124,156,255,.28);stroke:var(--acc);stroke-width:2;transform-origin:125px 125px;
transform:scale(.01);transition:transform 1.4s cubic-bezier(.2,.8,.2,1) .3s}.radar.in .area{transform:scale(1)}
.radar text{fill:var(--mute);font-size:10.5px;font-family:inherit}.radar .ax{stroke:var(--line)}.radar .rg{fill:none;stroke:var(--line)}
.radar .dot{fill:#fff;stroke:var(--acc);stroke-width:2}
.three{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}
.score-card h3{margin:0;font-size:.95rem;color:var(--mute);font-weight:600;display:flex;justify-content:space-between}
.score-card .v{font-size:2.4rem;font-weight:800;letter-spacing:-1px;margin:6px 0 10px}.score-card .v small{font-size:1rem;color:var(--dim);font-weight:500}
.meter{height:7px;border-radius:9px;background:rgba(255,255,255,.08);overflow:hidden;margin-bottom:12px}
.meter i{display:block;height:100%;width:0;border-radius:9px;transition:width 1.4s cubic-bezier(.2,.8,.2,1) .2s}
.in .meter i,.meter.in i{width:var(--w)}
.score-card p{margin:0;color:#c5cee3;font-size:.92rem}
.gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.gauge{padding:16px 18px}.gauge .top2{display:flex;justify-content:space-between;align-items:baseline}
.gauge .lbl{color:var(--mute);font-size:.85rem}.gauge .val{font-size:1.5rem;font-weight:700;letter-spacing:-.5px}
.gauge .track{position:relative;height:8px;border-radius:9px;background:rgba(255,255,255,.07);margin:12px 0 8px}
.gauge .tgt{position:absolute;top:0;bottom:0;border-radius:9px;background:rgba(52,211,153,.28);border:1px solid rgba(52,211,153,.5)}
.gauge .mk{position:absolute;top:50%;width:16px;height:16px;border-radius:50%;transform:translate(-50%,-50%) scale(0);border:3px solid var(--bg);
transition:transform .6s cubic-bezier(.3,1.6,.5,1) .6s;left:var(--p)}.in .gauge .mk,.gauge.in .mk{transform:translate(-50%,-50%) scale(1)}
.gauge .note{color:var(--dim);font-size:.78rem}
.g-good .mk{background:var(--good);box-shadow:0 0 14px var(--good)}.g-ok .mk{background:var(--mid);box-shadow:0 0 14px var(--mid)}
.g-bad .mk{background:var(--bad);box-shadow:0 0 14px var(--bad)}.g-none .mk{background:var(--acc)}
.two{display:grid;grid-template-columns:1.4fr 1fr;gap:16px}
@media(max-width:860px){.hero{grid-template-columns:1fr;justify-items:center;text-align:center}.two{grid-template-columns:1fr}}
.chart svg{width:100%;height:auto;display:block}.chart .gl{stroke:var(--line);stroke-dasharray:3 5}.chart text{fill:var(--dim);font-size:11px;font-family:inherit}
.chart .band{fill:rgba(52,211,153,.1);stroke:rgba(52,211,153,.4);stroke-dasharray:4 4}
.chart .ln{fill:none;stroke:url(#lg);stroke-width:3;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:var(--len);stroke-dashoffset:var(--len);transition:stroke-dashoffset 2s ease .3s}
.chart.in .ln{stroke-dashoffset:0}.chart .ar{fill:url(#ag);opacity:0;transition:opacity 1.4s ease 1s}.chart.in .ar{opacity:1}
.hbars .row{display:grid;grid-template-columns:34px 1fr 46px;gap:12px;align-items:center;margin:12px 0}
.hbars .q{color:var(--mute);font-size:.82rem;font-weight:600}.hbars .t{height:12px;border-radius:9px;background:rgba(255,255,255,.07);overflow:hidden}
.hbars .t i{display:block;height:100%;width:0;border-radius:9px;transition:width 1.3s cubic-bezier(.2,.8,.2,1) .2s}.hbars.in .t i{width:var(--w)}
.hbars .s{font-weight:700;text-align:right}
.fix{display:grid;grid-template-columns:54px 1fr;gap:18px;margin-bottom:14px;position:relative;overflow:hidden}
.fix .idx{width:54px;height:54px;border-radius:16px;background:linear-gradient(135deg,var(--acc),var(--acc2));display:grid;place-items:center;font-weight:800;font-size:1.3rem;
box-shadow:0 10px 30px -8px rgba(124,156,255,.6)}
.fix h3{margin:2px 0 6px;font-size:1.12rem}.fix p{margin:0;color:#c5cee3;font-size:.94rem}
.drill{margin-top:14px!important;padding:12px 14px;border-radius:12px;background:rgba(56,225,255,.07);border:1px solid rgba(56,225,255,.22);color:#cdefff!important}
.drill b{color:var(--acc3)}
ul.tick{list-style:none;padding:0;margin:0}ul.tick li{position:relative;padding:10px 12px 10px 38px;margin-bottom:8px;border-radius:12px;background:var(--card);border:1px solid var(--line);font-size:.93rem;color:#d3dbee}
ul.tick li:before{content:"";position:absolute;left:12px;top:15px;width:14px;height:14px;border-radius:50%;background:var(--good);
box-shadow:0 0 12px rgba(52,211,153,.6);-webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z'/%3E%3C/svg%3E") center/contain no-repeat;mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z'/%3E%3C/svg%3E") center/contain no-repeat}
ul.tick.bad li:before{background:var(--bad);box-shadow:0 0 12px rgba(251,113,133,.6);-webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z'/%3E%3C/svg%3E") center/contain no-repeat;mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z'/%3E%3C/svg%3E") center/contain no-repeat}
.cov{display:grid;grid-template-columns:auto 1fr 1fr;gap:22px;align-items:start}
@media(max-width:860px){.cov{grid-template-columns:1fr}}
.cov h4{margin:0 0 10px;font-size:.8rem;letter-spacing:.14em;text-transform:uppercase;color:var(--mute)}
.mini{position:relative;width:120px;height:120px}.mini svg{transform:rotate(-90deg)}.mini b{position:absolute;inset:0;display:grid;place-items:center;font-size:1.5rem}
.chip{display:inline-block;margin:3px 6px 3px 0;padding:3px 11px;border-radius:99px;font-size:.82rem;border:1px solid var(--line);color:#d3dbee;background:var(--card)}
.chip.g{border-color:rgba(52,211,153,.5);background:rgba(52,211,153,.08)}.chip.r{border-color:rgba(251,113,133,.5);background:rgba(251,113,133,.08)}
details{background:var(--card);border:1px solid var(--line);border-radius:16px;margin-bottom:12px;transition:.25s;overflow:hidden}
details[open]{background:var(--card2);border-color:rgba(124,156,255,.35)}
summary{cursor:pointer;padding:16px 20px;font-weight:600;list-style:none;display:flex;gap:14px;align-items:center}summary::-webkit-details-marker{display:none}
summary .qn{flex:0 0 auto;width:34px;height:34px;border-radius:10px;background:rgba(124,156,255,.15);color:var(--acc);display:grid;place-items:center;font-size:.85rem}
summary .qt{flex:1}summary .sc{font-weight:800;padding:3px 12px;border-radius:99px;font-size:.88rem}
summary:after{content:"+";font-size:1.4rem;color:var(--mute);transition:.25s}details[open] summary:after{transform:rotate(45deg)}
.sc.good{background:rgba(52,211,153,.14);color:var(--good)}.sc.ok{background:rgba(251,191,36,.14);color:var(--mid)}.sc.bad{background:rgba(251,113,133,.14);color:var(--bad)}.sc.na{background:var(--card);color:var(--mute)}
details .in{padding:0 22px 20px}details h4{margin:18px 0 8px;font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:var(--mute)}
.tr{white-space:pre-wrap;color:#b4bfd6;font-size:.92rem;border-left:3px solid var(--acc);padding:6px 0 6px 14px;max-width:80ch}
.better{position:relative;padding:16px 18px;border-radius:14px;background:linear-gradient(135deg,rgba(124,156,255,.12),rgba(179,136,255,.1));border:1px solid rgba(124,156,255,.3);color:#e4e9f7;font-size:.94rem}
.better .btn{position:absolute;top:10px;right:10px;padding:4px 10px;font-size:.75rem}.better p{margin:0;padding-right:60px}
.plan label{display:flex;gap:14px;align-items:flex-start;padding:14px 16px;margin-bottom:10px;border-radius:14px;background:var(--card);border:1px solid var(--line);cursor:pointer;transition:.2s}
.plan label:hover{background:var(--card2)}.plan input{appearance:none;flex:0 0 auto;width:22px;height:22px;border-radius:7px;border:2px solid var(--dim);margin-top:2px;cursor:pointer;transition:.2s;display:grid;place-items:center}
.plan input:checked{background:var(--good);border-color:var(--good)}.plan input:checked:after{content:"\2713";color:#04130c;font-weight:900;font-size:.85rem}
.plan input:checked+span{text-decoration:line-through;color:var(--dim)}
.err{border-color:rgba(251,113,133,.4)!important;background:rgba(251,113,133,.07)}
.dlg p{margin:8px 0}.dlg .who{font-weight:700}.dlg .you{color:var(--acc3)}.dlg .int{color:var(--acc2)}
footer{margin-top:70px;padding-top:22px;border-top:1px solid var(--line);color:var(--dim);font-size:.8rem;max-width:90ch}
footer .pw{display:inline-flex;align-items:center;gap:8px;color:var(--mute);margin-bottom:8px;font-weight:600}
.spark{display:inline-block;width:14px;height:14px;vertical-align:-2px}
@media print{.aurora,.grid,header.top .btn{display:none}body{background:#fff;color:#111}.glass,details,.fix,ul.tick li{background:#fff!important;border-color:#ccc!important;box-shadow:none}
.reveal{opacity:1!important;transform:none!important}details .in{display:block}.headline,.ring .num b{color:#111!important;-webkit-text-fill-color:#111}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.reveal{opacity:1;transform:none}}
"""

JS = r"""
(function(){
var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('in');
e.target.querySelectorAll('[data-count]').forEach(count);io.unobserve(e.target)}})},{threshold:.15});
document.querySelectorAll('.reveal,.ring,.radar,.chart,.hbars,.meter,.gauge').forEach(function(el){io.observe(el)});
function count(el){var to=parseFloat(el.dataset.count),t0=null,d=1500;if(isNaN(to))return;
function step(t){if(!t0)t0=t;var p=Math.min(1,(t-t0)/d),e=1-Math.pow(1-p,3);el.textContent=Math.round(to*e);if(p<1)requestAnimationFrame(step)}
requestAnimationFrame(step)}
document.querySelectorAll('[data-count]').forEach(function(el){if(el.closest('.reveal'))return;count(el)});
document.querySelectorAll('[data-copy]').forEach(function(b){b.addEventListener('click',function(){
navigator.clipboard&&navigator.clipboard.writeText(b.parentElement.querySelector('p').innerText);b.textContent='Copied';setTimeout(function(){b.textContent='Copy'},1400)})});
var key='tether-plan-'+document.body.dataset.sid;
try{var saved=JSON.parse(localStorage.getItem(key)||'[]');document.querySelectorAll('.plan input').forEach(function(c,i){c.checked=!!saved[i];
c.addEventListener('change',function(){var s=[].map.call(document.querySelectorAll('.plan input'),function(x){return x.checked});localStorage.setItem(key,JSON.stringify(s))})})}catch(e){}
var p=document.getElementById('print');if(p)p.addEventListener('click',function(){document.querySelectorAll('details').forEach(function(d){d.open=true});window.print()});
})();
"""


# ------------------------------------------------------------------ helpers
def f0(x, d=0):
    try:
        return f"{float(x):.{d}f}"
    except (TypeError, ValueError):
        return "-"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def tone(score100):
    return "good" if score100 >= 75 else "mid" if score100 >= 50 else "bad"


def tone10(v):
    return "good" if v >= 7.5 else "ok" if v >= 5 else "bad"


COL = {"good": "#34d399", "mid": "#fbbf24", "ok": "#fbbf24", "bad": "#fb7185"}


def ul(items, cls=""):
    return f'<ul class="tick {cls}">' + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>" if items else ""


def chips(items, cls):
    return "".join(f'<span class="chip {cls}">{esc(i)}</span>' for i in items)


def band_score(v, lo, hi, falloff):
    """100 inside [lo, hi], falling linearly to 0 at `falloff` outside it."""
    if lo <= v <= hi:
        return 100
    d = lo - v if v < lo else v - hi
    return max(0, 100 - 100 * d / falloff)


# ------------------------------------------------------------------ components
def ring(score, size=210, stroke=14, big=True):
    r = (size - stroke) / 2
    c = 2 * math.pi * r
    pct = max(0, min(100, score or 0))
    off = c * (1 - pct / 100)
    col = COL[tone(pct)]
    gid = f"rg{int(size)}{int(pct)}"
    inner = (f'<div class="num"><b data-count="{pct:.0f}">0</b><span>readiness</span></div>' if big else
             f'<b data-count="{pct:.0f}">0</b>')
    return (f'<div class="{"ring" if big else "mini"}" style="--c:{c:.1f};--off:{off:.1f}">'
            f'<svg viewBox="0 0 {size} {size}"><defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{col}"/><stop offset="1" stop-color="#7c9cff"/></linearGradient></defs>'
            f'<circle class="track" cx="{size / 2}" cy="{size / 2}" r="{r}" fill="none" stroke-width="{stroke}"/>'
            f'<circle class="bar" cx="{size / 2}" cy="{size / 2}" r="{r}" fill="none" stroke="url(#{gid})" stroke-width="{stroke}"/>'
            f'</svg>{inner}</div>')


def radar(axes):
    """axes = [(label, 0-100)]"""
    cx = cy = 125
    R = 82
    n = len(axes)
    pts, labels, spokes = [], [], []
    for i, (lab, v) in enumerate(axes):
        a = -math.pi / 2 + 2 * math.pi * i / n
        x, y = cx + R * math.cos(a), cy + R * math.sin(a)
        spokes.append(f'<line class="ax" x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}"/>')
        k = max(0.04, min(1, v / 100))
        px, py = cx + R * k * math.cos(a), cy + R * k * math.sin(a)
        pts.append((px, py))
        lx, ly = cx + (R + 24) * math.cos(a), cy + (R + 20) * math.sin(a)
        anchor = "middle" if abs(math.cos(a)) < .3 else ("start" if math.cos(a) > 0 else "end")
        labels.append(f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="{anchor}">{esc(lab)}</text>')
    rings = "".join(
        f'<polygon class="rg" points="' + " ".join(
            f"{cx + R * f * math.cos(-math.pi / 2 + 2 * math.pi * i / n):.1f},{cy + R * f * math.sin(-math.pi / 2 + 2 * math.pi * i / n):.1f}"
            for i in range(n)) + '"/>' for f in (.33, .66, 1))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>' for x, y in pts)
    return (f'<svg class="radar" viewBox="-40 0 330 250">{rings}{"".join(spokes)}'
            f'<polygon class="area" points="{poly}"/>{dots}{"".join(labels)}</svg>')


def gauge(label, value, unit, lo, hi, tlo, thi, note, ok_lo=None, ok_hi=None, decimals=0):
    v = num(value)
    if v is None:
        return ""
    pos = max(0, min(100, (v - lo) / (hi - lo) * 100))
    if tlo is None:
        cls = "g-none"
    elif tlo <= v <= thi:
        cls = "g-good"
    elif ok_lo is not None and ok_lo <= v <= ok_hi:
        cls = "g-ok"
    else:
        cls = "g-bad"
    band = ""
    if tlo is not None:
        l, w = (tlo - lo) / (hi - lo) * 100, (thi - tlo) / (hi - lo) * 100
        band = f'<div class="tgt" style="left:{max(0, l):.1f}%;width:{min(100 - max(0, l), w):.1f}%"></div>'
    return (f'<div class="glass gauge {cls}"><div class="top2"><span class="lbl">{esc(label)}</span>'
            f'<span class="val">{v:.{decimals}f}<small style="font-size:.8rem;color:var(--dim)"> {esc(unit)}</small></span></div>'
            f'<div class="track">{band}<div class="mk" style="--p:{pos:.1f}%"></div></div>'
            f'<div class="note">{esc(note)}</div></div>')


def metric_gauges(sp, bh):
    g = []
    if bh:
        g.append(gauge("Eye contact", bh["eye_contact_pct"], "%", 0, 100, 55, 80, "target 55-80% toward the camera", 40, 92))
        g.append(gauge("Head movement", bh["head_motion_dps"], "deg/s", 0, 15, 0, 6, "steady is under 6", 0, 10, 1))
        g.append(gauge("Looked away 2s+", bh["away_events"], "times", 0, 12, 0, 4, f"longest {bh['longest_away_s']:.0f}s", 0, 8))
        g.append(gauge("Leaning back", bh["lean_back_pct"], "%", 0, 100, 0, 20, "of the time", 0, 40))
        g.append(gauge("Smiling", bh["smile_pct"], "%", 0, 100, 15, 60, "warmth on camera", 5, 80))
    if sp:
        g.append(gauge("Speaking pace", sp["wpm"], "wpm", 60, 220, 120, 165, "target 120-165", 100, 185))
        g.append(gauge("Filler words", sp["filler_per_min"], "/min", 0, 10, 0, 3, f"{sp['fillers']} total, aim under 3", 0, 6, 1))
        g.append(gauge("Hedging", sp["hedges"], "times", 0, 12, 0, 3, "'I think', 'maybe'...", 0, 8))
        g.append(gauge("First word after", sp["latency"], "s", 0, 12, 1, 4, "after the question", 0, 7, 1))
        g.append(gauge("Long pauses", sp["long_pauses"], "", 0, 10, None, None, f"longest {sp['longest_pause']:.1f}s"))
    return '<div class="gauges">' + "".join(x for x in g if x) + "</div>"


def eye_chart(vals):
    vals = [num(v) for v in vals if num(v) is not None]
    if not vals:
        return ""
    W, H, L, B, T = 640, 230, 44, 28, 14
    iw, ih = W - L - 34, H - B - T
    xs = [L + (iw * i / (len(vals) - 1) if len(vals) > 1 else iw / 2) for i in range(len(vals))]
    ys = [T + ih * (1 - max(0, min(100, v)) / 100) for v in vals]
    if len(vals) == 1:
        xs, ys = [L, L + iw], [ys[0], ys[0]]
        vals = vals * 2
    d = f"M{xs[0]:.1f},{ys[0]:.1f}"
    for i in range(1, len(xs)):
        mx = (xs[i - 1] + xs[i]) / 2
        d += f" C{mx:.1f},{ys[i - 1]:.1f} {mx:.1f},{ys[i]:.1f} {xs[i]:.1f},{ys[i]:.1f}"
    area = d + f" L{xs[-1]:.1f},{T + ih} L{xs[0]:.1f},{T + ih} Z"
    bt, bb = T + ih * (1 - .80), T + ih * (1 - .55)
    grid = "".join(f'<line class="gl" x1="{L}" x2="{L + iw}" y1="{T + ih * (1 - p / 100):.1f}" y2="{T + ih * (1 - p / 100):.1f}"/>'
                   f'<text x="{L - 8}" y="{T + ih * (1 - p / 100) + 4:.1f}" text-anchor="end">{p}%</text>' for p in (0, 25, 50, 75, 100))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#fff" stroke="#7c9cff" stroke-width="2"/>'
                   f'<text x="{x:.1f}" y="{T + ih + 18}" text-anchor="middle">min {i + 1}</text>' for i, (x, y) in enumerate(zip(xs, ys)) if len(vals) <= 14)
    return (f'<div class="chart"><svg viewBox="0 0 {W} {H}"><defs>'
            '<linearGradient id="lg" x1="0" x2="1"><stop offset="0" stop-color="#38e1ff"/><stop offset="1" stop-color="#b388ff"/></linearGradient>'
            '<linearGradient id="ag" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#7c9cff" stop-opacity=".45"/><stop offset="1" stop-color="#7c9cff" stop-opacity="0"/></linearGradient></defs>'
            f'{grid}<rect class="band" x="{L}" y="{bt:.1f}" width="{iw}" height="{bb - bt:.1f}" rx="6"/>'
            f'<path class="ar" d="{area}"/><path class="ln" style="--len:2200" d="{d}"/>{dots}</svg></div>')


def q_bars(answers):
    best = {}
    for a in answers:
        s = (a.get("eval") or {}).get("score")
        if isinstance(s, (int, float)):
            best[a["q_idx"]] = s  # last attempt wins
    if len(best) < 1:
        return ""
    rows = "".join(
        f'<div class="row"><span class="q">Q{qi + 1}</span><div class="t"><i style="--w:{max(3, s):.0f}%;background:linear-gradient(90deg,{COL[tone(s)]},#7c9cff)"></i></div>'
        f'<span class="s" style="color:{COL[tone(s)]}">{s:.0f}</span></div>' for qi, s in sorted(best.items()))
    return f'<div class="glass hbars">{rows}</div>'


def answer_blocks(answers):
    by_q = {}
    for a in answers:
        by_q.setdefault(a["q_idx"], []).append(a)
    out = []
    for qi in sorted(by_q):
        group = by_q[qi]
        scores = [(g.get("eval") or {}).get("score") for g in group]
        last = scores[-1]
        cls = "na" if not isinstance(last, (int, float)) else tone10(last / 10)
        sc = f'<span class="sc {cls}">{last:.0f}</span>' if isinstance(last, (int, float)) else '<span class="sc na">n/a</span>'
        delta = ""
        if len(group) > 1 and isinstance(scores[0], (int, float)) and isinstance(last, (int, float)):
            d = last - scores[0]
            delta = f' <span class="delta {"up" if d >= 0 else "down"}" style="font-size:.8rem">{d:+.0f} after retry</span>'
        inner = []
        for g in group:
            ev = g.get("eval") or {}
            s = f"{ev['score']:.0f}/100" if isinstance(ev.get("score"), (int, float)) else "no score"
            lead = f'Attempt {g["attempt"]} &middot; ' if len(group) > 1 else ""
            at = f' &middot; at {int(g["t_start"] // 60)}:{int(g["t_start"] % 60):02d}' if g.get("t_start") is not None else ""
            inner.append(f'<h4>{lead}{s} &middot; {g.get("duration", 0):.0f}s{at}</h4>')
            rel = ev.get("relevance") or {}
            if rel.get("verdict"):
                rc = "g" if rel["verdict"] == "relevant" else "r" if rel["verdict"] == "off topic" else ""
                inner.append(f'<p><span class="chip {rc}">{esc(rel["verdict"])}</span> {esc(rel.get("note", ""))}</p>')
            if g.get("error"):
                inner.append(f'<p class="sub">Could not analyse this answer: {esc(g["error"])}</p>')
                continue
            if g.get("transcript"):
                inner.append(f'<div class="tr">{esc(g["transcript"])}</div>')
            if ev.get("strengths"):
                inner.append("<h4>What worked</h4>" + ul(ev["strengths"]))
            if ev.get("improvements"):
                inner.append("<h4>Improve</h4>" + ul(ev["improvements"], "bad"))
            if ev.get("missing_keywords"):
                inner.append("<h4>Job-description terms you could use</h4>" + chips(ev["missing_keywords"], "r"))
            if ev.get("better_answer"):
                inner.append('<h4>A stronger version, built only from your own facts</h4>'
                             f'<div class="better"><button class="btn" data-copy>Copy</button><p>{esc(ev["better_answer"])}</p></div>')
        out.append(f'<details><summary><span class="qn">Q{qi + 1}</span><span class="qt">{esc(group[0]["question"])}{delta}</span>{sc}</summary>'
                   f'<div class="in">{"".join(inner)}</div></details>')
    return "".join(out)


def section(title, body, n=None):
    tag = f'<span class="n">{n}</span>' if n else ""
    return f'<section class="reveal"><h2>{esc(title)}{tag}</h2>{body}</section>'


# ------------------------------------------------------------------ page
def build(s):
    r = s.get("review") or {}
    brief = s.get("brief") or {}
    mode = s.get("mode", "practice")
    sp, bh = s.get("overall_speech"), s.get("overall_behavior")
    try:
        when = datetime.fromisoformat(s["created"]).strftime("%a %d %b %Y, %H:%M")
    except (KeyError, ValueError):
        when = ""
    sid = (s.get("created") or "x").replace(":", "")
    overall = num(r.get("overall_score"))
    ready = r.get("readiness", "")
    tcls = {"good": "b-good", "mid": "b-mid", "bad": "b-bad"}[tone(overall or 0)]

    trend = ""
    if s.get("prev_score") is not None and overall is not None:
        d = overall - s["prev_score"]
        trend = (f'<span class="pill"><span class="delta {"up" if d >= 0 else "down"}">'
                 f'{"&#9650;" if d >= 0 else "&#9660;"} {abs(d):.0f}</span> vs last practice ({f0(s["prev_score"])})</span>')

    # radar axes from the model's scores + measured numbers
    axes = []
    for key, lab in (("content", "Content"), ("delivery", "Delivery"), ("body_language", "Body language")):
        c = r.get(key) or {}
        v = num(c.get("score"))
        if v is not None and not (key == "body_language" and not bh):
            axes.append((lab, v * 10))
    if bh:
        axes.append(("Eye contact", band_score(bh["eye_contact_pct"], 55, 80, 55)))
    if sp:
        axes.append(("Pace", band_score(sp["wpm"], 120, 165, 90)))
        axes.append(("Fluency", band_score(sp["filler_per_min"], 0, 3, 6)))
    radar_html = radar(axes) if len(axes) >= 3 else ""

    err = (f'<div class="glass err" style="margin-bottom:18px"><b>The final review could not be generated.</b>'
           f'<p class="sub" style="margin:6px 0 0">{esc(s["review_error"])}</p></div>') if s.get("review_error") else ""

    hero = (f'<div class="glass hero reveal">{ring(overall) if overall is not None else ""}'
            f'<div><span class="badge {tcls}">{esc(ready) or "Review"}</span>'
            f'<div class="headline">{esc(r.get("headline", "Your session is ready to review."))}</div>{trend}</div>'
            f'{radar_html}</div>')

    cards = ""
    for key, title in (("content", "Your answers"), ("delivery", "Your delivery"), ("body_language", "Your body language")):
        if key == "body_language" and not bh:
            continue
        c = r.get(key) or {}
        v = num(c.get("score"))
        if v is None:
            continue
        col = COL[tone10(v)]
        cards += (f'<div class="glass score-card"><h3>{title}</h3><div class="v" style="color:{col}">{v:.0f}<small>/10</small></div>'
                  f'<div class="meter"><i style="--w:{v * 10:.0f}%;background:linear-gradient(90deg,{col},#7c9cff)"></i></div>'
                  f'<p>{esc(c.get("summary", ""))}</p></div>')
    cards = f'<div class="three reveal" style="margin-top:18px">{cards}</div>' if cards else ""

    body = [err, hero, cards]

    gauges = metric_gauges(sp, bh)
    body.append(section("The numbers", gauges + '<p class="sub" style="margin-top:12px">Camera numbers are estimates of observable behaviour, not emotions. '
                        'The green band on each gauge is the healthy range.</p>', "measured"))

    ec = (bh or {}).get("ec_by_minute") or []
    qb = q_bars(s.get("answers", [])) if mode != "live" or s.get("separated") else ""
    if ec or qb:
        left = (f'<div class="glass"><div class="sub" style="margin-bottom:6px">Eye contact by minute</div>{eye_chart(ec)}</div>' if ec else "")
        right = (f'<div><div class="sub" style="margin:0 0 6px 6px">Score by question</div>{qb}</div>' if qb else "")
        body.append(section("Trends", f'<div class="two">{left}{right}</div>' if left and right else (left or right), "over time"))

    if r.get("top_improvements"):
        fixes = "".join(
            f'<div class="glass fix"><div class="idx">{i + 1}</div><div><h3>{esc(x.get("title", ""))}</h3><p>{esc(x.get("detail", ""))}</p>'
            f'<p class="drill"><b>5-minute drill &rarr;</b> {esc(x.get("drill", ""))}</p></div></div>'
            for i, x in enumerate(r["top_improvements"]))
        body.append(section("Fix these first", fixes, "highest impact"))

    if r.get("strengths"):
        body.append(section("What you did well", ul(r["strengths"])))

    cov = r.get("jd_coverage") or {}
    if cov.get("covered") or cov.get("missing"):
        nc, nm = len(cov.get("covered", [])), len(cov.get("missing", []))
        pct = round(100 * nc / (nc + nm)) if nc + nm else 0
        body.append(section("Job description coverage",
                            f'<div class="glass cov">{ring(pct, 120, 11, False)}'
                            f'<div><h4>Shown in your answers</h4>{ul(cov.get("covered", []))}</div>'
                            f'<div><h4>Not shown yet</h4>{ul(cov.get("missing", []), "bad")}</div></div>', f"{pct}% covered"))

    if r.get("qa_breakdown"):
        rows = "".join(
            f'<div class="glass fix"><div class="idx" style="background:linear-gradient(135deg,{COL[tone10(num(x.get("rating")) or 0)]},#7c9cff)">{f0(x.get("rating"))}</div>'
            f'<div><h3>{esc(x.get("question", ""))}</h3><p>{esc(x.get("feedback", ""))}</p></div></div>' for x in r["qa_breakdown"])
        body.append(section("Question by question", rows))

    eff = "practice" if (mode != "live" or s.get("separated")) else "live"
    if eff == "practice" and s.get("answers"):
        body.append(section("Answer by answer", answer_blocks(s["answers"]), "tap to expand"))

    if r.get("practice_plan"):
        items = "".join(f'<label><input type="checkbox"><span>{esc(p)}</span></label>' for p in r["practice_plan"])
        body.append(section("Your practice plan", f'<div class="plan">{items}</div>', "tick them off"))

    if brief:
        fit = num(brief.get("fit_score")) or 0
        body.append(section("Role fit before you started",
                            f'<div class="glass cov">{ring(fit, 120, 11, False)}<div><h4>Lead with</h4>{ul(brief.get("strengths", []))}</div>'
                            f'<div><h4>Be ready to be probed on</h4>{ul(brief.get("gaps", []), "bad")}</div></div>'
                            f'<p class="sub" style="margin-top:10px">{esc(brief.get("summary", ""))}</p>', "resume vs role"))

    if s.get("dialogue"):
        rows = "".join(f'<p class="tr"><span class="who {"you" if sp_ == "You" else "int"}">[{int(t // 60)}:{int(t % 60):02d}] {esc(sp_)}:</span> {esc(tx)}</p>'
                       for t, sp_, tx in s["dialogue"])
        body.append(section("Full conversation", f'<details class="dlg"><summary><span class="qt">Show transcript</span></summary><div class="in">{rows}</div></details>'))

    logo = ('<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M4 12h4l2-6 4 12 2-6h4"/></svg>')
    footer = ('<footer><div class="pw">&#128274; Privacy</div><br>Audio (your microphone and, in real-interview mode, the call audio) is processed in memory and '
              'transcribed on this laptop, then discarded. No video is stored. Transcripts and numbers are saved in the interview_sessions folder on this computer; '
              'delete it any time. Text of your resume, the job description and transcripts is sent to the Gemini API to produce the feedback.</footer>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Interview review - {esc(s.get("role", ""))}</title>'
            '<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">'
            f'<style>{CSS}</style><noscript><style>.reveal{{opacity:1;transform:none}}</style></noscript></head><body data-sid="{esc(sid)}"><div class="aurora"><i></i><i></i><i></i></div><div class="grid"></div>'
            f'<header class="top"><div class="brand"><span class="logo">{logo}</span>Tether</div><span class="meta">{esc(s.get("role", ""))} &middot; {esc(when)} &middot; '
            f'{"real interview" if mode == "live" else "practice session"}</span><span class="sp"></span>'
            f'<button class="btn" id="print">Save as PDF</button></header>'
            f'<main>{"".join(body)}{footer}</main><script>{JS}</script></body></html>')


def save(session, path, open_browser=True):
    with open(path, "w", encoding="utf-8") as f:
        f.write(build(session))
    if open_browser:
        webbrowser.open("file://" + os.path.abspath(path))
    return path
