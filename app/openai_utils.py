from __future__ import annotations

from typing import Any

import requests

from app.gemini_utils import sanitize_sensitive_text


def extract_text_from_openai_response_json(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = content.get("text")
                if text:
                    chunks.append(str(text))

    return "\n".join(chunks).strip()


def format_openai_http_error(context: str, response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    message = sanitize_sensitive_text(_read(error, "message"))
    code = _read(error, "code")
    error_type = _read(error, "type")

    if response.status_code == 401:
        return "Falha de autenticação na OpenAI. Verifique a OPENAI_API_KEY."
    if response.status_code == 403:
        return "A OPENAI_API_KEY não tem permissão para esta operação."
    if response.status_code == 404:
        return f"O modelo usado para {context} não foi encontrado. Verifique o .env."
    if response.status_code == 429:
        return (
            "A OpenAI bloqueou a requisição por limite de uso da chave ou da conta. "
            "Aguarde um pouco ou verifique os limites do projeto."
        )
    if message:
        return f"Falha na OpenAI durante {context}: {message}"
    if code:
        return f"Falha na OpenAI durante {context}. code={code}"
    if error_type:
        return f"Falha na OpenAI durante {context}. type={error_type}"
    return f"Falha na OpenAI durante {context}. Tente novamente."


def _read(data: Any, key: str) -> str | None:
    if isinstance(data, dict):
        value = data.get(key)
        return str(value) if value is not None else None
    return None
