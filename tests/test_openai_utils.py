from types import SimpleNamespace

from app.openai_utils import extract_text_from_openai_response_json, format_openai_http_error


class FakeResponse(SimpleNamespace):
    def json(self) -> dict:
        return self.payload


def test_extract_text_from_openai_response_json_reads_output_text() -> None:
    data = {"output_text": "Título:\nTeste"}

    assert extract_text_from_openai_response_json(data) == "Título:\nTeste"


def test_extract_text_from_openai_response_json_reads_output_messages() -> None:
    data = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Linha 1"},
                    {"type": "output_text", "text": "Linha 2"},
                ],
            }
        ]
    }

    assert extract_text_from_openai_response_json(data) == "Linha 1\nLinha 2"


def test_format_openai_http_error_for_auth() -> None:
    response = FakeResponse(
        status_code=401,
        payload={"error": {"message": "Invalid API key", "type": "invalid_request_error"}},
    )

    message = format_openai_http_error("a formatação do bug report", response)

    assert "OPENAI_API_KEY" in message
