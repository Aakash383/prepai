# Tether + Gemini: setup in 5 steps

1. **Get a free key**: open https://aistudio.google.com/apikey, sign in with Google, click **Create API key**, copy it (starts with `AIza`).
2. **Install**: `pip install -r requirements.txt`  (this installs `google-genai`; the old `anthropic` package is no longer needed).
3. **Give Tether the key** (pick one):
   - Easiest: run `python app.py`, click **Add Gemini key** (top right), paste, press **Test key**.
   - Or set an environment variable before launching:
     - Windows (cmd): `set GEMINI_API_KEY=AIza...`
     - Windows (PowerShell): `$env:GEMINI_API_KEY="AIza..."`
     - Mac/Linux: `export GEMINI_API_KEY=AIza...`
4. **Run**: `python app.py` opens the new web UI at http://127.0.0.1:8765.
   (Old Tk window: `python app_classic.py`. Command line: `python interview.py --resume resume.pdf --role "SOC Analyst" --jd jd.txt`)
5. **Pick models (optional)**: defaults to `gemini-3.5-flash`. Override with `TETHER_MODEL` (final review) and `TETHER_FAST_MODEL` (per-answer feedback).
   If a model name is unavailable for your key, Tether automatically falls back to `gemini-2.5-flash`.

## Files changed for Gemini
| File | Change |
|---|---|
| `llm.py` (new) | One Gemini wrapper: JSON mode, retries on 429/503, model fallback, key test |
| `coach.py` | Uses `llm.generate_json` instead of the Anthropic client |
| `report.py` | Focus-coach note now uses `llm.generate_text` |
| `interview.py`, `live.py`, `interview_report.py` | Key check + wording updated |
| `app.py` + `ui/index.html` (new) | The new web UI |
| `interview_report.py` | Rebuilt: animated score ring, radar, gauges, charts |
| `app_classic.py` | The old Tk window, updated for Gemini |

Free-tier keys have rate limits. If you see a 429 message during a demo, wait a minute (Tether already retries automatically).
