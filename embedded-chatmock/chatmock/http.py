from __future__ import annotations

import queue
import threading
from typing import Any, Iterable

from flask import Response, jsonify, request


def build_cors_headers() -> dict:
    origin = request.headers.get("Origin", "*")
    req_headers = request.headers.get("Access-Control-Request-Headers")
    allow_headers = req_headers if req_headers else "Authorization, Content-Type, Accept"
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": allow_headers,
        "Access-Control-Max-Age": "86400",
    }


def json_error(message: str, status: int = 400) -> Response:
    resp = jsonify({"error": {"message": message}})
    response: Response = Response(response=resp.response, status=status, mimetype="application/json")
    for k, v in build_cors_headers().items():
        response.headers.setdefault(k, v)
    return response


_STREAM_SENTINEL = object()


def wrap_sse_stream_with_heartbeat(
    iterator: Iterable[Any],
    *,
    interval_seconds: float = 15.0,
    heartbeat_payload: bytes = b": keep-alive\n\n",
):
    q: "queue.Queue[Any]" = queue.Queue()

    def _producer() -> None:
        try:
            for chunk in iterator:
                q.put(chunk)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(_STREAM_SENTINEL)

    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()

    while True:
        try:
            item = q.get(timeout=max(1.0, float(interval_seconds)))
        except queue.Empty:
            yield heartbeat_payload
            continue

        if item is _STREAM_SENTINEL:
            break
        if isinstance(item, Exception):
            raise item
        if isinstance(item, (bytes, bytearray)):
            yield bytes(item)
        else:
            yield str(item).encode("utf-8")

