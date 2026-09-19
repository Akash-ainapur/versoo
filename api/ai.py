"""POST /api/ai  — the two AI jobs in Reel Check, through OpenRouter.

  {"task": "checklist", "brief": "..."}
      -> {"items": [{text, kind, terms}], "model": "..."}
  {"task": "check", "items": [{id, text, kind}], "caption": "...", "script": "..."}
      -> {"results": [{id, verdict, evidence}], "model": "..."}

Vercel runs this file as a serverless function; server.py reuses it locally.
The key comes from OPENROUTER_API_KEY and never reaches the browser.
"""
import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

CHECKLIST_PROMPT = """You turn an influencer campaign brief into a checklist a creator's reel is checked against.
Return ONLY a JSON array. No preamble, no explanation, no markdown.
Each element: {"text": string, "kind": "speech" | "caption" | "manual", "terms": [string]}
- text: one requirement the creator must act on, in plain words, close to the brief's wording.
- kind "caption": must appear in the post caption (tags, @handles, hashtags, comment keywords, links).
- kind "speech": must be said on camera or in the voiceover (claims, CTA, product points).
- kind "manual": visual, only a person can judge it by watching (what is shown, footage used, modes or
  settings used in the tool, style). Instructions about how to make the video ("use X", "show Y") are manual.
- terms: for caption items, the exact handles/hashtags/keywords, lowercase. For speech items, 2 to 4 single
  lowercase words that any wording of the point would still contain. For manual items, [].
Skip headings, background and anything the creator does not have to act on. Do not merge separate requirements."""

CHECK_PROMPT = """You check a creator's reel submission against a brand's checklist.
Input JSON: {"items": [{"id", "text", "kind"}], "caption": string, "script": string}.
- kind "caption": judge ONLY against the caption. @handles, #hashtags and quoted keywords must appear exactly
  (case-insensitive). Everything else in a caption item may be worded freely.
- kind "speech": judge ONLY against the script (what the creator says on camera). Any wording that clearly
  covers the point counts; exact words are not required. "costs nothing" covers "say it is free".
For every item return {"id": the same id, "verdict": "pass" | "miss" | "unsure", "evidence": string}.
- pass: clearly covered. evidence = the shortest exact quote (max 12 words) from the caption or script that covers it.
- miss: clearly not covered. evidence = one short, friendly sentence naming what is missing. Never scold.
- unsure: partly covered or ambiguous. evidence = one short sentence on what is unclear.
Return ONLY a JSON array with one element per item. No markdown, no commentary."""


def model():
    return os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")


def strip_fences(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("["), s.rfind("]")
    return s[start:end + 1] if start != -1 and end != -1 else s


def ask(system, user, max_tokens):
    body = json.dumps({
        "model": model(), "temperature": 0.1, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}",
                 "Content-Type": "application/json", "X-Title": "Reel Check"})
    with urllib.request.urlopen(req, timeout=55) as r:
        raw = json.load(r)["choices"][0]["message"]["content"]
    try:
        return json.loads(strip_fences(raw))
    except json.JSONDecodeError:
        print("Model did not return JSON:\n", raw)
        raise


def build_checklist(brief):
    out = []
    for it in ask(CHECKLIST_PROMPT, brief, 1500):
        text = str(it.get("text", "")).strip()
        kind = it.get("kind") if it.get("kind") in ("speech", "caption", "manual") else "manual"
        terms = [] if kind == "manual" else [str(t).strip().lower() for t in it.get("terms", []) if str(t).strip()]
        if text:
            out.append({"text": text, "kind": kind, "terms": terms[:6]})
    return out


def check(items, caption, script):
    items = [{"id": str(i["id"]), "text": str(i["text"]), "kind": i["kind"]}
             for i in items if i.get("kind") in ("speech", "caption")]
    if not items:
        return []
    ids = {i["id"] for i in items}
    user = json.dumps({"items": items, "caption": caption or "", "script": script or ""}, ensure_ascii=False)
    out = []
    for r in ask(CHECK_PROMPT, user, 2000):
        if str(r.get("id")) in ids:
            verdict = r.get("verdict") if r.get("verdict") in ("pass", "miss", "unsure") else "unsure"
            out.append({"id": str(r["id"]), "verdict": verdict, "evidence": str(r.get("evidence", ""))[:220]})
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
        body = json.loads(h.rfile.read(int(h.headers.get("Content-Length", 0))))
        task = body.get("task")
        if task == "checklist":
            return send(200, {"items": build_checklist(body["brief"]), "model": model()})
        if task == "check":
            return send(200, {"results": check(body["items"], body.get("caption"), body.get("script")), "model": model()})
        send(400, {"error": "task must be checklist or check"})
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        print("OpenRouter error", e.code, detail)
        send(502, {"error": f"OpenRouter {e.code}: {detail}"})
    except Exception as e:
        print("AI call failed:", repr(e))
        send(500, {"error": str(e)})


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        respond(self)
