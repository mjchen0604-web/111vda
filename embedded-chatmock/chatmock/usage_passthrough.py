from __future__ import annotations

from typing import Any, Dict


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def normalize_usage_dict(raw_usage: Any) -> Dict[str, Any] | None:
    if not isinstance(raw_usage, dict):
        return None

    input_tokens = _coerce_int(raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens"))
    output_tokens = _coerce_int(raw_usage.get("output_tokens") or raw_usage.get("completion_tokens"))
    total_tokens = _coerce_int(raw_usage.get("total_tokens"))

    input_details = raw_usage.get("input_tokens_details") if isinstance(raw_usage.get("input_tokens_details"), dict) else {}
    prompt_details = raw_usage.get("prompt_tokens_details") if isinstance(raw_usage.get("prompt_tokens_details"), dict) else {}
    merged_details = {**prompt_details, **input_details}

    cached_tokens = _coerce_int(
        merged_details.get("cached_tokens")
        or raw_usage.get("cached_tokens")
        or raw_usage.get("cache_read_input_tokens")
    )
    cached_creation_tokens = _coerce_int(
        merged_details.get("cached_creation_tokens")
        or raw_usage.get("cache_creation_input_tokens")
    )
    prompt_cache_hit_tokens = _coerce_int(raw_usage.get("prompt_cache_hit_tokens"))

    if total_tokens <= 0 and (input_tokens > 0 or output_tokens > 0):
        total_tokens = input_tokens + output_tokens

    normalized: Dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }

    details: Dict[str, int] = {}
    if cached_tokens > 0:
        details["cached_tokens"] = cached_tokens
    if cached_creation_tokens > 0:
        details["cached_creation_tokens"] = cached_creation_tokens
    if details:
        normalized["input_tokens_details"] = details
    if prompt_cache_hit_tokens > 0:
        normalized["prompt_cache_hit_tokens"] = prompt_cache_hit_tokens

    has_usage = (
        input_tokens > 0
        or output_tokens > 0
        or total_tokens > 0
        or cached_tokens > 0
        or cached_creation_tokens > 0
        or prompt_cache_hit_tokens > 0
    )
    return normalized if has_usage else None


def extract_responses_usage_from_event(evt: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(evt, dict):
        return None
    response = evt.get("response")
    if not isinstance(response, dict):
        return None
    return normalize_usage_dict(response.get("usage"))


def to_responses_usage(usage: Dict[str, Any] | None) -> Dict[str, Any] | None:
    normalized = normalize_usage_dict(usage)
    if normalized is None:
        return None

    payload: Dict[str, Any] = {
        "input_tokens": normalized.get("input_tokens", 0),
        "output_tokens": normalized.get("output_tokens", 0),
        "total_tokens": normalized.get("total_tokens", 0),
    }
    details = normalized.get("input_tokens_details")
    if isinstance(details, dict) and details:
        payload["input_tokens_details"] = dict(details)
    if normalized.get("prompt_cache_hit_tokens"):
        payload["prompt_cache_hit_tokens"] = normalized["prompt_cache_hit_tokens"]
    return payload


def to_chat_usage(usage: Dict[str, Any] | None) -> Dict[str, Any] | None:
    normalized = normalize_usage_dict(usage)
    if normalized is None:
        return None

    payload: Dict[str, Any] = {
        "prompt_tokens": normalized.get("input_tokens", 0),
        "completion_tokens": normalized.get("output_tokens", 0),
        "total_tokens": normalized.get("total_tokens", 0),
    }
    details = normalized.get("input_tokens_details")
    if isinstance(details, dict) and details:
        payload["prompt_tokens_details"] = dict(details)
        payload["input_tokens_details"] = dict(details)
    if normalized.get("prompt_cache_hit_tokens"):
        payload["prompt_cache_hit_tokens"] = normalized["prompt_cache_hit_tokens"]
    return payload


def to_anthropic_usage(usage: Dict[str, Any] | None) -> Dict[str, Any] | None:
    normalized = normalize_usage_dict(usage)
    if normalized is None:
        return None

    payload: Dict[str, Any] = {
        "input_tokens": normalized.get("input_tokens", 0),
        "output_tokens": normalized.get("output_tokens", 0),
    }
    details = normalized.get("input_tokens_details")
    if isinstance(details, dict):
        if details.get("cached_tokens"):
            payload["cache_read_input_tokens"] = details["cached_tokens"]
        if details.get("cached_creation_tokens"):
            payload["cache_creation_input_tokens"] = details["cached_creation_tokens"]
    return payload
