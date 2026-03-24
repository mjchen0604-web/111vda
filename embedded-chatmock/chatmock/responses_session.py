from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .conversation_history import append_conversation_history, response_items_from_response_obj
from .shallow_graft import explicit_previous_response_id, shallow_graft_mode_enabled
from .thread_sessions import save_thread_session
from .upstream_errors import normalized_error_payload


def is_previous_response_not_found(error_info: Dict[str, Any] | None) -> bool:
    if not isinstance(error_info, dict):
        return False
    raw_code = str(error_info.get("raw_code") or "").strip().lower()
    raw_message = str(error_info.get("raw_message") or "").strip().lower()
    return raw_code == "previous_response_not_found" or "previous_response_not_found" in raw_message


def should_retry_without_previous_response(error_info: Dict[str, Any] | None) -> bool:
    if is_previous_response_not_found(error_info):
        return True
    if not isinstance(error_info, dict):
        return False
    raw_status = error_info.get("raw_status")
    if not isinstance(raw_status, int) or raw_status != 400:
        return False
    normalized = normalized_error_payload(error_info)
    if str(normalized.get("type") or "").strip().lower() != "invalid_request_error":
        return False
    raw_code = str(error_info.get("raw_code") or "").strip().lower()
    if raw_code not in ("", "invalid_request", "invalid_request_error", "bad_request"):
        return False
    source = str(error_info.get("source") or "").strip().lower()
    if source not in ("", "chatgpt-backend", "upstream", "chatcore"):
        return False
    raw_message = str(error_info.get("raw_message") or "").strip().lower()
    normalized_message = str(normalized.get("message") or "").strip().lower()
    return "invalid request" in raw_message or normalized_message == "invalid request"


def resolve_turn_state(
    payload: Dict[str, Any],
    full_input_items: List[Dict[str, Any]],
    thread_session: Dict[str, Any] | None,
) -> Tuple[List[Dict[str, Any]], str | None]:
    explicit_thread_id = explicit_previous_response_id(payload)

    if shallow_graft_mode_enabled() and explicit_thread_id is None:
        return list(full_input_items), None

    return list(full_input_items), explicit_thread_id


def should_skip_compaction_for_thread_resume(
    payload: Dict[str, Any],
    thread_session: Dict[str, Any] | None,
) -> bool:
    _ = thread_session
    return explicit_previous_response_id(payload) is not None


def save_response_session(
    thread_session: Dict[str, Any] | None,
    *,
    response_id: str | None = None,
    response_obj: Dict[str, Any] | None = None,
    full_input_items: List[Dict[str, Any]],
    upstream: Any | None,
) -> None:
    if not isinstance(thread_session, dict):
        return
    session_key = thread_session.get("session_key")
    effective_response_id = response_id
    if not isinstance(effective_response_id, str) or not effective_response_id.strip():
        effective_response_id = response_obj.get("id") if isinstance(response_obj, dict) else None
    if not isinstance(session_key, str) or not session_key.strip():
        return
    if not isinstance(effective_response_id, str) or not effective_response_id.strip():
        return
    save_thread_session(
        session_key,
        thread_id=effective_response_id.strip(),
        candidate_label=str(getattr(upstream, "chatmock_candidate_label", "") or ""),
        candidate_url=str(getattr(upstream, "chatmock_candidate_url", "") or ""),
        input_items=full_input_items,
    )
    append_conversation_history(
        thread_session,
        full_input_items,
        response_items_from_response_obj(response_obj if isinstance(response_obj, dict) else None),
    )
