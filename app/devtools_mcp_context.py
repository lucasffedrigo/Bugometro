from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DevToolsMcpContext:
    page_url: str = ""
    page_title: str = ""
    browser: str = ""
    user_agent: str = ""
    page_state: str = ""
    selected_element_selector: str = ""
    selected_element_text: str = ""
    console_lines: tuple[str, ...] = ()
    network_lines: tuple[str, ...] = ()
    exception_lines: tuple[str, ...] = ()
    performance_lines: tuple[str, ...] = ()
    request_summary_lines: tuple[str, ...] = ()
    bottleneck_lines: tuple[str, ...] = ()

    def has_signal(self) -> bool:
        return any(
            (
                self.page_url,
                self.page_title,
                self.browser,
                self.user_agent,
                self.page_state,
                self.selected_element_selector,
                self.selected_element_text,
                self.console_lines,
                self.network_lines,
                self.exception_lines,
                self.performance_lines,
                self.request_summary_lines,
                self.bottleneck_lines,
            )
        )

    def to_prompt_block(self) -> str:
        lines = ["Contexto tecnico opcional vindo do Google DevTools MCP:"]
        if self.page_title:
            lines.append(f"- Titulo da pagina: {self.page_title}")
        if self.page_url:
            lines.append(f"- URL observada: {self.page_url}")
        if self.browser:
            lines.append(f"- Navegador observado: {self.browser}")
        if self.user_agent:
            lines.append(f"- User-Agent observado: {_trim_text(self.user_agent, 140)}")
        if self.page_state:
            lines.append(f"- Estado da pagina: {self.page_state}")
        if self.selected_element_selector:
            selected = f"- Elemento em foco ou inspecionado: {self.selected_element_selector}"
            if self.selected_element_text:
                selected += f" ({_trim_text(self.selected_element_text, 60)})"
            lines.append(selected)
        if self.exception_lines:
            lines.append("- Excecoes JavaScript relevantes:")
            lines.extend(f"  - {item}" for item in self.exception_lines)
        if self.performance_lines:
            lines.append("- Sinais de performance:")
            lines.extend(f"  - {item}" for item in self.performance_lines)
        if self.request_summary_lines:
            lines.append("- Resumo de rede:")
            lines.extend(f"  - {item}" for item in self.request_summary_lines)
        if self.console_lines:
            lines.append("- Logs de console relevantes:")
            lines.extend(f"  - {item}" for item in self.console_lines)
        if self.network_lines:
            lines.append("- Requisicoes de rede relevantes:")
            lines.extend(f"  - {item}" for item in self.network_lines)
        if self.bottleneck_lines:
            lines.append("- Possiveis gargalos para investigar:")
            lines.extend(f"  - {item}" for item in self.bottleneck_lines)
        lines.extend(
            [
                "- Use esse contexto apenas quando ele estiver consistente com a captura local e com o relato por voz.",
                "- Nao invente passos, erros ou detalhes tecnicos que nao estejam presentes nos sinais coletados.",
            ]
        )
        return "\n".join(lines)

    def evidence_lines(self) -> list[str]:
        lines: list[str] = []
        if self.page_url:
            page = f"DevTools MCP: pagina observada {self.page_url}"
            if self.page_title:
                page += f" ({_trim_text(self.page_title, 80)})"
            lines.append(page)
        elif self.page_title:
            lines.append(f"DevTools MCP: pagina observada {_trim_text(self.page_title, 100)}")

        if self.browser:
            lines.append(f"DevTools MCP: navegador observado {_trim_text(self.browser, 100)}")
        if self.page_state:
            lines.append(f"DevTools MCP: estado da pagina {self.page_state}")
        if self.selected_element_selector:
            selected = f"DevTools MCP: elemento em foco {self.selected_element_selector}"
            if self.selected_element_text:
                selected += f" ({_trim_text(self.selected_element_text, 60)})"
            lines.append(selected)

        lines.extend(f"DevTools MCP performance: {item}" for item in self.performance_lines[:6])
        lines.extend(f"DevTools MCP rede: {item}" for item in self.request_summary_lines[:5])
        lines.extend(f"DevTools MCP excecao JS: {item}" for item in self.exception_lines[:3])
        lines.extend(f"DevTools MCP console: {item}" for item in self.console_lines[:3])
        lines.extend(f"DevTools MCP requisicao: {item}" for item in self.network_lines[:5])
        lines.extend(f"DevTools MCP gargalo possivel: {item}" for item in self.bottleneck_lines[:6])
        return lines

    def evidence_file_paths(self) -> list[Path]:
        return []


@dataclass(frozen=True)
class CombinedEvidenceContext:
    contexts: tuple[Any, ...]

    def to_prompt_block(self) -> str:
        blocks = [
            context.to_prompt_block().strip()
            for context in self.contexts
            if context is not None and hasattr(context, "to_prompt_block")
        ]
        return "\n\n".join(block for block in blocks if block)

    def evidence_lines(self) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for context in self.contexts:
            if context is None or not hasattr(context, "evidence_lines"):
                continue
            for line in context.evidence_lines():
                normalized = line.strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                merged.append(normalized)
        return merged

    def evidence_file_paths(self) -> list[Path]:
        merged: list[Path] = []
        seen: set[Path] = set()
        for context in self.contexts:
            provider = getattr(context, "evidence_file_paths", None)
            if context is None or provider is None:
                continue
            for path in provider():
                if path in seen:
                    continue
                seen.add(path)
                merged.append(path)
        return merged


def combine_evidence_contexts(*contexts: Any) -> Any | None:
    active = tuple(context for context in contexts if context is not None)
    if not active:
        return None
    if len(active) == 1:
        return active[0]
    return CombinedEvidenceContext(contexts=active)


class DevToolsMcpClient:
    def __init__(
        self,
        *,
        enabled: bool,
        command: str,
        context_path: str,
        timeout_seconds: float,
        logger: logging.Logger,
        working_dir: Path | None = None,
    ) -> None:
        self.enabled = enabled
        self.command = command.strip()
        self.context_path = context_path.strip()
        self.timeout_seconds = max(0.5, timeout_seconds)
        self.logger = logger
        self.working_dir = working_dir

    def collect_context(self) -> DevToolsMcpContext | None:
        if not self.enabled:
            return None

        payload = self._load_payload()
        if payload is None:
            return None

        try:
            context = _build_context(payload)
        except Exception as exc:
            self.logger.warning("Falha ao interpretar o snapshot do DevTools MCP: %s", exc)
            return None

        if not context.has_signal():
            self.logger.info("Snapshot do DevTools MCP carregado sem sinais tecnicos uteis.")
            return None
        return context

    def _load_payload(self) -> dict[str, Any] | None:
        if self.command:
            payload = self._load_payload_from_command()
            if payload is not None:
                return payload
        if self.context_path:
            return self._load_payload_from_file()
        return None

    def _load_payload_from_command(self) -> dict[str, Any] | None:
        try:
            completed = subprocess.run(
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                cwd=self.working_dir,
            )
        except Exception as exc:
            self.logger.warning("Falha ao executar o comando do DevTools MCP: %s", exc)
            return None

        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "").strip().splitlines()
            reason = details[-1] if details else f"codigo {completed.returncode}"
            self.logger.warning("Comando do DevTools MCP falhou: %s", reason)
            return None

        return _parse_json_text(completed.stdout)

    def _load_payload_from_file(self) -> dict[str, Any] | None:
        path = Path(self.context_path)
        if not path.exists():
            self.logger.info("Arquivo de contexto do DevTools MCP nao encontrado: %s", path)
            return None
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.logger.warning("Falha ao ler o arquivo do DevTools MCP: %s", exc)
            return None
        return _parse_json_text(content)


def _parse_json_text(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None

    candidates = [text]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        candidates.append(lines[-1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _build_context(payload: dict[str, Any]) -> DevToolsMcpContext:
    root = _merge_dicts(
        payload,
        _as_dict(payload.get("context")),
        _as_dict(payload.get("snapshot")),
        _as_dict(payload.get("devtools")),
    )
    selected_element = _merge_dicts(
        _as_dict(root.get("selected_element")),
        _as_dict(root.get("selectedNode")),
        _as_dict(root.get("focused_element")),
        _as_dict(root.get("inspected_element")),
    )

    console_events = _pick_first_event_list(
        root.get("console"),
        root.get("console_events"),
        root.get("logs"),
        root.get("log_entries"),
    )
    exception_events = _pick_first_event_list(
        root.get("exceptions"),
        root.get("exception_events"),
        root.get("runtime_exceptions"),
        _extract_nested(root, "runtime", "exceptions"),
        _extract_nested(root, "page", "errors"),
    )
    network_events = _pick_first_event_list(
        root.get("network"),
        root.get("network_events"),
        root.get("requests"),
        root.get("request_events"),
        root.get("fetch"),
    )
    performance_payload = _merge_dicts(
        _as_dict(root.get("performance")),
        _as_dict(root.get("metrics")),
        _as_dict(root.get("web_vitals")),
        _as_dict(root.get("webVitals")),
        _as_dict(_extract_nested(root, "page", "performance")),
    )
    long_task_events = _pick_first_event_list(
        root.get("long_tasks"),
        root.get("longTasks"),
        _extract_nested(performance_payload, "long_tasks"),
        _extract_nested(performance_payload, "longTasks"),
    )
    network_analysis = _analyze_network_events(network_events)
    performance_analysis = _analyze_performance_payload(
        performance_payload,
        long_task_events,
    )

    return DevToolsMcpContext(
        page_url=_pick_first_non_empty(
            _as_str(root.get("page_url")),
            _as_str(root.get("url")),
            _as_str(root.get("active_url")),
            _as_str(_extract_nested(root, "page", "url")),
        ),
        page_title=_pick_first_non_empty(
            _as_str(root.get("page_title")),
            _as_str(root.get("title")),
            _as_str(root.get("active_title")),
            _as_str(_extract_nested(root, "page", "title")),
        ),
        browser=_pick_first_non_empty(
            _as_str(root.get("browser")),
            _as_str(root.get("browser_version")),
            _as_str(_extract_nested(root, "browser", "name")),
            _as_str(_extract_nested(root, "browser", "version")),
        ),
        user_agent=_pick_first_non_empty(
            _as_str(root.get("user_agent")),
            _as_str(root.get("userAgent")),
            _as_str(_extract_nested(root, "browser", "userAgent")),
        ),
        page_state=_pick_first_non_empty(
            _as_str(root.get("page_state")),
            _as_str(_extract_nested(root, "page", "state")),
            _as_str(_extract_nested(root, "page", "load_state")),
        ),
        selected_element_selector=_pick_first_non_empty(
            _as_str(selected_element.get("selector")),
            _as_str(selected_element.get("css_selector")),
        ),
        selected_element_text=_pick_first_non_empty(
            _as_str(selected_element.get("text")),
            _as_str(selected_element.get("label")),
        ),
        console_lines=tuple(_collect_console_lines(console_events)),
        network_lines=tuple(network_analysis.relevant_lines),
        exception_lines=tuple(_collect_exception_lines(exception_events)),
        performance_lines=tuple(performance_analysis.performance_lines),
        request_summary_lines=tuple(network_analysis.summary_lines),
        bottleneck_lines=tuple(
            _collect_bottleneck_lines(network_analysis, performance_analysis)
        ),
    )


def _merge_dicts(*values: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if value:
            merged.update(value)
    return merged


def _extract_nested(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _coerce_event_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("events", "entries", "items", "requests", "errors", "exceptions", "logs"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
    return []


def _pick_first_event_list(*values: Any) -> list[Any]:
    for value in values:
        items = _coerce_event_list(value)
        if items:
            return items
    return []


def _collect_console_lines(console_events: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(console_events, list):
        return lines

    for event in console_events:
        if not isinstance(event, dict):
            continue
        severity = _pick_first_non_empty(
            _as_str(event.get("level")),
            _as_str(event.get("type")),
            "log",
        )
        message = _pick_first_non_empty(
            _as_str(event.get("message")),
            _as_str(event.get("text")),
            _as_str(event.get("description")),
        )
        if not message:
            continue
        lines.append(f"[{severity}] {_trim_text(message, 140)}")
        if len(lines) >= 5:
            break
    return lines


def _collect_exception_lines(exception_events: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(exception_events, list):
        return lines

    for event in exception_events:
        if not isinstance(event, dict):
            continue
        kind = _pick_first_non_empty(
            _as_str(event.get("name")),
            _as_str(event.get("type")),
            _as_str(event.get("level")),
            "exception",
        )
        message = _pick_first_non_empty(
            _as_str(event.get("message")),
            _as_str(event.get("text")),
            _as_str(event.get("description")),
            _as_str(event.get("error")),
        )
        location = _build_location_suffix(event)
        if not message and not location:
            continue
        rendered = f"[{kind}] {message or location}"
        if message and location:
            rendered += f" em {location}"
        lines.append(_trim_text(rendered, 180))
        if len(lines) >= 4:
            break
    return lines


@dataclass(frozen=True)
class _NetworkAnalysis:
    summary_lines: tuple[str, ...]
    relevant_lines: tuple[str, ...]
    bottleneck_lines: tuple[str, ...]


@dataclass(frozen=True)
class _PerformanceAnalysis:
    performance_lines: tuple[str, ...]
    bottleneck_lines: tuple[str, ...]


def _analyze_network_events(network_events: Any) -> _NetworkAnalysis:
    if not isinstance(network_events, list):
        return _NetworkAnalysis((), (), ())

    interesting: list[str] = []
    fallback: list[str] = []
    bottlenecks: list[str] = []
    total = 0
    failures = 0
    slow = 0
    large = 0
    total_bytes = 0
    for event in network_events:
        if not isinstance(event, dict):
            continue
        total += 1
        rendered = _render_network_event(event)
        if not rendered:
            continue
        status = _as_int(
            event.get("status")
            or event.get("statusCode")
            or _extract_nested(event, "response", "status")
        )
        failure = _pick_first_non_empty(
            _as_str(event.get("errorText")),
            _as_str(event.get("failureReason")),
            _as_str(event.get("blockedReason")),
            _as_str(event.get("message")),
        )
        duration_ms = _extract_duration_ms(event)
        transfer_size = _extract_transfer_size(event)
        if transfer_size:
            total_bytes += transfer_size
        if (status is not None and status >= 400) or failure:
            failures += 1
            interesting.append(rendered)
            bottlenecks.append(f"Falha de rede: {rendered}")
            continue
        if duration_ms is not None and duration_ms >= 1000:
            slow += 1
            interesting.append(rendered)
            bottlenecks.append(f"Requisicao lenta ({duration_ms:.0f}ms): {rendered}")
            continue
        if transfer_size is not None and transfer_size >= 1_000_000:
            large += 1
            interesting.append(rendered)
            bottlenecks.append(f"Resposta pesada ({_format_bytes(transfer_size)}): {rendered}")
        else:
            fallback.append(rendered)

    summary: list[str] = []
    if total:
        summary.append(f"{total} requisicao(oes) observada(s)")
    if failures:
        summary.append(f"{failures} falha(s) HTTP/bloqueio detectada(s)")
    if slow:
        summary.append(f"{slow} requisicao(oes) acima de 1000ms")
    if large:
        summary.append(f"{large} resposta(s) acima de 1 MB")
    if total_bytes:
        summary.append(f"Transferencia observada: {_format_bytes(total_bytes)}")

    return _NetworkAnalysis(
        summary_lines=tuple(summary[:5]),
        relevant_lines=tuple((interesting + fallback)[:8]),
        bottleneck_lines=tuple(bottlenecks[:6]),
    )


def _analyze_performance_payload(
    performance_payload: dict[str, Any],
    long_task_events: Any,
) -> _PerformanceAnalysis:
    if not performance_payload and not long_task_events:
        return _PerformanceAnalysis((), ())

    lines: list[str] = []
    bottlenecks: list[str] = []

    metrics = {
        "LCP": _metric_ms(performance_payload, "lcp", "largestContentfulPaint", "largest_contentful_paint"),
        "FCP": _metric_ms(performance_payload, "fcp", "firstContentfulPaint", "first_contentful_paint"),
        "INP": _metric_ms(performance_payload, "inp", "interactionToNextPaint", "interaction_to_next_paint"),
        "TTFB": _metric_ms(performance_payload, "ttfb", "timeToFirstByte", "time_to_first_byte"),
        "DOMContentLoaded": _metric_ms(performance_payload, "domContentLoaded", "dom_content_loaded"),
        "Load": _metric_ms(performance_payload, "load", "loadEventEnd", "load_event_end"),
        "TBT": _metric_ms(performance_payload, "tbt", "totalBlockingTime", "total_blocking_time"),
        "Script": _metric_ms(performance_payload, "scriptDuration", "ScriptDuration"),
        "Layout": _metric_ms(performance_payload, "layoutDuration", "LayoutDuration"),
        "Recalc style": _metric_ms(performance_payload, "recalcStyleDuration", "RecalcStyleDuration"),
        "Task total": _metric_ms(performance_payload, "taskDuration", "TaskDuration"),
    }
    cls = _metric_float(performance_payload, "cls", "cumulativeLayoutShift")
    heap_used = _metric_bytes(performance_payload, "jsHeapUsedSize", "js_heap_used_size")
    dom_nodes = _metric_count(performance_payload, "nodes", "Nodes")
    js_event_listeners = _metric_count(
        performance_payload,
        "jsEventListeners",
        "JSEventListeners",
    )

    for label, value in metrics.items():
        if value is not None:
            lines.append(f"{label}: {value:.0f}ms")
    if cls is not None:
        lines.append(f"CLS: {cls:.3f}")
    if heap_used is not None:
        lines.append(f"Heap JS usado: {_format_bytes(heap_used)}")
    if dom_nodes is not None:
        lines.append(f"Nos DOM observados: {dom_nodes}")
    if js_event_listeners is not None:
        lines.append(f"Event listeners JS: {js_event_listeners}")

    long_tasks = _coerce_event_list(long_task_events)
    if long_tasks:
        total_long_task_ms = sum(
            duration
            for duration in (_extract_duration_ms(event) for event in long_tasks if isinstance(event, dict))
            if duration is not None
        )
        lines.append(f"{len(long_tasks)} long task(s) observada(s)")
        if total_long_task_ms:
            lines.append(f"Tempo em long tasks: {total_long_task_ms:.0f}ms")

    lcp = metrics["LCP"]
    inp = metrics["INP"]
    tbt = metrics["TBT"]
    script = metrics["Script"]
    layout = metrics["Layout"]
    recalc_style = metrics["Recalc style"]
    task_total = metrics["Task total"]
    load = metrics["Load"]
    dom_content_loaded = metrics["DOMContentLoaded"]
    if lcp is not None and lcp > 2500:
        bottlenecks.append(f"LCP alto ({lcp:.0f}ms): investigar renderizacao inicial e recursos criticos")
    if inp is not None and inp > 200:
        bottlenecks.append(f"INP alto ({inp:.0f}ms): investigar handlers de interacao e tarefas longas")
    if cls is not None and cls > 0.1:
        bottlenecks.append(f"CLS elevado ({cls:.3f}): investigar mudancas de layout")
    if tbt is not None and tbt > 200:
        bottlenecks.append(f"TBT alto ({tbt:.0f}ms): investigar JavaScript bloqueando a main thread")
    if script is not None and script > 1000:
        bottlenecks.append(f"Tempo alto em script ({script:.0f}ms): investigar bundles e execucao JS")
    if layout is not None and layout > 300:
        bottlenecks.append(f"Tempo alto em layout ({layout:.0f}ms): investigar reflows e CSS/layout")
    if recalc_style is not None and recalc_style > 300:
        bottlenecks.append(f"Tempo alto recalculando estilos ({recalc_style:.0f}ms)")
    if task_total is not None and task_total > 2000:
        bottlenecks.append(f"Tempo total de tarefas alto ({task_total:.0f}ms)")
    if dom_content_loaded is not None and dom_content_loaded > 1500:
        bottlenecks.append(f"DOMContentLoaded lento ({dom_content_loaded:.0f}ms)")
    if load is not None and load > 3000:
        bottlenecks.append(f"Load event lento ({load:.0f}ms)")
    if long_tasks:
        bottlenecks.append("Long tasks presentes: investigar processamento pesado no thread principal")

    return _PerformanceAnalysis(
        performance_lines=tuple(lines[:10]),
        bottleneck_lines=tuple(bottlenecks[:8]),
    )


def _collect_bottleneck_lines(
    network_analysis: _NetworkAnalysis,
    performance_analysis: _PerformanceAnalysis,
) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for source in (network_analysis.bottleneck_lines, performance_analysis.bottleneck_lines):
        for item in source:
            if item in seen:
                continue
            seen.add(item)
            lines.append(item)
            if len(lines) >= 10:
                return lines
    return lines


def _render_network_event(event: dict[str, Any]) -> str:
    method = _pick_first_non_empty(
        _as_str(event.get("method")),
        _as_str(_extract_nested(event, "request", "method")),
    )
    status = _as_int(
        event.get("status")
        or event.get("statusCode")
        or _extract_nested(event, "response", "status")
    )
    url = _pick_first_non_empty(
        _as_str(event.get("url")),
        _as_str(_extract_nested(event, "request", "url")),
        _as_str(_extract_nested(event, "response", "url")),
        _as_str(event.get("name")),
    )
    resource_type = _pick_first_non_empty(
        _as_str(event.get("resourceType")),
        _as_str(event.get("type")),
        _as_str(_extract_nested(event, "request", "resourceType")),
    )
    failure = _pick_first_non_empty(
        _as_str(event.get("errorText")),
        _as_str(event.get("failureReason")),
        _as_str(event.get("blockedReason")),
        _as_str(event.get("message")),
    )
    duration_ms = _extract_duration_ms(event)
    transfer_size = _extract_transfer_size(event)
    if not any((method, status is not None, url, resource_type, failure)):
        return ""

    parts: list[str] = []
    if method:
        parts.append(method.upper())
    if status is not None:
        parts.append(str(status))
    if resource_type:
        parts.append(f"[{resource_type}]")
    if url:
        parts.append(url)
    rendered = " ".join(parts) if parts else "requisicao"
    details: list[str] = []
    if duration_ms is not None:
        details.append(f"{duration_ms:.0f}ms")
    if transfer_size is not None:
        details.append(_format_bytes(transfer_size))
    if details:
        rendered += f" ({', '.join(details)})"
    if failure:
        rendered += f" - {_trim_text(failure, 80)}"
    return _trim_text(rendered, 180)


def _extract_duration_ms(event: dict[str, Any]) -> float | None:
    candidates = (
        event.get("duration_ms"),
        event.get("durationMs"),
        event.get("duration"),
        event.get("time"),
        event.get("elapsed_ms"),
        event.get("elapsedMs"),
        _extract_nested(event, "timing", "duration"),
        _extract_nested(event, "response", "duration_ms"),
        _extract_nested(event, "response", "durationMs"),
    )
    for value in candidates:
        parsed = _as_float(value)
        if parsed is None:
            continue
        if parsed < 20 and any(key in event for key in ("duration", "time")):
            return parsed * 1000
        return parsed
    start = _as_float(event.get("startTime") or event.get("started_at"))
    end = _as_float(event.get("endTime") or event.get("finished_at"))
    if start is not None and end is not None and end >= start:
        duration = end - start
        return duration * 1000 if duration < 20 else duration
    return None


def _extract_transfer_size(event: dict[str, Any]) -> int | None:
    candidates = (
        event.get("transferSize"),
        event.get("transfer_size"),
        event.get("encodedDataLength"),
        event.get("encoded_data_length"),
        event.get("size"),
        event.get("bytes"),
        _extract_nested(event, "response", "transferSize"),
        _extract_nested(event, "response", "encodedDataLength"),
        _extract_nested(event, "response", "size"),
    )
    for value in candidates:
        parsed = _as_int(value)
        if parsed is not None and parsed >= 0:
            return parsed
    return None


def _metric_ms(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _extract_metric_value(payload, key)
        parsed = _as_float(value)
        if parsed is None:
            continue
        return parsed * 1000 if parsed and parsed < 20 else parsed
    return None


def _metric_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        parsed = _as_float(_extract_metric_value(payload, key))
        if parsed is not None:
            return parsed
    return None


def _metric_bytes(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        parsed = _as_int(_extract_metric_value(payload, key))
        if parsed is not None:
            return parsed
    return None


def _metric_count(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        parsed = _as_int(_extract_metric_value(payload, key))
        if parsed is not None:
            return parsed
    return None


def _extract_metric_value(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload.get(key)
    metrics = payload.get("metrics")
    if isinstance(metrics, dict) and key in metrics:
        return metrics.get(key)
    if isinstance(metrics, list):
        for item in metrics:
            if not isinstance(item, dict):
                continue
            name = _as_str(item.get("name") or item.get("metric"))
            if name and name.lower() == key.lower():
                return item.get("value")
    timings = payload.get("timings")
    if isinstance(timings, dict) and key in timings:
        return timings.get(key)
    return None


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    amount = float(max(value, 0))
    unit_index = 0
    while amount >= 1024 and unit_index < len(units) - 1:
        amount /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(amount)} {units[unit_index]}"
    return f"{amount:.1f} {units[unit_index]}"


def _build_location_suffix(event: dict[str, Any]) -> str:
    url = _pick_first_non_empty(
        _as_str(event.get("url")),
        _as_str(event.get("sourceURL")),
        _as_str(event.get("file")),
        _as_str(event.get("filename")),
    )
    line_number = _as_int(event.get("lineNumber") or event.get("line"))
    column_number = _as_int(event.get("columnNumber") or event.get("column"))
    if not url:
        return ""
    location = url
    if line_number is not None:
        location += f":{line_number}"
        if column_number is not None:
            location += f":{column_number}"
    return _trim_text(location, 100)


def _pick_first_non_empty(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def _trim_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
