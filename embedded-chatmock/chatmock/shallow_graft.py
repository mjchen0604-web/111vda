from __future__ import annotations

from typing import Any, Dict, List


def shallow_graft_mode_enabled() -> bool:
    return True


def explicit_previous_response_id(payload: Dict[str, Any]) -> str | None:
    value = payload.get("previous_response_id")
    if isinstance(value, str):
        normalized = value.strip()
        if normalized and normalized.lower() not in ("undefined", "[undefined]"):
            return normalized
    return None


def shallow_thread_session(
    thread_session: Dict[str, Any] | None,
    full_input_items: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    if not isinstance(thread_session, dict):
        return None
    session_key = thread_session.get("session_key")
    candidate_label = thread_session.get("candidate_label")
    candidate_url = thread_session.get("candidate_url")
    if not (
        (isinstance(session_key, str) and session_key.strip())
        or (isinstance(candidate_label, str) and candidate_label.strip())
        or (isinstance(candidate_url, str) and candidate_url.strip())
    ):
        return None
    return {
        "session_key": session_key,
        "candidate_label": candidate_label,
        "candidate_url": candidate_url,
        "thread_mode": "start",
        "turn_input_items": list(full_input_items),
        "full_input_items": list(full_input_items),
    }
