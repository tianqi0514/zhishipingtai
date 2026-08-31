#!/usr/bin/env python3
"""Real HTTP/multipart protocol checks for all configured model kinds."""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packages.semantica_adapter.models import test_model_connection
from packages.semantica_adapter.transcription import transcribe_media
from packages.semantica_adapter.vision import describe_visual


ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "generated"
OBSERVED: dict[str, int] = {
    "chat": 0,
    "images": 0,
    "audio": 0,
    "embedding": 0,
    "rerank": 0,
    "retry_attempts": 0,
    "timeout_attempts": 0,
}


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def respond(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            # A timeout test intentionally disconnects before the delayed
            # fixture response can be written.
            pass

    def rate_limited(self) -> None:
        body = json.dumps({"error": {"message": "fixture rate limit", "type": "rate_limit"}}).encode()
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path == "/v1/chat/completions":
            OBSERVED["chat"] += 1
            payload = json.loads(body)
            if payload.get("model") == "retry-fixture":
                OBSERVED["retry_attempts"] += 1
                if OBSERVED["retry_attempts"] == 1:
                    self.rate_limited()
                    return
            if payload.get("model") == "timeout-fixture":
                OBSERVED["timeout_attempts"] += 1
                time.sleep(0.2)
            if payload.get("model") == "kimi-k3":
                assert payload.get("temperature") == 1.0
            serialized = json.dumps(payload)
            OBSERVED["images"] += serialized.count("data:image/")
            self.respond({
                "id": "chatcmpl-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": payload.get("model"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "画面展示 NexusOne 企业知识平台。"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        elif self.path == "/v1/audio/transcriptions":
            assert b"filename=" in body and len(body) > 100
            OBSERVED["audio"] += 1
            self.respond({
                "text": "NexusOne 支持多模态知识解析。",
                "language": "zh",
                "duration": 1.5,
                "segments": [{"start": 0.0, "end": 1.5, "text": "NexusOne 支持多模态知识解析。"}],
            })
        elif self.path == "/v1/embeddings":
            OBSERVED["embedding"] += 1
            self.respond({
                "object": "list",
                "model": "embedding-fixture",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            })
        elif self.path == "/v1/rerank":
            OBSERVED["rerank"] += 1
            self.respond({"results": [{"index": 0, "relevance_score": 0.99}, {"index": 1, "relevance_score": 0.1}]})
        else:
            self.send_error(404)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        image = describe_visual(
            ROOT / "fact.png", "image", api_key="fixture", model="vision-fixture", base_url=base_url
        )
        video = describe_visual(
            ROOT / "fact.mp4", "video", api_key="fixture", model="vision-fixture", base_url=base_url,
            keyframe_count=2,
        )
        audio = transcribe_media(
            ROOT / "fact.wav", "audio", api_key="fixture", model="asr-fixture", base_url=base_url
        )
        video_audio = transcribe_media(
            ROOT / "fact.mp4", "video", api_key="fixture", model="asr-fixture", base_url=base_url
        )
        checks = {
            kind: test_model_connection(
                model_kind=kind,
                provider="openai_compatible",
                model_name="kimi-k3" if kind == "llm" else f"{kind}-fixture",
                api_key="fixture",
                base_url=base_url,
                config={},
            )
            for kind in ("llm", "embedding", "reranker", "vision", "asr")
        }
        retry_check = test_model_connection(
            model_kind="llm",
            provider="openai_compatible",
            model_name="retry-fixture",
            api_key="fixture",
            base_url=base_url,
            config={"retry": 2, "timeout": 2},
        )
        timeout_failed = False
        started = time.monotonic()
        try:
            test_model_connection(
                model_kind="llm",
                provider="openai_compatible",
                model_name="timeout-fixture",
                api_key="fixture",
                base_url=base_url,
                config={"retry": 0, "timeout": 0.05},
            )
        except Exception as exc:
            timeout_failed = "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()
        timeout_elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()

    assert image["vision_status"] == "succeeded"
    assert video["vision_status"] == "succeeded" and len(video["keyframes"]) == 2
    assert audio["segments"][0]["end"] == 1.5
    assert video_audio["transcription_status"] == "succeeded"
    assert checks["embedding"]["dimension"] == 3
    assert retry_check["status"] == "ok" and OBSERVED["retry_attempts"] == 2
    assert timeout_failed and timeout_elapsed < 2, (timeout_failed, timeout_elapsed)
    assert OBSERVED["chat"] >= 3 and OBSERVED["images"] >= 4
    assert OBSERVED["audio"] >= 3 and OBSERVED["embedding"] == 1 and OBSERVED["rerank"] == 1
    print(json.dumps({"model_kinds": len(checks), "observed": OBSERVED}, ensure_ascii=False))


if __name__ == "__main__":
    main()
