from __future__ import annotations

import copy
import json
import os
import threading
import time
from typing import Any, Dict, List, Tuple


_LOCK = threading.RLock()
_HISTORY: Dict[str, Dict[str, Any]] = {}
_MAX_SESSIONS = 4096
_DEFAULT_TTL_SECONDS = 1800
_DEFAULT_MAX_ITEMS = 96


def _ttl_seconds() -> int:
    raw = (os.getenv("CHATGPT_LOCAL_HISTORY_TTL_SECONDS") or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = _DEFAULT_TTL_SECONDS
    return max(60, min(86400, value))


def _max_items() -> int:
    raw = (os.getenv("CHATGPT_LOCAL_HISTORY_MAX_ITEMS") or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = _DEFAULT_MAX_ITEMS
    return max(8, min(512, value))


def _serialize_input_items(input_items: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for item in input_items or []:
        try:
            out.append(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        except Exception:
            out.append(str(item))
    return out


def _prune_history(now: float | None = None) -> None:
    current = float(now if isinstance(now, (int, float)) else time.time())
    ttl = _ttl_seconds()
    expired: List[str] = []
    for session_key, record in list(_HISTORY.items()):
        updated_at = float((record or {}).get("updated_at") or 0.0)
        if updated_at <= 0 or current - updated_at > ttl:
            expired.append(session_key)
    for session_key in expired:
        _HISTORY.pop(session_key, None)
    if len(_HISTORY) > _MAX_SESSIONS:
        ordered = sorted(
            _HISTORY.items(),
            key=lambda item: float((item[1] or {}).get("updated_at") or 0.0),
        )
        overflow = len(_HISTORY) - _MAX_SESSIONS
        for session_key, _ in ordered[:overflow]:
            _HISTORY.pop(session_key, None)


def session_key_from_thread_session(thread_session: Dict[str, Any] | None) -> str | None:
    if not isinstance(thread_session, dict):
        return None
    session_key = thread_session.get("session_key")
    if not isinstance(session_key, str) or not session_key.strip():
        return None
    return session_key.strip()


def get_conversation_history(session_key: str | None) -> List[Dict[str, Any]]:
    if not isinstance(session_key, str) or not session_key.strip():
        return []
    with _LOCK:
        _prune_history()
        record = _HISTORY.get(session_key.strip())
        if not isinstance(record, dict):
            return []
        record["updated_at"] = time.time()
        return copy.deepcopy(record.get("items") or [])


def clear_conversation_history(session_key: str | None) -> None:
    if not isinstance(session_key, str) or not session_key.strip():
        return
    with _LOCK:
        _HISTORY.pop(session_key.strip(), None)


def _max_overlap(previous_items: List[Dict[str, Any]], current_items: List[Dict[str, Any]]) -> int:
    previous_serialized = _serialize_input_items(previous_items)
    current_serialized = _serialize_input_items(current_items)
    max_candidate = min(len(previous_serialized), len(current_serialized))
    for overlap in range(max_candidate, 0, -1):
        if previous_serialized[-overlap:] == current_serialized[:overlap]:
            return overlap
    return 0


def replay_conversation_history(
    thread_session: Dict[str, Any] | None,
    current_input_items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    session_key = session_key_from_thread_session(thread_session)
    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "no_session",
        "history_items": 0,
        "current_items": len(current_input_items or []),
    }
    if not session_key:
        return list(current_input_items or []), meta

    history_items = get_conversation_history(session_key)
    meta["history_items"] = len(history_items)
    if not history_items:
        meta["reason"] = "no_history"
        return list(current_input_items or []), meta

    current_items = list(current_input_items or [])
    if not current_items:
        meta["reason"] = "empty_current"
        return list(history_items), meta

    current_serialized = _serialize_input_items(current_items)
    history_serialized = _serialize_input_items(history_items)
    if len(current_serialized) >= len(history_serialized) and current_serialized[: len(history_serialized)] == history_serialized:
        meta["reason"] = "client_full_history"
        return current_items, meta

    overlap = _max_overlap(history_items, current_items)
    merged = list(history_items)
    if overlap > 0:
        merged.extend(current_items[overlap:])
        meta["reason"] = "overlap_merge"
    else:
        merged.extend(current_items)
        meta["reason"] = "history_replayed"
    meta["applied"] = True
    meta["overlap"] = overlap
    meta["replayed_items"] = len(merged) - len(current_items)
    return merged, meta


def _trim_history(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    limit = _max_items()
    if len(items) <= limit:
        return items
    return items[-limit:]


def append_conversation_history(
    thread_session: Dict[str, Any] | None,
    request_items: List[Dict[str, Any]],
    response_items: List[Dict[str, Any]] | None,
) -> None:
    session_key = session_key_from_thread_session(thread_session)
    if not session_key:
        return
    new_items = list(request_items or [])
    if isinstance(response_items, list) and response_items:
        new_items.extend(copy.deepcopy(response_items))

    with _LOCK:
        _prune_history()
        existing_items = copy.deepcopy((_HISTORY.get(session_key) or {}).get("items") or [])
        overlap = _max_overlap(existing_items, new_items)
        merged = list(existing_items)
        if overlap > 0:
            merged.extend(new_items[overlap:])
        else:
            merged.extend(new_items)
        _HISTORY[session_key] = {
            "items": _trim_history(merged),
            "updated_at": time.time(),
        }


def response_items_from_response_obj(response_obj: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(response_obj, dict):
        return []
    output = response_obj.get("output")
    if not isinstance(output, list):
        return []
    reusable: List[Dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning":
            continue
        reusable.append(copy.deepcopy(item))
    return reusable


def response_items_from_chat_message(message: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    from .utils import convert_chat_messages_to_responses_input

    return convert_chat_messages_to_responses_input([message])


def response_items_from_anthropic_message(message_obj: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(message_obj, dict):
        return []
    content = message_obj.get("content")
    if not isinstance(content, list):
        return []

    items: List[Dict[str, Any]] = []
    text_parts: List[Dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                text_parts.append({"type": "output_text", "text": text})
        elif block_type == "tool_use":
            tool_input = block.get("input")
            arguments = tool_input if isinstance(tool_input, str) else json.dumps(tool_input or {}, ensure_ascii=False)
            items.append(
                {
                    "type": "function_call",
                    "name": block.get("name") or "",
                    "arguments": arguments,
                    "call_id": block.get("id") or "",
                }
            )

    if text_parts:
        items.insert(
            0,
            {
                "type": "message",
                "role": "assistant",
                "content": text_parts,
            },
        )
    return items
