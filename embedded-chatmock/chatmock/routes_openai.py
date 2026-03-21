from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from flask import Blueprint, Response, current_app, has_app_context, jsonify, make_response, request

from .config import BASE_INSTRUCTIONS, GPT5_CODEX_INSTRUCTIONS
from .context_compaction import build_compaction_summary, maybe_compact_input_items
from .limits import record_rate_limits_from_response
from .http import build_cors_headers, wrap_sse_stream_with_heartbeat
from .responses_session import resolve_turn_state, save_response_session, should_retry_without_previous_response
from .reasoning import (
    allowed_efforts_for_model,
    apply_reasoning_to_message,
    build_reasoning_param,
    extract_reasoning_from_model_name,
    extract_service_tier_from_model_name,
    parse_fast_mode,
    public_model_name,
)
from .surface_names import public_upstream_name
from .upstream_errors import (
    build_error_info,
    build_openai_error_response,
    error_info_from_event_response,
    error_info_from_flask_response,
    error_info_from_http_response,
    normalized_error_payload,
    should_retry_next_candidate,
)
from .upstream import normalize_model_name, start_upstream_request
from .thread_sessions import resolve_thread_session_state
from .usage_passthrough import (
    extract_responses_usage_from_event,
    to_chat_usage,
    to_responses_usage,
)
from .utils import (
    RetryableStreamError,
    convert_chat_messages_to_responses_input,
    convert_tools_chat_to_responses,
    extract_response_output_text,
    merge_response_text,
    restore_reserved_tool_name,
    sanitize_reserved_tool_name,
    sse_translate_chat,
    sse_translate_text,
)


openai_bp = Blueprint("openai", __name__)


def _log_json(prefix: str, payload: Any) -> None:
    try:
        print(f"{prefix}\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    except Exception:
        try:
            print(f"{prefix}\n{payload}")
        except Exception:
            pass


def _log_invalid_request_diagnostic(
    label: str,
    *,
    payload: Dict[str, Any] | None,
    error_info: Dict[str, Any] | None,
) -> None:
    info = error_info if isinstance(error_info, dict) else {}
    if normalized_error_payload(info).get("type") != "invalid_request_error":
        return
    snapshot = payload if isinstance(payload, dict) else {}
    compact = {
        "model": snapshot.get("model"),
        "prompt_mode": snapshot.get("prompt_mode"),
        "service_tier": snapshot.get("service_tier"),
        "fast_mode": snapshot.get("fast_mode"),
        "responses_tool_choice": snapshot.get("responses_tool_choice"),
        "tool_choice": snapshot.get("tool_choice"),
        "has_previous_response_id": bool(snapshot.get("previous_response_id")),
        "has_prompt_cache_key": bool(snapshot.get("prompt_cache_key")),
        "input_count": len(snapshot.get("input") or []) if isinstance(snapshot.get("input"), list) else None,
        "messages_count": len(snapshot.get("messages") or []) if isinstance(snapshot.get("messages"), list) else None,
        "raw_status": info.get("raw_status"),
        "raw_code": info.get("raw_code"),
        "raw_message": info.get("raw_message"),
        "raw_body": info.get("raw_body"),
    }
    _log_json(label, compact)


def _wrap_stream_logging(label: str, iterator, enabled: bool):
    if not enabled:
        return iterator

    def _gen():
        for chunk in iterator:
            try:
                text = (
                    chunk.decode("utf-8", errors="replace")
                    if isinstance(chunk, (bytes, bytearray))
                    else str(chunk)
                )
                print(f"{label}\n{text}")
            except Exception:
                pass
            yield chunk

    return _gen()


def _log_fast_probe(
    phase: str,
    *,
    requested_model: str | None,
    normalized_model: str | None,
    selected_mode: str,
    requested_service_tier: str | None,
    observed_service_tier: str | None = None,
    is_stream: bool = False,
    upstream: Any | None = None,
    extra: Dict[str, Any] | None = None,
) -> None:
    requested_model_text = str(requested_model or "").lower()
    should_log = bool(requested_service_tier) or ("-fast" in requested_model_text)
    if not should_log:
        return
    payload: Dict[str, Any] = {
        "phase": phase,
        "requested_model": requested_model,
        "normalized_model": normalized_model,
        "selected_path": public_upstream_name(selected_mode),
        "stream": bool(is_stream),
    }
    if upstream is not None:
        payload["upstream_path"] = public_upstream_name(getattr(upstream, "chatmock_source", None))
        payload["candidate_label"] = getattr(upstream, "chatmock_candidate_label", None)
        payload["thread_mode"] = getattr(upstream, "chatmock_thread_mode", None)
    if isinstance(extra, dict):
        payload.update(extra)
    try:
        current_app.logger.info("perf_trace %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception:
        try:
            print(f"perf_trace {payload}")
        except Exception:
            pass


def _instructions_for_model(model: str) -> str:
    base = current_app.config.get("BASE_INSTRUCTIONS", BASE_INSTRUCTIONS)
    if "codex" in (model or "").lower():
        codex = current_app.config.get("GPT5_CODEX_INSTRUCTIONS") or GPT5_CODEX_INSTRUCTIONS
        if isinstance(codex, str) and codex.strip():
            return codex
    return base


def _resolve_prompt_mode(payload: Dict[str, Any]) -> str:
    value = payload.get("prompt_mode")
    if isinstance(value, str) and value.strip().lower() == "native":
        return "native"
    return "default"


def _resolve_bridge_instructions(model: str, payload: Dict[str, Any]) -> str | None:
    if _resolve_prompt_mode(payload) != "native":
        return _instructions_for_model(model)
    system_prompt = payload.get("system_prompt")
    if isinstance(system_prompt, str) and system_prompt.strip():
        return system_prompt.strip()
    return ""


def _upstream_attempt_limit(is_stream: bool, model: str | None = None, service_tier: str | None = None) -> int:
    _ = is_stream, model, service_tier
    return 1


def _resolve_service_tier(payload: Dict[str, Any], requested_model: str | None = None) -> str | None:
    fast_mode = parse_fast_mode(payload.get("fast_mode"))
    if fast_mode is True:
        return "priority"
    if fast_mode is False:
        return None
    alias_value = extract_service_tier_from_model_name(requested_model)
    if isinstance(alias_value, str) and alias_value:
        if alias_value == "fast":
            return "priority"
        return alias_value
    configured = current_app.config.get("SERVICE_TIER")
    if isinstance(configured, str) and configured.strip():
        normalized = configured.strip().lower()
        if normalized in ("off", "none", "unset"):
            return None
        return normalized
    return None


def _resolve_thread_session(payload: Dict[str, Any], input_items: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    return resolve_thread_session_state(
        payload=payload,
        input_items=input_items,
        headers=request.headers,
    )


def _prepare_route_turn_state(
    payload: Dict[str, Any],
    input_items: List[Dict[str, Any]],
    instructions: str | None,
    *,
    thread_session: Dict[str, Any] | None,
) -> tuple[List[Dict[str, Any]], str | None, Dict[str, Any] | None, List[Dict[str, Any]], List[Dict[str, Any]], str | None, Dict[str, Any]]:
    next_input_items, next_instructions, compaction_meta = maybe_compact_input_items(
        payload,
        input_items,
        instructions,
    )
    full_input_items = list(next_input_items)
    effective_input_items, effective_previous_response_id = resolve_turn_state(
        payload,
        full_input_items,
        thread_session,
    )
    return (
        next_input_items,
        next_instructions,
        thread_session,
        full_input_items,
        effective_input_items,
        effective_previous_response_id,
        compaction_meta,
    )


def _resolve_web_search_mode(
    payload: Dict[str, Any],
    tools_payload: List[Dict[str, Any]],
    responses_tools_payload: List[Dict[str, Any]],
) -> str:
    request_value = payload.get("web_search_mode")
    if isinstance(request_value, str):
        normalized = request_value.strip().lower()
        if normalized in ("disabled", "off", "none", "unset", "false"):
            return "disabled"
        if normalized in ("cached", "preview", "web_search_preview"):
            return "cached"
        if normalized in ("live", "on", "true", "web_search"):
            return "live"

    responses_tool_choice = payload.get("responses_tool_choice")
    if isinstance(responses_tool_choice, str) and responses_tool_choice.strip().lower() == "none":
        return "disabled"

    requested_modes: List[str] = []
    for tool in list(tools_payload or []) + list(responses_tools_payload or []):
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if tool_type == "web_search":
            requested_modes.append("live")
        elif tool_type == "web_search_preview":
            requested_modes.append("cached")

    if "live" in requested_modes:
        return "live"
    if "cached" in requested_modes:
        return "cached"
    return "disabled"


def _strip_builtin_search_tools(tools_payload: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], bool]:
    sanitized: List[Dict[str, Any]] = []
    removed = False
    for tool in tools_payload or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") in ("web_search", "web_search_preview"):
            removed = True
            continue
        sanitized.append(tool)
    return sanitized, removed


def _should_retry_nonstream_candidate(error_info: Dict[str, Any] | None) -> bool:
    if not isinstance(error_info, dict):
        return False
    return should_retry_next_candidate(error_info)


def _normalize_responses_input(payload: Dict[str, Any]) -> tuple[List[Dict[str, Any]] | None, str | None]:
    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        return [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": raw_input}],
            }
        ], None
    if not isinstance(raw_input, list) or not raw_input:
        return None, "input must be a non-empty string or array"

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_input):
        if not isinstance(item, dict):
            return None, f"input[{idx}] must be an object"
        item_copy = dict(item)
        if item_copy.get("type") == "function_call" and isinstance(item_copy.get("name"), str):
            item_copy["name"] = sanitize_reserved_tool_name(item_copy.get("name"))
        normalized.append(item_copy)
    return normalized, None


def _normalize_responses_tools(tools_payload: Any) -> tuple[List[Dict[str, Any]] | None, str | None]:
    if tools_payload is None:
        return [], None
    if not isinstance(tools_payload, list):
        return None, "tools must be an array"

    normalized: List[Dict[str, Any]] = []
    for idx, tool in enumerate(tools_payload):
        if not isinstance(tool, dict):
            return None, f"tools[{idx}] must be an object"
        tool_copy = dict(tool)
        if tool_copy.get("type") == "function" and isinstance(tool_copy.get("name"), str):
            tool_copy["name"] = sanitize_reserved_tool_name(tool_copy.get("name"))
        normalized.append(tool_copy)
    return normalized, None


def _normalize_responses_tool_choice(choice_payload: Any) -> Any:
    if not isinstance(choice_payload, dict):
        return choice_payload
    choice = dict(choice_payload)
    function_block = choice.get("function")
    if isinstance(function_block, dict) and isinstance(function_block.get("name"), str):
        choice["function"] = {
            **function_block,
            "name": sanitize_reserved_tool_name(function_block.get("name")),
        }
    elif isinstance(choice.get("name"), str):
        choice["name"] = sanitize_reserved_tool_name(choice.get("name"))
    return choice


def _resolve_responses_instructions(model: str, payload: Dict[str, Any]) -> str | None:
    instructions = payload.get("instructions")
    if isinstance(instructions, str):
        return instructions
    return _resolve_bridge_instructions(model, payload)


def _build_responses_extra_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    passthrough_keys = (
        "previous_response_id",
        "include",
        "metadata",
        "truncation",
        "text",
        "user",
        "prompt_cache_key",
        "prompt_cache_retention",
        "safety_identifier",
        "conversation",
        "store",
        "temperature",
        "top_p",
        "max_tool_calls",
        "prompt",
    )
    extra: Dict[str, Any] = {}
    for key in passthrough_keys:
        if key in payload and payload.get(key) is not None:
            extra[key] = payload.get(key)
    return extra


def _presented_client_model(requested_model: str | None, observed_model: str | None) -> str | None:
    if isinstance(requested_model, str) and requested_model.strip():
        return requested_model
    if isinstance(observed_model, str) and observed_model.strip():
        return observed_model
    return observed_model


def _build_minimal_responses_payload(
    *,
    response_id: str,
    model: str,
    created_at: int,
    output_text: str,
    usage_obj: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    message_id = f"msg_{response_id}"
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": message_id,
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": output_text,
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": usage_obj or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


def _client_metadata_minimization_enabled() -> bool:
    if has_app_context():
        return bool(current_app.config.get("CLIENT_METADATA_MINIMIZATION", True))
    return (os.getenv("CHATMOCK_CLIENT_METADATA_MINIMIZATION") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _strip_client_visible_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if key in ("system_fingerprint", "fingerprint", "service_tier"):
                continue
            cleaned[key] = _strip_client_visible_metadata(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_client_visible_metadata(item) for item in value]
    return value


def _sanitize_responses_response_obj(
    response_obj: Dict[str, Any],
    *,
    requested_model: str | None,
    requested_service_tier: str | None,
    observed_service_tier: str | None = None,
) -> Dict[str, Any]:
    if not _client_metadata_minimization_enabled():
        return response_obj
    cleaned = _strip_client_visible_metadata(response_obj)
    if not isinstance(cleaned, dict):
        return response_obj
    presented_model = _presented_client_model(requested_model, cleaned.get("model"))
    if presented_model:
        cleaned["model"] = presented_model
    return cleaned


def _sanitize_responses_stream_event(
    evt: Dict[str, Any],
    *,
    requested_model: str | None,
    requested_service_tier: str | None,
    metadata_minimization_enabled: bool,
) -> Dict[str, Any]:
    if not metadata_minimization_enabled:
        return evt
    cleaned = _strip_client_visible_metadata(evt)
    if isinstance(cleaned, dict) and isinstance(cleaned.get("response"), dict):
        presented_model = _presented_client_model(
            requested_model,
            cleaned["response"].get("model"),
        )
        if presented_model:
            cleaned["response"]["model"] = presented_model
    return cleaned


def _consume_responses_nonstream(
    upstream: Any,
    *,
    requested_model: str | None,
    model: str,
    created: int,
) -> Dict[str, Any]:
    response_obj: Dict[str, Any] | None = None
    response_id = "resp"
    usage_obj: Dict[str, Any] | None = None
    observed_service_tier: str | None = None
    full_text = ""
    error_info: Dict[str, Any] | None = None

    try:
        for raw in upstream.iter_lines(decode_unicode=False):
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else raw
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                evt = json.loads(data)
            except Exception:
                continue
            kind = evt.get("type")
            mu = extract_responses_usage_from_event(evt)
            if mu:
                usage_obj = to_responses_usage(mu)
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("service_tier"), str):
                observed_service_tier = evt["response"].get("service_tier") or observed_service_tier
            if kind == "response.output_text.delta":
                full_text += evt.get("delta") or ""
            elif kind == "response.output_text.done":
                full_text, _ = merge_response_text(full_text, evt.get("text") or "")
            elif kind == "response.content_part.done":
                part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
                full_text, _ = merge_response_text(full_text, part.get("text") or "")
            elif kind == "response.output_item.done":
                item = evt.get("item") or {}
                full_text, _ = merge_response_text(full_text, extract_response_output_text(item))
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                break
            elif kind == "response.completed":
                candidate = evt.get("response")
                if isinstance(candidate, dict):
                    response_obj = candidate
                break
    finally:
        upstream.close()

    if error_info is not None:
        return {"ok": False, "error_info": error_info}
    if response_obj is None:
        response_obj = _build_minimal_responses_payload(
            response_id=response_id,
            model=requested_model or model,
            created_at=created,
            output_text=full_text,
            usage_obj=usage_obj,
        )
    return {
        "ok": True,
        "response": response_obj,
        "observed_service_tier": observed_service_tier,
    }


def _responses_stream_passthrough(
    upstream: Any,
    *,
    requested_model: str | None,
    requested_service_tier: str | None,
    metadata_minimization_enabled: bool,
    retry_factory=None,
    on_completed=None,
):
    current_upstream = upstream
    retried = False
    while True:
        should_restart = False
        had_visible_output = False
        try:
            for raw in current_upstream.iter_lines(decode_unicode=False):
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
                if not line.startswith("data: "):
                    yield line.encode("utf-8") + b"\n\n"
                    continue
                data = line[len("data: "):].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    yield b"data: [DONE]\n\n"
                    continue
                try:
                    evt = json.loads(data)
                except Exception:
                    yield line.encode("utf-8") + b"\n\n"
                    continue
                kind = evt.get("type")
                if kind in (
                    "response.output_text.delta",
                    "response.output_text.done",
                    "response.content_part.added",
                    "response.content_part.done",
                    "response.output_item.added",
                    "response.output_item.done",
                    "response.function_call_arguments.delta",
                    "response.function_call_arguments.done",
                ):
                    had_visible_output = True
                if kind == "response.failed":
                    error_info = error_info_from_event_response(
                        getattr(current_upstream, "chatmock_source", "upstream"),
                        "stream",
                        evt.get("response"),
                    )
                    if (
                        not retried
                        and not had_visible_output
                        and retry_factory is not None
                        and should_retry_without_previous_response(error_info)
                    ):
                        retried = True
                        should_restart = True
                        break
                if kind == "response.completed" and callable(on_completed):
                    response_obj = evt.get("response")
                    if isinstance(response_obj, dict):
                        on_completed(response_obj, current_upstream)
                sanitized_evt = _sanitize_responses_stream_event(
                    evt,
                    requested_model=requested_model,
                    requested_service_tier=requested_service_tier,
                    metadata_minimization_enabled=metadata_minimization_enabled,
                )
                yield f"data: {json.dumps(sanitized_evt, ensure_ascii=False)}\n\n".encode("utf-8")
        finally:
            current_upstream.close()
        if should_restart:
            next_upstream = retry_factory()
            if next_upstream is None:
                return
            current_upstream = next_upstream
            continue
        return


def _consume_chat_completion_nonstream(
    upstream: Any,
    *,
    requested_model: str | None,
    model: str,
    created: int,
    reasoning_compat: str,
) -> Dict[str, Any]:
    full_text = ""
    reasoning_summary_text = ""
    reasoning_full_text = ""
    response_id = "chatcmpl"
    tool_calls: List[Dict[str, Any]] = []
    error_message: str | None = None
    error_info: Dict[str, Any] | None = None
    usage_obj: Dict[str, Any] | None = None
    observed_service_tier: str | None = None
    completed_ok = False

    try:
        for raw in upstream.iter_lines(decode_unicode=False):
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else raw
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):].strip()
            if not data:
                continue
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
            except Exception:
                continue
            kind = evt.get("type")
            mu = extract_responses_usage_from_event(evt)
            if mu:
                usage_obj = to_chat_usage(mu)
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("service_tier"), str):
                observed_service_tier = evt["response"].get("service_tier") or observed_service_tier
            if kind == "response.output_text.delta":
                full_text += evt.get("delta") or ""
            elif kind == "response.output_text.done":
                full_text, _ = merge_response_text(full_text, evt.get("text") or "")
            elif kind == "response.reasoning_summary_text.delta":
                reasoning_summary_text += evt.get("delta") or ""
            elif kind == "response.reasoning_text.delta":
                reasoning_full_text += evt.get("delta") or ""
            elif kind == "response.content_part.done":
                part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
                full_text, _ = merge_response_text(full_text, part.get("text") or "")
            elif kind == "response.output_item.done":
                item = evt.get("item") or {}
                if isinstance(item, dict) and item.get("type") == "function_call":
                    call_id = item.get("call_id") or item.get("id") or ""
                    name = item.get("name") or ""
                    args = item.get("arguments") or ""
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args, ensure_ascii=False)
                        except Exception:
                            args = "{}"
                    if isinstance(call_id, str) and isinstance(name, str) and isinstance(args, str):
                        tool_calls.append(
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": restore_reserved_tool_name(name), "arguments": args},
                            }
                        )
                else:
                    full_text, _ = merge_response_text(full_text, extract_response_output_text(item))
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                error_message = error_info.get("raw_message") or "response.failed"
            elif kind == "response.completed":
                full_text, _ = merge_response_text(
                    full_text,
                    extract_response_output_text(evt.get("response")),
                )
                completed_ok = True
                break
    finally:
        if completed_ok and hasattr(upstream, "mark_success"):
            try:
                upstream.mark_success()
            except Exception:
                pass
        elif error_message and hasattr(upstream, "mark_failure"):
            try:
                upstream.mark_failure(error_message)
            except Exception:
                pass
        upstream.close()

    if error_message:
        if error_info is None:
            error_info = build_error_info(
                source=getattr(upstream, "chatmock_source", "upstream"),
                phase="stream",
                raw_status=int(getattr(upstream, "status_code", 502) or 502),
                raw_message=error_message,
                raw_body={"message": error_message},
            )
        return {"ok": False, "error_info": error_info}

    if not completed_ok and not full_text and not tool_calls:
        return {
            "ok": False,
            "error_info": build_error_info(
                source=getattr(upstream, "chatmock_source", "upstream"),
                phase="stream",
                raw_status=int(getattr(upstream, "status_code", 502) or 502),
                raw_message="stream ended before response.completed",
                raw_body={"message": "stream ended before response.completed"},
            ),
        }

    if tool_calls:
        message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
    else:
        message = {"role": "assistant", "content": full_text if full_text else None}
        message = apply_reasoning_to_message(message, reasoning_summary_text, reasoning_full_text, reasoning_compat)

    return {
        "ok": True,
        "response_id": response_id or "chatcmpl",
        "message": message,
        "usage_obj": usage_obj,
        "observed_service_tier": observed_service_tier,
        "created": created,
        "model": requested_model or model,
    }


def _consume_text_completion_nonstream(
    upstream: Any,
    *,
    requested_model: str | None,
    model: str,
    created: int,
) -> Dict[str, Any]:
    full_text = ""
    response_id = "cmpl"
    usage_obj: Dict[str, Any] | None = None
    observed_service_tier: str | None = None
    completed_ok = False
    error_message: str | None = None
    error_info: Dict[str, Any] | None = None

    try:
        for raw_line in upstream.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):].strip()
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    break
                continue
            try:
                evt = json.loads(data)
            except Exception:
                continue
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("service_tier"), str):
                observed_service_tier = evt["response"].get("service_tier") or observed_service_tier
            mu = extract_responses_usage_from_event(evt)
            if mu:
                usage_obj = to_chat_usage(mu)
            kind = evt.get("type")
            if kind == "response.output_text.delta":
                full_text += evt.get("delta") or ""
            elif kind == "response.output_text.done":
                full_text, _ = merge_response_text(full_text, evt.get("text") or "")
            elif kind == "response.content_part.done":
                part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
                full_text, _ = merge_response_text(full_text, part.get("text") or "")
            elif kind == "response.output_item.done":
                item = evt.get("item") or {}
                full_text, _ = merge_response_text(full_text, extract_response_output_text(item))
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                error_message = error_info.get("raw_message") or "response.failed"
            elif kind == "response.completed":
                full_text, _ = merge_response_text(
                    full_text,
                    extract_response_output_text(evt.get("response")),
                )
                completed_ok = True
                break
    finally:
        if completed_ok and hasattr(upstream, "mark_success"):
            try:
                upstream.mark_success()
            except Exception:
                pass
        upstream.close()

    if error_message:
        if error_info is None:
            error_info = build_error_info(
                source=getattr(upstream, "chatmock_source", "upstream"),
                phase="stream",
                raw_status=int(getattr(upstream, "status_code", 502) or 502),
                raw_message=error_message,
                raw_body={"message": error_message},
            )
        return {"ok": False, "error_info": error_info}

    return {
        "ok": True,
        "response_id": response_id or "cmpl",
        "full_text": full_text,
        "usage_obj": usage_obj,
        "observed_service_tier": observed_service_tier,
        "created": created,
        "model": requested_model or model,
    }


@openai_bp.route("/v1/responses", methods=["POST"])
def responses() -> Response:
    verbose = bool(current_app.config.get("VERBOSE"))
    reasoning_effort = current_app.config.get("REASONING_EFFORT", "medium")
    reasoning_summary = current_app.config.get("REASONING_SUMMARY", "auto")
    raw = request.get_data(cache=True, as_text=True) or ""
    if verbose:
        try:
            print("IN POST /v1/responses\n" + raw)
        except Exception:
            pass
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        err = {"error": {"message": "Invalid JSON body"}}
        if verbose:
            _log_json("OUT POST /v1/responses", err)
        return jsonify(err), 400

    requested_model = payload.get("model")
    model = normalize_model_name(requested_model, current_app.config.get("DEBUG_MODEL"))
    input_items, input_err = _normalize_responses_input(payload)
    if input_err:
        err = {"error": {"message": input_err}}
        if verbose:
            _log_json("OUT POST /v1/responses", err)
        return jsonify(err), 400
    assert isinstance(input_items, list)

    tools_payload, tools_err = _normalize_responses_tools(payload.get("tools"))
    if tools_err:
        err = {"error": {"message": tools_err}}
        if verbose:
            _log_json("OUT POST /v1/responses", err)
        return jsonify(err), 400
    assert isinstance(tools_payload, list)

    tool_choice = _normalize_responses_tool_choice(payload.get("tool_choice", "auto"))
    parallel_tool_calls = bool(payload.get("parallel_tool_calls", False))
    stream_req = bool(payload.get("stream", False))
    model_reasoning = extract_reasoning_from_model_name(requested_model)
    service_tier = _resolve_service_tier(payload, requested_model)
    reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else model_reasoning
    reasoning_param = build_reasoning_param(
        reasoning_effort,
        reasoning_summary,
        reasoning_overrides,
        allowed_efforts=allowed_efforts_for_model(model),
    )
    bridge_instructions = _resolve_responses_instructions(model, payload)
    thread_session = _resolve_thread_session(payload, input_items)
    (
        input_items,
        bridge_instructions,
        thread_session,
        full_input_items,
        effective_input_items,
        effective_previous_response_id,
        _,
    ) = _prepare_route_turn_state(
        payload,
        input_items,
        bridge_instructions,
        thread_session=thread_session,
    )
    extra_payload = _build_responses_extra_payload(payload)
    if effective_previous_response_id:
        extra_payload["previous_response_id"] = effective_previous_response_id

    upstream, error_resp = start_upstream_request(
        model,
        effective_input_items,
        instructions=bridge_instructions,
        tools=tools_payload,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        reasoning_param=reasoning_param,
        service_tier=service_tier,
        thread_session=thread_session,
        extra_payload=extra_payload,
    )
    if error_resp is not None:
        try:
            _log_invalid_request_diagnostic(
                "INVALID REQUEST /v1/responses request_start",
                payload=payload,
                error_info=error_info_from_flask_response("chatcore", "request_start", error_resp),
            )
        except Exception:
            pass
        return error_resp

    record_rate_limits_from_response(upstream)
    created = int(time.time())
    if upstream.status_code >= 400:
        error_info = error_info_from_http_response(getattr(upstream, "chatmock_source", "upstream"), "http", upstream)
        if effective_previous_response_id and should_retry_without_previous_response(error_info):
            retry_extra_payload = dict(extra_payload)
            retry_extra_payload.pop("previous_response_id", None)
            retry_upstream, retry_error = start_upstream_request(
                model,
                full_input_items,
                instructions=bridge_instructions,
                tools=tools_payload,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                reasoning_param=reasoning_param,
                service_tier=service_tier,
                thread_session=thread_session,
                extra_payload=retry_extra_payload,
            )
            if retry_error is None and retry_upstream is not None and retry_upstream.status_code < 400:
                upstream = retry_upstream
            else:
                if retry_error is not None:
                    error_info = error_info_from_flask_response("chatcore", "request_start", retry_error)
                elif retry_upstream is not None:
                    error_info = error_info_from_http_response(
                        getattr(retry_upstream, "chatmock_source", "upstream"),
                        "http",
                        retry_upstream,
                    )
                _log_invalid_request_diagnostic(
                    "INVALID REQUEST /v1/responses upstream_http",
                    payload=payload,
                    error_info=error_info,
                )
                return build_openai_error_response(error_info)
        else:
            _log_invalid_request_diagnostic(
                "INVALID REQUEST /v1/responses upstream_http",
                payload=payload,
                error_info=error_info,
            )
            return build_openai_error_response(error_info)
    if upstream.status_code >= 400:
        _log_invalid_request_diagnostic(
            "INVALID REQUEST /v1/responses upstream_http",
            payload=payload,
            error_info=error_info,
        )
        return build_openai_error_response(error_info)

    if stream_req:
        metadata_minimization_enabled = _client_metadata_minimization_enabled()
        def _retry_without_previous_response():
            retry_extra_payload = dict(extra_payload)
            retry_extra_payload.pop("previous_response_id", None)
            retry_upstream, retry_error = start_upstream_request(
                model,
                full_input_items,
                instructions=bridge_instructions,
                tools=tools_payload,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                reasoning_param=reasoning_param,
                service_tier=service_tier,
                thread_session=thread_session,
                extra_payload=retry_extra_payload,
            )
            if retry_error is not None or retry_upstream is None or retry_upstream.status_code >= 400:
                return None
            return retry_upstream

        stream_iter = _wrap_stream_logging(
            "STREAM OUT /v1/responses",
            _responses_stream_passthrough(
                upstream,
                requested_model=requested_model,
                requested_service_tier=service_tier,
                metadata_minimization_enabled=metadata_minimization_enabled,
                retry_factory=_retry_without_previous_response if effective_previous_response_id else None,
                on_completed=lambda response_obj, completed_upstream: save_response_session(
                    thread_session,
                    response_obj=response_obj,
                    full_input_items=full_input_items,
                    upstream=completed_upstream,
                ),
            ),
            verbose,
        )
        resp = Response(
            wrap_sse_stream_with_heartbeat(stream_iter),
            status=upstream.status_code,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    completed_upstream = upstream
    result = _consume_responses_nonstream(
        upstream,
        requested_model=requested_model,
        model=model,
        created=created,
    )
    if not result.get("ok") and effective_previous_response_id and should_retry_without_previous_response(result.get("error_info")):
        retry_extra_payload = dict(extra_payload)
        retry_extra_payload.pop("previous_response_id", None)
        retry_upstream, retry_error = start_upstream_request(
            model,
            full_input_items,
            instructions=bridge_instructions,
            tools=tools_payload,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            thread_session=thread_session,
            extra_payload=retry_extra_payload,
        )
        if retry_error is None and retry_upstream is not None and retry_upstream.status_code < 400:
            completed_upstream = retry_upstream
            result = _consume_responses_nonstream(
                retry_upstream,
                requested_model=requested_model,
                model=model,
                created=created,
            )
    if not result.get("ok"):
        return build_openai_error_response(result.get("error_info"))

    raw_response_obj = result.get("response") or {}
    save_response_session(
        thread_session,
        response_obj=raw_response_obj,
        full_input_items=full_input_items,
        upstream=completed_upstream,
    )
    response_obj = _sanitize_responses_response_obj(
        raw_response_obj,
        requested_model=requested_model,
        requested_service_tier=service_tier,
        observed_service_tier=result.get("observed_service_tier"),
    )
    if verbose:
        _log_json("OUT POST /v1/responses", response_obj)
    resp = make_response(jsonify(response_obj), 200)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


@openai_bp.route("/v1/responses/compact", methods=["POST"])
def responses_compact() -> Response:
    verbose = bool(current_app.config.get("VERBOSE"))
    raw = request.get_data(cache=True, as_text=True) or ""
    if verbose:
        try:
            print("IN POST /v1/responses/compact\n" + raw)
        except Exception:
            pass
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        err = {"error": {"message": "Invalid JSON body"}}
        if verbose:
            _log_json("OUT POST /v1/responses/compact", err)
        return jsonify(err), 400

    input_items, input_err = _normalize_responses_input(payload)
    if input_err:
        err = {"error": {"message": input_err}}
        if verbose:
            _log_json("OUT POST /v1/responses/compact", err)
        return jsonify(err), 400
    assert isinstance(input_items, list)

    summary_text, _ = build_compaction_summary(payload, input_items)
    response_obj = {
        "id": f"comp_{int(time.time() * 1000)}",
        "object": "response.compaction",
        "created_at": int(time.time()),
        "output": [{"type": "summary_text", "text": summary_text or ""}],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }
    if verbose:
        _log_json("OUT POST /v1/responses/compact", response_obj)
    resp = make_response(jsonify(response_obj), 200)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


@openai_bp.route("/v1/chat/completions", methods=["POST"])
def chat_completions() -> Response:
    verbose = bool(current_app.config.get("VERBOSE"))
    verbose_obfuscation = bool(current_app.config.get("VERBOSE_OBFUSCATION"))
    reasoning_effort = current_app.config.get("REASONING_EFFORT", "medium")
    reasoning_summary = current_app.config.get("REASONING_SUMMARY", "auto")
    reasoning_compat = current_app.config.get("REASONING_COMPAT", "current")
    debug_model = current_app.config.get("DEBUG_MODEL")

    raw = request.get_data(cache=True, as_text=True) or ""
    if verbose:
        try:
            print("IN POST /v1/chat/completions\n" + raw)
        except Exception:
            pass
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        try:
            payload = json.loads(raw.replace("\r", "").replace("\n", ""))
        except Exception:
            err = {"error": {"message": "Invalid JSON body"}}
            if verbose:
                _log_json("OUT POST /v1/chat/completions", err)
            return jsonify(err), 400

    requested_model = payload.get("model")
    model = normalize_model_name(requested_model, debug_model)
    messages = payload.get("messages")
    if messages is None and isinstance(payload.get("prompt"), str):
        messages = [{"role": "user", "content": payload.get("prompt") or ""}]
    if messages is None and isinstance(payload.get("input"), str):
        messages = [{"role": "user", "content": payload.get("input") or ""}]
    if messages is None:
        messages = []
    if not isinstance(messages, list):
        err = {"error": {"message": "Request must include messages: []"}}
        if verbose:
            _log_json("OUT POST /v1/chat/completions", err)
        return jsonify(err), 400

    if isinstance(messages, list):
        sys_idx = next((i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "system"), None)
        if isinstance(sys_idx, int):
            sys_msg = messages.pop(sys_idx)
            content = sys_msg.get("content") if isinstance(sys_msg, dict) else ""
            messages.insert(0, {"role": "user", "content": content})
    is_stream = bool(payload.get("stream"))
    responses_compat_internal = bool(payload.get("_chatcore_responses_compat"))
    stream_options = payload.get("stream_options") if isinstance(payload.get("stream_options"), dict) else {}
    include_usage = bool(stream_options.get("include_usage", False))

    raw_tools_payload = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    raw_tools_payload, stripped_builtin_search = _strip_builtin_search_tools(raw_tools_payload)
    tool_choice = payload.get("tool_choice", "auto")
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        function_block = tool_choice.get("function")
        if isinstance(function_block, dict) and isinstance(function_block.get("name"), str):
            tool_choice = {
                **tool_choice,
                "function": {
                    **function_block,
                    "name": sanitize_reserved_tool_name(function_block.get("name")),
                },
            }
    elif isinstance(tool_choice, dict) and isinstance(tool_choice.get("name"), str):
        tool_choice = {
            **tool_choice,
            "name": sanitize_reserved_tool_name(tool_choice.get("name")),
        }
    parallel_tool_calls = bool(payload.get("parallel_tool_calls", False))
    responses_tools_payload = payload.get("responses_tools") if isinstance(payload.get("responses_tools"), list) else []
    responses_tools_payload, stripped_responses_builtin_search = _strip_builtin_search_tools(responses_tools_payload)
    tools_responses = convert_tools_chat_to_responses(list(raw_tools_payload) + list(responses_tools_payload))
    builtin_search_tools: List[Dict[str, Any]] = []
    had_builtin_search_tools = False
    responses_tool_choice = payload.get("responses_tool_choice")
    if stripped_builtin_search or stripped_responses_builtin_search:
        responses_tool_choice = None

    input_items = convert_chat_messages_to_responses_input(messages)
    if not input_items and isinstance(payload.get("prompt"), str) and payload.get("prompt").strip():
        input_items = [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": payload.get("prompt")}]}
        ]

    model_reasoning = extract_reasoning_from_model_name(requested_model)
    reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else model_reasoning
    service_tier = _resolve_service_tier(payload, requested_model)
    web_search_mode = _resolve_web_search_mode(payload, raw_tools_payload, responses_tools_payload)
    reasoning_param = build_reasoning_param(
        reasoning_effort,
        reasoning_summary,
        reasoning_overrides,
        allowed_efforts=allowed_efforts_for_model(model),
    )
    bridge_instructions = _resolve_bridge_instructions(model, payload)
    thread_session = _resolve_thread_session(payload, input_items)
    (
        input_items,
        bridge_instructions,
        thread_session,
        full_input_items,
        effective_input_items,
        effective_previous_response_id,
        compaction_meta,
    ) = _prepare_route_turn_state(
        payload,
        input_items,
        bridge_instructions,
        thread_session=thread_session,
    )
    extra_payload = {}
    if effective_previous_response_id:
        extra_payload["previous_response_id"] = effective_previous_response_id
    selected_mode = "chatgpt-backend"
    _log_fast_probe(
        "start",
        requested_model=requested_model,
        normalized_model=model,
        selected_mode=selected_mode,
        requested_service_tier=service_tier,
        is_stream=is_stream,
        extra={"compaction": compaction_meta} if compaction_meta.get("applied") else None,
    )

    attempt_limit = _upstream_attempt_limit(is_stream, model, service_tier)
    last_error_info: Dict[str, Any] | None = None
    upstream = None
    created = int(time.time())
    nonstream_result: Dict[str, Any] | None = None
    for attempt_index in range(attempt_limit):
        upstream, error_resp = start_upstream_request(
            model,
            effective_input_items,
            instructions=bridge_instructions,
            tools=tools_responses,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            web_search_mode=web_search_mode,
            thread_session=thread_session,
            extra_payload=extra_payload,
        )
        if error_resp is not None:
            error_info = error_info_from_flask_response("chatcore", "request_start", error_resp)
            last_error_info = error_info
            if not is_stream and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                continue
            _log_fast_probe(
                "request_start_error",
                requested_model=requested_model,
                normalized_model=model,
                selected_mode=selected_mode,
                requested_service_tier=service_tier,
                is_stream=is_stream,
                extra={"error_info": error_info},
            )
            _log_invalid_request_diagnostic(
                "INVALID REQUEST /v1/chat/completions request_start",
                payload=payload,
                error_info=error_info,
            )
            return build_openai_error_response(error_info)

        record_rate_limits_from_response(upstream)
        created = int(time.time())
        if upstream.status_code >= 400:
            error_info = error_info_from_http_response(getattr(upstream, "chatmock_source", "upstream"), "http", upstream)
            last_error_info = error_info
            retry_recovered = False
            if had_builtin_search_tools:
                if verbose:
                    print("[Passthrough] Upstream rejected tools; retrying without extra tools (args redacted)")
                base_tools_only = convert_tools_chat_to_responses(payload.get("tools"))
                safe_choice = payload.get("tool_choice", "auto")
                upstream2, err2 = start_upstream_request(
                    model,
                    input_items,
                    instructions=bridge_instructions,
                    tools=base_tools_only,
                    tool_choice=safe_choice,
                    parallel_tool_calls=parallel_tool_calls,
                    reasoning_param=reasoning_param,
                    service_tier=service_tier,
                    web_search_mode="disabled",
                    thread_session=thread_session,
                )
                record_rate_limits_from_response(upstream2)
                if err2 is None and upstream2 is not None and upstream2.status_code < 400:
                    upstream = upstream2
                    if is_stream:
                        break
                if err2 is not None:
                    error_info = error_info_from_flask_response("chatcore", "tool_retry", err2)
                elif upstream2 is not None:
                    error_info = error_info_from_http_response(getattr(upstream2, "chatmock_source", "upstream"), "tool_retry", upstream2)
                last_error_info = error_info
            if effective_previous_response_id and should_retry_without_previous_response(error_info):
                retry_extra_payload = dict(extra_payload)
                retry_extra_payload.pop("previous_response_id", None)
                retry_upstream, retry_error = start_upstream_request(
                    model,
                    full_input_items,
                    instructions=bridge_instructions,
                    tools=tools_responses,
                    tool_choice=tool_choice,
                    parallel_tool_calls=parallel_tool_calls,
                    reasoning_param=reasoning_param,
                    service_tier=service_tier,
                    web_search_mode=web_search_mode,
                    thread_session=thread_session,
                    extra_payload=retry_extra_payload,
                )
                if retry_error is None and retry_upstream is not None and retry_upstream.status_code < 400:
                    upstream = retry_upstream
                    record_rate_limits_from_response(upstream)
                    created = int(time.time())
                    retry_recovered = True
                if retry_error is not None:
                    error_info = error_info_from_flask_response("chatcore", "request_start", retry_error)
                elif retry_upstream is not None:
                    error_info = error_info_from_http_response(
                        getattr(retry_upstream, "chatmock_source", "upstream"),
                        "http",
                        retry_upstream,
                    )
                last_error_info = error_info
            if not retry_recovered:
                if not is_stream and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                    try:
                        upstream.close()
                    except Exception:
                        pass
                    continue
                _log_fast_probe(
                    "http_error",
                    requested_model=requested_model,
                    normalized_model=model,
                    selected_mode=selected_mode,
                    requested_service_tier=service_tier,
                    is_stream=is_stream,
                    upstream=upstream,
                    extra={"error_info": error_info},
                )
                _log_invalid_request_diagnostic(
                    "INVALID REQUEST /v1/chat/completions upstream_http",
                    payload=payload,
                    error_info=error_info,
                )
                return build_openai_error_response(error_info)
        if not is_stream:
            nonstream_result = _consume_chat_completion_nonstream(
                upstream,
                requested_model=requested_model,
                model=model,
                created=created,
                reasoning_compat=reasoning_compat,
            )
            if not nonstream_result.get("ok"):
                error_info = nonstream_result.get("error_info")
                if isinstance(error_info, dict):
                    last_error_info = error_info
                if _should_retry_nonstream_candidate(error_info) and attempt_index + 1 < attempt_limit:
                    upstream = None
                    nonstream_result = None
                    continue
                _log_fast_probe(
                    "nonstream_error",
                    requested_model=requested_model,
                    normalized_model=model,
                    selected_mode=selected_mode,
                    requested_service_tier=service_tier,
                    is_stream=is_stream,
                    upstream=upstream,
                    extra={"error_info": error_info},
                )
                _log_invalid_request_diagnostic(
                    "INVALID REQUEST /v1/chat/completions nonstream",
                    payload=payload,
                    error_info=error_info if isinstance(error_info, dict) else None,
                )
                return build_openai_error_response(error_info or build_error_info(
                    source="chatcore",
                    phase="nonstream",
                    raw_status=502,
                    raw_message="Unknown upstream failure",
                    raw_body={"message": "Unknown upstream failure"},
                ))
            break
        break

    if upstream is None:
        _log_fast_probe(
            "retry_exhausted",
            requested_model=requested_model,
            normalized_model=model,
            selected_mode=selected_mode,
            requested_service_tier=service_tier,
            is_stream=is_stream,
            extra={"error_info": last_error_info},
        )
        return build_openai_error_response(
            last_error_info
            or build_error_info(
                source="chatcore",
                phase="retry_exhausted",
                raw_status=502,
                raw_message="No candidate succeeded",
                raw_body={"message": "No candidate succeeded"},
            )
        )

    if is_stream:
        if verbose:
            print("OUT POST /v1/chat/completions (streaming response)")

        def _retrying_stream():
            current_upstream = upstream
            current_created = created
            remaining_attempts = max(1, attempt_limit)
            recovered_previous_response = False
            while remaining_attempts > 0:
                try:
                    yield from sse_translate_chat(
                        current_upstream,
                        requested_model or model,
                        current_created,
                        verbose=verbose_obfuscation,
                        vlog=print if verbose_obfuscation else None,
                        reasoning_compat=reasoning_compat,
                        include_usage=include_usage,
                        on_response_completed=lambda response_obj, completed_upstream: save_response_session(
                            thread_session,
                            response_obj=response_obj,
                            full_input_items=full_input_items,
                            upstream=completed_upstream,
                        ),
                    )
                    return
                except RetryableStreamError as exc:
                    if (
                        effective_previous_response_id
                        and not recovered_previous_response
                        and should_retry_without_previous_response(exc.error_info)
                    ):
                        retry_extra_payload = dict(extra_payload)
                        retry_extra_payload.pop("previous_response_id", None)
                        next_upstream, next_error = start_upstream_request(
                            model,
                            full_input_items,
                            instructions=bridge_instructions,
                            tools=tools_responses,
                            tool_choice=tool_choice,
                            parallel_tool_calls=parallel_tool_calls,
                            reasoning_param=reasoning_param,
                            service_tier=service_tier,
                            web_search_mode=web_search_mode,
                            thread_session=thread_session,
                            extra_payload=retry_extra_payload,
                        )
                        if next_error is not None:
                            next_error_info = error_info_from_flask_response("chatcore", "request_start", next_error)
                            payload = {"error": normalized_error_payload(next_error_info)}
                            yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                            yield b"data: [DONE]\n\n"
                            return
                        current_upstream = next_upstream
                        current_created = int(time.time())
                        recovered_previous_response = True
                        continue
                    remaining_attempts -= 1
                    if remaining_attempts <= 0:
                        payload = {"error": normalized_error_payload(exc.error_info)}
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    next_upstream, next_error = start_upstream_request(
                        model,
                        effective_input_items,
                        instructions=bridge_instructions,
                        tools=tools_responses,
                        tool_choice=tool_choice,
                        parallel_tool_calls=parallel_tool_calls,
                        reasoning_param=reasoning_param,
                        service_tier=service_tier,
                        web_search_mode=web_search_mode,
                        thread_session=thread_session,
                        extra_payload=extra_payload,
                    )
                    if next_error is not None:
                        next_error_info = error_info_from_flask_response("chatcore", "request_start", next_error)
                        payload = {"error": normalized_error_payload(next_error_info)}
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    current_upstream = next_upstream
                    current_created = int(time.time())

        stream_iter = _wrap_stream_logging("STREAM OUT /v1/chat/completions", _retrying_stream(), verbose)
        resp = Response(
            wrap_sse_stream_with_heartbeat(stream_iter),
            status=upstream.status_code,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        _log_fast_probe(
            "stream_ready",
            requested_model=requested_model,
            normalized_model=model,
            selected_mode=selected_mode,
            requested_service_tier=service_tier,
            observed_service_tier=getattr(upstream, "_observed_service_tier", None),
            is_stream=is_stream,
            upstream=upstream,
        )
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    completed_upstream = upstream
    if (
        isinstance(nonstream_result, dict)
        and not nonstream_result.get("ok")
        and effective_previous_response_id
        and should_retry_without_previous_response(nonstream_result.get("error_info"))
    ):
        retry_extra_payload = dict(extra_payload)
        retry_extra_payload.pop("previous_response_id", None)
        retry_upstream, retry_error = start_upstream_request(
            model,
            full_input_items,
            instructions=bridge_instructions,
            tools=tools_responses,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            web_search_mode=web_search_mode,
            thread_session=thread_session,
            extra_payload=retry_extra_payload,
        )
        if retry_error is None and retry_upstream is not None and retry_upstream.status_code < 400:
            completed_upstream = retry_upstream
            nonstream_result = _consume_chat_completion_nonstream(
                retry_upstream,
                requested_model=requested_model,
                model=model,
                created=int(time.time()),
                reasoning_compat=reasoning_compat,
            )

    if not isinstance(nonstream_result, dict) or not nonstream_result.get("ok"):
        return build_openai_error_response(
            last_error_info
            or build_error_info(
                source="chatcore",
                phase="nonstream",
                raw_status=502,
                raw_message="No candidate succeeded",
                raw_body={"message": "No candidate succeeded"},
            )
        )

    response_id = nonstream_result.get("response_id") or "chatcmpl"
    message = nonstream_result.get("message") or {"role": "assistant", "content": None}
    usage_obj = nonstream_result.get("usage_obj")
    observed_service_tier = nonstream_result.get("observed_service_tier")
    save_response_session(
        thread_session,
        response_id=response_id,
        full_input_items=full_input_items,
        upstream=completed_upstream,
    )
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    completion = {
        "id": response_id or "chatcmpl",
        "object": "chat.completion",
        "created": created,
        "model": requested_model or model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        **({"usage": usage_obj} if usage_obj else {}),
    }
    if verbose:
        _log_json("OUT POST /v1/chat/completions", completion)
    resp = make_response(jsonify(completion), upstream.status_code)
    _log_fast_probe(
        "done",
        requested_model=requested_model,
        normalized_model=model,
        selected_mode=selected_mode,
        requested_service_tier=service_tier,
        observed_service_tier=observed_service_tier,
        is_stream=is_stream,
        upstream=upstream,
        extra={"response_id": response_id},
    )
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


@openai_bp.route("/v1/completions", methods=["POST"])
def completions() -> Response:
    verbose = bool(current_app.config.get("VERBOSE"))
    verbose_obfuscation = bool(current_app.config.get("VERBOSE_OBFUSCATION"))
    debug_model = current_app.config.get("DEBUG_MODEL")
    reasoning_effort = current_app.config.get("REASONING_EFFORT", "medium")
    reasoning_summary = current_app.config.get("REASONING_SUMMARY", "auto")

    raw = request.get_data(cache=True, as_text=True) or ""
    if verbose:
        try:
            print("IN POST /v1/completions\n" + raw)
        except Exception:
            pass
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        err = {"error": {"message": "Invalid JSON body"}}
        if verbose:
            _log_json("OUT POST /v1/completions", err)
        return jsonify(err), 400

    requested_model = payload.get("model")
    model = normalize_model_name(requested_model, debug_model)
    prompt = payload.get("prompt")
    if isinstance(prompt, list):
        prompt = "".join([p if isinstance(p, str) else "" for p in prompt])
    if not isinstance(prompt, str):
        prompt = payload.get("suffix") or ""
    stream_req = bool(payload.get("stream", False))
    stream_options = payload.get("stream_options") if isinstance(payload.get("stream_options"), dict) else {}
    include_usage = bool(stream_options.get("include_usage", False))

    messages = [{"role": "user", "content": prompt or ""}]
    input_items = convert_chat_messages_to_responses_input(messages)

    model_reasoning = extract_reasoning_from_model_name(requested_model)
    reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else model_reasoning
    service_tier = _resolve_service_tier(payload, requested_model)
    reasoning_param = build_reasoning_param(
        reasoning_effort,
        reasoning_summary,
        reasoning_overrides,
        allowed_efforts=allowed_efforts_for_model(model),
    )
    bridge_instructions = _resolve_bridge_instructions(model, payload)
    thread_session = _resolve_thread_session(payload, input_items)
    (
        input_items,
        bridge_instructions,
        thread_session,
        full_input_items,
        effective_input_items,
        effective_previous_response_id,
        _,
    ) = _prepare_route_turn_state(
        payload,
        input_items,
        bridge_instructions,
        thread_session=thread_session,
    )
    extra_payload = {}
    if effective_previous_response_id:
        extra_payload["previous_response_id"] = effective_previous_response_id
    selected_mode = "chatgpt-backend"
    _log_fast_probe(
        "start",
        requested_model=requested_model,
        normalized_model=model,
        selected_mode=selected_mode,
        requested_service_tier=service_tier,
        is_stream=stream_req,
    )
    attempt_limit = _upstream_attempt_limit(stream_req, model, service_tier)
    last_error_info: Dict[str, Any] | None = None
    upstream = None
    created = int(time.time())
    nonstream_result: Dict[str, Any] | None = None
    for attempt_index in range(attempt_limit):
        upstream, error_resp = start_upstream_request(
            model,
            effective_input_items,
            instructions=bridge_instructions,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            thread_session=thread_session,
            extra_payload=extra_payload,
        )
        if error_resp is not None:
            error_info = error_info_from_flask_response("chatcore", "request_start", error_resp)
            last_error_info = error_info
            if not stream_req and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                continue
            _log_fast_probe(
                "request_start_error",
                requested_model=requested_model,
                normalized_model=model,
                selected_mode=selected_mode,
                requested_service_tier=service_tier,
                is_stream=stream_req,
                extra={"error_info": error_info},
            )
            return build_openai_error_response(error_info)

        record_rate_limits_from_response(upstream)
        created = int(time.time())
        if upstream.status_code >= 400:
            error_info = error_info_from_http_response(getattr(upstream, "chatmock_source", "upstream"), "http", upstream)
            last_error_info = error_info
            retry_recovered = False
            if effective_previous_response_id and should_retry_without_previous_response(error_info):
                retry_extra_payload = dict(extra_payload)
                retry_extra_payload.pop("previous_response_id", None)
                retry_upstream, retry_error = start_upstream_request(
                    model,
                    full_input_items,
                    instructions=bridge_instructions,
                    reasoning_param=reasoning_param,
                    service_tier=service_tier,
                    thread_session=thread_session,
                    extra_payload=retry_extra_payload,
                )
                if retry_error is None and retry_upstream is not None and retry_upstream.status_code < 400:
                    upstream = retry_upstream
                    record_rate_limits_from_response(upstream)
                    created = int(time.time())
                    retry_recovered = True
                if retry_error is not None:
                    error_info = error_info_from_flask_response("chatcore", "request_start", retry_error)
                elif retry_upstream is not None:
                    error_info = error_info_from_http_response(
                        getattr(retry_upstream, "chatmock_source", "upstream"),
                        "http",
                        retry_upstream,
                    )
                last_error_info = error_info
            if not retry_recovered:
                if not stream_req and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                    try:
                        upstream.close()
                    except Exception:
                        pass
                    continue
                _log_fast_probe(
                    "http_error",
                    requested_model=requested_model,
                    normalized_model=model,
                    selected_mode=selected_mode,
                    requested_service_tier=service_tier,
                    is_stream=stream_req,
                    upstream=upstream,
                    extra={"error_info": error_info},
                )
                _log_invalid_request_diagnostic(
                    "INVALID REQUEST /v1/completions upstream_http",
                    payload=payload,
                    error_info=error_info,
                )
                return build_openai_error_response(error_info)
        if not stream_req:
            nonstream_result = _consume_text_completion_nonstream(
                upstream,
                requested_model=requested_model,
                model=model,
                created=created,
            )
            if not nonstream_result.get("ok"):
                error_info = nonstream_result.get("error_info")
                if isinstance(error_info, dict):
                    last_error_info = error_info
                if _should_retry_nonstream_candidate(error_info) and attempt_index + 1 < attempt_limit:
                    upstream = None
                    nonstream_result = None
                    continue
                _log_fast_probe(
                    "nonstream_error",
                    requested_model=requested_model,
                    normalized_model=model,
                    selected_mode=selected_mode,
                    requested_service_tier=service_tier,
                    is_stream=stream_req,
                    upstream=upstream,
                    extra={"error_info": error_info},
                )
                _log_invalid_request_diagnostic(
                    "INVALID REQUEST /v1/completions nonstream",
                    payload=payload,
                    error_info=error_info if isinstance(error_info, dict) else None,
                )
                return build_openai_error_response(error_info or build_error_info(
                    source="chatcore",
                    phase="nonstream",
                    raw_status=502,
                    raw_message="Unknown upstream failure",
                    raw_body={"message": "Unknown upstream failure"},
                ))
            break
        break

    if upstream is None:
        _log_fast_probe(
            "retry_exhausted",
            requested_model=requested_model,
            normalized_model=model,
            selected_mode=selected_mode,
            requested_service_tier=service_tier,
            is_stream=stream_req,
            extra={"error_info": last_error_info},
        )
        return build_openai_error_response(
            last_error_info
            or build_error_info(
                source="chatcore",
                phase="retry_exhausted",
                raw_status=502,
                raw_message="No candidate succeeded",
                raw_body={"message": "No candidate succeeded"},
            )
        )

    if stream_req:
        if verbose:
            print("OUT POST /v1/completions (streaming response)")
        def _retrying_text_stream():
            current_upstream = upstream
            current_created = created
            remaining_attempts = max(1, attempt_limit)
            recovered_previous_response = False
            while remaining_attempts > 0:
                try:
                    yield from sse_translate_text(
                        current_upstream,
                        requested_model or model,
                        current_created,
                        verbose=verbose_obfuscation,
                        vlog=(print if verbose_obfuscation else None),
                        include_usage=include_usage,
                        on_response_completed=lambda response_obj, completed_upstream: save_response_session(
                            thread_session,
                            response_obj=response_obj,
                            full_input_items=full_input_items,
                            upstream=completed_upstream,
                        ),
                    )
                    return
                except RetryableStreamError as exc:
                    if (
                        effective_previous_response_id
                        and not recovered_previous_response
                        and should_retry_without_previous_response(exc.error_info)
                    ):
                        retry_extra_payload = dict(extra_payload)
                        retry_extra_payload.pop("previous_response_id", None)
                        next_upstream, next_error = start_upstream_request(
                            model,
                            full_input_items,
                            instructions=bridge_instructions,
                            reasoning_param=reasoning_param,
                            service_tier=service_tier,
                            thread_session=thread_session,
                            extra_payload=retry_extra_payload,
                        )
                        if next_error is not None:
                            next_error_info = error_info_from_flask_response("chatcore", "request_start", next_error)
                            payload = {"error": normalized_error_payload(next_error_info)}
                            yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                            yield b"data: [DONE]\n\n"
                            return
                        current_upstream = next_upstream
                        current_created = int(time.time())
                        recovered_previous_response = True
                        continue
                    remaining_attempts -= 1
                    if remaining_attempts <= 0:
                        payload = {"error": normalized_error_payload(exc.error_info)}
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    next_upstream, next_error = start_upstream_request(
                        model,
                        effective_input_items,
                        instructions=bridge_instructions,
                        reasoning_param=reasoning_param,
                        service_tier=service_tier,
                        thread_session=thread_session,
                        extra_payload=extra_payload,
                    )
                    if next_error is not None:
                        next_error_info = error_info_from_flask_response("chatcore", "request_start", next_error)
                        payload = {"error": normalized_error_payload(next_error_info)}
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    current_upstream = next_upstream
                    current_created = int(time.time())

        stream_iter = sse_translate_text(
            upstream,
            requested_model or model,
            created,
            verbose=verbose_obfuscation,
            vlog=(print if verbose_obfuscation else None),
            include_usage=include_usage,
        )
        stream_iter = _retrying_text_stream()
        stream_iter = _wrap_stream_logging("STREAM OUT /v1/completions", stream_iter, verbose)
        resp = Response(
            wrap_sse_stream_with_heartbeat(stream_iter),
            status=upstream.status_code,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        _log_fast_probe(
            "stream_ready",
            requested_model=requested_model,
            normalized_model=model,
            selected_mode=selected_mode,
            requested_service_tier=service_tier,
            observed_service_tier=getattr(upstream, "_observed_service_tier", None),
            is_stream=stream_req,
            upstream=upstream,
        )
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    completed_upstream = upstream
    if (
        isinstance(nonstream_result, dict)
        and not nonstream_result.get("ok")
        and effective_previous_response_id
        and should_retry_without_previous_response(nonstream_result.get("error_info"))
    ):
        retry_extra_payload = dict(extra_payload)
        retry_extra_payload.pop("previous_response_id", None)
        retry_upstream, retry_error = start_upstream_request(
            model,
            full_input_items,
            instructions=bridge_instructions,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            thread_session=thread_session,
            extra_payload=retry_extra_payload,
        )
        if retry_error is None and retry_upstream is not None and retry_upstream.status_code < 400:
            completed_upstream = retry_upstream
            nonstream_result = _consume_text_completion_nonstream(
                retry_upstream,
                requested_model=requested_model,
                model=model,
                created=int(time.time()),
            )

    if not isinstance(nonstream_result, dict) or not nonstream_result.get("ok"):
        return build_openai_error_response(
            last_error_info
            or build_error_info(
                source="chatcore",
                phase="nonstream",
                raw_status=502,
                raw_message="No candidate succeeded",
                raw_body={"message": "No candidate succeeded"},
            )
        )

    full_text = nonstream_result.get("full_text") or ""
    response_id = nonstream_result.get("response_id") or "cmpl"
    usage_obj = nonstream_result.get("usage_obj")
    observed_service_tier = nonstream_result.get("observed_service_tier")
    save_response_session(
        thread_session,
        response_id=response_id,
        full_input_items=full_input_items,
        upstream=completed_upstream,
    )

    completion = {
        "id": response_id or "cmpl",
        "object": "text_completion",
        "created": created,
        "model": requested_model or model,
        "choices": [
            {"index": 0, "text": full_text, "finish_reason": "stop", "logprobs": None}
        ],
        **({"usage": usage_obj} if usage_obj else {}),
    }
    if verbose:
        _log_json("OUT POST /v1/completions", completion)
    resp = make_response(jsonify(completion), upstream.status_code)
    _log_fast_probe(
        "done",
        requested_model=requested_model,
        normalized_model=model,
        selected_mode=selected_mode,
        requested_service_tier=service_tier,
        observed_service_tier=observed_service_tier,
        is_stream=stream_req,
        upstream=upstream,
        extra={"response_id": response_id},
    )
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


@openai_bp.route("/v1/models", methods=["GET"])
def list_models() -> Response:
    expose_variants = bool(current_app.config.get("EXPOSE_REASONING_MODELS"))
    model_groups = [
        ("gpt-5", ["high", "medium", "low", "minimal"]),
        ("gpt-5.1", ["high", "medium", "low"]),
        ("gpt-5.2", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.4", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.4-fast", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.4-mini", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.3-codex", ["xhigh", "high", "medium", "low"]),
        ("gpt-5-codex", ["high", "medium", "low"]),
        ("gpt-5.2-codex", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.1-codex", ["high", "medium", "low"]),
        ("gpt-5.1-codex-max", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.1-codex-mini", []),
    ]
    model_ids: List[str] = []
    for base, efforts in model_groups:
        model_ids.append(base)
        if expose_variants:
            model_ids.extend([f"{base}-{effort}" for effort in efforts])
    data = [
        {"id": public_model_name(mid), "object": "model", "owned_by": "owner"}
        for mid in model_ids
    ]
    models = {"object": "list", "data": data}
    resp = make_response(jsonify(models), 200)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp
