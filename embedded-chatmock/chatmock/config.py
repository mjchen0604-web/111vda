from __future__ import annotations

import os
import sys
from pathlib import Path


CLIENT_ID_DEFAULT = os.getenv("CHATGPT_LOCAL_CLIENT_ID") or "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_ISSUER_DEFAULT = os.getenv("CHATGPT_LOCAL_ISSUER") or "https://auth.openai.com"
OAUTH_TOKEN_URL = f"{OAUTH_ISSUER_DEFAULT}/oauth/token"

CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


def _read_prompt_text(filename: str) -> str | None:
    candidates = [
        Path(__file__).parent.parent / filename,
        Path(__file__).parent / filename,
        Path(getattr(sys, "_MEIPASS", "")) / filename if getattr(sys, "_MEIPASS", None) else None,
        Path.cwd() / filename,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            if candidate.exists():
                content = candidate.read_text(encoding="utf-8")
                if isinstance(content, str) and content.strip():
                    return content
        except Exception:
            continue
    return None


def read_base_instructions() -> str:
    content = _read_prompt_text("prompt.md")
    if content is None:
        raise FileNotFoundError("Failed to read prompt.md; expected adjacent to package or CWD.")
    return content


def read_gpt5_codex_instructions(fallback: str) -> str:
    content = _read_prompt_text("prompt_gpt5_codex.md")
    return content if isinstance(content, str) and content.strip() else fallback


def read_claude_opus_instructions(fallback: str) -> str:
    content = _read_prompt_text("prompt_claude_opus_46.md")
    return content if isinstance(content, str) and content.strip() else fallback


BASE_INSTRUCTIONS = read_base_instructions()
GPT5_CODEX_INSTRUCTIONS = read_gpt5_codex_instructions(BASE_INSTRUCTIONS)
CLAUDE_OPUS_INSTRUCTIONS = read_claude_opus_instructions(BASE_INSTRUCTIONS)


def should_use_gpt5_codex_instructions(model: str | None) -> bool:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    return "codex" in normalized or normalized.startswith("gpt-5.4") or normalized.startswith("gpt5.4")


def should_use_claude_opus_instructions(model: str | None) -> bool:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith("claude-opus-4-6") or normalized.startswith("claude-sonnet-4-5") or normalized.startswith("claude-haiku-4-5")
