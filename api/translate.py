"""Vercel serverless function: POST /api/translate -> intent-first EN->ES JSON."""
import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import intent_translator  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return self._send(400, {"error": "invalid JSON body"})
        text = (data.get("text") or "").strip()
        dialect = data.get("dialect", "latam")
        if dialect not in ("latam", "castilian"):
            dialect = "latam"
        if not text:
            return self._send(400, {"error": "empty text"})
        try:
            res = asyncio.run(intent_translator.translate(text, dialect=dialect))
        except Exception as e:
            return self._send(500, {"error": str(e)})
        return self._send(200, {
            "input": res.input,
            "translation": res.translation,
            "literal_gloss": res.literal_gloss,
            "intent": res.intent,
            "tone": res.tone,
            "dialect": res.dialect,
            "confidence": res.confidence,
            "note": res.note,
            "engine": res.engine,
            "corrected": res.corrected,
        })

    def do_GET(self):
        return self._send(200, {"ok": True, "usage": "POST {text, dialect} to /api/translate"})
