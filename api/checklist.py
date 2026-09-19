"""POST /api/checklist  {"brief": "..."}  ->  {"items": [{text, kind, terms}], "model": "..."}

Turns a campaign brief into a checklist through OpenRouter.
Vercel runs this file as a serverless function; server.py reuses it locally.
The key comes from the OPENROUTER_API_KEY environment variable and never reaches the browser.
"""
import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

SYSTEM = """You turn an influencer campaign brief into a checklist a creator's reel is checked against.
Return ONLY a JSON array. No preamble, no explanation, no markdown.
Each element: {"text": string, "kind": "speech" | "caption" | "manual", "terms": [string]}
- text: one requirement the creator must act on, in plain words, close to the brief's wording.
- kind "caption": must appear in the post caption (tags, @handles, hashtags, comment keywords, links).
- kind "speech": must be said on camera or in the voiceover (claims, CTA, product points).
- kind "manual": visual, only a person can judge it by watching (what is shown, footage used, modes or
  settings used in the tool, style). Instructions about how to make the video ("use X", "show Y") are manual.
- terms: for caption items, the exact handles/hashtags/keywords, lowercase. For speech items, 2 to 4 single
  lowercase words (or "sign up"-style fixed phrases) that any wording of the point would still contain,
  e.g. "free", not "free to use". For manual items, [].
Skip headings, background and anything the creator does not have to act on. Do not merge separate requirements."""


def model():
    return os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")


def strip_fences(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("["), s.rfind("]")
    return s[start:end + 1] if start != -1 and end != -1 else s


def build_checklist(brief):
    body = json.dumps({
        "model": model(),
        "temperature": 0.1,
        "max_tokens": 1500,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": brief}],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}",
                 "Content-Type": "application/json", "X-Title": "Reel Check"})
    with urllib.request.urlopen(req, timeout=55) as r:
        raw = json.load(r)["choices"][0]["message"]["content"]
    try:
        items = json.loads(strip_fences(raw))
    except json.JSONDecodeError:
        print("Model did not return JSON:\n", raw)
        raise
    out = []
    for it in items:
        text = str(it.get("text", "")).strip()
        kind = it.get("kind") if it.get("kind") in ("speech", "caption", "manual") else "manual"
        terms = [] if kind == "manual" else [str(t).strip().lower() for t in it.get("terms", []) if str(t).strip()]
        if text:
            out.append({"text": text, "kind": kind, "terms": terms[:6]})
    return out


def respond(h):
    """Handle a POST on any BaseHTTPRequestHandler: Vercel's or server.py's."""
    def send(code, data):
        payload = json.dumps(data).encode()
        h.send_response(code)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(payload)))
        h.end_headers()
        h.wfile.write(payload)

    if not os.environ.get("OPENROUTER_API_KEY"):
        return send(503, {"error": "OPENROUTER_API_KEY is not set"})
    try:
        brief = json.loads(h.rfile.read(int(h.headers.get("Content-Length", 0))))["brief"]
        send(200, {"items": build_checklist(brief), "model": model()})
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        print("OpenRouter error", e.code, detail)
        send(502, {"error": f"OpenRouter {e.code}: {detail}"})
    except Exception as e:
        print("Checklist failed:", repr(e))
        send(500, {"error": str(e)})


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        respond(self)
