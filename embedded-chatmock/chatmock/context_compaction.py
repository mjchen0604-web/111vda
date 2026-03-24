from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple


_DEFAULT_ENABLED = False
_DEFAULT_MIN_INPUT_ITEMS = 12
_DEFAULT_MAX_INPUT_ITEMS = 24
_DEFAULT_PRESERVE_RECENT_ITEMS = 8
_DEFAULT_MAX_SUMMARY_CHARS = 4000
_DEFAULT_MAX_ITEM_CHARS = 480
_SUMMARY_HEADER = "[Gateway compacted conversation summary]"


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on", "enabled", "enable", "auto"):
            return True
        if normalized in ("0", "false", "no", "off", "disabled", "disable", "none"):
            return False
    return default


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _load_context_management(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    raw = payload.get("context_management") if isinstance(payload, dict) else None
    if isinstance(raw, dict):
        nested = raw.get("compaction")
        if isinstance(nested, dict):
            merged = dict(raw)
            merged.update(nested)
            return merged
        return dict(raw)
    if isinstance(raw, bool):
        return {"enabled": raw}
    if isinstance(raw, str):
        return {"enabled": _coerce_bool(raw, _DEFAULT_ENABLED)}
    return {}


def _resolve_settings(payload: Dict[str, Any] | None) -> Dict[str, int | bool]:
    raw = _load_context_management(payload)
    enabled_default = _DEFAULT_ENABLED
    if isinstance(raw, dict) and raw:
        enabled_default = True
    enabled = _coerce_bool(raw.get("enabled"), enabled_default)
    mode = raw.get("mode") or raw.get("type") or raw.get("strategy")
    if isinstance(mode, str) and mode.strip().lower() in ("disabled", "off", "none"):
        enabled = False

    return {
        "enabled": enabled,
        "min_input_items": _coerce_int(
            raw.get("min_input_items") or raw.get("min_messages") or raw.get("min_items"),
            _DEFAULT_MIN_INPUT_ITEMS,
            minimum=4,
            maximum=256,
        ),
        "max_input_items": _coerce_int(
            raw.get("max_input_items") or raw.get("max_messages") or raw.get("max_items"),
            _DEFAULT_MAX_INPUT_ITEMS,
            minimum=6,
            maximum=512,
        ),
        "preserve_recent_items": _coerce_int(
            raw.get("preserve_recent_items") or raw.get("keep_recent_items") or raw.get("keep_recent"),
            _DEFAULT_PRESERVE_RECENT_ITEMS,
            minimum=2,
            maximum=128,
        ),
        "max_summary_chars": _coerce_int(
            raw.get("max_summary_chars") or raw.get("summary_max_chars"),
            _DEFAULT_MAX_SUMMARY_CHARS,
            minimum=600,
            maximum=24000,
        ),
        "max_item_chars": _coerce_int(
            raw.get("max_item_chars") or raw.get("item_max_chars"),
            _DEFAULT_MAX_ITEM_CHARS,
            minimum=120,
            maximum=2000,
        ),
    }


def _safe_dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(32, limit // 2)
    tail = max(24, limit - head - 16)
    return text[:head] + " ...[trimmed]... " + text[-tail:]


def _content_part_to_text(part: Dict[str, Any]) -> str:
    part_type = str(part.get("type") or "").strip().lower()
    if part_type in ("input_text", "output_text", "text", "summary_text"):
        text = part.get("text")
        return text if isinstance(text, str) else ""
    if part_type == "input_image":
        image_url = part.get("image_url")
        if isinstance(image_url, str) and image_url.startswith("data:"):
            return "[image:data-url]"
        if isinstance(image_url, str) and image_url:
            return f"[image:{image_url}]"
        return "[image]"
    if part_type == "input_file":
        file_block = part.get("file")
        if isinstance(file_block, dict):
            file_name = file_block.get("filename")
            if isinstance(file_name, str) and file_name:
                return f"[file:{file_name}]"
        return "[file]"
    if part_type == "input_audio":
        return "[audio]"
    return ""


def _summarize_message_item(item: Dict[str, Any], max_item_chars: int) -> str:
    role = str(item.get("role") or "user").strip().upper()
    content = item.get("content")
    text_parts: List[str] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            text = _content_part_to_text(part)
            if text:
                text_parts.append(text)
    elif isinstance(content, str) and content:
        text_parts.append(content)
    if not text_parts:
        text_parts.append("[empty]")
    return _truncate_text(f"{role}: {' '.join(text_parts)}", max_item_chars)


def _summarize_function_call(item: Dict[str, Any], max_item_chars: int) -> str:
    name = str(item.get("name") or "tool").strip()
    call_id = str(item.get("call_id") or item.get("id") or "").strip()
    arguments = item.get("arguments")
    arg_text = arguments if isinstance(arguments, str) else _safe_dump(arguments)
    prefix = f"TOOL_CALL[{name}"
    if call_id:
        prefix += f"#{call_id}"
    prefix += "]"
    return _truncate_text(f"{prefix}: {arg_text}", max_item_chars)


def _summarize_function_result(item: Dict[str, Any], max_item_chars: int) -> str:
    call_id = str(item.get("call_id") or item.get("id") or "").strip()
    output = item.get("output")
    output_text = output if isinstance(output, str) else _safe_dump(output)
    prefix = "TOOL_RESULT"
    if call_id:
        prefix += f"[{call_id}]"
    return _truncate_text(f"{prefix}: {output_text}", max_item_chars)


def _summarize_input_item(item: Dict[str, Any], max_item_chars: int) -> str:
    item_type = str(item.get("type") or "").strip().lower()
    if item_type == "message":
        return _summarize_message_item(item, max_item_chars)
    if item_type == "function_call":
        return _summarize_function_call(item, max_item_chars)
    if item_type == "function_call_output":
        return _summarize_function_result(item, max_item_chars)
    return _truncate_text(f"{item_type or 'item'}: {_safe_dump(item)}", max_item_chars)


def _fit_lines(lines: List[str], max_chars: int) -> str:
    if not lines:
        return ""
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined

    head_count = min(4, len(lines))
    head = lines[:head_count]
    marker = f"[...{max(0, len(lines) - head_count)} earlier items compacted...]"
    used = len("\n".join(head + [marker]))
    tail: List[str] = []
    for line in reversed(lines[head_count:]):
        extra = len(line) + 1
        if used + extra > max_chars:
            break
        tail.insert(0, line)
        used += extra

    candidate = head + [marker] + tail
    fitted = "\n".join(candidate)
    if len(fitted) <= max_chars:
        return fitted
    return _truncate_text(fitted, max_chars)


def _build_summary_text(old_items: List[Dict[str, Any]], max_summary_chars: int, max_item_chars: int) -> str:
    lines = [
        _summarize_input_item(item, max_item_chars)
        for item in old_items
        if isinstance(item, dict)
    ]
    lines = [line for line in lines if line]
    return _fit_lines(lines, max_summary_chars)


def _merge_instructions(instructions: str | None, summary_text: str, compacted_count: int, total_items: int) -> str:
    summary_block = (
        f"{_SUMMARY_HEADER}\n"
        f"Compacted earlier input items: {compacted_count} of {total_items}.\n"
        "Treat the summary below as background context. If it conflicts with the preserved recent turns, "
        "prefer the preserved recent turns.\n"
        f"{summary_text}"
    )
    if isinstance(instructions, str) and instructions.strip():
        return instructions.rstrip() + "\n\n" + summary_block
    return summary_block


def maybe_compact_input_items(
    payload: Dict[str, Any] | None,
    input_items: List[Dict[str, Any]],
    instructions: str | None,
) -> Tuple[List[Dict[str, Any]], str | None, Dict[str, Any]]:
    settings = _resolve_settings(payload)
    meta: Dict[str, Any] = {
        "applied": False,
        "settings": settings,
        "original_items": len(input_items or []),
        "kept_items": len(input_items or []),
    }

    if not settings["enabled"]:
        meta["reason"] = "disabled"
        return list(input_items or []), instructions, meta

    if not isinstance(input_items, list) or not input_items:
        meta["reason"] = "empty"
        return list(input_items or []), instructions, meta

    total_serialized_chars = sum(len(_safe_dump(item)) for item in input_items)
    keep_recent = min(int(settings["preserve_recent_items"]), max(1, len(input_items) - 1))
    oversized_body = len(input_items) > 1 and total_serialized_chars > int(settings["max_summary_chars"]) * 2
    should_compact = len(input_items) > int(settings["max_input_items"]) or oversized_body or (
        len(input_items) >= int(settings["min_input_items"])
        and total_serialized_chars > int(settings["max_summary_chars"]) * 2
    )
    if not should_compact or keep_recent >= len(input_items):
        meta["reason"] = "below_threshold"
        return list(input_items), instructions, meta

    old_items = input_items[:-keep_recent]
    recent_items = input_items[-keep_recent:]
    summary_text = _build_summary_text(
        old_items,
        int(settings["max_summary_chars"]),
        int(settings["max_item_chars"]),
    )
    if not summary_text:
        meta["reason"] = "empty_summary"
        return list(input_items), instructions, meta

    meta.update(
        {
            "applied": True,
            "reason": "compacted",
            "compacted_items": len(old_items),
            "kept_items": len(recent_items),
            "summary_chars": len(summary_text),
        }
    )
    return recent_items, _merge_instructions(instructions, summary_text, len(old_items), len(input_items)), meta


def build_compaction_summary(
    payload: Dict[str, Any] | None,
    input_items: List[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    settings = _resolve_settings(payload)
    meta: Dict[str, Any] = {
        "settings": settings,
        "input_items": len(input_items or []),
    }
    if not isinstance(input_items, list) or not input_items:
        meta["reason"] = "empty"
        return "", meta

    summary_text = _build_summary_text(
        list(input_items),
        int(settings["max_summary_chars"]),
        int(settings["max_item_chars"]),
    )
    if not summary_text:
        meta["reason"] = "empty_summary"
        return "", meta
    meta["reason"] = "ok"
    meta["summary_chars"] = len(summary_text)
    return summary_text, meta
