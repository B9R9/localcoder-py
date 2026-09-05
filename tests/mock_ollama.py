"""Minimal fake Ollama server (same behavior as the Node version's
test/mock-ollama.mjs) — mimics /api/chat's streaming NDJSON format and
/api/embed, so the request/response/tool-call loop can be exercised without
a real model. Not part of the shipped CLI.
"""

from __future__ import annotations

import json
import os
import string
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

turn = 0
embed_call_count = 0


def fake_embed(text: str) -> list[float]:
    global embed_call_count
    embed_call_count += 1
    vec = [0] * 26
    for ch in text.lower():
        code = ord(ch) - 97
        if 0 <= code < 26:
            vec[code] += 1
    return vec


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[mock] {fmt % args}", file=sys.stderr)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path == "/debug/embed-count":
            body = json.dumps({"embedCallCount": embed_call_count}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/tags":
            body = json.dumps({"models": [{"name": "devstral-small-2"}, {"name": "qwen3-coder:30b"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        global turn

        if self.path == "/api/generate":
            # Warm-up call: preloads the model, no actual generation.
            self._read_json()
            body = json.dumps({"done": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/embed":
            payload = self._read_json()
            raw = payload.get("input")
            inputs = raw if isinstance(raw, list) else [raw]
            body = json.dumps({"embeddings": [fake_embed(t) for t in inputs]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return

        payload = self._read_json()
        messages = payload.get("messages", [])
        turn += 1

        import os
        import time

        # Simulates a chat-only model (plenty exist on the Hub) rejecting a
        # request that includes tools — real Ollama returns exactly this
        # shape (400 + this error text) for that case.
        if os.environ.get("MOCK_NO_TOOLS") and payload.get("tools"):
            body = json.dumps({"error": f"{payload.get('model')} does not support tools"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()

        def send(obj: dict) -> None:
            self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))

        if os.environ.get("MOCK_NO_TOOLS"):
            send({"message": {"role": "assistant", "content": "ok without tools"}, "done": False})
            send({"done": True, "total_duration": 1, "prompt_eval_count": len(messages) * 50, "eval_count": 1})
            return

        slow = os.environ.get("MOCK_SLOW")

        if os.environ.get("MOCK_SIMPLE"):
            words = ["ok", " (", str(len(messages)), " messages)"]
            for w in words:
                if slow:
                    time.sleep(float(slow))
                send({"message": {"role": "assistant", "content": w}, "done": False})
                if slow:
                    self.wfile.flush()
            send({"done": True, "total_duration": 1, "prompt_eval_count": len(messages) * 50, "eval_count": len(words)})
            return

        if turn == 1:
            send(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "call_1", "function": {"name": "read_file", "arguments": '{"path":"package.json"}'}}],
                    },
                    "done": False,
                }
            )
            send({"done": True, "total_duration": 123456789, "prompt_eval_count": len(messages) * 50, "eval_count": 10})
        elif turn == 2:
            send(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_2",
                                "function": {
                                    "name": "write_file",
                                    "arguments": '{"path":"hello.txt","content":"hello from localcoder\\n"}',
                                },
                            }
                        ],
                    },
                    "done": False,
                }
            )
            send({"done": True, "total_duration": 123456789, "prompt_eval_count": len(messages) * 50, "eval_count": 10})
        else:
            words = ["Done", " — ", "I", " read", " package.json", " and", " wrote", " hello.txt", "."]
            for w in words:
                send({"message": {"role": "assistant", "content": w}, "done": False})
            send({"done": True, "total_duration": 123456789, "prompt_eval_count": len(messages) * 50, "eval_count": len(words)})


def main():
    port = 11434
    if "OLLAMA_PORT" in os.environ:
        port = int(os.environ["OLLAMA_PORT"])
    elif "OLLAMA_HOST" in os.environ:
        from urllib.parse import urlparse
        parsed = urlparse(os.environ["OLLAMA_HOST"])
        if parsed.port:
            port = parsed.port
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[mock] listening on :{port}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
