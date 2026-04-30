import json
import logging
from pathlib import Path

from app.devtools_mcp_context import (
    DevToolsMcpClient,
    DevToolsMcpContext,
    combine_evidence_contexts,
)
from app.native_capture import NativeCaptureContext


def test_devtools_mcp_client_reads_context_from_file(tmp_path: Path) -> None:
    payload = {
        "context": {
            "page_url": "https://app.local/dashboard",
            "page_title": "Dashboard",
            "browser": "Chrome 135",
            "page_state": "interactive",
            "selected_element": {
                "selector": "#save-button",
                "text": "Salvar",
            },
            "console": {
                "entries": [
                    {"level": "error", "message": "TypeError: x is undefined"}
                ]
            },
            "exceptions": [
                {
                    "name": "TypeError",
                    "message": "Cannot read properties of undefined",
                    "url": "https://app.local/app.js",
                    "lineNumber": 87,
                }
            ],
            "network": {
                "entries": [
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://app.local/api/save",
                            "resourceType": "xhr",
                        },
                        "response": {
                            "status": 500,
                            "encodedDataLength": 1450000,
                        },
                        "durationMs": 1420,
                    }
                ]
            },
            "performance": {
                "lcp": 3200,
                "inp": 260,
                "cls": 0.13,
                "totalBlockingTime": 350,
                "jsHeapUsedSize": 12400000,
                "longTasks": [
                    {"durationMs": 180},
                    {"durationMs": 90},
                ],
                "metrics": [
                    {"name": "ScriptDuration", "value": 1.7},
                    {"name": "LayoutDuration", "value": 0.42},
                    {"name": "Nodes", "value": 2100},
                    {"name": "JSEventListeners", "value": 85},
                ],
            },
        }
    }
    path = tmp_path / "devtools-context.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    client = DevToolsMcpClient(
        enabled=True,
        command="",
        context_path=str(path),
        timeout_seconds=2,
        logger=logging.getLogger("test-devtools-file"),
    )

    context = client.collect_context()

    assert context is not None
    assert context.page_url == "https://app.local/dashboard"
    assert context.selected_element_selector == "#save-button"
    assert "TypeError" in context.exception_lines[0]
    assert "POST 500 [xhr] https://app.local/api/save" in context.network_lines[0]
    assert "1420ms" in context.network_lines[0]
    assert "LCP: 3200ms" in context.performance_lines
    assert "Script: 1700ms" in context.performance_lines
    assert "Nos DOM observados: 2100" in context.performance_lines
    assert "2 long task(s) observada(s)" in context.performance_lines
    assert any("LCP alto" in line for line in context.bottleneck_lines)
    assert any("Tempo alto em script" in line for line in context.bottleneck_lines)
    assert any("Falha de rede" in line for line in context.bottleneck_lines)
    evidence = context.evidence_lines()
    assert any("DevTools MCP: pagina observada" in line for line in evidence)
    assert any("DevTools MCP performance: LCP: 3200ms" in line for line in evidence)
    assert any("DevTools MCP gargalo possivel: Falha de rede" in line for line in evidence)


def test_devtools_mcp_client_reads_context_from_command(monkeypatch) -> None:
    payload = {
        "snapshot": {
            "title": "Configuracoes",
            "url": "https://app.local/settings",
            "logs": [{"level": "warning", "text": "Hydration mismatch"}],
        }
    }

    class Completed:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(
        "app.devtools_mcp_context.subprocess.run",
        lambda *args, **kwargs: Completed(),
    )

    client = DevToolsMcpClient(
        enabled=True,
        command="bridge-devtools",
        context_path="",
        timeout_seconds=2,
        logger=logging.getLogger("test-devtools-command"),
    )

    context = client.collect_context()

    assert context is not None
    assert context.page_title == "Configuracoes"
    assert "Hydration mismatch" in context.console_lines[0]


def test_devtools_mcp_client_returns_none_for_invalid_payload(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("not-json", encoding="utf-8")

    client = DevToolsMcpClient(
        enabled=True,
        command="",
        context_path=str(path),
        timeout_seconds=2,
        logger=logging.getLogger("test-devtools-invalid"),
    )

    assert client.collect_context() is None


def test_combined_evidence_context_merges_prompt_blocks_and_files(tmp_path: Path) -> None:
    video = tmp_path / "native_evidence.gif"
    video.write_bytes(b"video")
    native_context = NativeCaptureContext(
        target_kind="janela",
        target_title="Cadastro - Producao",
        video_path=video,
        duration_seconds=7.5,
        capture_fps=10,
        capture_rect=(120, 40, 1440, 900),
    )
    devtools_context = DevToolsMcpContext(
        page_url="https://app.local/cadastro",
        console_lines=("[error] TypeError: save failed",),
    )

    combined = combine_evidence_contexts(native_context, devtools_context)

    assert combined is not None
    assert combined.evidence_file_paths() == [video]
    prompt = combined.to_prompt_block()
    assert "Captura nativa local do Windows" in prompt
    assert "Google DevTools MCP" in prompt
    assert "https://app.local/cadastro" in prompt


def test_devtools_context_prompt_includes_performance_and_bottlenecks() -> None:
    context = DevToolsMcpContext(
        page_url="https://app.local/checkout",
        performance_lines=("LCP: 4100ms", "TBT: 620ms"),
        request_summary_lines=("12 requisicao(oes) observada(s)", "3 requisicao(oes) acima de 1000ms"),
        network_lines=("GET 200 [script] https://app.local/main.js (1800ms, 1.4 MB)",),
        bottleneck_lines=(
            "LCP alto (4100ms): investigar renderizacao inicial e recursos criticos",
            "Requisicao lenta (1800ms): GET 200 [script] https://app.local/main.js",
        ),
    )

    prompt = context.to_prompt_block()

    assert "Sinais de performance" in prompt
    assert "Resumo de rede" in prompt
    assert "Possiveis gargalos para investigar" in prompt
    assert "LCP alto" in prompt
