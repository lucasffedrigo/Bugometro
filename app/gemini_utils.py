from __future__ import annotations

import re
from typing import Any

import requests


def extract_text_from_response_json(data: dict[str, Any]) -> str:
    candidates = data.get("candidates", [])
    chunks: list[str] = []
    for candidate in candidates:
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                chunks.append(str(text))
    return "\n".join(chunks).strip()


def format_gemini_http_error(context: str, response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    message = sanitize_sensitive_text(_read(error, "message"))
    status = _read(error, "status")

    if response.status_code == 400 and message and "API key not valid" in message:
        return "A chave do Gemini é inválida. Verifique a GEMINI_API_KEY."
    if response.status_code == 401:
        return "Falha de autenticação no Gemini. Verifique a GEMINI_API_KEY."
    if response.status_code == 403:
        return "A chave do Gemini não tem permissão para esta operação."
    if response.status_code == 404:
        return f"O modelo usado para {context} não foi encontrado. Verifique o .env."
    if response.status_code == 429:
        return (
            "O Gemini bloqueou a requisição por limite de uso da chave ou da conta. "
            "Aguarde um pouco ou verifique os limites do projeto."
        )
    if message:
        return f"Falha no Gemini durante {context}: {message}"
    if status:
        return f"Falha no Gemini durante {context}. status={status}"
    return f"Falha no Gemini durante {context}. Tente novamente."


def sanitize_sensitive_text(value: str | None) -> str | None:
    if value is None:
        return None
    sanitized = re.sub(r"AIza[A-Za-z0-9_-]{20,}", "[GEMINI_API_KEY_REDACTED]", value)
    sanitized = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[OPENAI_API_KEY_REDACTED]", sanitized)
    return sanitized


def _read(data: Any, key: str) -> str | None:
    if isinstance(data, dict):
        value = data.get(key)
        return str(value) if value is not None else None
    return None
