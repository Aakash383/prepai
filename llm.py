"""Gemini wrapper used by the whole project (replaces the Anthropic client).

    llm.generate_json(system, user, model=..., max_tokens=...)  -> dict
    llm.generate_text(system, user, model=..., max_tokens=...)  -> str
    llm.has_key() / llm.set_key(key) / llm.check_key()

Key: set GEMINI_API_KEY (GOOGLE_API_KEY also works). Get one free at https://aistudio.google.com/apikey
Models: TETHER_MODEL (deep review) and TETHER_FAST_MODEL (per-answer feedback) can be overridden.
If a model name is not available for your key, the next one in FALLBACK_MODELS is tried automatically.
"""
import json
import os
import re
import time

MAIN_MODEL = os.environ.get("TETHER_MODEL", "gemini-3.5-flash")
FAST_MODEL = os.environ.get("TETHER_FAST_MODEL", "gemini-3.5-flash")
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3-flash-preview"]

_client = None


def _key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""


def has_key():
    return bool(_key())


def set_key(key):
    """Use a new key (also resets the cached client)."""
    global _client
    key = (key or "").strip()
    if key:
        os.environ["GEMINI_API_KEY"] = key
        os.environ.pop("GOOGLE_API_KEY", None)
    _client = None


def client():
    global _client
    if _client is None:
        if not _key():
            raise RuntimeError("GEMINI_API_KEY is not set")
        from google import genai
        _client = genai.Client(api_key=_key())
    return _client


def _models_to_try(model):
    seen, out = set(), []
    for m in [model] + FALLBACK_MODELS:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _is_missing_model(err):
    s = str(err).lower()
    return "404" in s or "not found" in s or "is not supported" in s


def _is_transient(err):
    s = str(err).lower()
    return any(x in s for x in ("429", "500", "502", "503", "504", "unavailable", "overloaded",
                                "deadline", "timed out", "timeout", "resource_exhausted"))


def _call(model, system, contents, max_tokens, json_mode):
    from google.genai import types
    cfg = dict(system_instruction=system, max_output_tokens=max_tokens, temperature=0.4)
    if json_mode:
        cfg["response_mime_type"] = "application/json"
    resp = client().models.generate_content(
        model=model, contents=contents, config=types.GenerateContentConfig(**cfg))
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty response (it may have been blocked or cut off)")
    return text


def _generate(system, user, model, max_tokens, json_mode):
    """Retry transient errors with backoff; fall back to another model if the name is unknown."""
    last = None
    for m in _models_to_try(model):
        for attempt in range(4):
            try:
                return _call(m, system, user, max_tokens, json_mode)
            except Exception as e:                      # noqa: BLE001 - SDK raises many types
                last = e
                if _is_missing_model(e):
                    break                               # try the next model name
                if _is_transient(e) and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
    raise last


def parse_json(text):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            return json.loads(t[i:j + 1])
        raise


def generate_json(system, user, model=None, max_tokens=4096):
    model = model or MAIN_MODEL
    # Gemini counts its internal "thinking" tokens inside max_output_tokens, so leave generous room.
    budget = max(max_tokens * 4, 8192)
    prompt = user
    for attempt in range(2):
        text = _generate(system, prompt, model, budget, True)
        try:
            return parse_json(text)
        except json.JSONDecodeError:
            if attempt == 1:
                raise
            prompt = user + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."


def generate_text(system, user, model=None, max_tokens=1024):
    return _generate(system, user, model or MAIN_MODEL, max(max_tokens * 4, 4096), False).strip()


def check_key(key=None):
    """Cheap live test of a key. Returns (ok, message)."""
    old = _key()
    try:
        if key:
            set_key(key)
        out = generate_text("Reply with one word.", "Say: ready", model=FAST_MODEL, max_tokens=16)
        return True, "Key works (" + out[:20] + ")"
    except Exception as e:                              # noqa: BLE001
        s = str(e)
        if "API_KEY_INVALID" in s or "API key not valid" in s or "400" in s and "key" in s.lower():
            return False, "Gemini rejected this key. Copy it again from aistudio.google.com/apikey"
        if "429" in s or "RESOURCE_EXHAUSTED" in s:
            return False, "Key is valid but the free quota is used up right now. Wait a minute and retry."
        return False, s[:240]
    finally:
        if key and not _key() and old:
            set_key(old)
