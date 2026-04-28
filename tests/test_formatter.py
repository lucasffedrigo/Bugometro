from pathlib import Path
from types import SimpleNamespace

import pytest

from app.devtools_mcp_context import DevToolsMcpContext
from app.formatter import Formatter, FormattingError
from app.native_capture import NativeCaptureContext


def test_render_prompt_replaces_placeholder(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Relato:\n{{TRANSCRICAO}}", encoding="utf-8")

    formatter = Formatter(
        api_key="test",
        model="gemini-2.5-flash",
        prompt_path=prompt_path,
        timeout_seconds=10,
    )

    rendered = formatter.render_prompt("Botao trava ao clicar no Chrome em producao.")

    assert "{{TRANSCRICAO}}" not in rendered
    assert "Botao trava ao clicar" in rendered
    assert "Contexto tecnico detectado" in rendered


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


def test_render_prompt_includes_capture_context(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Relato:\n{{TRANSCRICAO}}", encoding="utf-8")
    video_path = tmp_path / "native_evidence.mp4"
    video_path.write_bytes(b"video")

    formatter = Formatter(
        api_key="test",
        model="gemini-2.5-flash",
        prompt_path=prompt_path,
        timeout_seconds=10,
    )

    rendered = formatter.render_prompt(
        "Falha ao abrir o menu.",
        evidence_context=NativeCaptureContext(
            target_kind="janela",
            target_title="Cadastro - Producao",
            video_path=video_path,
            duration_seconds=8.2,
            capture_fps=10,
            capture_rect=(120, 40, 1440, 900),
        ),
    )

    assert "Contexto adicional da captura" in rendered
    assert "Cadastro - Producao" in rendered


def test_extract_output_text_reads_dict_candidates() -> None:
    response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Titulo:\nTeste"},
                        {"text": "Resumo:\nAlgo aconteceu"},
                    ]
                }
            }
        ]
    }

    assert "Resumo" in Formatter.extract_output_text(response)


def test_extract_output_text_reads_text_attribute() -> None:
    response = SimpleNamespace(text="Titulo:\nTeste")

    assert Formatter.extract_output_text(response) == "Titulo:\nTeste"


def test_extract_output_text_reads_openai_response_shape() -> None:
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Titulo:\nTeste"},
                ],
            }
        ]
    }

    assert Formatter.extract_output_text(response) == "Titulo:\nTeste"


def test_inject_evidence_lines_keeps_environment_section_clean() -> None:
    content = (
        "[Web/App] login falha\n\n"
        "Resumo:\ntexto\n\n"
        "**Comportamento atual:**\n- a\n\n"
        "**Comportamento esperado:**\n- b\n\n"
        "**Passos para reproducao:**\n1. x\n\n"
        "**Evidencias:**\n"
        "Video da captura e arquivos locais armazenados em C:\\Users\\lucas\\AppData\\Local\\Temp\\bug_voice_reporter_capture_123.\n\n"
        "**Informacoes do ambiente:**\n"
        "- Dispositivo: Nao informado\n"
        "- Versao: Nao informado\n"
        "- Sistema operacional: Windows (Win32)\n"
        "- Navegador: Nao informado\n"
    )

    class StubEvidenceContext:
        def to_prompt_block(self) -> str:
            return "Captura local"

        def evidence_lines(self) -> list[str]:
            return ["Video local disponivel para anexo via CTRL+SHIFT+V."]

    injected = Formatter._inject_evidence_lines(content, StubEvidenceContext())

    assert "AppData\\Local\\Temp" not in injected
    assert "Video local disponivel para anexo via CTRL+SHIFT+V." in injected
    assert "**Informacoes do ambiente:**" in injected


def test_inject_evidence_lines_handles_accented_evidence_heading() -> None:
    content = (
        "[Web/App] salvar formulario falha\n\n"
        "Resumo:\ntexto\n\n"
        "**Comportamento atual:**\n- a\n\n"
        "**Comportamento esperado:**\n- b\n\n"
        "**Passos para reprodução:**\n1. x\n\n"
        "**Evidências:**\n"
        "Não informado\n\n"
        "**Informações do ambiente:**\n"
        "- Dispositivo: Desktop\n"
        "- Versão: Producao\n"
        "- Sistema operacional: Windows\n"
        "- Navegador: Chrome\n"
    )

    injected = Formatter._inject_evidence_lines(
        content,
        DevToolsMcpContext(
            page_url="https://app.local/checkout",
            performance_lines=("LCP: 3200ms",),
            bottleneck_lines=(
                "LCP alto (3200ms): investigar renderizacao inicial e recursos criticos",
            ),
        ),
    )

    assert "Não informado" not in injected
    assert "DevTools MCP: pagina observada https://app.local/checkout" in injected
    assert "DevTools MCP performance: LCP: 3200ms" in injected
    assert "DevTools MCP gargalo possivel: LCP alto" in injected
