from __future__ import annotations

import threading
import time
from typing import Any, Dict, Tuple

import requests
from requests.adapters import HTTPAdapter


_LOCK = threading.RLock()
_SESSION_SLOT_BINDINGS: Dict[str, str] = {}
_CONNECTION_SLOTS: Dict[str, Dict[str, Any]] = {}
_NEXT_SLOT_ID = 0
_MAX_SLOTS_PER_CANDIDATE = 8
_SLOT_TTL_SECONDS = 1800


def _build_slot_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=16,
        pool_maxsize=16,
        max_retries=0,
        pool_block=False,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _candidate_identity(candidate: Dict[str, Any]) -> Tuple[str, str]:
    if not isinstance(candidate, dict):
        return "", ""
    return (
        str(candidate.get("label") or "").strip(),
        str(candidate.get("source_path") or "").strip(),
    )


def _slot_matches_candidate(slot: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    slot_label = str(slot.get("label") or "").strip()
    slot_source = str(slot.get("source_path") or "").strip()
    candidate_label, candidate_source = _candidate_identity(candidate)
    if slot_label and candidate_label and slot_label == candidate_label:
        return True
    if slot_source and candidate_source and slot_source == candidate_source:
        return True
    return False


def _prune_slots(now: float | None = None) -> None:
    current = float(now if isinstance(now, (int, float)) else time.time())
    expired_slot_ids = []
    for slot_id, slot in list(_CONNECTION_SLOTS.items()):
        updated_at = float(slot.get("updated_at") or 0.0)
        inflight = int(slot.get("inflight") or 0)
        if inflight > 0:
            continue
        if updated_at <= 0 or current - updated_at > _SLOT_TTL_SECONDS:
            expired_slot_ids.append(slot_id)
    for slot_id in expired_slot_ids:
        slot = _CONNECTION_SLOTS.pop(slot_id, None)
        if isinstance(slot, dict):
            session_obj = slot.get("session")
            if isinstance(session_obj, requests.Session):
                try:
                    session_obj.close()
                except Exception:
                    pass
    if expired_slot_ids:
        for session_id, bound_slot_id in list(_SESSION_SLOT_BINDINGS.items()):
            if bound_slot_id in expired_slot_ids:
                _SESSION_SLOT_BINDINGS.pop(session_id, None)


def _new_slot_id() -> str:
    global _NEXT_SLOT_ID
    _NEXT_SLOT_ID += 1
    return f"slot-{_NEXT_SLOT_ID}"


def acquire_chatgpt_connection_slot(candidate: Dict[str, Any], session_id: str | None) -> tuple[str | None, requests.Session | None]:
    if not isinstance(candidate, dict):
        return None, None
    normalized_session_id = str(session_id or "").strip()
    with _LOCK:
        _prune_slots()
        if normalized_session_id:
            bound_slot_id = _SESSION_SLOT_BINDINGS.get(normalized_session_id)
            bound_slot = _CONNECTION_SLOTS.get(bound_slot_id or "")
            if isinstance(bound_slot, dict) and _slot_matches_candidate(bound_slot, candidate):
                bound_slot["updated_at"] = time.time()
                bound_slot["inflight"] = int(bound_slot.get("inflight") or 0) + 1
                session_obj = bound_slot.get("session")
                if isinstance(session_obj, requests.Session):
                    return bound_slot_id, session_obj

        candidate_slots = [
            (slot_id, slot)
            for slot_id, slot in _CONNECTION_SLOTS.items()
            if isinstance(slot, dict) and _slot_matches_candidate(slot, candidate)
        ]
        candidate_slots.sort(
            key=lambda item: (
                int((item[1] or {}).get("inflight") or 0),
                float((item[1] or {}).get("updated_at") or 0.0),
            )
        )

        selected_slot_id = None
        selected_slot = None
        should_create_dedicated_slot = bool(normalized_session_id) and len(candidate_slots) < _MAX_SLOTS_PER_CANDIDATE
        if not should_create_dedicated_slot:
            for slot_id, slot in candidate_slots:
                if int(slot.get("inflight") or 0) <= 0:
                    selected_slot_id = slot_id
                    selected_slot = slot
                    break
        if selected_slot is None and len(candidate_slots) < _MAX_SLOTS_PER_CANDIDATE:
            selected_slot_id = _new_slot_id()
            selected_slot = {
                "session": _build_slot_session(),
                "label": _candidate_identity(candidate)[0],
                "source_path": _candidate_identity(candidate)[1],
                "created_at": time.time(),
                "updated_at": time.time(),
                "inflight": 0,
            }
            _CONNECTION_SLOTS[selected_slot_id] = selected_slot
        if selected_slot is None and candidate_slots:
            selected_slot_id, selected_slot = candidate_slots[0]

        if not isinstance(selected_slot, dict) or not selected_slot_id:
            return None, None
        selected_slot["updated_at"] = time.time()
        selected_slot["inflight"] = int(selected_slot.get("inflight") or 0) + 1
        if normalized_session_id:
            _SESSION_SLOT_BINDINGS[normalized_session_id] = selected_slot_id
        session_obj = selected_slot.get("session")
        if not isinstance(session_obj, requests.Session):
            return None, None
        return selected_slot_id, session_obj


def release_chatgpt_connection_slot(slot_id: str | None) -> None:
    normalized_slot_id = str(slot_id or "").strip()
    if not normalized_slot_id:
        return
    with _LOCK:
        slot = _CONNECTION_SLOTS.get(normalized_slot_id)
        if not isinstance(slot, dict):
            return
        current = int(slot.get("inflight") or 0)
        slot["inflight"] = max(0, current - 1)
        slot["updated_at"] = time.time()


def get_chatgpt_connection_slot_state() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        _prune_slots()
        result: Dict[str, Dict[str, Any]] = {}
        for slot_id, slot in _CONNECTION_SLOTS.items():
            result[slot_id] = {
                "label": str(slot.get("label") or "").strip(),
                "source_path": str(slot.get("source_path") or "").strip(),
                "created_at": float(slot.get("created_at") or 0.0),
                "updated_at": float(slot.get("updated_at") or 0.0),
                "inflight": int(slot.get("inflight") or 0),
            }
        return result


def clear_chatgpt_connection_slots() -> None:
    with _LOCK:
        for slot in _CONNECTION_SLOTS.values():
            if isinstance(slot, dict):
                session_obj = slot.get("session")
                if isinstance(session_obj, requests.Session):
                    try:
                        session_obj.close()
                    except Exception:
                        pass
        _CONNECTION_SLOTS.clear()
        _SESSION_SLOT_BINDINGS.clear()
