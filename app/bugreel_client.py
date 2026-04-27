from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests

from app.bugreel_context import BugReelContext, EnrichedBugReelContext


class BugReelClientError(RuntimeError):
    """Raised when BugReel context cannot be fetched or prepared."""


@dataclass(frozen=True)
class BugReelDownloadSettings:
    enabled: bool = True
    frame_limit: int = 3


@dataclass(frozen=True)
class BugReelRecordingSummary:
    recording_id: str
    share_token: str | None
    status: str
    created_at: str
    duration_seconds: float | None = None
    video_filename: str | None = None
    audio_filename: str | None = None
    artifacts_ready: bool = False
    ai_status: str = ""

    def has_importable_artifacts(self) -> bool:
        return bool(
            self.artifacts_ready
            or self.video_filename
            or self.audio_filename
        )

    def is_ready_for_import(self) -> bool:
        normalized = self.status.strip().lower()
        if not normalized:
            return True
        if normalized in {
            "recording",
            "uploading",
            "processing",
            "transcribing",
            "analyzing",
            "queued",
            "pending",
            "staged",
            "starting",
        }:
            return False
        if self.has_importable_artifacts():
            return True
        return normalized not in {
            "error",
            "failed",
            "canceled",
            "cancelled",
            "aborted",
        }

    def is_failed_terminal(self) -> bool:
        return (
            self.status.strip().lower() in {
                "error",
                "failed",
                "canceled",
                "cancelled",
                "aborted",
            }
            and not self.has_importable_artifacts()
        )

    def estimated_end_at(self) -> datetime | None:
        created_at = self._parse_created_at()
        if created_at is None:
            return None
        if self.duration_seconds and self.duration_seconds > 0:
            return created_at + timedelta(seconds=self.duration_seconds)
        return created_at

    def _parse_created_at(self) -> datetime | None:
        raw = self.created_at.strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    def to_context(self, base_url: str) -> BugReelContext:
        normalized_base_url = base_url.rstrip("/")
        if self.share_token:
            return BugReelContext(
                url=f"{normalized_base_url}/report/{self.share_token}",
                base_url=normalized_base_url,
                source_kind="report",
                source_id=self.share_token,
            )
        return BugReelContext(
            url=f"{normalized_base_url}/recording/{self.recording_id}",
            base_url=normalized_base_url,
            source_kind="recording",
            source_id=self.recording_id,
        )


class BugReelClient:
    def __init__(
        self,
        api_token: str = "",
        timeout_seconds: float = 20,
        download_settings: BugReelDownloadSettings | None = None,
        bundle_dir_factory: Callable[[], Path] | None = None,
    ) -> None:
        self.api_token = api_token.strip()
        self.timeout_seconds = timeout_seconds
        self.download_settings = download_settings or BugReelDownloadSettings()
        self.bundle_dir_factory = bundle_dir_factory

    def enrich_context(self, context: BugReelContext) -> EnrichedBugReelContext:
        payload = self._fetch_payload(context)
        enriched = self._build_context(context, payload)
        if self.download_settings.enabled and self.bundle_dir_factory is not None:
            enriched = self._download_evidence(enriched)
        return enriched

    def list_recordings(self, base_url: str, limit: int = 10) -> list[BugReelRecordingSummary]:
        payload = self._fetch_json(
            f"{base_url.rstrip('/')}/api/recordings?limit={int(limit)}",
            action_name="listar as gravações",
        )
        rows = payload.get("recordings")
        if not isinstance(rows, list):
            raise BugReelClientError(
                "O BugReel não retornou a lista esperada de gravações."
            )

        summaries: list[BugReelRecordingSummary] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            recording_id = _as_str(row.get("id"))
            if not recording_id:
                continue
            summaries.append(
                BugReelRecordingSummary(
                    recording_id=recording_id,
                    share_token=_as_str(row.get("share_token")) or None,
                    status=_as_str(row.get("status")) or "",
                    created_at=_as_str(row.get("created_at")) or "",
                    duration_seconds=_as_float(row.get("duration_seconds")),
                    video_filename=_as_str(row.get("video_filename")),
                    audio_filename=_as_str(row.get("audio_filename")),
                    artifacts_ready=_as_bool(row.get("artifacts_ready")),
                    ai_status=_as_str(row.get("ai_status")) or "",
                )
            )
        return summaries

    def wait_for_new_recording(
        self,
        base_url: str,
        existing_ids: set[str],
        timeout_seconds: float,
        poll_interval_seconds: float = 1.5,
    ) -> BugReelRecordingSummary:
        deadline = time.monotonic() + timeout_seconds
        known_ids = {item for item in existing_ids if item}

        while time.monotonic() < deadline:
            summaries = self.list_recordings(
                base_url,
                limit=max(len(known_ids) + 5, 10),
            )
            for summary in summaries:
                if summary.recording_id in known_ids:
                    continue
                if summary.is_ready_for_import():
                    return summary
            time.sleep(poll_interval_seconds)

        raise BugReelClientError(
            "O BugReel não finalizou uma nova gravação a tempo. Conclua a captura no BugReel e tente novamente."
        )

    def _fetch_payload(self, context: BugReelContext) -> dict[str, Any]:
        return self._fetch_json(
            f"{context.base_url}{context.api_path()}",
            action_name="carregar o relatório",
        )

    def _fetch_json(self, url: str, action_name: str) -> dict[str, Any]:
        try:
            response = requests.get(
                url,
                headers=self._build_headers(),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise BugReelClientError(
                f"Não foi possível consultar o BugReel ao tentar {action_name}."
            ) from exc

        if response.status_code >= 400:
            if response.status_code in {401, 403}:
                raise BugReelClientError(
                    "O BugReel recusou a autenticação. Configure um token válido no .env."
                )
            if response.status_code == 404:
                raise BugReelClientError(
                    "O BugReel não encontrou a gravação solicitada."
                )
            raise BugReelClientError(
                f"O BugReel respondeu com erro {response.status_code} ao tentar {action_name}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise BugReelClientError(
                "O BugReel retornou um payload inválido."
            ) from exc

        if not isinstance(payload, dict):
            raise BugReelClientError(
                "O BugReel retornou uma resposta inesperada."
            )
        return payload

    def _build_context(
        self,
        context: BugReelContext,
        payload: dict[str, Any],
    ) -> EnrichedBugReelContext:
        recording = payload.get("recording") or {}
        if not isinstance(recording, dict):
            recording = {}
        frames = payload.get("frames") or []
        card = payload.get("card") or {}
        if not isinstance(card, dict):
            card = {}

        recording_id = str(recording.get("id") or context.source_id)
        share_token = _as_str(recording.get("share_token")) or (
            context.source_id if context.source_kind == "report" else None
        )
        reference_id = share_token or recording_id

        report_url = (
            context.url
            if context.source_kind == "report"
            else urljoin(context.base_url, f"/report/{reference_id}")
        )
        video_url = (
            urljoin(context.base_url, f"/api/recordings/{reference_id}/video")
            if reference_id
            else None
        )

        analysis = recording.get("analysis") or {}
        transcript = recording.get("transcript") or {}
        transcript_text = _extract_transcript_text(transcript)
        metadata = _extract_json_dict(recording.get("metadata"))
        artifacts_ready = _as_bool(recording.get("artifacts_ready")) or bool(
            recording.get("video_filename") or recording.get("audio_filename")
        )
        ai_status = _pick_first_non_empty(
            _as_str(recording.get("ai_status")),
            _as_str(metadata.get("ai_status")),
        )

        return EnrichedBugReelContext(
            raw=context,
            recording_id=recording_id,
            share_token=share_token,
            report_url=report_url,
            video_url=video_url,
            recording_status=_as_str(recording.get("status")) or "",
            artifacts_ready=artifacts_ready,
            ai_status=ai_status,
            title=_pick_first_non_empty(
                _as_str(card.get("title")),
                _as_str(analysis.get("title")),
            ),
            summary=_pick_first_non_empty(
                _as_str(card.get("summary")),
                _as_str(analysis.get("summary")),
                _as_str(analysis.get("context")),
            ),
            duration_seconds=_as_float(recording.get("duration_seconds")),
            transcript_excerpt=_trim_text(transcript_text, 500),
            metadata_lines=tuple(_collect_metadata_lines(recording, metadata)),
            audio_capture_lines=tuple(_collect_audio_capture_lines(metadata)),
            context_limitations=tuple(_collect_context_limitations(recording, metadata)),
            console_lines=tuple(_collect_console_lines(recording.get("console_events"))),
            action_lines=tuple(_collect_action_lines(recording.get("action_events"))),
            navigation_lines=tuple(_collect_navigation_lines(recording.get("url_events"))),
            frame_lines=tuple(_collect_frame_lines(frames)),
            frame_filenames=tuple(_collect_frame_filenames(frames)),
        )

    def _download_evidence(self, context: EnrichedBugReelContext) -> EnrichedBugReelContext:
        bundle_dir = self.bundle_dir_factory()
        bundle_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []

        if context.video_url:
            video_path = bundle_dir / "bugreel_video.webm"
            if self._download_file(context.video_url, video_path):
                downloaded.append(video_path)

        reference_id = context.share_token or context.recording_id
        for filename in context.frame_filenames[: self.download_settings.frame_limit]:
            frame_url = urljoin(
                context.raw.base_url,
                f"/api/recordings/{reference_id}/frames/{filename}",
            )
            frame_path = bundle_dir / filename
            if self._download_file(frame_url, frame_path):
                downloaded.append(frame_path)

        return EnrichedBugReelContext(
            raw=context.raw,
            recording_id=context.recording_id,
            share_token=context.share_token,
            report_url=context.report_url,
            video_url=context.video_url,
            recording_status=context.recording_status,
            artifacts_ready=context.artifacts_ready,
            ai_status=context.ai_status,
            title=context.title,
            summary=context.summary,
            duration_seconds=context.duration_seconds,
            transcript_excerpt=context.transcript_excerpt,
            metadata_lines=context.metadata_lines,
            audio_capture_lines=context.audio_capture_lines,
            context_limitations=context.context_limitations,
            console_lines=context.console_lines,
            action_lines=context.action_lines,
            navigation_lines=context.navigation_lines,
            frame_lines=context.frame_lines,
            frame_filenames=context.frame_filenames,
            local_bundle_dir=bundle_dir if downloaded else None,
            local_files=tuple(downloaded),
        )

    def _download_file(self, url: str, destination: Path) -> bool:
        try:
            response = requests.get(
                url,
                headers=self._build_headers(),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException:
            return False

        if response.status_code >= 400:
            return False

        destination.write_bytes(response.content)
        return True

    def _build_headers(self) -> dict[str, str]:
        if not self.api_token:
            return {}
        return {"Authorization": f"Bearer {self.api_token}"}


def _extract_transcript_text(transcript: Any) -> str:
    if isinstance(transcript, dict):
        text = transcript.get("text")
        if isinstance(text, str):
            return text.strip()
        words = transcript.get("words")
        if isinstance(words, list):
            pieces = [
                str(item.get("word", "")).strip()
                for item in words
                if isinstance(item, dict)
            ]
            return " ".join(piece for piece in pieces if piece)
    return ""


def _extract_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _collect_metadata_lines(recording: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    url = (
        _as_str(recording.get("page_url"))
        or _as_str(metadata.get("active_tab_url"))
        or _as_str(metadata.get("page_url"))
        or _as_str(metadata.get("url"))
    )
    title = _as_str(metadata.get("active_tab_title"))
    browser = _as_str(metadata.get("browser")) or _as_str(metadata.get("browser_name"))
    os_name = _as_str(metadata.get("os")) or _as_str(metadata.get("platform"))
    viewport = _as_str(metadata.get("viewport")) or _build_viewport(metadata)
    author = _as_str(recording.get("author"))
    capture_mode = _as_str(metadata.get("capture_mode"))
    recorder_segment_count = _as_str(recording.get("recorder_segment_count"))

    if author:
        lines.append(f"Autor da gravação: {author}")
    if title:
        lines.append(f"Aba em foco: {title}")
    if url:
        lines.append(f"URL observada: {url}")
    if browser:
        lines.append(f"Navegador: {browser}")
    if os_name:
        lines.append(f"Sistema operacional: {os_name}")
    if viewport:
        lines.append(f"Viewport: {viewport}")
    if capture_mode:
        lines.append(f"Modo de captura: {capture_mode}")
    if recorder_segment_count and recorder_segment_count not in {"0", "1"}:
        lines.append(f"Reinícios do recorder detectados: {recorder_segment_count}")
    return lines[:8]


def _collect_audio_capture_lines(metadata: dict[str, Any]) -> list[str]:
    mix_mode = _as_str(metadata.get("audio_mix_mode"))
    mic_enabled = _as_bool(metadata.get("mic_enabled_setting"))
    system_enabled = _as_bool(metadata.get("system_audio_enabled_setting"))
    mic_track_present = _as_bool(metadata.get("mic_track_present"))
    system_track_present = _as_bool(metadata.get("system_audio_track_present"))
    final_track_present = _as_bool(metadata.get("final_audio_track_present"))

    lines: list[str] = []
    if mix_mode:
        labels = {
            "mic_only": "som final gravado apenas pelo microfone",
            "system_only": "som final gravado apenas pelo áudio do sistema",
            "mic_plus_system": "som final gravado com microfone e áudio do sistema",
            "no_audio": "nenhuma trilha de áudio final foi gerada",
        }
        lines.append(labels.get(mix_mode, f"modo de áudio final: {mix_mode}"))
    else:
        if final_track_present:
            lines.append("há trilha de áudio final publicada no vídeo")
        else:
            lines.append("não há trilha de áudio final publicada no vídeo")

    lines.append(f"microfone habilitado nas configurações: {'sim' if mic_enabled else 'não'}")
    lines.append(f"áudio do sistema habilitado nas configurações: {'sim' if system_enabled else 'não'}")
    lines.append(f"trilha de microfone detectada na captura: {'sim' if mic_track_present else 'não'}")
    lines.append(f"trilha de áudio do sistema detectada na captura: {'sim' if system_track_present else 'não'}")
    return lines


def _collect_context_limitations(recording: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    limitations: list[str] = []
    active_url = _as_str(metadata.get("active_tab_url")) or _as_str(metadata.get("page_url"))
    if active_url and active_url.startswith("chrome://"):
        limitations.append(
            "a aba em foco era uma página interna do Chrome; logs, ações e console podem ficar indisponíveis"
        )
    if not _extract_any_events(recording.get("console_events")):
        limitations.append("nenhum evento de console útil foi capturado")
    if not _extract_any_events(recording.get("action_events")):
        limitations.append("nenhuma ação de usuário útil foi capturada pelo content script")
    return limitations[:3]


def _extract_any_events(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _collect_console_lines(console_events: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(console_events, list):
        return lines

    for event in console_events:
        if not isinstance(event, dict):
            continue
        severity = _as_str(event.get("type")) or _as_str(event.get("level")) or "log"
        message = _as_str(event.get("message")) or _as_str(event.get("text"))
        if not message:
            continue
        lines.append(f"[{severity}] {_trim_text(message, 140)}")
        if len(lines) >= 5:
            break
    return lines


def _collect_action_lines(action_events: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(action_events, list):
        return lines

    for event in action_events:
        if not isinstance(event, dict):
            continue
        action = _pick_first_non_empty(
            _as_str(event.get("type")),
            _as_str(event.get("action")),
            _as_str(event.get("eventType")),
            _as_str(event.get("name")),
        )
        target = _pick_first_non_empty(
            _as_str(event.get("selector")),
            _as_str(event.get("target")),
            _as_str(event.get("label")),
            _as_str(event.get("text")),
            _as_str(event.get("path")),
            _as_str(event.get("url")),
        )
        if not action and not target:
            continue
        text = action or "ação"
        if target:
            text += f" em {target}"
        lines.append(_trim_text(text, 120))
        if len(lines) >= 6:
            break
    return lines


def _collect_navigation_lines(url_events: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(url_events, list):
        return lines

    for event in url_events:
        if not isinstance(event, dict):
            continue
        url = _as_str(event.get("url"))
        title = _as_str(event.get("title"))
        if not url and not title:
            continue
        text = title or "Navegação"
        if url:
            text += f": {url}"
        lines.append(_trim_text(text, 160))
        if len(lines) >= 5:
            break
    return lines


def _collect_frame_lines(frames: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(frames, list):
        return lines

    for frame in frames:
        if not isinstance(frame, dict):
            continue
        description = _as_str(frame.get("description")) or "Frame relevante"
        detail = _as_str(frame.get("detail"))
        time_value = _as_float(frame.get("time_seconds"))
        label = f"{_format_time(time_value)} - {description}" if time_value is not None else description
        if detail:
            label += f" ({_trim_text(detail, 100)})"
        lines.append(label)
        if len(lines) >= 4:
            break
    return lines


def _collect_frame_filenames(frames: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(frames, list):
        return names
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        filename = _as_str(frame.get("filename"))
        if filename:
            names.append(filename)
        if len(names) >= 10:
            break
    return names


def _build_viewport(metadata: dict[str, Any]) -> str:
    width = metadata.get("viewport_width") or metadata.get("innerWidth")
    height = metadata.get("viewport_height") or metadata.get("innerHeight")
    if width and height:
        return f"{width}x{height}"
    return ""


def _pick_first_non_empty(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def _trim_text(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0"):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _format_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(int(seconds), 0)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
