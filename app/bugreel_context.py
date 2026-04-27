from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


@dataclass(frozen=True)
class BugReelContext:
    url: str
    base_url: str
    source_kind: str
    source_id: str

    def api_path(self) -> str:
        if self.source_kind == "report":
            return f"/api/recordings/by-token/{self.source_id}"
        return f"/api/recordings/{self.source_id}"

    def to_prompt_block(self) -> str:
        return (
            "Relatório privado do BugReel vinculado ao relato:\n"
            f"- URL do BugReel: {self.url}\n"
            "- O vídeo de evidência está disponível nesse relatório.\n"
            "- Use o BugReel apenas como contexto complementar e evidência externa.\n"
            "- Não invente eventos, erros, passos ou capturas que não estejam no relato do usuário.\n"
            "- Em Evidências, não inclua links; cite apenas que há vídeo para anexo via CTRL+SHIFT+V."
        )

    def evidence_lines(self) -> list[str]:
        return [
            "Evidência em vídeo capturada no BugReel (anexe com CTRL+SHIFT+V).",
        ]


@dataclass(frozen=True)
class EnrichedBugReelContext:
    raw: BugReelContext
    recording_id: str
    share_token: str | None
    report_url: str
    video_url: str | None
    recording_status: str = ""
    artifacts_ready: bool = False
    ai_status: str = ""
    title: str = ""
    summary: str = ""
    duration_seconds: float | None = None
    transcript_excerpt: str = ""
    metadata_lines: tuple[str, ...] = ()
    audio_capture_lines: tuple[str, ...] = ()
    context_limitations: tuple[str, ...] = ()
    console_lines: tuple[str, ...] = ()
    action_lines: tuple[str, ...] = ()
    navigation_lines: tuple[str, ...] = ()
    frame_lines: tuple[str, ...] = ()
    frame_filenames: tuple[str, ...] = ()
    local_bundle_dir: Path | None = None
    local_files: tuple[Path, ...] = field(default_factory=tuple)

    def to_prompt_block(self) -> str:
        sections = [
            "Relatório privado do BugReel enriquecido:",
            f"- URL do relatório: {self.report_url}",
        ]
        if self.title:
            sections.append(f"- Título detectado: {self.title}")
        if self.summary:
            sections.append(f"- Resumo detectado: {self.summary}")
        if self.duration_seconds:
            sections.append(f"- Duração aproximada: {self.duration_seconds:.1f}s")
        if self.recording_status:
            sections.append(f"- Status publicado pelo BugReel: {self.recording_status}")
        if self.artifacts_ready:
            sections.append("- Artefatos publicados: vídeo e/ou áudio disponíveis.")
        if self.ai_status:
            sections.append(f"- Situação da IA interna do BugReel: {self.ai_status}")
        if self.transcript_excerpt:
            sections.append(f"- Trecho de transcrição do BugReel: {self.transcript_excerpt}")
        if self.metadata_lines:
            sections.append("- Ambiente e contexto:")
            sections.extend(f"  - {item}" for item in self.metadata_lines)
        if self.audio_capture_lines:
            sections.append("- Caminho de áudio detectado:")
            sections.extend(f"  - {item}" for item in self.audio_capture_lines)
        if self.context_limitations:
            sections.append("- Limitações de contexto detectadas:")
            sections.extend(f"  - {item}" for item in self.context_limitations)
        if self.console_lines:
            sections.append("- Logs de console relevantes:")
            sections.extend(f"  - {item}" for item in self.console_lines)
        if self.action_lines:
            sections.append("- Ações do usuário relevantes:")
            sections.extend(f"  - {item}" for item in self.action_lines)
        if self.navigation_lines:
            sections.append("- Navegação e URLs observadas:")
            sections.extend(f"  - {item}" for item in self.navigation_lines)
        if self.frame_lines:
            sections.append("- Frames e capturas relevantes:")
            sections.extend(f"  - {item}" for item in self.frame_lines)
        sections.extend(
            [
                "- Use esse contexto apenas quando estiver consistente com o relato por voz.",
                "- Não invente fatos ausentes no vídeo, nos logs ou no relato do usuário.",
                "- Em Evidências, não inclua links; cite apenas que há vídeo para anexo via CTRL+SHIFT+V.",
            ]
        )
        return "\n".join(sections)

    def evidence_lines(self) -> list[str]:
        return ["Evidência em vídeo capturada no BugReel (anexe com CTRL+SHIFT+V)."]

    def evidence_file_paths(self) -> list[Path]:
        return list(self.local_files)


def extract_bugreel_context(text: str, base_url: str = "") -> BugReelContext | None:
    if not text or not text.strip():
        return None

    urls = [_strip_trailing_punctuation(match.group(0)) for match in URL_PATTERN.finditer(text)]
    candidates = [url for url in urls if _is_allowed_url(url, base_url)]
    for url in reversed(candidates):
        parsed = urlparse(url)
        source_kind, source_id = _extract_source(parsed.path)
        if source_kind and source_id:
            return BugReelContext(
                url=url,
                base_url=f"{parsed.scheme}://{parsed.netloc}",
                source_kind=source_kind,
                source_id=source_id,
            )
    return None


def _extract_source(path: str) -> tuple[str | None, str | None]:
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return None, None

    if segments[0] == "report" and segments[1]:
        return "report", segments[1]
    if segments[0] in {"recording", "recordings"} and segments[1]:
        return "recording", segments[1]
    return None, None


def _is_allowed_url(url: str, base_url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    if not base_url.strip():
        return True

    try:
        expected = urlparse(base_url.strip())
    except ValueError:
        return False

    if expected.scheme and parsed.scheme != expected.scheme:
        return False

    expected_host = expected.netloc or expected.path
    return parsed.netloc.lower() == expected_host.lower()


def _strip_trailing_punctuation(url: str) -> str:
    return url.rstrip(".,);]>}\"'")
