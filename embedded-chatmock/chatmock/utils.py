from __future__ import annotations

import base64
import datetime
import glob
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import CHATGPT_RESPONSES_URL, CLIENT_ID_DEFAULT, OAUTH_TOKEN_URL
from .reasoning import normalize_reasoning_compat
from .upstream_errors import (
    build_error_info,
    classify_error,
    error_info_from_event_response,
    extract_retry_after_unlock_ts,
    normalized_error_payload,
    should_retry_next_candidate,
)
from .usage_passthrough import extract_responses_usage_from_event, to_chat_usage


_AUTH_POOL_RR_LOCK = threading.Lock()
_AUTH_POOL_RR_INDEX = 0
_AUTH_POOL_STATE_LOCK = threading.RLock()
_AUTH_POOL_STATE: Dict[str, Dict[str, Any]] = {}
_INVALID_AUTH_LOCK = threading.RLock()
_INVALID_AUTH_LABELS: set[str] = set()
_INVALID_AUTH_ACCOUNT_IDS: set[str] = set()
_AUTH_INFLIGHT_LOCK = threading.RLock()
_AUTH_INFLIGHT_COUNTS: Dict[str, int] = {}
_AUTH_SESSION_STICKY_LOCK = threading.RLock()
_AUTH_SESSION_STICKY: Dict[str, Dict[str, Any]] = {}
_AUTH_SESSION_STICKY_MAX = 10000


def eprint(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


def sanitize_reserved_tool_name(name: Any) -> Any:
    if not isinstance(name, str):
        return name
    normalized = name.strip()
    if not normalized.startswith("mcp__"):
        return normalized
    return "tool_" + normalized


def restore_reserved_tool_name(name: Any) -> Any:
    if not isinstance(name, str):
        return name
    normalized = name.strip()
    if not normalized.startswith("tool_mcp__"):
        return normalized
    return normalized[len("tool_"):]


def extract_response_output_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""

    direct_text = item.get("output_text")
    if isinstance(direct_text, str) and direct_text:
        return direct_text

    parts: List[str] = []
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in ("output_text", "text", "summary_text"):
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    if parts:
        return "".join(parts)

    output = item.get("output")
    if isinstance(output, list):
        nested_parts: List[str] = []
        for output_item in output:
            text = extract_response_output_text(output_item)
            if text:
                nested_parts.append(text)
        return "".join(nested_parts)

    return ""


def merge_response_text(existing_text: str, observed_text: Any) -> Tuple[str, str]:
    if not isinstance(observed_text, str) or not observed_text:
        return existing_text, ""
    if not existing_text:
        return observed_text, observed_text
    if observed_text.startswith(existing_text):
        return observed_text, observed_text[len(existing_text):]
    if existing_text.endswith(observed_text) or observed_text in existing_text:
        return existing_text, ""
    merged = existing_text + observed_text
    return merged, observed_text


class RetryableStreamError(RuntimeError):
    def __init__(self, error_info: Dict[str, Any]) -> None:
        self.error_info = error_info
        super().__init__(str((error_info or {}).get("raw_message") or "retryable stream failure"))


def _mark_upstream_failure(upstream: Any, error_info: Dict[str, Any] | None = None) -> None:
    if upstream is None or not hasattr(upstream, "mark_failure"):
        return
    info = error_info if isinstance(error_info, dict) else {}
    raw_message = str(info.get("raw_message") or "").strip()
    raw_status = info.get("raw_status") if isinstance(info.get("raw_status"), int) else None
    classification = classify_error(info) if info else "generic_failure"
    try:
        upstream.mark_failure(raw_message, status_code=raw_status, classification=classification)
    except Exception:
        pass


class ManagedAuthUpstream:
    def __init__(
        self,
        upstream: Any,
        candidate: Dict[str, Any],
        session_id: str | None = None,
        release_hook=None,
    ) -> None:
        self._upstream = upstream
        self._candidate = dict(candidate or {})
        self._session_id = str(session_id or "").strip() or None
        self._released = False
        self._marked = False
        self._release_hook = release_hook

    def __getattr__(self, name: str) -> Any:
        return getattr(self._upstream, name)

    def _release(self) -> None:
        if self._released:
            return
        self._released = True
        if callable(self._release_hook):
            try:
                self._release_hook()
            except Exception:
                pass
        _release_auth_candidate_slot(self._candidate)

    def _mark_success(self) -> None:
        if self._marked:
            return
        self._marked = True
        mark_chatgpt_auth_result(
            str(self._candidate.get("label") or "").strip(),
            success=True,
            status_code=int(getattr(self._upstream, "status_code", 200) or 200),
            account_id=str(self._candidate.get("account_id") or "").strip(),
        )
        bind_chatgpt_auth_session(self._session_id, self._candidate)

    def mark_success(self) -> None:
        self._mark_success()

    def mark_failure(self, error_message: str = "", status_code: int | None = None, classification: str | None = None) -> None:
        if self._marked:
            return
        self._marked = True
        clear_chatgpt_auth_session_binding(self._session_id)
        effective_status = status_code
        if not isinstance(effective_status, int) or effective_status < 400:
            effective_status = int(getattr(self._upstream, "status_code", 0) or 0)
        if not isinstance(effective_status, int) or effective_status < 400:
            effective_status = 502
        mark_chatgpt_auth_result(
            str(self._candidate.get("label") or "").strip(),
            success=False,
            status_code=effective_status,
            account_id=str(self._candidate.get("account_id") or "").strip(),
            error_message=error_message,
            classification=classification or "generic_failure",
        )

    def close(self) -> None:
        try:
            if not self._marked:
                self._mark_success()
        finally:
            self._release()
            return self._upstream.close()

    def iter_lines(self, decode_unicode: bool = False):
        try:
            for raw in self._upstream.iter_lines(decode_unicode=decode_unicode):
                yield raw
            if not self._marked:
                self._mark_success()
        finally:
            self._release()


def get_home_dir() -> str:
    home = os.getenv("CHATGPT_LOCAL_HOME")
    if not home:
        home = os.path.expanduser("~/.chatgpt-local")
    return home


def _session_sticky_enabled() -> bool:
    raw = (os.getenv("CHATGPT_LOCAL_SESSION_STICKY_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _session_sticky_ttl_seconds() -> int:
    raw = (os.getenv("CHATGPT_LOCAL_SESSION_STICKY_TTL_SECONDS") or "1800").strip()
    try:
        value = int(raw)
    except Exception:
        value = 1800
    return max(60, min(86400, value))


def _prune_chatgpt_auth_session_bindings(now: float | None = None) -> None:
    if not _AUTH_SESSION_STICKY:
        return
    current = float(now if isinstance(now, (int, float)) else time.time())
    ttl = _session_sticky_ttl_seconds()
    expired: List[str] = []
    for session_id, record in list(_AUTH_SESSION_STICKY.items()):
        updated_at = float(record.get("updated_at") or 0.0)
        if updated_at <= 0 or current-updated_at > ttl:
            expired.append(session_id)
    for session_id in expired:
        _AUTH_SESSION_STICKY.pop(session_id, None)
    if len(_AUTH_SESSION_STICKY) > _AUTH_SESSION_STICKY_MAX:
        ordered = sorted(
            _AUTH_SESSION_STICKY.items(),
            key=lambda item: float((item[1] or {}).get("updated_at") or 0.0),
        )
        overflow = len(_AUTH_SESSION_STICKY) - _AUTH_SESSION_STICKY_MAX
        for session_id, _ in ordered[:overflow]:
            _AUTH_SESSION_STICKY.pop(session_id, None)


def get_chatgpt_auth_session_binding(session_id: str | None) -> Dict[str, Any] | None:
    if not _session_sticky_enabled():
        return None
    normalized = str(session_id or "").strip()
    if not normalized:
        return None
    with _AUTH_SESSION_STICKY_LOCK:
        _prune_chatgpt_auth_session_bindings()
        record = _AUTH_SESSION_STICKY.get(normalized)
        if not isinstance(record, dict):
            return None
        updated = dict(record)
        updated["updated_at"] = time.time()
        _AUTH_SESSION_STICKY[normalized] = updated
        return dict(updated)


def clear_chatgpt_auth_session_binding(session_id: str | None) -> None:
    normalized = str(session_id or "").strip()
    if not normalized:
        return
    with _AUTH_SESSION_STICKY_LOCK:
        _AUTH_SESSION_STICKY.pop(normalized, None)


def bind_chatgpt_auth_session(session_id: str | None, candidate: Dict[str, Any] | None) -> None:
    if not _session_sticky_enabled():
        return
    normalized = str(session_id or "").strip()
    if not normalized or not isinstance(candidate, dict):
        return
    label = str(candidate.get("label") or "").strip()
    source_path = str(candidate.get("source_path") or "").strip()
    if not label and not source_path:
        return
    with _AUTH_SESSION_STICKY_LOCK:
        _prune_chatgpt_auth_session_bindings()
        _AUTH_SESSION_STICKY[normalized] = {
            "label": label,
            "source_path": source_path,
            "updated_at": time.time(),
        }


def _preferred_chatgpt_auth_candidate_for_session(
    candidates: List[Dict[str, Any]],
    session_id: str | None,
) -> Dict[str, Any] | None:
    binding = get_chatgpt_auth_session_binding(session_id)
    if not isinstance(binding, dict):
        return None
    binding_label = str(binding.get("label") or "").strip()
    binding_source_path = str(binding.get("source_path") or "").strip()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_label = str(candidate.get("label") or "").strip()
        candidate_source_path = str(candidate.get("source_path") or "").strip()
        if binding_label and candidate_label == binding_label:
            return candidate
        if binding_source_path and candidate_source_path == binding_source_path:
            return candidate
    clear_chatgpt_auth_session_binding(session_id)
    return None


def _preferred_chatgpt_auth_candidate_for_hint(
    candidates: List[Dict[str, Any]],
    preferred_label: str | None,
    preferred_source_path: str | None,
) -> Dict[str, Any] | None:
    label = str(preferred_label or "").strip()
    source_path = str(preferred_source_path or "").strip()
    if not label and not source_path:
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_label = str(candidate.get("label") or "").strip()
        candidate_source_path = str(candidate.get("source_path") or "").strip()
        if label and candidate_label == label:
            return candidate
        if source_path and candidate_source_path == source_path:
            return candidate
    return None


def _candidate_auth_bases() -> List[str]:
    bases: List[str] = []
    explicit_bases = [
        os.getenv("CHATGPT_LOCAL_HOME"),
    ]
    if any(isinstance(base, str) and base for base in explicit_bases):
        source_bases = explicit_bases
    else:
        source_bases = [
            os.path.expanduser("~/.chatgpt-local"),
        ]
    for base in source_bases:
        if not isinstance(base, str) or not base:
            continue
        if base not in bases:
            bases.append(base)
    return bases


def _read_json_file(path: str) -> Dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _write_json_file(path: str, payload: Any) -> bool:
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except Exception as exc:
        eprint(f"ERROR: unable to create directory for {path}: {exc}")
        return False
    try:
        with open(path, "w", encoding="utf-8") as fp:
            if hasattr(os, "fchmod"):
                os.fchmod(fp.fileno(), 0o600)
            json.dump(payload, fp, indent=2)
        return True
    except Exception as exc:
        eprint(f"ERROR: unable to write JSON file {path}: {exc}")
        return False


def _delete_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except Exception as exc:
        eprint(f"ERROR: unable to delete file {path}: {exc}")
        return False


def _read_raw_json_file(path: str) -> Dict[str, Any] | List[Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, (dict, list)) else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _find_auth_file_path(filename: str) -> str | None:
    for base in _candidate_auth_bases():
        path = os.path.join(base, filename)
        if os.path.exists(path):
            return path
    return None


def read_auth_file() -> Dict[str, Any] | None:
    path = _find_auth_file_path("auth.json")
    if not path:
        return None
    return _read_json_file(path)


def write_auth_file(auth: Dict[str, Any]) -> bool:
    home = get_home_dir()
    path = os.path.join(home, "auth.json")
    return _write_json_file(path, auth)


def parse_jwt_claims(token: str) -> Dict[str, Any] | None:
    if not token or token.count(".") != 2:
        return None
    try:
        _, payload, _ = token.split(".")
        padded = payload + "=" * (-len(payload) % 4)
        data = base64.urlsafe_b64decode(padded.encode())
        return json.loads(data.decode())
    except Exception:
        return None


def generate_pkce() -> "PkceCodes":
    from .models import PkceCodes

    code_verifier = secrets.token_hex(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PkceCodes(code_verifier=code_verifier, code_challenge=code_challenge)


def convert_chat_messages_to_responses_input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _normalize_image_data_url(url: str) -> str:
        try:
            if not isinstance(url, str):
                return url
            if not url.startswith("data:image/"):
                return url
            if ";base64," not in url:
                return url
            header, data = url.split(",", 1)
            try:
                from urllib.parse import unquote

                data = unquote(data)
            except Exception:
                pass
            data = data.strip().replace("\n", "").replace("\r", "")
            data = data.replace("-", "+").replace("_", "/")
            pad = (-len(data)) % 4
            if pad:
                data = data + ("=" * pad)
            try:
                base64.b64decode(data, validate=True)
            except Exception:
                return url
            return f"{header},{data}"
        except Exception:
            return url

    input_items: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue

        if role == "tool":
            call_id = message.get("tool_call_id") or message.get("id")
            if isinstance(call_id, str) and call_id:
                content = message.get("content", "")
                if isinstance(content, list):
                    texts = []
                    for part in content:
                        if isinstance(part, dict):
                            t = part.get("text") or part.get("content")
                            if isinstance(t, str) and t:
                                texts.append(t)
                    content = "\n".join(texts)
                if isinstance(content, str):
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": content,
                        }
                    )
            continue
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            for tc in message.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                tc_type = tc.get("type", "function")
                if tc_type != "function":
                    continue
                call_id = tc.get("id") or tc.get("call_id")
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name") if isinstance(fn, dict) else None
                args = fn.get("arguments") if isinstance(fn, dict) else None
                if isinstance(call_id, str) and isinstance(name, str) and isinstance(args, str):
                    input_items.append(
                        {
                            "type": "function_call",
                            "name": sanitize_reserved_tool_name(name),
                            "arguments": args,
                            "call_id": call_id,
                        }
                    )

        content = message.get("content", "")
        content_items: List[Dict[str, Any]] = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    text = part.get("text") or part.get("content") or ""
                    if isinstance(text, str) and text:
                        kind = "output_text" if role == "assistant" else "input_text"
                        content_items.append({"type": kind, "text": text})
                elif ptype == "image_url":
                    image = part.get("image_url")
                    url = image.get("url") if isinstance(image, dict) else image
                    if isinstance(url, str) and url:
                        content_items.append({"type": "input_image", "image_url": _normalize_image_data_url(url)})
        elif isinstance(content, str) and content:
            kind = "output_text" if role == "assistant" else "input_text"
            content_items.append({"type": kind, "text": content})

        if not content_items:
            continue
        role_out = "assistant" if role == "assistant" else "user"
        input_items.append({"type": "message", "role": role_out, "content": content_items})
    return input_items


def convert_tools_chat_to_responses(tools: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(tools, list):
        return out
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") != "function":
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else {}
        name = fn.get("name") if isinstance(fn, dict) else None
        if not isinstance(name, str) or not name:
            continue
        desc = fn.get("description") if isinstance(fn, dict) else None
        params = fn.get("parameters") if isinstance(fn, dict) else None
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "name": sanitize_reserved_tool_name(name),
                "description": desc or "",
                "strict": False,
                "parameters": params,
            }
        )
    return out


def load_chatgpt_tokens(ensure_fresh: bool = True) -> tuple[str | None, str | None, str | None]:
    auth = read_auth_file()
    if not isinstance(auth, dict):
        return None, None, None

    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    access_token: Optional[str] = tokens.get("access_token")
    account_id: Optional[str] = tokens.get("account_id")
    id_token: Optional[str] = tokens.get("id_token")
    refresh_token: Optional[str] = tokens.get("refresh_token")
    last_refresh = auth.get("last_refresh")

    if ensure_fresh and isinstance(refresh_token, str) and refresh_token and CLIENT_ID_DEFAULT:
        needs_refresh = _should_refresh_access_token(access_token, last_refresh)
        if needs_refresh or not (isinstance(access_token, str) and access_token):
            refreshed = _refresh_chatgpt_tokens(refresh_token, CLIENT_ID_DEFAULT)
            if refreshed:
                access_token = refreshed.get("access_token") or access_token
                id_token = refreshed.get("id_token") or id_token
                refresh_token = refreshed.get("refresh_token") or refresh_token
                account_id = refreshed.get("account_id") or account_id

                updated_tokens = dict(tokens)
                if isinstance(access_token, str) and access_token:
                    updated_tokens["access_token"] = access_token
                if isinstance(id_token, str) and id_token:
                    updated_tokens["id_token"] = id_token
                if isinstance(refresh_token, str) and refresh_token:
                    updated_tokens["refresh_token"] = refresh_token
                if isinstance(account_id, str) and account_id:
                    updated_tokens["account_id"] = account_id

                persisted = _persist_refreshed_auth(auth, updated_tokens)
                if persisted is not None:
                    auth, tokens = persisted
                else:
                    tokens = updated_tokens

    if not isinstance(account_id, str) or not account_id:
        account_id = _derive_account_id(id_token)

    access_token = access_token if isinstance(access_token, str) and access_token else None
    id_token = id_token if isinstance(id_token, str) and id_token else None
    account_id = account_id if isinstance(account_id, str) and account_id else None
    return access_token, account_id, id_token


def _extract_tokens_from_auth_obj(auth_obj: Dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None, Any]:
    tokens = auth_obj.get("tokens") if isinstance(auth_obj.get("tokens"), dict) else {}
    source = tokens if isinstance(tokens, dict) and tokens else auth_obj
    access_token = source.get("access_token") if isinstance(source.get("access_token"), str) else None
    account_id = source.get("account_id") if isinstance(source.get("account_id"), str) else None
    id_token = source.get("id_token") if isinstance(source.get("id_token"), str) else None
    refresh_token = source.get("refresh_token") if isinstance(source.get("refresh_token"), str) else None
    last_refresh = auth_obj.get("last_refresh")
    return access_token, account_id, id_token, refresh_token, last_refresh


def _should_refresh_access_token(access_token: Optional[str], last_refresh: Any) -> bool:
    if not isinstance(access_token, str) or not access_token:
        return True

    claims = parse_jwt_claims(access_token) or {}
    exp = claims.get("exp") if isinstance(claims, dict) else None
    now = datetime.datetime.now(datetime.timezone.utc)
    if isinstance(exp, (int, float)):
        try:
            expiry = datetime.datetime.fromtimestamp(float(exp), datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            expiry = None
        if expiry is not None:
            return expiry <= now + datetime.timedelta(minutes=5)

    if isinstance(last_refresh, str):
        refreshed_at = _parse_iso8601(last_refresh)
        if refreshed_at is not None:
            return refreshed_at <= now - datetime.timedelta(minutes=55)
    return False


def _refresh_chatgpt_tokens(refresh_token: str, client_id: str) -> Optional[Dict[str, Optional[str]]]:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "scope": "openid profile email offline_access",
    }

    try:
        resp = requests.post(OAUTH_TOKEN_URL, json=payload, timeout=30)
    except requests.RequestException as exc:
        eprint(f"ERROR: failed to refresh ChatGPT token: {exc}")
        return None

    if resp.status_code >= 400:
        eprint(f"ERROR: refresh token request returned status {resp.status_code}")
        return None

    try:
        data = resp.json()
    except ValueError as exc:
        eprint(f"ERROR: unable to parse refresh token response: {exc}")
        return None

    id_token = data.get("id_token")
    access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token") or refresh_token
    if not isinstance(id_token, str) or not isinstance(access_token, str):
        eprint("ERROR: refresh token response missing expected tokens")
        return None

    account_id = _derive_account_id(id_token)
    new_refresh_token = new_refresh_token if isinstance(new_refresh_token, str) and new_refresh_token else refresh_token
    return {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "account_id": account_id,
    }


def _persist_refreshed_auth(auth: Dict[str, Any], updated_tokens: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    updated_auth = dict(auth)
    updated_auth["tokens"] = updated_tokens
    updated_auth["last_refresh"] = _now_iso8601()
    if write_auth_file(updated_auth):
        return updated_auth, updated_tokens
    eprint("ERROR: unable to persist refreshed auth tokens")
    return None


def _derive_account_id(id_token: Optional[str]) -> Optional[str]:
    if not isinstance(id_token, str) or not id_token:
        return None
    claims = parse_jwt_claims(id_token) or {}
    auth_claims = claims.get("https://api.openai.com/auth") if isinstance(claims, dict) else None
    if isinstance(auth_claims, dict):
        account_id = auth_claims.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    return None


def _derive_workspace_fields(
    id_token: Optional[str],
    access_token: Optional[str],
    auth_obj: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    org_id = ""
    project_id = ""
    for token in (id_token, access_token):
        claims = parse_jwt_claims(token) or {}
        if not isinstance(claims, dict):
            continue
        if not org_id:
            value = claims.get("organization_id")
            if isinstance(value, str):
                org_id = value.strip()
        if not project_id:
            value = claims.get("project_id")
            if isinstance(value, str):
                project_id = value.strip()
        auth_claims = claims.get("https://api.openai.com/auth")
        if isinstance(auth_claims, dict):
            if not org_id:
                value = auth_claims.get("organization_id")
                if isinstance(value, str):
                    org_id = value.strip()
            if not project_id:
                value = auth_claims.get("project_id")
                if isinstance(value, str):
                    project_id = value.strip()
    if isinstance(auth_obj, dict):
        if not org_id:
            value = auth_obj.get("org_id")
            if isinstance(value, str):
                org_id = value.strip()
        if not project_id:
            value = auth_obj.get("project_id")
            if isinstance(value, str):
                project_id = value.strip()
    return org_id, project_id


def _parse_iso8601(value: str) -> Optional[datetime.datetime]:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def _now_iso8601() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _dashboard_settings_path() -> str | None:
    explicit = (os.getenv("CHATMOCK_DASHBOARD_SETTINGS_PATH") or "").strip()
    if explicit:
        return explicit
    data_dir = (os.getenv("CHATMOCK_DATA_DIR") or "").strip()
    if data_dir:
        return os.path.join(data_dir, "accounts", "_dashboard_settings.json")
    return None


def _load_dashboard_settings() -> Dict[str, Any] | None:
    path = _dashboard_settings_path()
    if not path:
        return None
    data = _read_raw_json_file(path)
    return data if isinstance(data, dict) else None


def _persist_dashboard_auth_files(paths: List[str]) -> bool:
    path = _dashboard_settings_path()
    if not path:
        return False
    payload = _load_dashboard_settings() or {}
    payload["authFiles"] = list(paths)
    payload["updatedAt"] = _now_iso8601()
    return _write_json_file(path, payload)


def _quarantined_auth_files(stored: Dict[str, Any] | None = None) -> List[str]:
    stored = stored if isinstance(stored, dict) else (_load_dashboard_settings() or {})
    raw = stored.get("quarantinedAuthFiles")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return []


def _persist_dashboard_quarantined_auth_files(paths: List[str]) -> bool:
    path = _dashboard_settings_path()
    if not path:
        return False
    payload = _load_dashboard_settings() or {}
    payload["quarantinedAuthFiles"] = list(paths)
    payload["updatedAt"] = _now_iso8601()
    return _write_json_file(path, payload)


def _clear_quarantined_auth_paths(paths: List[str]) -> bool:
    normalized_targets = {
        _canonical_auth_path(item)
        for item in paths
        if isinstance(item, str) and item.strip()
    }
    if not normalized_targets:
        return False
    existing = _quarantined_auth_files()
    updated = [
        item
        for item in existing
        if _canonical_auth_path(item) not in normalized_targets
    ]
    if len(updated) == len(existing):
        return False
    return _persist_dashboard_quarantined_auth_files(updated)


def _persist_dashboard_default_auth_fields(
    access_token: str = "",
    account_id: str = "",
    plan_type: str = "",
) -> bool:
    path = _dashboard_settings_path()
    if not path:
        return False
    payload = _load_dashboard_settings() or {}
    payload["chatgptAuthAccessToken"] = str(access_token or "")
    payload["chatgptAuthAccountId"] = str(account_id or "")
    payload["chatgptAuthPlanType"] = str(plan_type or "")
    payload["updatedAt"] = _now_iso8601()
    return _write_json_file(path, payload)


def _invalid_auth_account_ids(stored: Dict[str, Any] | None = None) -> List[str]:
    stored = stored if isinstance(stored, dict) else (_load_dashboard_settings() or {})
    raw = stored.get("invalidAuthAccountIds")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return []


def _persist_dashboard_invalid_auth_account_ids(account_ids: List[str]) -> bool:
    path = _dashboard_settings_path()
    if not path:
        return False
    payload = _load_dashboard_settings() or {}
    payload["invalidAuthAccountIds"] = list(dict.fromkeys([str(item).strip() for item in account_ids if str(item).strip()]))
    payload["updatedAt"] = _now_iso8601()
    return _write_json_file(path, payload)


def _canonical_auth_path(path: str) -> str:
    expanded = os.path.expanduser(str(path or "").strip())
    return os.path.normcase(os.path.normpath(expanded)) if expanded else ""


def _is_quarantined_auth_path(path: str) -> bool:
    normalized = _canonical_auth_path(path)
    if not normalized:
        return False
    quarantined = {_canonical_auth_path(item) for item in _quarantined_auth_files()}
    return normalized in quarantined


def _known_auth_file_paths(include_quarantined: bool = False) -> List[str]:
    paths = _parse_auth_files_env(include_quarantined=include_quarantined)
    if include_quarantined:
        for item in _quarantined_auth_files():
            normalized = str(item).strip()
            if normalized and normalized not in paths:
                paths.append(normalized)
    return paths


def _has_explicit_auth_files_config() -> bool:
    raw_flag = (os.getenv("CHATGPT_LOCAL_AUTH_FILES_CONFIGURED") or "").strip().lower()
    if raw_flag in ("1", "true", "yes", "on"):
        return True
    stored = _load_dashboard_settings()
    return isinstance(stored, dict) and "authFiles" in stored


def _remove_path_from_auth_files_env(path: str) -> List[str]:
    current = _parse_auth_files_env()
    updated = [item for item in current if item != path]
    if updated:
        os.environ["CHATGPT_LOCAL_AUTH_FILES"] = ",".join(updated)
    else:
        os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
    return updated


def _remove_label_state(label: str) -> None:
    with _AUTH_POOL_STATE_LOCK:
        _AUTH_POOL_STATE.pop(label, None)


def _account_state_key(account_id: str) -> str:
    normalized = str(account_id or "").strip()
    return f"account::{normalized}" if normalized else ""


def _remove_account_state(account_id: str) -> None:
    key = _account_state_key(account_id)
    if not key:
        return
    with _AUTH_POOL_STATE_LOCK:
        _AUTH_POOL_STATE.pop(key, None)


def _mark_invalid_auth_candidate(*, label: str = "", account_id: str = "") -> None:
    with _INVALID_AUTH_LOCK:
        if isinstance(label, str) and label.strip():
            _INVALID_AUTH_LABELS.add(label.strip())
        if isinstance(account_id, str) and account_id.strip():
            _INVALID_AUTH_ACCOUNT_IDS.add(account_id.strip())
        persisted = set(_invalid_auth_account_ids())
        if isinstance(account_id, str) and account_id.strip():
            persisted.add(account_id.strip())
        if persisted:
            _persist_dashboard_invalid_auth_account_ids(sorted(persisted))


def _clear_invalid_auth_candidate(*, label: str = "", account_id: str = "") -> None:
    with _INVALID_AUTH_LOCK:
        if isinstance(label, str) and label.strip():
            _INVALID_AUTH_LABELS.discard(label.strip())
        persisted = set(_invalid_auth_account_ids())
        if isinstance(account_id, str) and account_id.strip():
            _INVALID_AUTH_ACCOUNT_IDS.discard(account_id.strip())
            persisted.discard(account_id.strip())
        _persist_dashboard_invalid_auth_account_ids(sorted(persisted))


def _is_invalid_auth_candidate(*, label: str = "", account_id: str = "") -> bool:
    with _INVALID_AUTH_LOCK:
        if isinstance(label, str) and label.strip() and label.strip() in _INVALID_AUTH_LABELS:
            return True
        if isinstance(account_id, str) and account_id.strip() and account_id.strip() in _INVALID_AUTH_ACCOUNT_IDS:
            return True
    if isinstance(account_id, str) and account_id.strip():
        return account_id.strip() in set(_invalid_auth_account_ids())
    return False


def _set_account_cooldown(*, account_id: str = "", until_ts: float = 0.0) -> None:
    key = _account_state_key(account_id)
    if not key:
        return
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(key) or {})
        state["cooldown_until"] = float(until_ts) if until_ts > 0 else 0.0
        _AUTH_POOL_STATE[key] = state


def _get_account_cooldown(account_id: str) -> float:
    key = _account_state_key(account_id)
    if not key:
        return 0.0
    with _AUTH_POOL_STATE_LOCK:
        until_ts = float((_AUTH_POOL_STATE.get(key) or {}).get("cooldown_until") or 0.0)
    now = time.time()
    if until_ts <= now:
        _set_account_cooldown(account_id=account_id, until_ts=0.0)
        return 0.0
    return until_ts


def _get_candidate_rate_limit_cooldown(label: str) -> float:
    if not label:
        return 0.0
    with _AUTH_POOL_STATE_LOCK:
        until_ts = float((_AUTH_POOL_STATE.get(label) or {}).get("codex_cooldown_until") or 0.0)
    now = time.time()
    if until_ts <= now:
        with _AUTH_POOL_STATE_LOCK:
            state = dict(_AUTH_POOL_STATE.get(label) or {})
            state["codex_cooldown_until"] = 0.0
            _AUTH_POOL_STATE[label] = state
        return 0.0
    return until_ts


def _candidate_codex_pressure_score(candidate: Dict[str, Any]) -> tuple[int, float]:
    if not isinstance(candidate, dict):
        return (1, 1000.0)
    label = str(candidate.get("label") or "").strip()
    if not label:
        return (1, 1000.0)
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(label) or {})
    primary_used = float(state.get("codex_primary_used_percent") or 0.0)
    secondary_used = float(state.get("codex_secondary_used_percent") or 0.0)
    exhausted = 1 if _get_candidate_rate_limit_cooldown(label) > time.time() else 0
    return (exhausted, max(primary_used, secondary_used))


def _sort_candidates_by_codex_pressure(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(candidates) <= 1:
        return candidates
    return sorted(candidates, key=_candidate_codex_pressure_score)


def update_chatgpt_candidate_rate_limits(
    label: str,
    *,
    primary_used_percent: float | None = None,
    primary_window_minutes: int | None = None,
    primary_resets_in_seconds: int | None = None,
    secondary_used_percent: float | None = None,
    secondary_window_minutes: int | None = None,
    secondary_resets_in_seconds: int | None = None,
) -> None:
    if not isinstance(label, str) or not label.strip():
        return
    label = label.strip()
    now = time.time()
    exhausted_windows: List[float] = []
    for used_percent, resets_in_seconds in (
        (primary_used_percent, primary_resets_in_seconds),
        (secondary_used_percent, secondary_resets_in_seconds),
    ):
        if isinstance(used_percent, (int, float)) and float(used_percent) >= 100.0 and isinstance(resets_in_seconds, int) and resets_in_seconds > 0:
            exhausted_windows.append(now + float(resets_in_seconds))
    codex_cooldown_until = max(exhausted_windows) if exhausted_windows else 0.0
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(label) or {})
        if primary_used_percent is not None:
            state["codex_primary_used_percent"] = float(primary_used_percent)
        if primary_window_minutes is not None:
            state["codex_primary_window_minutes"] = int(primary_window_minutes)
        if primary_resets_in_seconds is not None:
            state["codex_primary_resets_in_seconds"] = int(primary_resets_in_seconds)
        if secondary_used_percent is not None:
            state["codex_secondary_used_percent"] = float(secondary_used_percent)
        if secondary_window_minutes is not None:
            state["codex_secondary_window_minutes"] = int(secondary_window_minutes)
        if secondary_resets_in_seconds is not None:
            state["codex_secondary_resets_in_seconds"] = int(secondary_resets_in_seconds)
        state["codex_cooldown_until"] = codex_cooldown_until
        state["updated_at"] = now
        _AUTH_POOL_STATE[label] = state


def is_auth_candidate_blocked(candidate: Dict[str, Any]) -> bool:
    if not isinstance(candidate, dict):
        return True
    label = str(candidate.get("label") or "").strip()
    account_id = str(candidate.get("account_id") or "").strip()
    if _is_invalid_auth_candidate(label=label, account_id=account_id):
        return True
    cooldown_until = _get_cooldown_until(label)
    if cooldown_until > time.time():
        return True
    account_cooldown_until = _get_account_cooldown(account_id)
    if account_cooldown_until > time.time():
        return True
    codex_cooldown_until = _get_candidate_rate_limit_cooldown(label)
    if codex_cooldown_until > time.time():
        return True
    return False


def _label_for_auth_file_path(path: str) -> str:
    dirname = os.path.basename(os.path.dirname(path))
    filename = os.path.basename(path)
    return f"{dirname}/{filename}" if dirname else (filename or path)


def _account_id_from_auth_obj(auth_obj: Dict[str, Any]) -> str:
    access_token, account_id, id_token, _, _ = _extract_tokens_from_auth_obj(auth_obj)
    del access_token  # unused in this helper
    if not isinstance(account_id, str) or not account_id:
        account_id = _derive_account_id(id_token) or ""
    return str(account_id).strip()


def _remove_auth_from_pool_file(pool_path: str, index: int) -> bool:
    raw_pool = _read_raw_json_file(pool_path)
    if not isinstance(raw_pool, (dict, list)):
        return False
    removed = False
    if isinstance(raw_pool, list):
        if 0 <= index < len(raw_pool):
            raw_pool.pop(index)
            removed = True
    else:
        accounts = raw_pool.get("accounts")
        if isinstance(accounts, list) and 0 <= index < len(accounts):
            accounts.pop(index)
            raw_pool["accounts"] = accounts
            removed = True
    if not removed:
        return False
    return _write_json_file(pool_path, raw_pool)


def remove_chatgpt_auth_candidate(candidate: Dict[str, Any], *, reason: str = "") -> bool:
    if not isinstance(candidate, dict):
        return False
    label = str(candidate.get("label") or "").strip()
    source_kind = str(candidate.get("source_kind") or "").strip()
    source_path = str(candidate.get("source_path") or "").strip()
    source_index = candidate.get("source_index")

    success = False
    if source_kind in ("auth_file", "default_auth"):
        current_paths = _parse_auth_files_env()
        paths_to_remove: List[str] = [source_path] if source_path else []
        if paths_to_remove:
            updated = [item for item in current_paths if item not in paths_to_remove]
            if updated:
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = ",".join(updated)
            else:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
            _persist_dashboard_auth_files(updated)
            for path in paths_to_remove:
                if _delete_file(path):
                    success = True
                _remove_label_state(_label_for_auth_file_path(path))
    if source_kind == "default_auth":
        had_default_env = False
        for env_name in (
            "CHATMOCK_CODEX_ACCESS_TOKEN",
            "CHATMOCK_CODEX_ACCOUNT_ID",
            "CHATMOCK_CODEX_PLAN_TYPE",
        ):
            if os.environ.get(env_name):
                had_default_env = True
            os.environ.pop(env_name, None)
        if had_default_env:
            success = True
        if _persist_dashboard_default_auth_fields("", "", ""):
            success = True
    elif source_kind == "auth_pool":
        if source_path and isinstance(source_index, int):
            success = _remove_auth_from_pool_file(source_path, source_index)

    if success and label:
        _remove_label_state(label)
        if reason:
            eprint(f"INFO: removed ChatGPT auth candidate {label}: {reason}")
        else:
            eprint(f"INFO: removed ChatGPT auth candidate {label}")
    return success


def quarantine_chatgpt_auth_candidate(candidate: Dict[str, Any], *, reason: str = "") -> bool:
    if not isinstance(candidate, dict):
        return False
    label = str(candidate.get("label") or "").strip()
    source_kind = str(candidate.get("source_kind") or "").strip()
    source_path = str(candidate.get("source_path") or "").strip()

    if source_kind == "auth_pool":
        return remove_chatgpt_auth_candidate(candidate, reason=reason or "Quarantined auth_pool candidate")

    paths_to_quarantine: List[str] = [source_path] if source_path else []

    if not paths_to_quarantine:
        return False

    active_paths = _parse_auth_files_env(include_quarantined=False)
    updated_active = [item for item in active_paths if item not in paths_to_quarantine]
    if updated_active:
        os.environ["CHATGPT_LOCAL_AUTH_FILES"] = ",".join(updated_active)
    else:
        os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
    _persist_dashboard_auth_files(updated_active)

    existing_quarantined = _quarantined_auth_files()
    normalized_existing = {_canonical_auth_path(item): item for item in existing_quarantined}
    for path in paths_to_quarantine:
        canonical = _canonical_auth_path(path)
        if canonical and canonical not in normalized_existing:
            existing_quarantined.append(path)
            normalized_existing[canonical] = path

    _persist_dashboard_quarantined_auth_files(existing_quarantined)
    for path in paths_to_quarantine:
        _remove_label_state(_label_for_auth_file_path(path))

    if label:
        _remove_label_state(label)
        if reason:
            eprint(f"INFO: quarantined ChatGPT auth candidate {label}: {reason}")
        else:
            eprint(f"INFO: quarantined ChatGPT auth candidate {label}")
    return True


def get_effective_chatgpt_auth() -> tuple[str | None, str | None]:
    access_token, account_id, id_token = load_chatgpt_tokens()
    if not account_id:
        account_id = _derive_account_id(id_token)
    return access_token, account_id


def _candidate_from_auth_obj(
    auth_obj: Dict[str, Any],
    *,
    label: str,
    ensure_fresh: bool,
    source_kind: str | None = None,
    source_path: str | None = None,
    source_index: int | None = None,
) -> tuple[Dict[str, Any] | None, bool]:
    access_token, account_id, id_token, refresh_token, last_refresh = _extract_tokens_from_auth_obj(auth_obj)
    changed = False
    refreshed = False

    if ensure_fresh and isinstance(refresh_token, str) and refresh_token and CLIENT_ID_DEFAULT:
        needs_refresh = _should_refresh_access_token(access_token, last_refresh)
        if needs_refresh or not (isinstance(access_token, str) and access_token):
            updated = _refresh_chatgpt_tokens(refresh_token, CLIENT_ID_DEFAULT)
            if updated:
                access_token = updated.get("access_token") or access_token
                id_token = updated.get("id_token") or id_token
                refresh_token = updated.get("refresh_token") or refresh_token
                account_id = updated.get("account_id") or account_id
                refreshed = True
                changed = True

    if not isinstance(account_id, str) or not account_id:
        derived = _derive_account_id(id_token)
        if isinstance(derived, str) and derived:
            account_id = derived
            changed = True

    if changed:
        tokens = auth_obj.get("tokens") if isinstance(auth_obj.get("tokens"), dict) else {}
        updated_tokens = dict(tokens) if isinstance(tokens, dict) else {}
        if isinstance(access_token, str) and access_token:
            updated_tokens["access_token"] = access_token
        if isinstance(id_token, str) and id_token:
            updated_tokens["id_token"] = id_token
        if isinstance(refresh_token, str) and refresh_token:
            updated_tokens["refresh_token"] = refresh_token
        if isinstance(account_id, str) and account_id:
            updated_tokens["account_id"] = account_id
        auth_obj["tokens"] = updated_tokens
        if refreshed:
            auth_obj["last_refresh"] = _now_iso8601()

    if not (isinstance(access_token, str) and access_token):
        return None, changed
    if not (isinstance(account_id, str) and account_id):
        return None, changed
    return {
        "label": label,
        "access_token": access_token,
        "account_id": account_id,
        "source_kind": source_kind or "",
        "source_path": source_path or "",
        "source_index": source_index,
    }, changed


def _load_auth_candidates_from_auth_files(ensure_fresh: bool = True) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    paths = _parse_auth_files_env()
    for idx, path in enumerate(paths):
        auth_obj = _read_json_file(path)
        if not isinstance(auth_obj, dict):
            eprint(f"WARNING: skipped invalid auth file: {path}")
            continue
        dirname = os.path.basename(os.path.dirname(path))
        filename = os.path.basename(path)
        label = f"{dirname}/{filename}" if dirname else (filename or f"file-{idx + 1}")
        account_id = _account_id_from_auth_obj(auth_obj)
        if _is_invalid_auth_candidate(label=label, account_id=account_id):
            continue
        candidate, changed = _candidate_from_auth_obj(
            auth_obj,
            label=label,
            ensure_fresh=ensure_fresh,
            source_kind="auth_file",
            source_path=path,
        )
        if changed:
            _write_json_file(path, auth_obj)
        if candidate is not None:
            out.append(candidate)
    return out


def _load_auth_candidates_from_pool_file(ensure_fresh: bool = True) -> List[Dict[str, Any]]:
    path = _find_auth_file_path("auth_pool.json")
    if not path:
        return []
    raw_pool = _read_raw_json_file(path)
    if not isinstance(raw_pool, (dict, list)):
        return []
    accounts = _extract_pool_accounts(raw_pool)
    if not accounts:
        return []

    changed = False
    out: List[Dict[str, Any]] = []
    for idx, account_obj in enumerate(accounts):
        label = ""
        for key in ("name", "alias", "label"):
            value = account_obj.get(key)
            if isinstance(value, str) and value.strip():
                label = value.strip()
                break
        if not label:
            label = f"pool-{idx + 1}"
        account_id = _account_id_from_auth_obj(account_obj)
        if _is_invalid_auth_candidate(label=label, account_id=account_id):
            continue
        candidate, account_changed = _candidate_from_auth_obj(
            account_obj,
            label=label,
            ensure_fresh=ensure_fresh,
            source_kind="auth_pool",
            source_path=path,
            source_index=idx,
        )
        changed = changed or account_changed
        if candidate is not None:
            out.append(candidate)

    if changed:
        _write_json_file(path, raw_pool)
    return out


def _dedupe_candidates_by_account_id(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen_entries: set[str] = set()
    seen_labels: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        label = str(candidate.get("label") or "").strip()
        account_id = str(candidate.get("account_id") or "").strip()
        source_path = str(candidate.get("source_path") or candidate.get("auth_path") or "").strip()
        dedupe_key = account_id or source_path or label
        if not dedupe_key:
            continue
        if dedupe_key in seen_entries or label in seen_labels:
            continue
        seen_entries.add(dedupe_key)
        if label:
            seen_labels.add(label)
        deduped.append(candidate)
    return deduped


def get_effective_chatgpt_auth_candidates(ensure_fresh: bool = True) -> List[Dict[str, Any]]:
    candidates = _load_auth_candidates_from_auth_files(ensure_fresh=ensure_fresh)
    if not candidates and not _has_explicit_auth_files_config():
        candidates = _load_auth_candidates_from_pool_file(ensure_fresh=ensure_fresh)
    if not candidates and not _has_explicit_auth_files_config():
        access_token, account_id, id_token = load_chatgpt_tokens(ensure_fresh=ensure_fresh)
        if not account_id:
            account_id = _derive_account_id(id_token)
        if (
            isinstance(access_token, str)
            and access_token
            and isinstance(account_id, str)
            and account_id
            and not _is_invalid_auth_candidate(label="default", account_id=account_id)
        ):
            candidates = [{
                "label": "default",
                "access_token": access_token,
                "account_id": account_id,
                "source_kind": "default_auth",
                "source_path": _find_auth_file_path("auth.json") or "auth.json",
                "source_index": None,
            }]
    candidates = _dedupe_candidates_by_account_id(candidates)
    candidates = _apply_account_cooldown(candidates)
    return _ordered_candidates_by_strategy(candidates)


def _ordered_candidates_round_robin(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if len(candidates) <= 1:
        return candidates
    global _AUTH_POOL_RR_INDEX
    with _AUTH_POOL_RR_LOCK:
        start = _AUTH_POOL_RR_INDEX % len(candidates)
        _AUTH_POOL_RR_INDEX = (_AUTH_POOL_RR_INDEX + 1) % len(candidates)
    if start == 0:
        return candidates
    return candidates[start:] + candidates[:start]


def _routing_strategy() -> str:
    raw = (os.getenv("CHATGPT_LOCAL_ROUTING_STRATEGY") or "round-robin").strip().lower()
    if raw in ("round-robin", "rr"):
        return "round-robin"
    if raw in ("random", "rand"):
        return "random"
    return "first"


def _ordered_candidates_by_strategy(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if len(candidates) <= 1:
        return candidates
    strategy = _routing_strategy()
    if strategy == "round-robin":
        return _ordered_candidates_round_robin(candidates)
    if strategy == "random":
        out = list(candidates)
        for i in range(len(out) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            out[i], out[j] = out[j], out[i]
        return out
    return candidates


def get_max_inflight_per_account() -> int:
    raw = (os.getenv("CHATGPT_LOCAL_MAX_INFLIGHT_PER_ACCOUNT") or "2").strip()
    try:
        value = int(raw)
    except Exception:
        value = 2
    return max(1, min(32, value))


def _candidate_busy_key(candidate: Dict[str, Any]) -> str:
    if not isinstance(candidate, dict):
        return ""
    source_path = str(candidate.get("source_path") or "").strip()
    if source_path:
        return source_path
    return str(candidate.get("label") or "").strip()


def _get_inflight_count_for_key(key: str) -> int:
    if not key:
        return 0
    with _AUTH_INFLIGHT_LOCK:
        return int(_AUTH_INFLIGHT_COUNTS.get(key) or 0)


def _reserve_auth_candidate_slot(candidate: Dict[str, Any]) -> None:
    key = _candidate_busy_key(candidate)
    if not key:
        return
    label = str(candidate.get("label") or "").strip()
    with _AUTH_INFLIGHT_LOCK:
        inflight = int(_AUTH_INFLIGHT_COUNTS.get(key) or 0) + 1
        _AUTH_INFLIGHT_COUNTS[key] = inflight
    if label:
        with _AUTH_POOL_STATE_LOCK:
            state = dict(_AUTH_POOL_STATE.get(label) or {})
            state["inflight"] = inflight
            _AUTH_POOL_STATE[label] = state


def _release_auth_candidate_slot(candidate: Dict[str, Any]) -> None:
    key = _candidate_busy_key(candidate)
    if not key:
        return
    label = str(candidate.get("label") or "").strip()
    with _AUTH_INFLIGHT_LOCK:
        current = int(_AUTH_INFLIGHT_COUNTS.get(key) or 0)
        if current <= 1:
            _AUTH_INFLIGHT_COUNTS.pop(key, None)
            inflight = 0
        else:
            inflight = current - 1
            _AUTH_INFLIGHT_COUNTS[key] = inflight
    if label:
        with _AUTH_POOL_STATE_LOCK:
            state = dict(_AUTH_POOL_STATE.get(label) or {})
            state["inflight"] = inflight
            _AUTH_POOL_STATE[label] = state


def _apply_account_capacity(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(candidates) <= 1:
        return candidates
    limit = get_max_inflight_per_account()
    preferred = [candidate for candidate in candidates if _get_inflight_count_for_key(_candidate_busy_key(candidate)) < limit]
    if preferred:
        return preferred
    return candidates


def claim_chatgpt_auth_candidate(
    *,
    ensure_fresh: bool = True,
    excluded_labels: set[str] | None = None,
    session_id: str | None = None,
    preferred_label: str | None = None,
    preferred_source_path: str | None = None,
) -> Dict[str, Any] | None:
    excluded = excluded_labels or set()
    candidates = get_effective_chatgpt_auth_candidates(ensure_fresh=ensure_fresh)
    candidates = [candidate for candidate in candidates if str(candidate.get("label") or "").strip() not in excluded]
    if not candidates:
        return None
    sticky_candidate = _preferred_chatgpt_auth_candidate_for_hint(
        candidates,
        preferred_label,
        preferred_source_path,
    )
    if not isinstance(sticky_candidate, dict):
        sticky_candidate = _preferred_chatgpt_auth_candidate_for_session(candidates, session_id)
    if isinstance(sticky_candidate, dict):
        sticky_label = str(sticky_candidate.get("label") or "").strip()
        prioritized: List[Dict[str, Any]] = [sticky_candidate]
        tail_candidates: List[Dict[str, Any]] = []
        for candidate in candidates:
            candidate_label = str(candidate.get("label") or "").strip()
            if sticky_label and candidate_label == sticky_label:
                continue
            tail_candidates.append(candidate)
        prioritized.extend(_sort_candidates_by_codex_pressure(tail_candidates))
        candidates = prioritized
    else:
        candidates = _sort_candidates_by_codex_pressure(candidates)
    limit = get_max_inflight_per_account()
    preferred = []
    fallback = []
    for candidate in candidates:
        if _get_inflight_count_for_key(_candidate_busy_key(candidate)) < limit:
            preferred.append(candidate)
        else:
            fallback.append(candidate)
    ordered = preferred if preferred else fallback
    if not ordered:
        return None
    with _AUTH_INFLIGHT_LOCK:
        preferred_now = [
            candidate
            for candidate in ordered
            if _get_inflight_count_for_key(_candidate_busy_key(candidate)) < limit
        ]
        selected = preferred_now[0] if preferred_now else ordered[0]
        selected_copy = dict(selected)
        _reserve_auth_candidate_slot(selected_copy)
        return selected_copy


def get_request_retry_limit() -> int:
    raw = (os.getenv("CHATGPT_LOCAL_REQUEST_RETRY") or "0").strip()
    try:
        value = int(raw)
    except Exception:
        value = 0
    return max(0, min(10, value))


def get_max_retry_interval_seconds() -> int:
    raw = (os.getenv("CHATGPT_LOCAL_MAX_RETRY_INTERVAL") or "5").strip()
    try:
        value = int(raw)
    except Exception:
        value = 5
    return max(1, min(300, value))


def get_retryable_statuses() -> set[int]:
    return {401, 403, 429, 500, 502, 503, 504}


def _get_cooldown_until(label: str) -> float:
    with _AUTH_POOL_STATE_LOCK:
        state = _AUTH_POOL_STATE.get(label) or {}
        try:
            return float(state.get("cooldown_until") or 0.0)
        except Exception:
            return 0.0


def _set_auth_pool_state(
    label: str,
    *,
    status: str,
    cooldown_until: float,
    failures: int,
    last_status: int | None,
    last_error: str,
    classification: str,
    raw_code: str | None = None,
    raw_message: str | None = None,
) -> None:
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(label) or {})
        state["status"] = status
        state["cooldown_until"] = cooldown_until
        state["failures"] = failures
        state["last_status"] = last_status
        state["last_error"] = last_error
        state["last_classification"] = classification
        state["last_raw_code"] = raw_code or ""
        state["last_raw_message"] = raw_message or last_error or ""
        state["updated_at"] = time.time()
        _AUTH_POOL_STATE[label] = state


def _apply_account_cooldown(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = time.time()
    available: List[Dict[str, Any]] = []
    for candidate in candidates:
        label = str(candidate.get("label") or "").strip()
        account_id = str(candidate.get("account_id") or "").strip()
        cooldown_until = max(_get_cooldown_until(label), _get_account_cooldown(account_id))
        if cooldown_until <= now:
            available.append(candidate)
    if available:
        return available
    return []


def mark_chatgpt_auth_result(
    label: str,
    *,
    success: bool,
    status_code: int | None = None,
    account_id: str | None = None,
    error_message: str | None = None,
    classification: str | None = None,
    cooldown_seconds: int | None = None,
    cooldown_until_ts: float | None = None,
    raw_code: str | None = None,
    raw_message: str | None = None,
) -> None:
    if not isinstance(label, str) or not label:
        return
    now = time.time()
    max_retry_interval = get_max_retry_interval_seconds()
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(label) or {})
        if success:
            effective_success_status = status_code if isinstance(status_code, int) and 200 <= status_code < 400 else 200
            _clear_invalid_auth_candidate(label=label, account_id=str(account_id or "").strip())
            if account_id:
                _set_account_cooldown(account_id=account_id, until_ts=0.0)
            _set_auth_pool_state(
                label,
                status="ready",
                cooldown_until=0.0,
                failures=0,
                last_status=effective_success_status,
                last_error="",
                classification="ready",
                raw_code="",
                raw_message="",
            )
            return

        failures = int(state.get("failures") or 0) + 1
        category = (classification or "").strip() or "generic_failure"
        if isinstance(cooldown_until_ts, (int, float)) and float(cooldown_until_ts) > now:
            cooldown_until = float(cooldown_until_ts)
            state_status = (
                "cooldown_insufficient_balance"
                if category == "insufficient_balance"
                else "cooldown_rate_limited"
                if category == "rate_limited"
                else "temporary_failure"
            )
        elif isinstance(cooldown_seconds, int) and cooldown_seconds > 0:
            cooldown_until = now + float(cooldown_seconds)
            state_status = (
                "cooldown_insufficient_balance"
                if category == "insufficient_balance"
                else "cooldown_rate_limited"
                if category == "rate_limited"
                else "temporary_failure"
            )
        elif isinstance(status_code, int) and status_code in (401, 403):
            base = 5
            cooldown = min(max_retry_interval, base * (2 ** max(0, failures - 1)))
            cooldown_until = now + float(cooldown)
            state_status = "temporary_failure"
        elif isinstance(status_code, int) and status_code == 429:
            base = 2
            cooldown = min(max_retry_interval, base * (2 ** max(0, failures - 1)))
            cooldown_until = now + float(cooldown)
            state_status = "temporary_failure"
        else:
            base = 1
            cooldown = min(max_retry_interval, base * (2 ** max(0, failures - 1)))
            cooldown_until = now + float(cooldown)
            state_status = "temporary_failure"
        _set_auth_pool_state(
            label,
            status=state_status,
            cooldown_until=cooldown_until,
            failures=failures,
            last_status=status_code,
            last_error=error_message or "",
            classification=category,
            raw_code=raw_code,
            raw_message=raw_message,
        )
        if account_id and category in ("insufficient_balance", "rate_limited"):
            _set_account_cooldown(account_id=account_id, until_ts=cooldown_until)


def handle_chatgpt_candidate_failure(candidate: Dict[str, Any], info: Dict[str, Any]) -> str:
    label = str(candidate.get("label") or "").strip()
    account_id = str(candidate.get("account_id") or "").strip()
    classification = classify_error(info)
    raw_status = info.get("raw_status") if isinstance(info.get("raw_status"), int) else None
    raw_code = info.get("raw_code") if isinstance(info.get("raw_code"), str) else None
    raw_message = info.get("raw_message") if isinstance(info.get("raw_message"), str) else None
    retry_at_until = extract_retry_after_unlock_ts(info)
    effective_classification = classification
    if retry_at_until is not None and effective_classification == "generic_failure":
        effective_classification = "rate_limited"

    if effective_classification in ("insufficient_balance", "rate_limited"):
        cooldown_until = float(retry_at_until) if retry_at_until is not None else time.time() + float(5 * 60 * 60)
        mark_chatgpt_auth_result(
            label,
            success=False,
            status_code=raw_status,
            account_id=account_id,
            error_message=raw_message,
            classification=effective_classification,
            cooldown_seconds=None if retry_at_until is not None else 5 * 60 * 60,
            cooldown_until_ts=retry_at_until,
            raw_code=raw_code,
            raw_message=raw_message,
        )
        return effective_classification

    if effective_classification == "account_invalid":
        _mark_invalid_auth_candidate(label=label, account_id=account_id)
        remove_chatgpt_auth_candidate(candidate, reason=raw_message or "Account invalid")
        return effective_classification

    mark_chatgpt_auth_result(
        label,
        success=False,
        status_code=raw_status,
        account_id=account_id,
        error_message=raw_message,
        classification=effective_classification,
        raw_code=raw_code,
        raw_message=raw_message,
    )
    return effective_classification


def get_chatgpt_auth_pool_state() -> Dict[str, Dict[str, Any]]:
    with _AUTH_POOL_STATE_LOCK:
        return {k: dict(v) for k, v in _AUTH_POOL_STATE.items()}


def _compact_account_id(raw: str | None) -> str:
    if not isinstance(raw, str) or not raw:
        return ""
    if len(raw) <= 12:
        return raw
    return f"{raw[:8]}...{raw[-4:]}"


def _state_for_label(label: str) -> Dict[str, Any]:
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(label) or {})
    now = time.time()
    cooldown_until = float(state.get("cooldown_until") or 0.0)
    remaining = max(0, int(cooldown_until - now))
    unlock_at = (
        datetime.datetime.fromtimestamp(cooldown_until, datetime.timezone.utc).isoformat()
        if cooldown_until > now
        else ""
    )
    inflight = int(state.get("inflight") or 0)
    codex_cooldown_until = float(state.get("codex_cooldown_until") or 0.0)
    codex_remaining = max(0, int(codex_cooldown_until - now))
    return {
        "status": state.get("status") or "ready",
        "failures": int(state.get("failures") or 0),
        "last_status": state.get("last_status"),
        "last_error": state.get("last_error") or "",
        "last_classification": state.get("last_classification") or "",
        "last_raw_code": state.get("last_raw_code") or "",
        "last_raw_message": state.get("last_raw_message") or "",
        "cooldown_until": cooldown_until,
        "cooldown_remaining": remaining,
        "unlock_at": unlock_at,
        "codex_cooldown_until": codex_cooldown_until,
        "codex_cooldown_remaining": codex_remaining,
        "codex_primary_used_percent": state.get("codex_primary_used_percent"),
        "codex_primary_window_minutes": state.get("codex_primary_window_minutes"),
        "codex_primary_resets_in_seconds": state.get("codex_primary_resets_in_seconds"),
        "codex_secondary_used_percent": state.get("codex_secondary_used_percent"),
        "codex_secondary_window_minutes": state.get("codex_secondary_window_minutes"),
        "codex_secondary_resets_in_seconds": state.get("codex_secondary_resets_in_seconds"),
        "inflight": inflight,
        "updated_at": state.get("updated_at"),
    }


def _state_for_candidate(label: str, account_id: str | None = None) -> Dict[str, Any]:
    state = _state_for_label(label)
    account_cooldown_until = _get_account_cooldown(str(account_id or "").strip())
    if account_cooldown_until > float(state.get("cooldown_until") or 0.0):
        now = time.time()
        state["cooldown_until"] = account_cooldown_until
        state["cooldown_remaining"] = max(0, int(account_cooldown_until - now))
        state["unlock_at"] = datetime.datetime.fromtimestamp(account_cooldown_until, datetime.timezone.utc).isoformat()
        if not state.get("last_classification"):
            state["last_classification"] = "rate_limited"
    return state


def _auth_record_from_obj(
    auth_obj: Dict[str, Any],
    *,
    label: str,
    source: str,
) -> Dict[str, Any]:
    access_token, account_id, id_token, refresh_token, last_refresh = _extract_tokens_from_auth_obj(auth_obj)
    if not isinstance(account_id, str) or not account_id:
        account_id = _derive_account_id(id_token)
    state = _state_for_candidate(label, account_id)
    id_claims = parse_jwt_claims(id_token) or {}
    access_claims = parse_jwt_claims(access_token) or {}
    plan_raw = (access_claims.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type") or ""
    org_id, project_id = _derive_workspace_fields(id_token, access_token, auth_obj)
    workspace_display = " / ".join([part for part in (org_id, project_id) if part])
    if not workspace_display:
        source_parent = os.path.basename(os.path.dirname(source)) if isinstance(source, str) and source else ""
        workspace_display = source_parent or label or "-"
    return {
        "label": label,
        "source": source,
        "account_id": _compact_account_id(account_id),
        "org_id": org_id,
        "project_id": project_id,
        "workspace_display": workspace_display,
        "email": id_claims.get("email") or id_claims.get("preferred_username") or "",
        "plan": str(plan_raw).lower() if isinstance(plan_raw, str) else "",
        "last_refresh": last_refresh if isinstance(last_refresh, str) else "",
        "has_access_token": bool(isinstance(access_token, str) and access_token),
        "has_refresh_token": bool(isinstance(refresh_token, str) and refresh_token),
        "has_id_token": bool(isinstance(id_token, str) and id_token),
        **state,
    }


def get_chatgpt_auth_records(*, include_quarantined: bool = False) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    auth_files = _known_auth_file_paths(include_quarantined=include_quarantined)
    explicit_auth_files = _has_explicit_auth_files_config()
    if auth_files:
        for idx, path in enumerate(auth_files):
            auth_obj = _read_json_file(path)
            dirname = os.path.basename(os.path.dirname(path))
            filename = os.path.basename(path)
            label = f"{dirname}/{filename}" if dirname else (filename or f"file-{idx + 1}")
            if not isinstance(auth_obj, dict):
                records.append(
                    {
                        "label": label,
                        "source": path,
                        "error": "invalid auth file",
                        **_state_for_label(label),
                    }
                )
                continue
            records.append(_auth_record_from_obj(auth_obj, label=label, source=path))
        return records

    if explicit_auth_files:
        return records

    pool_path = _find_auth_file_path("auth_pool.json")
    if pool_path:
        raw_pool = _read_raw_json_file(pool_path)
        accounts = _extract_pool_accounts(raw_pool) if isinstance(raw_pool, (dict, list)) else []
        for idx, account_obj in enumerate(accounts):
            label = ""
            for key in ("name", "alias", "label"):
                value = account_obj.get(key)
                if isinstance(value, str) and value.strip():
                    label = value.strip()
                    break
            if not label:
                label = f"pool-{idx + 1}"
            records.append(_auth_record_from_obj(account_obj, label=label, source=f"{pool_path}#{idx + 1}"))
        if records:
            return records

    default_path = _find_auth_file_path("auth.json")
    default_auth = read_auth_file()
    if isinstance(default_auth, dict):
        records.append(_auth_record_from_obj(default_auth, label="default", source=default_path or "auth.json"))
    return records


def _runtime_candidate_record(candidate: Dict[str, Any], records_by_label: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    label = str(candidate.get("label") or "").strip()
    source_path = str(candidate.get("source_path") or "").strip()
    account_id = str(candidate.get("account_id") or "").strip()
    record = dict(records_by_label.get(label) or {})
    result = {
        "label": label,
        "source": record.get("source") or source_path,
        "source_kind": str(candidate.get("source_kind") or "").strip(),
        "source_index": candidate.get("source_index"),
        "account_id": _compact_account_id(account_id),
        "workspace_display": record.get("workspace_display") or "",
        "email": record.get("email") or "",
        "plan": record.get("plan") or "",
        "has_access_token": bool(record.get("has_access_token")),
        "has_refresh_token": bool(record.get("has_refresh_token")),
        "has_id_token": bool(record.get("has_id_token")),
        "sticky_bound": False,
        **_state_for_candidate(label, account_id),
    }
    with _AUTH_SESSION_STICKY_LOCK:
        _prune_chatgpt_auth_session_bindings()
        sticky_count = 0
        for binding in _AUTH_SESSION_STICKY.values():
            if not isinstance(binding, dict):
                continue
            binding_label = str(binding.get("label") or "").strip()
            binding_source_path = str(binding.get("source_path") or "").strip()
            if (binding_label and binding_label == label) or (binding_source_path and binding_source_path == source_path):
                sticky_count += 1
        result["sticky_bound"] = sticky_count > 0
        result["sticky_sessions"] = sticky_count
    return result


def _runtime_excluded_reason(record: Dict[str, Any]) -> str:
    classification = str(record.get("last_classification") or "").strip().lower()
    raw_code = str(record.get("last_raw_code") or "").strip().lower()
    if classification == "account_invalid" or raw_code == "deactivated_workspace":
        return "account_invalid"
    if int(record.get("codex_cooldown_remaining") or 0) > 0:
        return "codex_rate_limited"
    if int(record.get("cooldown_remaining") or 0) > 0:
        return "cooldown"
    if not bool(record.get("has_access_token")):
        return "missing_access_token"
    return "not_selected"


def get_chatgpt_runtime_candidate_records(ensure_fresh: bool = True) -> Dict[str, Any]:
    records = get_chatgpt_auth_records(include_quarantined=True)
    records_by_label = {
        str(record.get("label") or "").strip(): record
        for record in records
        if isinstance(record, dict) and str(record.get("label") or "").strip()
    }
    candidates = get_effective_chatgpt_auth_candidates(ensure_fresh=ensure_fresh)
    candidate_labels = {
        str(candidate.get("label") or "").strip()
        for candidate in candidates
        if isinstance(candidate, dict) and str(candidate.get("label") or "").strip()
    }
    runtime_candidates = [
        _runtime_candidate_record(candidate, records_by_label)
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    excluded_records = []
    for record in records:
        if not isinstance(record, dict):
            continue
        label = str(record.get("label") or "").strip()
        if label and label in candidate_labels:
            continue
        excluded = dict(record)
        excluded["excluded_reason"] = _runtime_excluded_reason(record)
        excluded_records.append(excluded)
    return {
        "count": len(runtime_candidates),
        "rawCount": len(records),
        "stickyEnabled": _session_sticky_enabled(),
        "stickyTtlSeconds": _session_sticky_ttl_seconds(),
        "candidates": runtime_candidates,
        "excluded": excluded_records,
    }


def sweep_invalid_chatgpt_auth_candidates() -> Dict[str, Any]:
    records = get_chatgpt_auth_records()
    scanned = 0
    removed = 0
    details: List[Dict[str, Any]] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        scanned += 1
        reason = _runtime_excluded_reason(record)
        if reason != "account_invalid":
            continue

        label = str(record.get("label") or "").strip()
        source = str(record.get("source") or "").strip()
        candidate: Dict[str, Any] = {
            "label": label,
            "account_id": "",
            "source_kind": "auth_file",
            "source_path": source,
            "source_index": None,
        }
        if "#" in source and not source.lower().endswith("auth.json"):
            pool_path, _, index_text = source.partition("#")
            candidate["source_kind"] = "auth_pool"
            candidate["source_path"] = pool_path
            try:
                candidate["source_index"] = max(0, int(index_text) - 1)
            except Exception:
                candidate["source_index"] = None
        elif source:
            auth_obj = _read_json_file(source)
            if isinstance(auth_obj, dict):
                candidate["account_id"] = _account_id_from_auth_obj(auth_obj)

        ok = remove_chatgpt_auth_candidate(
            candidate,
            reason=str(record.get("last_raw_message") or record.get("last_error") or "Invalid account").strip(),
        )
        if ok:
            removed += 1
        details.append(
            {
                "label": label,
                "source": source,
                "reason": reason,
                "removed": bool(ok),
            }
        )

    return {
        "ok": True,
        "scanned": scanned,
        "removed": removed,
        "details": details,
        "auth_files": os.environ.get("CHATGPT_LOCAL_AUTH_FILES", ""),
        "runtime": get_chatgpt_runtime_candidate_records(),
    }


def _probe_chatgpt_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    access_token = str(candidate.get("access_token") or "").strip()
    account_id = str(candidate.get("account_id") or "").strip()
    if not access_token or not account_id:
        return build_error_info(
            source="probe",
            phase="probe",
            raw_status=401,
            raw_message="Missing ChatGPT credentials",
            raw_body={"message": "Missing ChatGPT credentials"},
            category_override="account_invalid",
        )

    probe_payload = {
        "model": "gpt-5.4-mini",
        "instructions": "You are a helpful assistant.",
        "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Reply with exactly: OK"}]}],
        "tools": [],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "store": False,
        "stream": True,
        "max_output_tokens": 1,
        "prompt_cache_key": f"probe-{secrets.token_hex(8)}",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "session_id": f"probe-{secrets.token_hex(8)}",
    }

    try:
        response = requests.post(
            CHATGPT_RESPONSES_URL,
            headers=headers,
            json=probe_payload,
            stream=True,
            timeout=90,
        )
    except requests.RequestException as exc:
        return build_error_info(
            source="probe",
            phase="probe",
            raw_status=502,
            raw_message=str(exc),
            raw_body={"exception": str(exc)},
        )

    try:
        if int(response.status_code or 0) < 400:
            return build_error_info(
                source="probe",
                phase="probe",
                raw_status=int(response.status_code or 200),
                raw_message="ok",
                raw_body={"message": "ok"},
                category_override="ready",
            )
        return error_info_from_http_response("probe", "probe", response)
    finally:
        try:
            response.close()
        except Exception:
            pass


def probe_chatgpt_auth_candidates_and_quarantine_invalid() -> Dict[str, Any]:
    records = get_chatgpt_auth_records(include_quarantined=True)
    scanned = 0
    quarantined = 0
    details: List[Dict[str, Any]] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        scanned += 1
        label = str(record.get("label") or "").strip()
        source = str(record.get("source") or "").strip()
        try:
            auth_obj = _read_json_file(source) if source else None
            candidate = None
            changed = False
            if isinstance(auth_obj, dict):
                candidate, changed = _candidate_from_auth_obj(
                    auth_obj,
                    label=label,
                    ensure_fresh=True,
                    source_kind="auth_file",
                    source_path=source,
                )
                if changed and source:
                    _write_json_file(source, auth_obj)

            if candidate is None:
                info = build_error_info(
                    source="probe",
                    phase="probe",
                    raw_status=401,
                    raw_message="Missing ChatGPT credentials",
                    raw_body={"message": "Missing ChatGPT credentials"},
                    category_override="account_invalid",
                )
            else:
                info = _probe_chatgpt_candidate(candidate)

            classification = classify_error(info)
            status = int(info.get("raw_status") or 0) if isinstance(info.get("raw_status"), int) else None
            if isinstance(status, int) and 200 <= status < 400:
                classification = "ready"
            message = str(info.get("raw_message") or "").strip()
            raw_code = str(info.get("raw_code") or "").strip()

            detail = {
                "label": label,
                "source": source,
                "status": status,
                "classification": classification,
                "raw_code": raw_code,
                "message": message,
                "quarantined": False,
            }

            if classification == "account_invalid":
                quarantine_candidate = candidate or {
                    "label": label,
                    "account_id": "",
                    "source_kind": "auth_file",
                    "source_path": source,
                    "source_index": None,
                }
                if quarantine_chatgpt_auth_candidate(quarantine_candidate, reason=message or raw_code or "Invalid account"):
                    quarantined += 1
                    detail["quarantined"] = True
            elif candidate is not None:
                if isinstance(status, int) and 200 <= status < 400:
                    mark_chatgpt_auth_result(
                        label,
                        success=True,
                        status_code=status,
                        account_id=str(candidate.get("account_id") or "").strip(),
                    )
                else:
                    handle_chatgpt_candidate_failure(candidate, info)
        except Exception as exc:
            detail = {
                "label": label,
                "source": source,
                "status": 500,
                "classification": "probe_internal_error",
                "raw_code": "probe_internal_error",
                "message": str(exc),
                "quarantined": False,
            }

        details.append(detail)

    return {
        "ok": True,
        "scanned": scanned,
        "quarantined": quarantined,
        "details": details,
        "runtime": get_chatgpt_runtime_candidate_records(),
    }


def _parse_auth_files_env(*, include_quarantined: bool = False) -> List[str]:
    raw = (os.getenv("CHATGPT_LOCAL_AUTH_FILES") or "").strip()
    paths: List[str] = []
    if raw:
        for part in raw.split(","):
            path = part.strip()
            if path and path not in paths:
                paths.append(path)

    if _has_explicit_auth_files_config():
        if include_quarantined:
            return paths
        quarantined = {_canonical_auth_path(item) for item in _quarantined_auth_files()}
        if not quarantined:
            return paths
        return [path for path in paths if _canonical_auth_path(path) not in quarantined]

    roots: List[str] = []
    explicit_root = (os.getenv("CHATMOCK_DASHBOARD_AUTH_DIR") or "").strip()
    if explicit_root:
        roots.append(explicit_root)
    data_dir = (os.getenv("CHATMOCK_DATA_DIR") or "").strip()
    if data_dir:
        roots.append(os.path.join(data_dir, "accounts"))
    for path in list(paths):
        expanded = os.path.expanduser(path)
        if os.path.basename(expanded) != "auth.json":
            continue
        parent = os.path.dirname(expanded)
        grandparent = os.path.dirname(parent)
        if os.path.basename(parent).startswith("acc") and grandparent:
            roots.append(grandparent)

    for root in roots:
        if not root:
            continue
        for discovered in sorted(glob.glob(os.path.join(os.path.expanduser(root), "acc*/auth.json"))):
            if discovered not in paths:
                paths.append(discovered)
    if include_quarantined:
        return paths
    quarantined = {_canonical_auth_path(item) for item in _quarantined_auth_files()}
    if not quarantined:
        return paths
    return [path for path in paths if _canonical_auth_path(path) not in quarantined]
    return paths


def _extract_pool_accounts(raw_pool: Dict[str, Any] | List[Any]) -> List[Dict[str, Any]]:
    if isinstance(raw_pool, list):
        return [entry for entry in raw_pool if isinstance(entry, dict)]
    if isinstance(raw_pool, dict):
        accounts = raw_pool.get("accounts")
        if isinstance(accounts, list):
            return [entry for entry in accounts if isinstance(entry, dict)]
    return []


def sse_translate_chat(
    upstream,
    model: str,
    created: int,
    verbose: bool = False,
    vlog=None,
    reasoning_compat: str = "current",
    *,
    include_usage: bool = False,
    on_response_completed=None,
):
    response_id = "chatcmpl-stream"
    compat = normalize_reasoning_compat(reasoning_compat)
    think_open = False
    think_closed = False
    saw_output = False
    sent_stop_chunk = False
    sent_tool_finish = False
    saw_any_summary = False
    pending_summary_paragraph = False
    upstream_usage = None
    has_visible_output = False
    emitted_output_text = ""
    ws_state: dict[str, Any] = {}
    ws_index: dict[str, int] = {}
    ws_next_index: int = 0
    saw_completed = False
    
    def _serialize_tool_args(eff_args: Any) -> str:
        """
        Serialize tool call arguments with proper JSON handling.
        
        Args:
            eff_args: Arguments to serialize (dict, list, str, or other)
            
        Returns:
            JSON string representation of the arguments
        """
        if isinstance(eff_args, (dict, list)):
            return json.dumps(eff_args)
        elif isinstance(eff_args, str):
            try:
                parsed = json.loads(eff_args)
                if isinstance(parsed, (dict, list)):
                    return json.dumps(parsed) 
                else:
                    return json.dumps({"query": eff_args})  
            except (json.JSONDecodeError, ValueError):
                return json.dumps({"query": eff_args})
        else:
            return "{}"
    
    try:
        try:
            line_iterator = upstream.iter_lines(decode_unicode=False)
        except requests.exceptions.ChunkedEncodingError as e:
            if verbose and vlog:
                vlog(f"Failed to start stream: {e}")
            yield b"data: [DONE]\n\n"
            return

        for raw in line_iterator:
            try:
                if not raw:
                    continue
                line = (
                    raw.decode("utf-8", errors="ignore")
                    if isinstance(raw, (bytes, bytearray))
                    else raw
                )
                if verbose and vlog:
                    vlog(line)
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    evt = json.loads(data)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
            except (
                requests.exceptions.ChunkedEncodingError,
                ConnectionError,
                BrokenPipeError,
            ) as e:
                # Connection interrupted mid-stream - end gracefully
                if verbose and vlog:
                    vlog(f"Stream interrupted: {e}")
                yield b"data: [DONE]\n\n"
                return
            kind = evt.get("type")
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id

            if isinstance(kind, str) and ("web_search_call" in kind):
                continue

            if kind == "response.output_text.delta":
                delta = evt.get("delta") or ""
                if compat == "think-tags" and think_open and not think_closed:
                    close_chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": "</think>"}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(close_chunk)}\n\n".encode("utf-8")
                    think_open = False
                    think_closed = True
                saw_output = True
                has_visible_output = True
                emitted_output_text += delta
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.output_item.done":
                item = evt.get("item") or {}
                if isinstance(item, dict) and item.get("type") == "function_call":
                    call_id = item.get("call_id") or item.get("id") or ""
                    name = item.get("name") or ""
                    raw_args = item.get("arguments")
                    if isinstance(raw_args, dict):
                        try:
                            ws_state.setdefault(call_id, {}).update(raw_args)
                        except Exception:
                            pass
                    eff_args = ws_state.get(call_id, raw_args if isinstance(raw_args, (dict, list, str)) else {})
                    try:
                        args = _serialize_tool_args(eff_args)
                    except Exception:
                        args = "{}"
                    if call_id not in ws_index:
                        ws_index[call_id] = ws_next_index
                        ws_next_index += 1
                    _idx = ws_index.get(call_id, 0)
                    if isinstance(call_id, str) and isinstance(name, str) and isinstance(args, str):
                        delta_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": _idx,
                                                "id": call_id,
                                                "type": "function",
                                                "function": {"name": name, "arguments": args},
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(delta_chunk)}\n\n".encode("utf-8")
                        has_visible_output = True

                        finish_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                        }
                        yield f"data: {json.dumps(finish_chunk)}\n\n".encode("utf-8")
                        sent_stop_chunk = True
                        sent_tool_finish = True
                else:
                    emitted_output_text, missing_delta = merge_response_text(
                        emitted_output_text,
                        extract_response_output_text(item),
                    )
                    if missing_delta:
                        saw_output = True
                        has_visible_output = True
                        chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": missing_delta}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.reasoning_summary_part.added":
                if compat in ("think-tags", "o3"):
                    if saw_any_summary:
                        pending_summary_paragraph = True
                    else:
                        saw_any_summary = True
            elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                delta_txt = evt.get("delta") or ""
                if compat == "o3":
                    if kind == "response.reasoning_summary_text.delta" and pending_summary_paragraph:
                        nl_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"reasoning": {"content": [{"type": "text", "text": "\n"}]}},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(nl_chunk)}\n\n".encode("utf-8")
                        pending_summary_paragraph = False
                    chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"reasoning": {"content": [{"type": "text", "text": delta_txt}]}},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                elif compat == "think-tags":
                    if not think_open and not think_closed:
                        open_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": "<think>"}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(open_chunk)}\n\n".encode("utf-8")
                        think_open = True
                    if think_open and not think_closed:
                        if kind == "response.reasoning_summary_text.delta" and pending_summary_paragraph:
                            nl_chunk = {
                                "id": response_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{"index": 0, "delta": {"content": "\n"}, "finish_reason": None}],
                            }
                            yield f"data: {json.dumps(nl_chunk)}\n\n".encode("utf-8")
                            pending_summary_paragraph = False
                        content_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": delta_txt}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(content_chunk)}\n\n".encode("utf-8")
                else:
                    if kind == "response.reasoning_summary_text.delta":
                        chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"reasoning_summary": delta_txt, "reasoning": delta_txt},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                    else:
                        chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {"index": 0, "delta": {"reasoning": delta_txt}, "finish_reason": None}
                            ],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif isinstance(kind, str) and kind.endswith(".done") and kind not in (
                "response.output_text.done",
                "response.content_part.done",
            ):
                pass
            elif kind == "response.output_text.done":
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    evt.get("text") or "",
                )
                if missing_delta:
                    saw_output = True
                    has_visible_output = True
                    chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": missing_delta}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                sent_stop_chunk = True
            elif kind == "response.content_part.done":
                part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    part.get("text") or "",
                )
                if missing_delta:
                    saw_output = True
                    has_visible_output = True
                    chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": missing_delta}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                if not has_visible_output and should_retry_next_candidate(error_info):
                    _mark_upstream_failure(upstream, error_info)
                    raise RetryableStreamError(error_info)
                _mark_upstream_failure(upstream, error_info)
                chunk = {"error": normalized_error_payload(error_info)}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                break
            elif kind == "response.completed":
                saw_completed = True
                m = extract_responses_usage_from_event(evt)
                if m:
                    upstream_usage = to_chat_usage(m)
                if callable(on_response_completed):
                    response_obj = evt.get("response")
                    if isinstance(response_obj, dict):
                        try:
                            on_response_completed(response_obj, upstream)
                        except Exception:
                            pass
                if compat == "think-tags" and think_open and not think_closed:
                    close_chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": "</think>"}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(close_chunk)}\n\n".encode("utf-8")
                    think_open = False
                    think_closed = True
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    extract_response_output_text(evt.get("response")),
                )
                if missing_delta:
                    saw_output = True
                    has_visible_output = True
                    chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": missing_delta}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                if not sent_stop_chunk:
                    chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                    sent_stop_chunk = True

                if include_usage and upstream_usage:
                    try:
                        usage_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                            "usage": upstream_usage,
                        }
                        yield f"data: {json.dumps(usage_chunk)}\n\n".encode("utf-8")
                    except Exception:
                        pass
                yield b"data: [DONE]\n\n"
                break
        if not saw_completed:
            error_info = normalized_error_payload(
                build_error_info(
                    source=getattr(upstream, "chatmock_source", "upstream"),
                    phase="stream",
                    raw_status=int(getattr(upstream, "status_code", 502) or 502),
                    raw_message="stream ended before response.completed",
                    raw_body={"message": "stream ended before response.completed"},
                )
            )
            if not has_visible_output and not sent_tool_finish:
                if should_retry_next_candidate(error_info):
                    _mark_upstream_failure(upstream, error_info)
                    raise RetryableStreamError(error_info)
                _mark_upstream_failure(upstream, error_info)
                chunk = {"error": error_info}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return
            if not sent_stop_chunk:
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        if not saw_completed:
            error_info = normalized_error_payload(
                build_error_info(
                    source=getattr(upstream, "chatmock_source", "upstream"),
                    phase="stream",
                    raw_status=int(getattr(upstream, "status_code", 502) or 502),
                    raw_message="stream ended before response.completed",
                    raw_body={"message": "stream ended before response.completed"},
                )
            )
            if not has_visible_output and not sent_tool_finish:
                if should_retry_next_candidate(error_info):
                    _mark_upstream_failure(upstream, error_info)
                    raise RetryableStreamError(error_info)
                _mark_upstream_failure(upstream, error_info)
                chunk = {"error": error_info}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return
            if not sent_stop_chunk:
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
    finally:
        upstream.close()


def sse_translate_text(
    upstream,
    model: str,
    created: int,
    verbose: bool = False,
    vlog=None,
    *,
    include_usage: bool = False,
    on_response_completed=None,
):
    response_id = "cmpl-stream"
    upstream_usage = None
    has_visible_output = False
    emitted_output_text = ""
    saw_completed = False
    
    try:
        for raw_line in upstream.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
            if verbose and vlog:
                vlog(line)
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):].strip()
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    chunk = {
                        "id": response_id,
                        "object": "text_completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "text": "", "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                continue
            try:
                evt = json.loads(data)
            except Exception:
                continue
            kind = evt.get("type")
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if kind == "response.output_text.delta":
                delta_text = evt.get("delta") or ""
                has_visible_output = has_visible_output or bool(delta_text)
                emitted_output_text += delta_text
                chunk = {
                    "id": response_id,
                    "object": "text_completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "text": delta_text, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.output_text.done":
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    evt.get("text") or "",
                )
                if missing_delta:
                    has_visible_output = True
                    chunk = {
                        "id": response_id,
                        "object": "text_completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "text": missing_delta, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                chunk = {
                    "id": response_id,
                    "object": "text_completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "text": "", "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.content_part.done":
                part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    part.get("text") or "",
                )
                if missing_delta:
                    has_visible_output = True
                    chunk = {
                        "id": response_id,
                        "object": "text_completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "text": missing_delta, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.output_item.done":
                item = evt.get("item") or {}
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    extract_response_output_text(item),
                )
                if missing_delta:
                    has_visible_output = True
                    chunk = {
                        "id": response_id,
                        "object": "text_completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "text": missing_delta, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.completed":
                saw_completed = True
                m = extract_responses_usage_from_event(evt)
                if m:
                    upstream_usage = to_chat_usage(m)
                response_obj = evt.get("response")
                if callable(on_response_completed) and isinstance(response_obj, dict):
                    try:
                        on_response_completed(response_obj, upstream)
                    except Exception:
                        pass
                emitted_output_text, missing_delta = merge_response_text(
                    emitted_output_text,
                    extract_response_output_text(response_obj),
                )
                if missing_delta:
                    has_visible_output = True
                    chunk = {
                        "id": response_id,
                        "object": "text_completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "text": missing_delta, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                if include_usage and upstream_usage:
                    try:
                        usage_chunk = {
                            "id": response_id,
                            "object": "text_completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "text": "", "finish_reason": None}],
                            "usage": upstream_usage,
                        }
                        yield f"data: {json.dumps(usage_chunk)}\n\n".encode("utf-8")
                    except Exception:
                        pass
                yield b"data: [DONE]\n\n"
                break
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                if not has_visible_output and should_retry_next_candidate(error_info):
                    _mark_upstream_failure(upstream, error_info)
                    raise RetryableStreamError(error_info)
                _mark_upstream_failure(upstream, error_info)
                chunk = {"error": normalized_error_payload(error_info)}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                break
        if not saw_completed:
            error_info = normalized_error_payload(
                build_error_info(
                    source=getattr(upstream, "chatmock_source", "upstream"),
                    phase="stream",
                    raw_status=int(getattr(upstream, "status_code", 502) or 502),
                    raw_message="stream ended before response.completed",
                    raw_body={"message": "stream ended before response.completed"},
                )
            )
            if not has_visible_output:
                if should_retry_next_candidate(error_info):
                    _mark_upstream_failure(upstream, error_info)
                    raise RetryableStreamError(error_info)
                _mark_upstream_failure(upstream, error_info)
                chunk = {"error": error_info}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return
            chunk = {
                "id": response_id,
                "object": "text_completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "text": "", "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        if not saw_completed:
            error_info = normalized_error_payload(
                build_error_info(
                    source=getattr(upstream, "chatmock_source", "upstream"),
                    phase="stream",
                    raw_status=int(getattr(upstream, "status_code", 502) or 502),
                    raw_message="stream ended before response.completed",
                    raw_body={"message": "stream ended before response.completed"},
                )
            )
            if not has_visible_output and not sent_tool_finish:
                if should_retry_next_candidate(error_info):
                    _mark_upstream_failure(upstream, error_info)
                    raise RetryableStreamError(error_info)
                _mark_upstream_failure(upstream, error_info)
                chunk = {"error": error_info}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return
            if not sent_stop_chunk:
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        if not saw_completed:
            error_info = normalized_error_payload(
                build_error_info(
                    source=getattr(upstream, "chatmock_source", "upstream"),
                    phase="stream",
                    raw_status=int(getattr(upstream, "status_code", 502) or 502),
                    raw_message="stream ended before response.completed",
                    raw_body={"message": "stream ended before response.completed"},
                )
            )
            if not has_visible_output and not sent_tool_finish:
                if should_retry_next_candidate(error_info):
                    _mark_upstream_failure(upstream, error_info)
                    raise RetryableStreamError(error_info)
                _mark_upstream_failure(upstream, error_info)
                chunk = {"error": error_info}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return
            if not sent_stop_chunk:
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
    finally:
        upstream.close()
