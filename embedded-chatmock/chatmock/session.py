from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any, Dict, List


_LOCK = threading.Lock()
_FINGERPRINT_TO_UUID: Dict[str, str] = {}
_ORDER: List[str] = []
_MAX_ENTRIES = 10000
_COMPACTION_SUMMARY_HEADER = "[Gateway compacted conversation summary]"


def _normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _strip_dynamic_summary_block(instructions: str | None) -> str | None:
    text = _normalize_text(instructions)
    if not text:
        return None
    marker_idx = text.find(_COMPACTION_SUMMARY_HEADER)
    if marker_idx == -1:
        return text
    static_text = text[:marker_idx].rstrip()
    return static_text or None


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonicalize_tool_choice(tool_choice: Any) -> Any:
    if isinstance(tool_choice, dict):
        return json.loads(_stable_json(tool_choice))
    if isinstance(tool_choice, str):
        normalized = tool_choice.strip()
        return normalized or None
    return None


def _canonicalize_tools(tools: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        normalized.append(json.loads(_stable_json(tool)))
    return normalized


def _canonicalize_first_user_message(input_items: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """
    Extract the first stable user message from Responses input items. Good use for a fingerprint for prompt caching.
    """
    for item in input_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        role = item.get("role")
        if role != "user":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        norm_content = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "input_text":
                text = part.get("text") if isinstance(part.get("text"), str) else ""
                if text:
                    norm_content.append({"type": "input_text", "text": text})
            elif ptype == "input_image":
                url = part.get("image_url") if isinstance(part.get("image_url"), str) else None
                if url:
                    norm_content.append({"type": "input_image", "image_url": url})
        if norm_content:
            return {"type": "message", "role": "user", "content": norm_content}
    return None


def canonicalize_prefix(
    instructions: str | None,
    input_items: List[Dict[str, Any]],
    *,
    model: str | None = None,
    tools: List[Dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    parallel_tool_calls: bool = False,
) -> str:
    prefix: Dict[str, Any] = {}
    normalized_instructions = _strip_dynamic_summary_block(instructions)
    if normalized_instructions:
        prefix["instructions"] = normalized_instructions
    normalized_model = _normalize_text(model)
    if normalized_model:
        prefix["model"] = normalized_model
    normalized_tools = _canonicalize_tools(tools)
    if normalized_tools:
        prefix["tools"] = normalized_tools
    normalized_tool_choice = _canonicalize_tool_choice(tool_choice)
    if normalized_tool_choice is not None:
        prefix["tool_choice"] = normalized_tool_choice
    prefix["parallel_tool_calls"] = bool(parallel_tool_calls)
    first_user = _canonicalize_first_user_message(input_items)
    if first_user is not None:
        prefix["first_user_message"] = first_user
    return _stable_json(prefix)


def _fingerprint(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _remember(fp: str, sid: str) -> None:
    if fp in _FINGERPRINT_TO_UUID:
        return
    _FINGERPRINT_TO_UUID[fp] = sid
    _ORDER.append(fp)
    if len(_ORDER) > _MAX_ENTRIES:
        oldest = _ORDER.pop(0)
        _FINGERPRINT_TO_UUID.pop(oldest, None)


def ensure_session_id(
    instructions: str | None,
    input_items: List[Dict[str, Any]],
    *,
    model: str | None = None,
    tools: List[Dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    parallel_tool_calls: bool = False,
    client_supplied: str | None = None,
) -> str:
    if isinstance(client_supplied, str) and client_supplied.strip():
        return client_supplied.strip()

    canon = canonicalize_prefix(
        instructions,
        input_items,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
    )
    fp = _fingerprint(canon)
    with _LOCK:
        if fp in _FINGERPRINT_TO_UUID:
            return _FINGERPRINT_TO_UUID[fp]
        sid = str(uuid.uuid4())
        _remember(fp, sid)
        return sid

