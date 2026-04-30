from pathlib import Path

from app.formatter import Formatter
import numpy as np

from app.native_capture import (
    AnnotationModel,
    NativeCaptureContext,
    _draw_cursor_on_frame,
)


def test_annotation_model_keeps_stroke_visible_for_hold_window() -> None:
    model = AnnotationModel(hold_seconds=1.5)
    model.begin(10, 10)
    model.extend(20, 20)
    model.end(now=5.0)

    assert len(model.snapshot(now=6.0)) == 1
    assert model.snapshot(now=6.6) == []
    assert model.completed_count == 1


def test_annotation_model_ignores_click_without_drag() -> None:
    model = AnnotationModel(hold_seconds=1.5)
    model.begin(10, 10)
    model.end(now=5.0)

    assert model.snapshot(now=5.2) == []
    assert model.completed_count == 0


def test_draw_cursor_on_frame_renders_pointer_at_relative_position() -> None:
    frame = np.zeros((48, 48, 3), dtype=np.uint8)

    rendered = _draw_cursor_on_frame(
        frame,
        origin=(100, 200),
        cursor_position=(110, 212),
    )

    assert rendered.sum() > 0
    assert rendered.max() >= 245


def test_draw_cursor_on_frame_ignores_cursor_outside_capture() -> None:
    frame = np.zeros((48, 48, 3), dtype=np.uint8)

    rendered = _draw_cursor_on_frame(
        frame,
        origin=(100, 200),
        cursor_position=(10, 20),
    )

    assert np.array_equal(rendered, frame)


def test_native_capture_context_exposes_prompt_and_files(tmp_path: Path) -> None:
    video = tmp_path / "native_evidence.gif"
    frame = tmp_path / "frame_01.jpg"
    video.write_bytes(b"video")
    frame.write_bytes(b"frame")

    context = NativeCaptureContext(
        target_kind="janela",
        target_title="Cadastro - Producao",
        video_path=video,
        frame_paths=(frame,),
        duration_seconds=9.4,
        capture_fps=10,
        capture_rect=(120, 40, 1440, 900),
        annotation_count=2,
    )

    prompt = context.to_prompt_block()

    assert "Captura nativa local do Windows" in prompt
    assert "Cadastro - Producao" in prompt
    assert "10" in prompt
    assert context.evidence_file_paths() == [video, frame]
    assert context.evidence_lines() == []


def test_formatter_injects_native_capture_evidence(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Relato:\n{{TRANSCRICAO}}", encoding="utf-8")

    formatter = Formatter(
        api_key="test",
        model="gemini-2.5-flash",
        prompt_path=prompt_path,
        timeout_seconds=10,
    )
    video = tmp_path / "native_evidence.gif"
    video.write_bytes(b"video")
    context = NativeCaptureContext(
        target_kind="desktop",
        target_title="Area de trabalho",
        video_path=video,
        duration_seconds=5.0,
        capture_fps=10,
        capture_rect=(0, 0, 1920, 1080),
    )
    content = (
        "[Web] usuario acessa a pagina e encontra falha\n\n"
        "Resumo:\nTexto.\n\n"
        "**Comportamento atual:**\n- Falha.\n\n"
        "**Comportamento esperado:**\n- Sucesso.\n\n"
        "**Passos para reproducao:**\n1. Abrir.\n2. Clicar.\n3. Observar.\n\n"
        "**Evidencias:**\nNao informado\n"
    )

    updated = Formatter._inject_evidence_lines(content, context)

    assert updated == content.strip()
