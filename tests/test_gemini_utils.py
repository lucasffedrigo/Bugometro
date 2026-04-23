from types import SimpleNamespace

from app.gemini_utils import (
    extract_text_from_response_json,
    format_gemini_http_error,
    sanitize_sensitive_text,
)


class FakeResponse(SimpleNamespace):
    def json(self) -> dict:
        return self.payload


def test_extract_text_from_response_json_reads_parts() -> None:
    data = {
        "candidates": [
            {"content": {"parts": [{"text": "Linha 1"}, {"text": "Linha 2"}]}}
        ]
    }

    assert extract_text_from_response_json(data) == "Linha 1\nLinha 2"


def test_formats_rate_limit_error() -> None:
    response = FakeResponse(
        status_code=429,
        payload={"error": {"message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}},
    )

    message = format_gemini_http_error("a transcrição do áudio", response)

    assert "limite de uso" in message


def test_sanitize_sensitive_text_redacts_keys() -> None:
    text = "Falha com chave AIzaSyAABBCCDDEEFFGGHHIIJJKKLLMMNN e sk-proj-1234567890abcdef"

    sanitized = sanitize_sensitive_text(text)

    assert "AIza" not in sanitized
    assert "sk-proj-" not in sanitized
    assert "REDACTED" in sanitized
