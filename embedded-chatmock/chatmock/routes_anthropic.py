from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Tuple

from flask import Blueprint, Response, current_app, jsonify, make_response, request

from .config import BASE_INSTRUCTIONS, GPT5_CODEX_INSTRUCTIONS
from .context_compaction import maybe_compact_input_items
from .http import build_cors_headers
from .limits import record_rate_limits_from_response
from .responses_session import is_previous_response_not_found, resolve_turn_state, save_response_session
from .reasoning import (
    allowed_efforts_for_model,
    build_reasoning_param,
    extract_reasoning_from_model_name,
    extract_service_tier_from_model_name,
    presented_service_tier_name,
)
from .upstream_errors import (
    build_anthropic_error_response,
    build_error_info,
    error_info_from_event_response,
    error_info_from_flask_response,
    error_info_from_http_response,
    should_retry_next_candidate,
)
from .upstream import normalize_model_name, start_upstream_request
from .thread_sessions import resolve_thread_session_state
from .usage_passthrough import extract_responses_usage_from_event, to_anthropic_usage
from .utils import (
    RetryableStreamError,
    extract_response_output_text,
    merge_response_text,
    restore_reserved_tool_name,
    sanitize_reserved_tool_name,
)


anthropic_bp = Blueprint("anthropic", __name__)


def _log_json(prefix: str, payload: Any) -> None:
    try:
        print(f"{prefix}\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    except Exception:
        try:
            print(f"{prefix}\n{payload}")
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
    alias_value = extract_service_tier_from_model_name(requested_model)
    if isinstance(alias_value, str) and alias_value:
        return alias_value
    configured = current_app.config.get("SERVICE_TIER")
    if isinstance(configured, str) and configured.strip():
        normalized = configured.strip().lower()
        if normalized in ("off", "none", "unset"):
            return None
        return normalized
    return None


def _error_response(message: str, status: int = 400, err_type: str = "invalid_request_error") -> Response:
    payload = {"type": "error", "error": {"type": err_type, "message": message}}
    resp = make_response(jsonify(payload), status)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


def _decode_json_body(raw: str) -> Dict[str, Any] | None:
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        try:
            return json.loads(raw.lstrip("\ufeff")) if raw else {}
        except Exception:
            return None


def _system_to_text(system_payload: Any) -> str:
    if isinstance(system_payload, str):
        return system_payload
    if not isinstance(system_payload, list):
        return ""
    parts: List[str] = []
    for block in system_payload:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def _image_source_to_url(source: Any) -> str | None:
    if not isinstance(source, dict):
        return None
    source_type = source.get("type")
    if source_type == "base64":
        media_type = source.get("media_type")
        data = source.get("data")
        if isinstance(media_type, str) and media_type and isinstance(data, str) and data:
            return f"data:{media_type};base64,{data}"
        return None
    if source_type == "url":
        url = source.get("url")
        return url if isinstance(url, str) and url else None
    return None


def _tool_result_output(block: Dict[str, Any]) -> str:
    content = block.get("content")
    is_error = bool(block.get("is_error"))
    output = ""
    if isinstance(content, str):
        output = content
    elif isinstance(content, list):
        texts: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        if texts:
            output = "\n".join(texts)
        else:
            try:
                output = json.dumps(content, ensure_ascii=False)
            except Exception:
                output = str(content)
    elif content is None:
        output = ""
    else:
        try:
            output = json.dumps(content, ensure_ascii=False)
        except Exception:
            output = str(content)

    if is_error:
        return f"[tool_error]\n{output}" if output else "[tool_error]"
    return output


def _safe_json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {"value": raw}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except Exception:
        return {"raw": raw}


def _flush_message_input(input_items: List[Dict[str, Any]], role: str, content_items: List[Dict[str, Any]]) -> None:
    if not content_items:
        return
    input_items.append(
        {
            "type": "message",
            "role": "assistant" if role == "assistant" else "user",
            "content": content_items[:],
        }
    )
    content_items.clear()


def _convert_anthropic_messages_to_input(messages: Any) -> tuple[List[Dict[str, Any]] | None, str | None]:
    if not isinstance(messages, list):
        return None, "messages must be an array"

    input_items: List[Dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return None, f"messages[{idx}] must be an object"
        role = msg.get("role")
        if role not in ("user", "assistant"):
            return None, f"messages[{idx}].role must be 'user' or 'assistant'"

        content = msg.get("content")
        blocks: List[Dict[str, Any]] = []
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = [b for b in content if isinstance(b, dict)]
        else:
            return None, f"messages[{idx}].content must be a string or block array"

        pending_content_items: List[Dict[str, Any]] = []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    pending_content_items.append(
                        {"type": ("output_text" if role == "assistant" else "input_text"), "text": text}
                    )
                continue

            if block_type == "image":
                if role != "user":
                    return None, f"messages[{idx}] image blocks are only supported for user role"
                url = _image_source_to_url(block.get("source"))
                if not url:
                    return None, f"messages[{idx}] invalid image block source"
                pending_content_items.append({"type": "input_image", "image_url": url})
                continue

            if block_type == "tool_use":
                if role != "assistant":
                    return None, f"messages[{idx}] tool_use blocks are only supported for assistant role"
                _flush_message_input(input_items, role, pending_content_items)
                call_id = block.get("id")
                name = block.get("name")
                if not isinstance(call_id, str) or not call_id:
                    return None, f"messages[{idx}] tool_use.id must be a non-empty string"
                if not isinstance(name, str) or not name:
                    return None, f"messages[{idx}] tool_use.name must be a non-empty string"
                try:
                    args = json.dumps(block.get("input") if block.get("input") is not None else {}, ensure_ascii=False)
                except Exception:
                    args = "{}"
                input_items.append(
                    {
                        "type": "function_call",
                        "name": name,
                        "arguments": args,
                        "call_id": call_id,
                    }
                )
                continue

            if block_type == "tool_result":
                if role != "user":
                    return None, f"messages[{idx}] tool_result blocks are only supported for user role"
                _flush_message_input(input_items, role, pending_content_items)
                call_id = block.get("tool_use_id")
                if not isinstance(call_id, str) or not call_id:
                    return None, f"messages[{idx}] tool_result.tool_use_id must be a non-empty string"
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _tool_result_output(block),
                    }
                )
                continue

            if isinstance(block_type, str) and block_type:
                return None, f"unsupported content block type: {block_type}"
            return None, f"messages[{idx}] includes invalid content block"

        _flush_message_input(input_items, role, pending_content_items)

    return input_items, None


def _convert_anthropic_tools(tools_payload: Any) -> tuple[List[Dict[str, Any]] | None, str | None]:
    if tools_payload is None:
        return [], None
    if not isinstance(tools_payload, list):
        return None, "tools must be an array"

    out: List[Dict[str, Any]] = []
    for idx, tool in enumerate(tools_payload):
        if not isinstance(tool, dict):
            return None, f"tools[{idx}] must be an object"
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            return None, f"tools[{idx}].name must be a non-empty string"
        desc = tool.get("description")
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "name": sanitize_reserved_tool_name(name),
                "description": desc if isinstance(desc, str) else "",
                "strict": False,
                "parameters": schema,
            }
        )
    return out, None


def _convert_anthropic_tool_choice(choice_payload: Any) -> tuple[Any, bool, str | None]:
    if choice_payload is None:
        return "auto", False, None

    if isinstance(choice_payload, str):
        normalized = choice_payload.strip().lower()
        if normalized in ("auto", "any"):
            return "auto", False, None
        if normalized == "none":
            return "none", False, None
        return None, False, "tool_choice must be auto/any/none or an object"

    if not isinstance(choice_payload, dict):
        return None, False, "tool_choice must be auto/any/none or an object"

    choice_type = str(choice_payload.get("type") or "").strip().lower()
    disable_parallel = bool(choice_payload.get("disable_parallel_tool_use", False))
    parallel = not disable_parallel
    if choice_type in ("auto", "any"):
        return "auto", parallel, None
    if choice_type == "none":
        return "none", parallel, None
    if choice_type == "tool":
        name = choice_payload.get("name")
        if not isinstance(name, str) or not name:
            return None, parallel, "tool_choice.type=tool requires non-empty name"
        return {"type": "function", "name": sanitize_reserved_tool_name(name)}, parallel, None
    return None, parallel, "unsupported tool_choice.type"


def _tool_use_payload_from_item(item: Dict[str, Any]) -> Dict[str, Any] | None:
    item_type = item.get("type")
    if item_type != "function_call":
        return None
    call_id = item.get("call_id") or item.get("id") or f"toolu_{uuid.uuid4().hex[:12]}"
    name = item.get("name") or "tool"
    arguments = item.get("arguments") or "{}"
    return {
        "id": call_id,
        "name": restore_reserved_tool_name(name),
        "input": _safe_json_object(arguments),
    }


def _anthropic_stream(upstream, model_out: str, verbose: bool, *, on_completed=None):
    def _emit(event: str, payload: Dict[str, Any]):
        data = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        if verbose:
            try:
                print(f"STREAM OUT /v1/messages\n{data}")
            except Exception:
                pass
        return data

    response_id = f"msg_{uuid.uuid4().hex}"
    stop_reason = "end_turn"
    usage_obj: Dict[str, Any] | None = None
    next_block_index = 0
    text_open = False
    text_index = -1
    emitted_output_text = ""
    saw_completed = False
    has_visible_output = False

    def _emit_text_delta(delta_text: str) -> str | None:
        nonlocal text_open, text_index, next_block_index, emitted_output_text
        if not isinstance(delta_text, str) or not delta_text:
            return None
        if not text_open:
            text_index = next_block_index
            next_block_index += 1
            text_open = True
            yield _emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": text_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
        emitted_output_text += delta_text
        yield _emit(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": text_index,
                "delta": {"type": "text_delta", "text": delta_text},
            },
        )
    try:
        yield _emit(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": response_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model_out,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": usage_obj or {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )

        for raw_line in upstream.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
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

            extracted_usage = extract_responses_usage_from_event(evt)
            if extracted_usage:
                usage_obj = to_anthropic_usage(extracted_usage)

            kind = evt.get("type")
            if kind == "response.output_text.delta":
                delta = evt.get("delta") or ""
                if isinstance(delta, str) and delta:
                    has_visible_output = True
                    for chunk in _emit_text_delta(delta):
                        yield chunk
                continue

            if kind == "response.output_text.done":
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    evt.get("text") or "",
                )
                if missing_delta:
                    has_visible_output = True
                    for chunk in _emit_text_delta(missing_delta):
                        yield chunk
                continue

            if kind == "response.content_part.done":
                part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    part.get("text") or "",
                )
                if missing_delta:
                    has_visible_output = True
                    for chunk in _emit_text_delta(missing_delta):
                        yield chunk
                continue

            if kind == "response.output_item.done":
                item = evt.get("item") if isinstance(evt.get("item"), dict) else {}
                tool_payload = _tool_use_payload_from_item(item)
                if tool_payload is None:
                    emitted_output_text, missing_delta = merge_response_text(
                        emitted_output_text,
                        extract_response_output_text(item),
                    )
                    if missing_delta:
                        has_visible_output = True
                        for chunk in _emit_text_delta(missing_delta):
                            yield chunk
                    continue
                has_visible_output = True
                if text_open:
                    yield _emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
                    text_open = False
                stop_reason = "tool_use"
                tool_index = next_block_index
                next_block_index += 1
                yield _emit(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": tool_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_payload["id"],
                            "name": tool_payload["name"],
                            "input": {},
                        },
                    },
                )
                partial_json = json.dumps(tool_payload["input"], ensure_ascii=False, separators=(",", ":"))
                yield _emit(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": tool_index,
                        "delta": {"type": "input_json_delta", "partial_json": partial_json},
                    },
                )
                yield _emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})
                continue

            if kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                if not has_visible_output and (
                    should_retry_next_candidate(error_info) or is_previous_response_not_found(error_info)
                ):
                    raise RetryableStreamError(error_info)
                payload = build_anthropic_error_response(error_info).get_json()
                yield _emit("error", payload)
                return

            if kind == "response.completed":
                saw_completed = True
                if callable(on_completed) and isinstance(evt.get("response"), dict):
                    try:
                        on_completed(evt.get("response"), upstream)
                    except Exception:
                        pass
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    extract_response_output_text(evt.get("response")),
                )
                if missing_delta:
                    has_visible_output = True
                    for chunk in _emit_text_delta(missing_delta):
                        yield chunk
                break

        if not saw_completed and not emitted_output_text and stop_reason != "tool_use":
            error_info = build_error_info(
                source=getattr(upstream, "chatmock_source", "upstream"),
                phase="stream",
                raw_status=int(getattr(upstream, "status_code", 502) or 502),
                raw_message="stream ended before response.completed",
                raw_body={"message": "stream ended before response.completed"},
            )
            payload = build_anthropic_error_response(error_info).get_json()
            yield _emit("error", payload)
            return
        if text_open:
            yield _emit("content_block_stop", {"type": "content_block_stop", "index": text_index})

        yield _emit(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": usage_obj or {"input_tokens": 0, "output_tokens": 0},
            },
        )
        yield _emit("message_stop", {"type": "message_stop"})
    finally:
        upstream.close()


def _build_anthropic_message_response(
    *,
    model_out: str,
    service_tier: str | None,
    response_id: str,
    full_text: str,
    tool_calls: List[Dict[str, Any]],
    usage_obj: Dict[str, Any] | None,
    observed_service_tier: str | None,
) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = []
    stop_reason = "end_turn"
    if full_text:
        content.append({"type": "text", "text": full_text})
    if tool_calls:
        stop_reason = "tool_use"
        content.extend([{"type": "tool_use", **tool_call} for tool_call in tool_calls])

    message_obj = {
        "id": response_id,
        "type": "message",
        "role": "assistant",
        "model": model_out,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage_obj or {"input_tokens": 0, "output_tokens": 0},
    }
    presented_service_tier = (
        presented_service_tier_name(service_tier, observed_service_tier)
        if bool(current_app.config.get("EXPOSE_SERVICE_TIER", False))
        else None
    )
    if presented_service_tier:
        message_obj["service_tier"] = presented_service_tier
    return message_obj


def _consume_anthropic_nonstream(
    upstream,
    *,
    model_out: str,
    service_tier: str | None,
) -> Dict[str, Any]:
    full_text = ""
    tool_calls: List[Dict[str, Any]] = []
    usage_obj: Dict[str, Any] | None = None
    response_id = f"msg_{uuid.uuid4().hex}"
    error_message: str | None = None
    error_info: Dict[str, Any] | None = None
    observed_service_tier: str | None = None
    completed_ok = False
    try:
        for raw_line in upstream.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
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

            extracted_usage = extract_responses_usage_from_event(evt)
            if extracted_usage:
                usage_obj = to_anthropic_usage(extracted_usage)
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("service_tier"), str):
                observed_service_tier = evt["response"].get("service_tier") or observed_service_tier

            kind = evt.get("type")
            if kind == "response.output_text.delta":
                full_text += evt.get("delta") or ""
            elif kind == "response.output_text.done":
                full_text, _ = merge_response_text(full_text, evt.get("text") or "")
            elif kind == "response.content_part.done":
                part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
                full_text, _ = merge_response_text(full_text, part.get("text") or "")
            elif kind == "response.output_item.done":
                item = evt.get("item") if isinstance(evt.get("item"), dict) else {}
                tool_payload = _tool_use_payload_from_item(item)
                if tool_payload is not None:
                    tool_calls.append(tool_payload)
                else:
                    full_text, _ = merge_response_text(
                        full_text,
                        extract_response_output_text(item),
                    )
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

    return {
        "ok": True,
        "response_id": response_id,
        "message_obj": _build_anthropic_message_response(
            model_out=model_out,
            service_tier=service_tier,
            response_id=response_id,
            full_text=full_text,
            tool_calls=tool_calls,
            usage_obj=usage_obj,
            observed_service_tier=observed_service_tier,
        ),
    }


@anthropic_bp.route("/v1/messages", methods=["POST"])
def messages() -> Response:
    verbose = bool(current_app.config.get("VERBOSE"))
    debug_model = current_app.config.get("DEBUG_MODEL")
    reasoning_effort = current_app.config.get("REASONING_EFFORT", "medium")
    reasoning_summary = current_app.config.get("REASONING_SUMMARY", "auto")

    raw = request.get_data(cache=True, as_text=True) or ""
    if verbose:
        try:
            print("IN POST /v1/messages\n" + raw)
        except Exception:
            pass

    payload = _decode_json_body(raw)
    if payload is None:
        return _error_response("invalid JSON body", 400, "invalid_request_error")

    requested_model = payload.get("model")
    model = normalize_model_name(requested_model, debug_model)

    input_items, msg_err = _convert_anthropic_messages_to_input(payload.get("messages"))
    if msg_err:
        return _error_response(msg_err, 400, "invalid_request_error")
    assert isinstance(input_items, list)
    if not input_items:
        return _error_response("messages must include at least one content block", 400, "invalid_request_error")

    system_text = _system_to_text(payload.get("system")).strip()
    instructions = _resolve_bridge_instructions(model, payload) or ""
    if system_text:
        instructions = (instructions + "\n\n" + system_text).strip() if instructions else system_text

    tools_responses, tools_err = _convert_anthropic_tools(payload.get("tools"))
    if tools_err:
        return _error_response(tools_err, 400, "invalid_request_error")
    assert isinstance(tools_responses, list)

    tool_choice, parallel_tool_calls, tool_choice_err = _convert_anthropic_tool_choice(payload.get("tool_choice"))
    if tool_choice_err:
        return _error_response(tool_choice_err, 400, "invalid_request_error")
    if isinstance(payload.get("parallel_tool_calls"), bool):
        parallel_tool_calls = bool(payload.get("parallel_tool_calls"))

    model_reasoning = extract_reasoning_from_model_name(requested_model)
    reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else model_reasoning
    service_tier = _resolve_service_tier(payload, requested_model)
    reasoning_param = build_reasoning_param(
        reasoning_effort,
        reasoning_summary,
        reasoning_overrides,
        allowed_efforts=allowed_efforts_for_model(model),
    )
    input_items, instructions, _ = maybe_compact_input_items(payload, input_items, instructions)
    thread_session = resolve_thread_session_state(
        payload=payload,
        input_items=input_items,
        headers=request.headers,
    )
    full_input_items = list(input_items)
    effective_input_items, effective_previous_response_id = resolve_turn_state(
        payload,
        full_input_items,
        thread_session,
    )
    extra_payload = {}
    if effective_previous_response_id:
        extra_payload["previous_response_id"] = effective_previous_response_id

    model_out = requested_model or model
    is_stream = bool(payload.get("stream"))
    attempt_limit = _upstream_attempt_limit(is_stream, model, service_tier)
    last_error_info: Dict[str, Any] | None = None
    upstream = None
    for attempt_index in range(attempt_limit):
        upstream, error_resp = start_upstream_request(
            model,
            effective_input_items,
            instructions=instructions,
            tools=tools_responses,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            thread_session=thread_session,
            extra_payload=extra_payload,
        )
        if error_resp is not None:
            error_info = error_info_from_flask_response("chatcore", "request_start", error_resp)
            last_error_info = error_info
            if not is_stream and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                continue
            return build_anthropic_error_response(error_info)

        record_rate_limits_from_response(upstream)
        if upstream.status_code >= 400:
            try:
                upstream.close()
            except Exception:
                pass
            error_info = error_info_from_http_response(getattr(upstream, "chatmock_source", "upstream"), "http", upstream)
            last_error_info = error_info
            if not is_stream and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                continue
            return build_anthropic_error_response(error_info)
        break

    if upstream is None:
        return build_anthropic_error_response(
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
        def _retrying_stream():
            current_upstream = upstream
            recovered_previous_response = False
            while True:
                try:
                    yield from _anthropic_stream(
                        current_upstream,
                        model_out,
                        verbose,
                        on_completed=lambda response_obj, completed_upstream: save_response_session(
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
                        and is_previous_response_not_found(exc.error_info)
                    ):
                        retry_extra_payload = dict(extra_payload)
                        retry_extra_payload.pop("previous_response_id", None)
                        next_upstream, next_error = start_upstream_request(
                            model,
                            full_input_items,
                            instructions=instructions,
                            tools=tools_responses,
                            tool_choice=tool_choice,
                            parallel_tool_calls=parallel_tool_calls,
                            reasoning_param=reasoning_param,
                            service_tier=service_tier,
                            thread_session=thread_session,
                            extra_payload=retry_extra_payload,
                        )
                        if next_error is not None:
                            next_error_info = error_info_from_flask_response("chatcore", "request_start", next_error)
                            payload = build_anthropic_error_response(next_error_info).get_json()
                            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            return
                        current_upstream = next_upstream
                        recovered_previous_response = True
                        continue
                    payload = build_anthropic_error_response(exc.error_info).get_json()
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    return

        resp = Response(
            _retrying_stream(),
            status=upstream.status_code,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    completed_upstream = upstream
    result = _consume_anthropic_nonstream(
        upstream,
        model_out=model_out,
        service_tier=service_tier,
    )
    if not result.get("ok") and effective_previous_response_id and is_previous_response_not_found(result.get("error_info")):
        retry_extra_payload = dict(extra_payload)
        retry_extra_payload.pop("previous_response_id", None)
        retry_upstream, retry_error = start_upstream_request(
            model,
            full_input_items,
            instructions=instructions,
            tools=tools_responses,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            thread_session=thread_session,
            extra_payload=retry_extra_payload,
        )
        if retry_error is None and retry_upstream is not None and retry_upstream.status_code < 400:
            completed_upstream = retry_upstream
            result = _consume_anthropic_nonstream(
                retry_upstream,
                model_out=model_out,
                service_tier=service_tier,
            )

    if not result.get("ok"):
        return build_anthropic_error_response(result.get("error_info"))

    message_obj = result.get("message_obj") or {}
    response_id = result.get("response_id")
    if verbose:
        _log_json("OUT POST /v1/messages", message_obj)

    save_response_session(
        thread_session,
        response_id=response_id,
        full_input_items=full_input_items,
        upstream=completed_upstream,
    )
    resp = make_response(jsonify(message_obj), completed_upstream.status_code)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp
