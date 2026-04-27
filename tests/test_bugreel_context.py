from pathlib import Path

from app.bugreel_context import (
    BugReelContext,
    EnrichedBugReelContext,
    extract_bugreel_context,
)
from app.formatter import Formatter


def test_extract_bugreel_context_returns_last_report_url() -> None:
    text = (
        "primeiro https://bugreel.local/report/abc123 "
        "segundo https://bugreel.local/report/xyz987"
    )

    context = extract_bugreel_context(text, base_url="https://bugreel.local")

    assert context is not None
    assert context.url == "https://bugreel.local/report/xyz987"
    assert context.source_kind == "report"
    assert context.source_id == "xyz987"


def test_extract_bugreel_context_supports_recording_url() -> None:
    context = extract_bugreel_context(
        "https://bugreel.local/recording/rec_123",
        base_url="https://bugreel.local",
    )

    assert context is not None
    assert context.source_kind == "recording"
    assert context.source_id == "rec_123"


def test_extract_bugreel_context_rejects_other_host_when_base_url_is_set() -> None:
    text = "https://example.com/report/abc123"

    context = extract_bugreel_context(text, base_url="https://bugreel.local")

    assert context is None


def test_render_prompt_includes_enriched_bugreel_context(tmp_path: Path) -> None:
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
        bugreel_context=EnrichedBugReelContext(
            raw=BugReelContext(
                url="https://bugreel.local/report/abc123",
                base_url="https://bugreel.local",
                source_kind="report",
                source_id="abc123",
            ),
            recording_id="rec-1",
            share_token="abc123",
            report_url="https://bugreel.local/report/abc123",
            video_url="https://bugreel.local/api/recordings/abc123/video",
            title="Título técnico",
            summary="Resumo técnico",
            duration_seconds=12.5,
            transcript_excerpt="Trecho da gravação",
            metadata_lines=("Navegador: Chrome",),
            console_lines=("[error] TypeError",),
            action_lines=("click em botão Salvar",),
            navigation_lines=("Página inicial: https://app.local",),
            frame_lines=("00:03 — Modal quebra",),
            frame_filenames=("001_3.0s.jpg",),
        ),
    )

    assert "Contexto adicional do BugReel" in rendered
    assert "Trecho da gravação" in rendered
    assert "https://bugreel.local/report/abc123" in rendered


def test_inject_bugreel_evidence_replaces_nao_informado() -> None:
    content = (
        "[Web] usuário acessa a página e encontra falha\n\n"
        "Resumo:\nTexto.\n\n"
        "**Comportamento atual:**\n- Falha.\n\n"
        "**Comportamento esperado:**\n- Sucesso.\n\n"
        "**Passos para reprodução:**\n1. Abrir.\n2. Clicar.\n3. Observar.\n\n"
        "**Evidências:**\nNão informado\n"
    )
    bugreel_context = EnrichedBugReelContext(
        raw=BugReelContext(
            url="https://bugreel.local/report/abc123",
            base_url="https://bugreel.local",
            source_kind="report",
            source_id="abc123",
        ),
        recording_id="rec-1",
        share_token="abc123",
        report_url="https://bugreel.local/report/abc123",
        video_url="https://bugreel.local/api/recordings/abc123/video",
        title="Título técnico",
        summary="Resumo técnico",
        duration_seconds=12.5,
        transcript_excerpt="Trecho da gravação",
        local_bundle_dir=Path(r"C:\Temp\bundle"),
        local_files=(Path(r"C:\Temp\bundle\bugreel_video.webm"),),
    )

    updated = Formatter._inject_bugreel_evidence(content, bugreel_context)

    assert "Não informado" not in updated
    assert "- Evidência em vídeo capturada no BugReel (anexe com CTRL+SHIFT+V)." in updated
    assert "Vídeo BugReel: https://bugreel.local/api/recordings/abc123/video" not in updated
    assert "Pacote local de evidências: C:\\Temp\\bundle" not in updated
