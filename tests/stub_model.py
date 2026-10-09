"""A stand-in for a model behind an OpenAI-compatible endpoint, for tests that run the world in
another process: it answers every council quietly and counts the questions it was asked."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sovereign_world.gateway.sovereign import prompt_hash

QUIET = json.dumps({"commands": [], "rationale": "Wait and watch."})


class StubModel:
    """Serves `POST /v1/chat/completions` (and Ollama's `GET /api/version`, `/api/tags` and
    `/api/ps`, and `POST /api/generate`, which loads a model) on a free loopback port, in a
    thread."""

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
        pulled: tuple[str, ...] = ("stub-model",),
        context_length: int = 32_768,
        loaded: bool = False,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.pulled = pulled
        """The models Ollama's `GET /api/tags` lists."""
        self.context_length = context_length
        """The context the running Ollama holds a loaded model with (`GET /api/ps`)."""
        self.loaded: list[str] = list(pulled) if loaded else []
        """The models `GET /api/ps` lists as loaded; `POST /api/generate` loads one."""
        self.loads: list[str] = []
        """Every model `POST /api/generate` was asked to load, in order."""
        self.calls: list[str] = []
        """The prompt hash of every question asked, in order."""
        self.formats: list[str | None] = []
        """The `response_format` type of every question asked, in order."""
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/generate":
                    # Ollama loads the model when asked with no prompt.
                    with stub._lock:
                        stub.loads.append(body["model"])
                        if body["model"] not in stub.loaded:
                            stub.loaded.append(body["model"])
                    self._send(json.dumps({"model": body["model"], "done": True}).encode())
                    return
                system, user = (message["content"] for message in body["messages"])
                with stub._lock:
                    stub.calls.append(prompt_hash(system, user))
                    stub.formats.append((body.get("response_format") or {}).get("type"))
                if stub.delay_seconds:
                    time.sleep(stub.delay_seconds)
                answer = json.dumps(
                    {
                        "id": "stub",
                        "object": "chat.completion",
                        "model": "stub-model",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": QUIET},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1000, "completion_tokens": 20},
                    }
                ).encode()
                self._send(answer)

            def _send(self, answer: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def do_GET(self) -> None:
                # What Ollama itself answers, for the readiness check.
                if self.path == "/api/version":
                    answer = json.dumps({"version": "0.0.0-stub"}).encode()
                elif self.path == "/api/tags":
                    answer = json.dumps(
                        {"models": [{"name": name} for name in stub.pulled]}
                    ).encode()
                elif self.path == "/api/ps":
                    with stub._lock:
                        running = [
                            {"name": name, "model": name, "context_length": stub.context_length}
                            for name in stub.loaded
                        ]
                    answer = json.dumps({"models": running}).encode()
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self._send(answer)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def asked(self) -> list[str]:
        with self._lock:
            return list(self.calls)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
