from pathlib import Path
from types import SimpleNamespace

import pytest

from app.formatter import Formatter, FormattingError


def test_render_prompt_replaces_placeholder(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Relato:\n{{TRANSCRICAO}}", encoding="utf-8")

    formatter = Formatter(
        api_key="test",
        model="gemini-2.5-flash",
        prompt_path=prompt_path,
        timeout_seconds=10,
    )

    rendered = formatter.render_prompt("Botão trava ao clicar no Chrome em produção.")

    assert "{{TRANSCRICAO}}" not in rendered
    assert "Botão trava ao clicar" in rendered
    assert "Contexto técnico detectado" in rendered


def test_render_prompt_rejects_blank_transcription(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("{{TRANSCRICAO}}", encoding="utf-8")

    formatter = Formatter(
        api_key="test",
        model="gemini-2.5-flash",
        prompt_path=prompt_path,
        timeout_seconds=10,
    )

    with pytest.raises(FormattingError):
        formatter.render_prompt("   ")


def test_extract_output_text_reads_dict_candidates() -> None:
    response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Título:\nTeste"},
                        {"text": "Resumo:\nAlgo aconteceu"},
                    ]
                }
            }
        ]
    }

    assert "Resumo" in Formatter.extract_output_text(response)


def test_extract_output_text_reads_text_attribute() -> None:
    response = SimpleNamespace(text="Título:\nTeste")

    assert Formatter.extract_output_text(response) == "Título:\nTeste"


def test_extract_output_text_reads_openai_response_shape() -> None:
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Título:\nTeste"},
                ],
            }
        ]
    }

    assert Formatter.extract_output_text(response) == "Título:\nTeste"
