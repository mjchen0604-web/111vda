from __future__ import annotations

import json
import time
from typing import Any, Dict, List

import requests
from flask import current_app, jsonify, make_response
from flask import request as flask_request

from .connection_slots import acquire_chatgpt_connection_slot, release_chatgpt_connection_slot
from .config import CHATGPT_RESPONSES_URL
from .http import build_cors_headers
from .reasoning import split_model_alias
from .session import ensure_session_id
from .upstream_errors import (
    build_error_info,
    build_openai_error_response,
    error_info_from_http_response,
    normalized_error_payload,
)
from .utils import (
    ManagedAuthUpstream,
    _release_auth_candidate_slot,
    claim_chatgpt_auth_candidate,
    get_effective_chatgpt_auth_candidates,
    get_max_retry_interval_seconds,
    get_request_retry_limit,
    get_retryable_statuses,
    handle_chatgpt_candidate_failure,
    is_auth_candidate_blocked,
    mark_chatgpt_auth_result,
)

_UPSTREAM_CONNECT_TIMEOUT_SECONDS = 30
_UPSTREAM_READ_TIMEOUT_SECONDS = 3600


def _log_json(prefix: str, payload: Any) -> None:
    try:
        print(f"{prefix}\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    except Exception:
        try:
            print(f"{prefix}\n{payload}")
        except Exception:
            pass


def normalize_model_name(name: str | None, debug_model: str | None = None) -> str:
    if isinstance(debug_model, str) and debug_model.strip():
        return debug_model.strip()
    if not isinstance(name, str) or not name.strip():
        return "gpt-5"
    base, _, _ = split_model_alias(name)
    mapping = {
        "gpt5": "gpt-5",
        "gpt-5-latest": "gpt-5",
        "gpt-5": "gpt-5",
        "gpt-5.1": "gpt-5.1",
        "gpt5.2": "gpt-5.2",
        "gpt-5.2": "gpt-5.2",
        "gpt-5.2-latest": "gpt-5.2",
        "gpt5.4": "gpt-5.4",
        "gpt-5.4": "gpt-5.4",
        "gpt-5.4-latest": "gpt-5.4",
        "gpt5.3-codex": "gpt-5.3-codex",
        "gpt-5.3-codex": "gpt-5.3-codex",
        "gpt-5.3-codex-latest": "gpt-5.3-codex",
        "gpt5.2-codex": "gpt-5.2-codex",
        "gpt-5.2-codex": "gpt-5.2-codex",
        "gpt-5.2-codex-latest": "gpt-5.2-codex",
        "gpt5-codex": "gpt-5-codex",
        "gpt-5-codex": "gpt-5-codex",
        "gpt-5-codex-latest": "gpt-5-codex",
        "gpt-5.1-codex": "gpt-5.1-codex",
        "gpt-5.1-codex-max": "gpt-5.1-codex-max",
        "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
    }
    return mapping.get(base, base or "gpt-5")


def _is_generic_invalid_request(error_info: Dict[str, Any] | None) -> bool:
    if not isinstance(error_info, dict):
        return False
    if int(error_info.get("raw_status") or 0) != 400:
        return False
    normalized = normalized_error_payload(error_info)
    if str(normalized.get("type") or "").strip().lower() != "invalid_request_error":
        return False
    raw_code = str(error_info.get("raw_code") or "").strip().lower()
    if raw_code not in ("", "invalid_request", "invalid_request_error", "bad_request"):
        return False
    raw_message = str(error_info.get("raw_message") or "").strip().lower()
    normalized_message = str(normalized.get("message") or "").strip().lower()
    return "invalid request" in raw_message or normalized_message == "invalid request"


def _is_undefined_text_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in ("", "undefined", "[undefined]")


def _build_invalid_request_retry_payloads(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    variants: List[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return variants

    def _clone(current: Dict[str, Any]) -> Dict[str, Any]:
        return json.loads(json.dumps(current, ensure_ascii=False))

    def _append(candidate: Dict[str, Any]) -> None:
        if candidate == payload:
            return
        if variants and candidate == variants[-1]:
            return
        variants.append(candidate)

    current = _clone(payload)
    if current.pop("previous_response_id", None) is not None:
        _append(_clone(current))
    include = current.get("include")
    if isinstance(include, list):
        filtered_include = [item for item in include if item != "reasoning.encrypted_content"]
        if filtered_include != include:
            if filtered_include:
                current["include"] = filtered_include
            else:
                current.pop("include", None)
            _append(_clone(current))
    if current.pop("service_tier", None) is not None:
        _append(_clone(current))
    if current.get("parallel_tool_calls") is True:
        current["parallel_tool_calls"] = False
        _append(_clone(current))
    tool_choice = current.get("tool_choice")
    if isinstance(tool_choice, dict):
        current["tool_choice"] = "auto"
        _append(_clone(current))
    elif isinstance(tool_choice, str) and tool_choice.strip().lower() not in ("", "auto", "none"):
        current["tool_choice"] = "auto"
        _append(_clone(current))
    return variants


def _stringify_function_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _sanitize_orphan_function_call_outputs(input_items: Any) -> Any:
    if not isinstance(input_items, list):
        return input_items
    sanitized: List[Dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict):
            sanitized.append(item)
            continue
        item_type = str(item.get("type") or "").strip()
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            if call_id:
                seen_call_ids.add(call_id)
            sanitized.append(item)
            continue
        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or "").strip()
            if call_id and call_id in seen_call_ids:
                sanitized.append(item)
                continue
            output_text = _stringify_function_output(item.get("output"))
            fallback_text = f"[tool_result:{call_id or 'unknown'}]\n{output_text}" if output_text else f"[tool_result:{call_id or 'unknown'}]"
            sanitized.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": fallback_text}],
                }
            )
            continue
        sanitized.append(item)
    return sanitized


def _flatten_response_history_items(input_items: Any, *, has_previous_response_id: bool) -> Any:
    if not isinstance(input_items, list):
        return input_items
    if has_previous_response_id:
        return _sanitize_orphan_function_call_outputs(input_items)

    flattened: List[Dict[str, Any]] = []
    for item in input_items:
        if not isinstance(item, dict):
            flattened.append(item)
            continue
        item_type = str(item.get("type") or "").strip()
        if item_type == "reasoning":
            continue
        if item_type == "function_call":
            continue
        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or "").strip() or "unknown"
            output_text = _stringify_function_output(item.get("output"))
            fallback_text = f"[tool_result:{call_id}]\n{output_text}" if output_text else f"[tool_result:{call_id}]"
            flattened.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": fallback_text}],
                }
            )
            continue
        item_copy = dict(item)
        item_copy.pop("id", None)
        flattened.append(item_copy)
    return _sanitize_orphan_function_call_outputs(flattened)


def prepare_upstream_input_items(input_items: Any, *, has_previous_response_id: bool) -> Any:
    return _flatten_response_history_items(input_items, has_previous_response_id=has_previous_response_id)


def _minimize_responses_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    minimized = json.loads(json.dumps(payload, ensure_ascii=False))
    has_previous_response_id = bool(minimized.get("previous_response_id"))
    minimized["input"] = _flatten_response_history_items(
        minimized.get("input"),
        has_previous_response_id=has_previous_response_id,
    )
    if minimized.get("input") is None:
        minimized.pop("input", None)

    for key in list(minimized.keys()):
        value = minimized.get(key)
        if _is_undefined_text_value(value):
            minimized.pop(key, None)
            continue
        if isinstance(value, list):
            filtered = [item for item in value if not _is_undefined_text_value(item)]
            if filtered != value:
                if filtered:
                    minimized[key] = filtered
                else:
                    minimized.pop(key, None)

    instructions = minimized.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        minimized.pop("instructions", None)

    include = minimized.get("include")
    if isinstance(include, list):
        filtered_include = [item for item in include if item != "reasoning.encrypted_content"]
        if filtered_include:
            minimized["include"] = filtered_include
        else:
            minimized.pop("include", None)

    tools = minimized.get("tools")
    if isinstance(tools, list) and len(tools) == 0:
        minimized.pop("tools", None)

    if minimized.get("tool_choice") == "auto":
        minimized.pop("tool_choice", None)

    if minimized.get("parallel_tool_calls") is False:
        minimized.pop("parallel_tool_calls", None)

    return minimized


def _post_with_invalid_request_fallback(
    request_callable,
    *,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    verbose: bool,
):
    current_payload = payload
    upstream = request_callable(
        url,
        headers=headers,
        json=current_payload,
        stream=True,
        timeout=(
            _UPSTREAM_CONNECT_TIMEOUT_SECONDS,
            _UPSTREAM_READ_TIMEOUT_SECONDS,
        ),
    )
    error_info = error_info_from_http_response("upstream", "http", upstream)
    if upstream.status_code < 400 or not _is_generic_invalid_request(error_info):
        return upstream, current_payload

    for retry_payload in _build_invalid_request_retry_payloads(current_payload):
        if verbose:
            try:
                _log_json("OUTBOUND >> ChatGPT Responses API invalid_request retry payload", retry_payload)
            except Exception:
                pass
        try:
            upstream.close()
        except Exception:
            pass
        current_payload = retry_payload
        upstream = request_callable(
            url,
            headers=headers,
            json=current_payload,
            stream=True,
            timeout=(
                _UPSTREAM_CONNECT_TIMEOUT_SECONDS,
                _UPSTREAM_READ_TIMEOUT_SECONDS,
            ),
        )
        error_info = error_info_from_http_response("upstream", "http", upstream)
        if upstream.status_code < 400 or not _is_generic_invalid_request(error_info):
            return upstream, current_payload
    return upstream, current_payload


def _normalize_service_tier(service_tier: str | None) -> str | None:
    if not isinstance(service_tier, str) or not service_tier.strip():
        return None
    normalized = service_tier.strip().lower()
    if normalized in ("off", "none", "unset", "default"):
        return None
    if normalized == "fast":
        return "priority"
    if normalized in ("priority", "flex"):
        return normalized
    return None


def resolve_upstream_mode(configured_mode: str, model: str, service_tier: str | None) -> str:
    _ = configured_mode, model, service_tier
    return "chatgpt-backend"


def _start_chatgpt_backend_request(
    model: str,
    input_items: List[Dict[str, Any]],
    *,
    instructions: str | None = None,
    tools: List[Dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    parallel_tool_calls: bool = False,
    reasoning_param: Dict[str, Any] | None = None,
    service_tier: str | None = None,
    extra_payload: Dict[str, Any] | None = None,
    thread_session: Dict[str, Any] | None = None,
    verbose: bool = False,
):
    normalized_service_tier = _normalize_service_tier(service_tier)
    auth_candidates = get_effective_chatgpt_auth_candidates(ensure_fresh=True)
    if not auth_candidates:
        resp = make_response(
            jsonify(
                {
                    "error": {
                        "message": (
                            "Missing ChatGPT credentials. Configure CHATGPT_LOCAL_AUTH_FILES "
                            "or place auth.json under CHATGPT_LOCAL_HOME."
                        ),
                    }
                }
            ),
            401,
        )
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return None, resp

    include: List[str] = []
    if isinstance(reasoning_param, dict):
        include.append("reasoning.encrypted_content")

    client_session_id = None
    try:
        client_session_id = (
            flask_request.headers.get("X-Session-Id")
            or flask_request.headers.get("session_id")
            or None
        )
    except Exception:
        client_session_id = None
    normalized_extra_payload = dict(extra_payload or {})
    payload_prompt_cache_key = normalized_extra_payload.get("prompt_cache_key")
    if not isinstance(payload_prompt_cache_key, str) or not payload_prompt_cache_key.strip():
        payload_prompt_cache_key = None
    thread_session_key = None
    if isinstance(thread_session, dict):
        raw_session_key = thread_session.get("session_key")
        if isinstance(raw_session_key, str) and raw_session_key.strip():
            thread_session_key = raw_session_key.strip()
    session_id = ensure_session_id(
        instructions,
        input_items,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        client_supplied=client_session_id or payload_prompt_cache_key or thread_session_key,
    )

    responses_payload = {
        "model": model,
        "instructions": instructions if isinstance(instructions, str) and instructions.strip() else instructions,
        "input": input_items,
        "tools": tools or [],
        "tool_choice": tool_choice if tool_choice in ("auto", "none") or isinstance(tool_choice, dict) else "auto",
        "parallel_tool_calls": bool(parallel_tool_calls),
        "store": False,
        "stream": True,
        "prompt_cache_key": session_id,
    }
    for key, value in normalized_extra_payload.items():
        if value is None:
            continue
        responses_payload[key] = value
    existing_include = responses_payload.get("include")
    merged_include: List[str] = []
    if isinstance(existing_include, list):
        for item in existing_include:
            if isinstance(item, str) and item and item not in merged_include:
                merged_include.append(item)
    for item in include:
        if item not in merged_include:
            merged_include.append(item)
    if merged_include:
        responses_payload["include"] = merged_include
    if reasoning_param is not None:
        responses_payload["reasoning"] = reasoning_param
    if normalized_service_tier is not None:
        responses_payload["service_tier"] = normalized_service_tier
    if not isinstance(responses_payload.get("prompt_cache_key"), str) or not str(responses_payload.get("prompt_cache_key") or "").strip():
        responses_payload["prompt_cache_key"] = session_id
    responses_payload = _minimize_responses_payload(responses_payload)
    if verbose:
        _log_json("OUTBOUND >> ChatGPT Responses API payload", responses_payload)

    retryable_statuses = get_retryable_statuses()
    request_retry_limit = get_request_retry_limit()
    max_retry_interval = get_max_retry_interval_seconds()
    last_exception = None
    last_upstream = None

    for round_idx in range(request_retry_limit + 1):
        if round_idx > 0:
            sleep_secs = min(max_retry_interval, 2 ** (round_idx - 1))
            if verbose:
                print(f"Retry round {round_idx}/{request_retry_limit} after {sleep_secs}s")
            time.sleep(sleep_secs)

        tried_labels: set[str] = set()
        round_candidates = get_effective_chatgpt_auth_candidates(ensure_fresh=True)
        if not round_candidates:
            break
        preferred_label = ""
        preferred_source_path = ""
        if isinstance(thread_session, dict):
            preferred_label = str(thread_session.get("candidate_label") or "").strip()
            preferred_source_path = str(thread_session.get("candidate_url") or "").strip()

        for idx in range(len(round_candidates)):
            candidate = claim_chatgpt_auth_candidate(
                ensure_fresh=True,
                excluded_labels=tried_labels,
                session_id=session_id,
                preferred_label=preferred_label,
                preferred_source_path=preferred_source_path,
            )
            if not isinstance(candidate, dict):
                break
            access_token = candidate.get("access_token")
            account_id = candidate.get("account_id")
            label = candidate.get("label") or f"candidate-{idx + 1}"
            tried_labels.add(str(label))
            if is_auth_candidate_blocked(candidate):
                _release_auth_candidate_slot(candidate)
                continue
            if not access_token or not account_id:
                _release_auth_candidate_slot(candidate)
                continue

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "chatgpt-account-id": account_id,
                "OpenAI-Beta": "responses=experimental",
                "session_id": session_id,
            }

            slot_id = None
            session_client = None
            try:
                slot_id, session_client = acquire_chatgpt_connection_slot(candidate, session_id)
                request_callable = session_client.post if isinstance(session_client, requests.Session) else requests.post
                upstream, responses_payload = _post_with_invalid_request_fallback(
                    request_callable,
                    url=CHATGPT_RESPONSES_URL,
                    headers=headers,
                    payload=responses_payload,
                    verbose=verbose,
                )
            except requests.RequestException as exc:
                last_exception = exc
                mark_chatgpt_auth_result(label, success=False, account_id=account_id, error_message=str(exc))
                release_chatgpt_connection_slot(slot_id)
                _release_auth_candidate_slot(candidate)
                if verbose:
                    print(f"Upstream request failed for {label}: {exc}")
                continue

            last_upstream = upstream
            status = int(upstream.status_code or 0)
            should_retry = status in retryable_statuses
            has_more_candidates = idx < len(round_candidates) - 1
            has_more_rounds = round_idx < request_retry_limit

            if should_retry:
                error_info = error_info_from_http_response("upstream", "http", upstream)
                handle_chatgpt_candidate_failure(candidate, error_info)
                _release_auth_candidate_slot(candidate)
                if has_more_candidates or has_more_rounds:
                    if verbose:
                        print(f"Upstream status {status} for {label}; retrying with next account.")
                    try:
                        upstream.close()
                    except Exception:
                        pass
                    continue
                return upstream, None

            wrapped_upstream = ManagedAuthUpstream(
                upstream,
                candidate,
                session_id=session_id,
                release_hook=(lambda sid=slot_id: release_chatgpt_connection_slot(sid)),
            )
            wrapped_upstream.chatmock_candidate_label = str(candidate.get("label") or "").strip()
            wrapped_upstream.chatmock_candidate_url = str(candidate.get("source_path") or CHATGPT_RESPONSES_URL).strip()
            wrapped_upstream.chatmock_source = "chatgpt-backend"
            wrapped_upstream.chatmock_connection_slot_id = slot_id
            if isinstance(thread_session, dict):
                wrapped_upstream.chatmock_thread_mode = thread_session.get("thread_mode")
            return wrapped_upstream, None

    if last_upstream is not None:
        return last_upstream, None

    if last_exception is not None:
        resp = make_response(
            jsonify({"error": {"message": f"Upstream ChatGPT request failed: {last_exception}"}}),
            502,
        )
    else:
        resp = make_response(
            jsonify({"error": {"message": "No valid ChatGPT account is available."}}),
            401,
        )
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return None, resp


def start_upstream_request(
    model: str,
    input_items: List[Dict[str, Any]],
    *,
    instructions: str | None = None,
    tools: List[Dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    parallel_tool_calls: bool = False,
    reasoning_param: Dict[str, Any] | None = None,
    service_tier: str | None = None,
    web_search_mode: str | None = None,
    thread_session: Dict[str, Any] | None = None,
    extra_payload: Dict[str, Any] | None = None,
):
    _ = web_search_mode
    verbose = False
    try:
        verbose = bool(current_app.config.get("VERBOSE"))
    except Exception:
        verbose = False
    if verbose:
        print(f"selected upstream -> chatgpt-backend for model {model}")
    return _start_chatgpt_backend_request(
        model,
        input_items,
        instructions=instructions,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        reasoning_param=reasoning_param,
        service_tier=service_tier,
        extra_payload=extra_payload,
        thread_session=thread_session,
        verbose=verbose,
    )
