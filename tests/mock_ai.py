"""Mock OpenAI-compatible endpoint + webhook receiver for testing the fix pipeline without an API key.
Run: python tests/mock_ai.py [port]   then set Config > AI: provider custom, base URL http://127.0.0.1:8899/v1, model mock"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
RECEIVED: list = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # OpenAI-compatible model list, so Config > AI's live model check can be exercised offline
        if self.path.endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": "mock"}, {"id": "mock-mini"}, {"id": "mock-large"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        if self.path.endswith("/chat/completions"):
            req = json.loads(raw)
            user = req["messages"][-1]["content"]
            out = []
            for line in user.splitlines():
                if line.startswith("{"):
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    base = o.get("current_title") or o.get("page_title") or o.get("broken_url") or "Page"
                    out.append({"id": o["id"], "proposed": f"{base[:48]} | Mock Fix", "rationale": "Mock provider: keeps the topic, adds brand suffix."})
            self._json(200, {"choices": [{"message": {"role": "assistant", "content": json.dumps(out)}}],
                             "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": len(json.dumps(out)) // 4}})
        elif self.path.endswith("/hook"):
            RECEIVED.append(json.loads(raw) if raw else {})
            self._json(200, {"ok": True, "received": len(RECEIVED)})
        else:
            self._json(404, {"error": "unknown path"})


print(f"mock AI + webhook on http://127.0.0.1:{PORT}")
HTTPServer(("127.0.0.1", PORT), H).serve_forever()
