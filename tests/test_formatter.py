from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bugreel_context import BugReelContext
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


def test_render_prompt_includes_bugreel_context(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Relato:\n{{TRANSCRICAO}}", encoding="utf-8")

    formatter = Formatter(
        api_key="test",
        model="gemini-2.5-flash",
        prompt_path=prompt_path,
        timeout_seconds=10,
    )

    rendered = formatter.render_prompt(
        "Falha ao abrir o menu.",
        bugreel_context=BugReelContext(
            url="https://bugreel.local/report/abc123",
            base_url="https://bugreel.local",
            source_kind="report",
            source_id="abc123",
        ),
    )

    assert "Contexto adicional do BugReel" in rendered
    assert "https://bugreel.local/report/abc123" in rendered


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


def test_inject_bugreel_evidence_keeps_environment_section_clean() -> None:
    content = (
        "[Web/App] login falha\n\n"
        "Resumo:\ntexto\n\n"
        "**Comportamento atual:**\n- a\n\n"
        "**Comportamento esperado:**\n- b\n\n"
        "**Passos para reprodução:**\n1. x\n\n"
        "**Evidências:**\n"
        "Vídeo do BugReel e arquivos locais armazenados em C:\\Users\\lucas\\AppData\\Local\\Temp\\bug_voice_reporter_bugreel_123.\n\n"
        "**Informações do ambiente:**\n"
        "- Dispositivo: Não informado\n"
        "- Versão: Não informado\n"
        "- Sistema operacional: Windows (Win32)\n"
        "- Navegador: Não informado\n"
    )
    context = BugReelContext(
        url="http://localhost:3500/report/abc",
        base_url="http://localhost:3500",
        source_kind="report",
        source_id="abc",
    )

    injected = Formatter._inject_bugreel_evidence(content, context)

    assert "AppData\\Local\\Temp" not in injected
    assert "Evidência em vídeo capturada no BugReel (anexe com CTRL+SHIFT+V)." in injected
    assert "**Informações do ambiente:**" in injected
