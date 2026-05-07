from __future__ import annotations

import ctypes
import os
import signal
import threading
import time
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from app.clipboard import ClipboardService
from app.config import AppConfig
from app.devtools_mcp_context import DevToolsMcpClient, combine_evidence_contexts
from app.formatter import Formatter, FormattingError
from app.hotkey import GlobalHotkeyManager
from app.logger import setup_logging
from app.native_capture import NativeScreenRecorder, NativeScreenRecordingResult
from app.output_validator import extract_title
from app.recorder import AudioRecorder, RecorderStopReason, RecordingResult
from app.silence_detector import SilenceDetector
from app.single_instance import SingleInstanceGuard
from app.sounds import SoundNotifier
from app.state import AppStatus, StateMachine
from app.storage import LocalStorage
from app.tray import SystemTrayController
from app.transcriber import Transcriber, TranscriptionError
from app.ui import StatusNotifier
import keyboard


@dataclass
class PendingNativeCapture:
    started_at: datetime
    stop_requested: bool = False
    finalized: bool = False
    audio_result: RecordingResult | None = None
    screen_result: NativeScreenRecordingResult | None = None
    transcription_future: Future[str] | None = None
    devtools_future: Future[object | None] | None = None


@dataclass
class PendingVoiceGifCapture:
    started_at: datetime
    stop_requested: bool = False
    screen_result: NativeScreenRecordingResult | None = None
    transcription_future: Future[str] | None = None
    devtools_future: Future[object | None] | None = None


class BugVoiceReporterApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = setup_logging(
            config.log_file_path,
            debug=config.debug_save_transcription,
            log_to_file=config.log_to_file,
        )
        self.state = StateMachine()
        self.status_ui = StatusNotifier()
        self.sound_notifier = SoundNotifier(True)

        self._lock = threading.RLock()
        self._idle_reset_timer: threading.Timer | None = None
        self._restart_cancel_event = threading.Event()
        self._restart_thread: threading.Thread | None = None
        self._restart_pending = False
        self._started = False
        self._status_label = self.state.current.value
        self._single_instance = SingleInstanceGuard("Local\\bugometro")
        self._signal_installed = False
        self._old_sigint_handler = None
        self._windows_ctrl_handler = None
        self._shutdown_event = threading.Event()
        self._active_native_capture: PendingNativeCapture | None = None
        self._active_voice_gif_capture: PendingVoiceGifCapture | None = None
        self._pending_voice_audio_result: RecordingResult | None = None
        self._last_native_video_path: Path | None = None
        self._screen_capture_shortcuts_enabled = True
        self._screen_capture_shortcuts_error: str | None = None
        self._last_formatted_report: str = ""
        self._native_prework_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="native-prework",
        )

        self._build_runtime_components()
        self.tray = SystemTrayController(
            status_provider=self._get_status_label,
            can_open_last_output=self._can_open_last_output,
            on_open_last_output=self.open_last_output,
            on_quit=self.shutdown,
        )

    def _build_runtime_components(self) -> None:
        self.storage = LocalStorage(
            last_output_path=self.config.last_output_path,
            last_transcription_path=self.config.last_transcription_path,
            debug_save_transcription=self.config.debug_save_transcription,
            save_last_output=self.config.save_last_output,
        )
        self.clipboard = ClipboardService(
            clear_after_seconds=self.config.clipboard_clear_seconds
        )
        self.transcriber = Transcriber(
            provider=self.config.transcription_provider,
            api_key=self._resolve_api_key(self.config.transcription_provider),
            model=self._resolve_transcription_model(self.config.transcription_provider),
            timeout_seconds=self._resolve_timeout(self.config.transcription_provider),
            retry_limit=1,
            min_transcription_chars=12,
        )
        self.formatter = Formatter(
            provider=self.config.formatter_provider,
            api_key=self._resolve_api_key(self.config.formatter_provider),
            model=self._resolve_formatter_model(self.config.formatter_provider),
            prompt_path=self.config.prompt_path,
            timeout_seconds=self._resolve_timeout(self.config.formatter_provider),
        )
        self.devtools_mcp_client = DevToolsMcpClient(
            enabled=self.config.devtools_mcp_enabled,
            command=self.config.devtools_mcp_command,
            context_path=self.config.devtools_mcp_context_path,
            timeout_seconds=self.config.devtools_mcp_timeout_seconds,
            logger=self.logger,
            working_dir=self.config.project_root,
        )
        self.recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
            block_size=self.config.block_size,
            max_recording_seconds=self.config.max_recording_seconds,
            silence_detector=SilenceDetector(
                silence_threshold=self.config.silence_threshold,
                silence_timeout_seconds=self.config.silence_timeout_seconds,
            ),
            auto_stop_on_silence=self.config.auto_stop_on_silence,
            temp_file_factory=self.storage.create_temp_wav_path,
            logger=self.logger,
            on_speech_started=self._on_speech_started,
            on_finished=self._on_recording_finished,
        )
        self.native_capture_audio_recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
            block_size=self.config.block_size,
            max_recording_seconds=self.config.max_recording_seconds,
            silence_detector=SilenceDetector(
                silence_threshold=self.config.silence_threshold,
                silence_timeout_seconds=self.config.silence_timeout_seconds,
            ),
            auto_stop_on_silence=False,
            temp_file_factory=self.storage.create_temp_wav_path,
            logger=self.logger,
            on_speech_started=self._on_native_capture_speech_started,
            on_finished=self._on_native_capture_audio_finished,
        )
        self.native_screen_recorder = NativeScreenRecorder(
            fps=self.config.native_capture_fps,
            target_mode=self.config.native_capture_target,
            frame_limit=self.config.native_capture_frame_limit,
            annotation_hold_seconds=self.config.native_capture_annotation_hold_seconds,
            bundle_dir_factory=self.storage.create_temp_evidence_dir,
            logger=self.logger,
            on_finished=self._on_native_capture_screen_finished,
        )
        self.toggle_hotkey = GlobalHotkeyManager(
            hotkey=self.config.hotkey,
            callback=self.handle_toggle_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
            suppress=True,
            exact=True,
        )
        self.restart_hotkey = (
            GlobalHotkeyManager(
                hotkey=self.config.restart_hotkey,
                callback=self.handle_restart_hotkey,
                debounce_ms=self.config.hotkey_debounce_ms,
            )
            if self.config.restart_hotkey.strip()
            else None
        )
        self.screen_capture_hotkey = GlobalHotkeyManager(
            hotkey=self.config.screen_capture_hotkey,
            callback=self.handle_native_capture_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
            suppress=True,
            exact=True,
        )
        self.voice_gif_hotkey = GlobalHotkeyManager(
            hotkey=self.config.voice_gif_hotkey,
            callback=self.handle_voice_gif_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
            suppress=True,
            exact=True,
        )
        self.title_paste_hotkey = GlobalHotkeyManager(
            hotkey=self.config.title_paste_hotkey,
            callback=self.handle_title_paste_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
            suppress=True,
        )
        self.video_attach_hotkey = GlobalHotkeyManager(
            hotkey=self.config.video_attach_hotkey,
            callback=self.handle_native_video_attach_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
            suppress=True,
        )

    def start(self) -> None:
        self._validate_configuration()
        if not self._single_instance.acquire():
            raise RuntimeError("Já existe uma instância do Bugômetro em execução.")

        self._install_signal_handlers()
        self.storage.cleanup_sensitive_outputs()
        self.storage.cleanup_stale_temp_audio()
        self.storage.cleanup_stale_temp_evidence()
        if not self.config.log_to_file:
            self.storage.cleanup_file(self.config.log_file_path)

        self.status_ui.start()
        self._evaluate_screen_capture_hotkey_configuration()
        self.toggle_hotkey.start()
        if self.restart_hotkey is not None:
            self.restart_hotkey.start()
        if self._screen_capture_shortcuts_enabled:
            self.screen_capture_hotkey.start()
            self.voice_gif_hotkey.start()
            self.video_attach_hotkey.start()
        self.title_paste_hotkey.start()
        self.tray.start()
        self._started = True
        self._set_status_label(AppStatus.IDLE.value)
        self.logger.info(
            "Bugômetro iniciado. Hotkeys toggle=%s restart=%s captura_tela=%s gif_na_voz=%s anexar=%s titulo=%s providers transcrição=%s formatação=%s",
            self.config.hotkey,
            self.config.restart_hotkey or "desativado",
            self.config.screen_capture_hotkey,
            self.config.voice_gif_hotkey,
            self.config.video_attach_hotkey,
            self.config.title_paste_hotkey,
            self.config.transcription_provider,
            self.config.formatter_provider,
        )
        self.status_ui.show_status(
            self._startup_hud_message(),
            persistent=False,
            duration_ms=20000,
            kind="hud",
        )

    def run_forever(self) -> None:
        try:
            self.start()
            self.tray.run()
        except KeyboardInterrupt:
            self.logger.info("Encerrando aplicação por interrupção do teclado.")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._shutdown_event.is_set() and not self._started:
            return
        self._shutdown_event.set()

        if self._active_native_capture is not None:
            self.native_capture_audio_recorder.stop()
            self.native_screen_recorder.stop()
        if self._active_voice_gif_capture is not None:
            self.native_screen_recorder.stop()

        self._cancel_restart_countdown()
        self._cancel_idle_reset()
        self.toggle_hotkey.stop()
        if self.restart_hotkey is not None:
            self.restart_hotkey.stop()
        self.screen_capture_hotkey.stop()
        self.voice_gif_hotkey.stop()
        self.video_attach_hotkey.stop()
        self.title_paste_hotkey.stop()
        self.tray.stop()
        self.clipboard.stop()
        self._native_prework_executor.shutdown(wait=False, cancel_futures=True)
        self.status_ui.stop()
        self._started = False
        self._single_instance.release()
        self._restore_signal_handlers()
        threading.Thread(target=self._force_exit_soon, daemon=True).start()

    def open_last_output(self) -> None:
        if not self.config.save_last_output:
            self.status_ui.show_status(
                "MODO PRIVADO ATIVO\nnenhum relatório local foi salvo",
                persistent=False,
                duration_ms=2200,
                kind="error",
            )
            return
        if not self.storage.open_file(self.config.last_output_path):
            self.status_ui.show_status(
                "RELATÓRIO NÃO ENCONTRADO\nnenhum arquivo local está disponível agora",
                persistent=False,
                duration_ms=1800,
                kind="error",
            )

    def handle_native_capture_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info(
                    "Hotkey de captura nativa acionada no estado %s (captura_ativa=%s stop_requested=%s)",
                    self.state.current,
                    self._active_native_capture is not None,
                    self._active_native_capture.stop_requested
                    if self._active_native_capture is not None
                    else False,
                )
                self._cancel_idle_reset()

                if self.state.is_processing:
                    self.logger.info("Hotkey de captura nativa ignorada porque o app esta processando.")
                    return
                if self.state.is_recording_active or self.recorder.is_recording:
                    self.status_ui.show_status(
                        f"VOZ EM ANDAMENTO\nfinalize o relato com {self._format_hotkey_label(self.config.hotkey)} antes de abrir a captura nativa",
                        persistent=False,
                        duration_ms=4200,
                        kind="error",
                    )
                    return
                if self._restart_pending:
                    self._cancel_restart_countdown()

                if self._active_native_capture is None:
                    if self.state.current in {AppStatus.COPIED, AppStatus.ERROR}:
                        self.state.reset()
                        self._set_status_label(AppStatus.IDLE.value)
                    self._start_native_capture()
                    return

                if self._active_native_capture.stop_requested:
                    self.status_ui.show_status(
                        "ENCERRAMENTO EM ANDAMENTO\no GIF ja esta sendo finalizado; aguarde o processamento",
                        persistent=False,
                        duration_ms=3600,
                        kind="hud",
                    )
                    return

                self._active_native_capture.stop_requested = True
                self._start_native_prework_locked(self._active_native_capture)
                self.native_screen_recorder.stop()
                self.native_capture_audio_recorder.stop(RecorderStopReason.MANUAL)
                self._set_status_label("FINALIZING_NATIVE_CAPTURE")
                self.status_ui.show_status(
                    "GRAVACAO ENCERRADA\nprocessando voz, GIF e evidencias locais",
                    persistent=True,
                    kind="processing",
                )
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey de captura nativa.")
            self._move_to_error("Nao foi possivel controlar a captura nativa.")

    def handle_native_video_attach_hotkey(self) -> None:
        try:
            with self._lock:
                video_path = self._last_native_video_path
                active_native = self._active_native_capture
                active_voice_gif = self._active_voice_gif_capture
                current_state = self.state.current
                processing_now = self.state.is_processing or (
                    active_native is not None and active_native.stop_requested
                )

            if video_path is None or not video_path.exists():
                if active_voice_gif is not None:
                    message = (
                        "GIF AINDA EM PREPARO\no bug report em texto ja pode ser usado; aguarde o arquivo finalizar"
                    )
                elif active_native is not None and active_native.stop_requested:
                    message = (
                        "GIF AINDA EM PREPARO\na captura terminou; aguarde a finalizacao"
                    )
                elif active_native is not None:
                    message = (
                        f"GIF AINDA NAO DISPONIVEL\na captura esta em andamento; finalize com {self._format_hotkey_label(self.config.screen_capture_hotkey)}"
                    )
                elif processing_now:
                    message = (
                        "GIF AINDA EM PROCESSAMENTO\naguarde a analise terminar antes de colar com CTRL+SHIFT+V"
                    )
                elif current_state == AppStatus.COPIED:
                    message = (
                        "ULTIMO FLUXO SEM GIF VALIDO\nrepita a captura nativa para gerar uma nova evidencia"
                    )
                else:
                    message = (
                        f"NENHUM GIF PARA COLAR\ninicie uma captura nativa com {self._format_hotkey_label(self.config.screen_capture_hotkey)}"
                    )
                self.status_ui.show_status(
                    message,
                    persistent=False,
                    duration_ms=3600,
                    kind="error",
                )
                return

            self.clipboard.copy_files([video_path])
            self._paste_clipboard_after_hotkey_release(self.config.video_attach_hotkey)
            self.status_ui.show_status(
                "COLANDO GIF\nmantenha o campo de anexo em foco",
                persistent=False,
                duration_ms=3600,
                kind="success",
            )
            self.sound_notifier.play_success()
        except Exception:
            self.logger.exception("Falha ao colar o GIF nativo pelo clipboard.")
            self.status_ui.show_status(
                "GIF INDISPONIVEL\na evidencia nao foi encontrada para colagem",
                persistent=False,
                duration_ms=2400,
                kind="error",
            )

    def handle_voice_gif_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info(
                    "Hotkey de GIF durante voz acionada no estado %s (gif_ativo=%s stop_requested=%s)",
                    self.state.current,
                    self._active_voice_gif_capture is not None,
                    self._active_voice_gif_capture.stop_requested
                    if self._active_voice_gif_capture is not None
                    else False,
                )
                self._cancel_idle_reset()

                if self.state.is_processing:
                    self.logger.info("Hotkey de GIF durante voz ignorada porque o app esta processando.")
                    return
                if self._active_native_capture is not None:
                    self.status_ui.show_status(
                        "CAPTURA NATIVA EM ANDAMENTO\nfinalize esse fluxo antes de alternar o GIF da voz",
                        persistent=False,
                        duration_ms=3000,
                        kind="error",
                    )
                    return
                if not self.state.is_recording_active or not self.recorder.is_recording:
                    self.status_ui.show_status(
                        f"VOZ NAO INICIADA\ncomece o relato com {self._format_hotkey_label(self.config.hotkey)} antes de gravar o GIF incremental",
                        persistent=False,
                        duration_ms=3600,
                        kind="error",
                    )
                    return

                if self._active_voice_gif_capture is None:
                    self.native_screen_recorder.start()
                    self._active_voice_gif_capture = PendingVoiceGifCapture(
                        started_at=datetime.now()
                    )
                    self._last_native_video_path = None
                    self._set_status_label("VOICE_WITH_GIF")
                    self.status_ui.show_status(
                        "GIF INCREMENTAL INICIADO\ncontinue explicando o bug; finalize o GIF com "
                        f"{self._format_hotkey_label(self.config.voice_gif_hotkey)} ou encerre tudo com {self._format_hotkey_label(self.config.hotkey)}",
                        persistent=True,
                        kind="recording",
                    )
                    self.sound_notifier.play_start()
                    return

                if self._active_voice_gif_capture.stop_requested:
                    self.status_ui.show_status(
                        "GIF FINALIZANDO\na voz continua gravando enquanto o arquivo e preparado",
                        persistent=False,
                        duration_ms=2800,
                        kind="hud",
                    )
                    return

                self._active_voice_gif_capture.stop_requested = True
                self._start_voice_gif_prework_locked(self._active_voice_gif_capture)
                self.native_screen_recorder.stop()
                self.status_ui.show_status(
                    "GIF ENCERRADO\ncontinue falando; finalize o audio com "
                    f"{self._format_hotkey_label(self.config.hotkey)}",
                    persistent=True,
                    kind="recording",
                )
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey de GIF durante voz.")
            self._move_to_error("Nao foi possivel controlar o GIF incremental.")

    def _paste_clipboard_after_hotkey_release(self, hotkey: str) -> None:
        threading.Thread(
            target=self._paste_clipboard_worker,
            args=(hotkey,),
            name="native-gif-paste",
            daemon=True,
        ).start()

    def _paste_clipboard_worker(self, hotkey: str) -> None:
        self._wait_for_hotkey_release(hotkey)
        keyboard.send("ctrl+v")

    @staticmethod
    def _wait_for_hotkey_release(hotkey: str) -> None:
        keys = [segment.strip().lower() for segment in hotkey.split("+") if segment.strip()]
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            if not any(keyboard.is_pressed(key) for key in keys):
                break
            time.sleep(0.01)
        time.sleep(0.02)

    def _start_native_capture(self) -> None:
        try:
            self._cancel_restart_countdown()
            self._last_native_video_path = None
            self.native_screen_recorder.start()
            self.native_capture_audio_recorder.start()
            self._active_native_capture = PendingNativeCapture(started_at=datetime.now())
            self._set_status_label("NATIVE_CAPTURE")
            self.status_ui.show_status(
                "CAPTURA NATIVA INICIADA\nreproduza o bug, fale normalmente e use o scroll pressionado para apontar\nfinalize com "
                f"{self._format_hotkey_label(self.config.screen_capture_hotkey)}",
                persistent=True,
                kind="recording",
            )
            self.sound_notifier.play_start()
        except Exception as exc:
            self.logger.exception("Nao foi possivel iniciar a captura nativa.")
            self.native_screen_recorder.stop()
            self.native_capture_audio_recorder.stop(RecorderStopReason.FAILED)
            raise RuntimeError(
                "Nao foi possivel iniciar a captura nativa. Revise as dependencias e tente novamente."
            ) from exc

    def _on_native_capture_speech_started(self) -> None:
        self.status_ui.show_status(
            f"VOZ DETECTADA\ncontinue narrando e finalize a captura com {self._format_hotkey_label(self.config.screen_capture_hotkey)}",
            persistent=True,
            kind="recording",
        )

    def _on_native_capture_audio_finished(self, result: RecordingResult) -> None:
        with self._lock:
            capture = self._active_native_capture
            if capture is None:
                self.storage.cleanup_file(result.path)
                return
            capture.audio_result = result
            self._start_native_prework_locked(capture)
        self._maybe_finalize_native_capture()

    def _on_native_capture_screen_finished(
        self,
        result: NativeScreenRecordingResult,
    ) -> None:
        handled_native_capture = False
        with self._lock:
            capture = self._active_native_capture
            if capture is None:
                voice_gif = self._active_voice_gif_capture
                if voice_gif is None:
                    return
                voice_gif.screen_result = result
                self._start_voice_gif_prework_locked(voice_gif)
                if result.reason != "failed" and result.video_path is not None:
                    self._last_native_video_path = result.video_path
                self._pending_voice_audio_result = None
                self._active_voice_gif_capture = None
                if result.reason == "failed":
                    self.status_ui.show_status(
                        "GIF INDISPONIVEL\no bug report em texto continua pronto para uso",
                        persistent=False,
                        duration_ms=3200,
                        kind="error",
                    )
                else:
                    self.status_ui.show_status(
                        "GIF PRONTO\nuse Ctrl+Shift+V no campo de anexo quando quiser",
                        persistent=False,
                        duration_ms=3600,
                        kind="success",
                    )
                return
            else:
                capture.screen_result = result
                if result.reason != "failed" and result.video_path is not None:
                    self._last_native_video_path = result.video_path
                self._start_native_prework_locked(capture)
                handled_native_capture = True
        if handled_native_capture:
            self._maybe_finalize_native_capture()

    def _start_native_prework_locked(self, capture: PendingNativeCapture) -> None:
        if (
            capture.audio_result is not None
            and capture.transcription_future is None
            and capture.audio_result.reason != RecorderStopReason.FAILED
            and capture.audio_result.path is not None
        ):
            audio_path = capture.audio_result.path
            capture.transcription_future = self._native_prework_executor.submit(
                self._transcribe_native_capture_audio,
                audio_path,
            )
            self.logger.info(
                "Transcricao da captura nativa iniciada em paralelo ao fechamento do GIF."
            )

        if capture.stop_requested and capture.devtools_future is None:
            capture.devtools_future = self._native_prework_executor.submit(
                self.devtools_mcp_client.collect_context
            )

    def _start_voice_gif_prework_locked(
        self,
        capture: PendingVoiceGifCapture,
        audio_result: RecordingResult | None = None,
    ) -> None:
        if (
            audio_result is not None
            and capture.transcription_future is None
            and audio_result.reason != RecorderStopReason.FAILED
            and audio_result.path is not None
        ):
            capture.transcription_future = self._native_prework_executor.submit(
                self._transcribe_native_capture_audio,
                audio_result.path,
            )
            self.logger.info(
                "Transcricao da voz iniciada em paralelo ao fechamento do GIF incremental."
            )

        if capture.devtools_future is None and (
            capture.stop_requested
            or capture.screen_result is not None
            or audio_result is not None
        ):
            capture.devtools_future = self._native_prework_executor.submit(
                self.devtools_mcp_client.collect_context
            )

    def _transcribe_native_capture_audio(self, audio_path: Path) -> str:
        started_at = time.perf_counter()
        transcription = self.transcriber.transcribe(audio_path)
        self.storage.save_last_transcription(transcription)
        self.logger.info(
            "Transcricao da captura nativa concluida em %.2fs.",
            time.perf_counter() - started_at,
        )
        return transcription

    def _maybe_finalize_native_capture(self) -> None:
        with self._lock:
            capture = self._active_native_capture
            if capture is None or capture.finalized:
                return
            if capture.audio_result is None or capture.screen_result is None:
                return
            self._start_native_prework_locked(capture)
            capture.finalized = True
            audio_result = capture.audio_result
            screen_result = capture.screen_result
            transcription_future = capture.transcription_future
            devtools_future = capture.devtools_future

        threading.Thread(
            target=self._process_native_capture,
            args=(audio_result, screen_result, transcription_future, devtools_future),
            name="native-capture-processor",
            daemon=True,
        ).start()

    def _process_native_capture(
        self,
        audio_result: RecordingResult,
        screen_result: NativeScreenRecordingResult,
        transcription_future: Future[str] | None = None,
        devtools_future: Future[object | None] | None = None,
    ) -> None:
        audio_path = audio_result.path
        try:
            if audio_result.reason == RecorderStopReason.FAILED:
                raise RuntimeError("Nao foi possivel concluir a gravacao de voz da captura nativa.")
            if audio_path is None:
                raise RuntimeError("Arquivo de audio da captura nativa nao foi gerado.")
            if screen_result.reason == "failed" or screen_result.video_path is None:
                raise RuntimeError(
                    screen_result.error_message
                    or "Nao foi possivel concluir a gravacao de tela nativa."
                )

            self.status_ui.show_status(
                "PROCESSANDO CAPTURA NATIVA\nfinalizando voz, GIF e evidencias locais em paralelo",
                persistent=True,
                kind="processing",
            )
            if transcription_future is not None:
                transcription = transcription_future.result()
            else:
                transcription = self._transcribe_native_capture_audio(audio_path)

            with self._lock:
                self._set_status_label(AppStatus.PROCESSING_FORMATTING.value)

            context = screen_result.to_context()
            devtools_context = (
                devtools_future.result()
                if devtools_future is not None
                else self.devtools_mcp_client.collect_context()
            )
            if devtools_context is not None:
                self.logger.info(
                    "Contexto tecnico adicional do DevTools MCP anexado ao fluxo da captura nativa."
                )
            evidence_context = combine_evidence_contexts(context, devtools_context) or context
            self._last_native_video_path = context.video_path
            formatted = self.formatter.format_bug_report(
                transcription,
                evidence_context=evidence_context,
            )
            self._last_formatted_report = formatted
            self.storage.save_last_output(formatted)
            file_bundle = (
                evidence_context.evidence_file_paths()
                if self.config.clipboard_include_files
                else []
            )
            clipboard_report = self._clipboard_body_from_report(formatted)
            self.clipboard.copy_payload(clipboard_report, file_bundle)

            with self._lock:
                if self.state.current in {AppStatus.ERROR, AppStatus.COPIED}:
                    self.state.reset()
                if self.state.current == AppStatus.IDLE:
                    self._transition_state(AppStatus.PROCESSING_FORMATTING)
                self._transition_state(AppStatus.COPIED)
                self._set_status_label(AppStatus.COPIED.value)
                self._active_native_capture = None

            self.status_ui.show_status(
                "BUG REPORT PRONTO\nCtrl+\" cola apenas o titulo\nCtrl+V cola o corpo do bug report\nCtrl+Shift+V cola o GIF no campo de anexo",
                persistent=False,
                duration_ms=5200,
                kind="success",
            )
            self.sound_notifier.play_success()
            self.logger.info("Fluxo de captura nativa concluido com sucesso.")
            self._schedule_idle_reset()
        except (FormattingError, TranscriptionError, RuntimeError, ValueError) as exc:
            self._move_to_error(str(exc), cleanup_path=audio_path)
        except Exception:
            self.logger.exception("Falha inesperada durante o processamento da captura nativa.")
            self._move_to_error(
                "Ocorreu um erro inesperado durante o processamento da captura nativa.",
                cleanup_path=audio_path,
            )
        finally:
            self.storage.cleanup_file(audio_path)

    def handle_toggle_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info("Hotkey toggle acionada no estado %s", self.state.current)
                self._cancel_idle_reset()

                if self._restart_pending:
                    self._cancel_restart_countdown()
                    self.status_ui.show_status(
                        "REINICIO CANCELADO\no relato atual foi mantido",
                        persistent=False,
                        duration_ms=1800,
                        kind="hud",
                    )
                    self.logger.info("Reinicio agendado cancelado pela hotkey toggle.")
                    return

                if self.state.is_processing:
                    self.logger.info("Hotkey toggle ignorada porque o app esta processando.")
                    return

                if self._active_native_capture is not None:
                    self.status_ui.show_status(
                        f"CAPTURA DE TELA EM ANDAMENTO\ntermine a captura com {self._format_hotkey_label(self.config.screen_capture_hotkey)} antes de gravar so a voz",
                        persistent=False,
                        duration_ms=4200,
                        kind="error",
                    )
                    return

                if self.state.current in {AppStatus.COPIED, AppStatus.ERROR}:
                    self.state.reset()
                    self._set_status_label(AppStatus.IDLE.value)

                if self.state.current == AppStatus.IDLE:
                    self._start_voice_recording()
                    return

                if self.state.current in {AppStatus.RECORDING, AppStatus.SILENCE_COUNTDOWN}:
                    if (
                        self._active_voice_gif_capture is not None
                        and not self._active_voice_gif_capture.stop_requested
                    ):
                        self._active_voice_gif_capture.stop_requested = True
                        self._start_voice_gif_prework_locked(
                            self._active_voice_gif_capture
                        )
                        self.native_screen_recorder.stop()
                    if self.recorder.stop(RecorderStopReason.MANUAL):
                        self.state.transition(AppStatus.PROCESSING_TRANSCRIPTION)
                        self._set_status_label(AppStatus.PROCESSING_TRANSCRIPTION.value)
                        self.status_ui.show_status(
                            "RELATO ENCERRADO\nfinalizando audio e GIF antes de montar o bug report"
                            if self._active_voice_gif_capture is not None
                            else "RELATO ENCERRADO\ntranscrevendo sua fala e montando o bug report",
                            persistent=True,
                            kind="processing",
                        )
                    return
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey toggle.")
            self._move_to_error("Nao foi possivel processar o atalho global.")

    def handle_restart_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info(
                    "Hotkey de reinicio acionada no estado %s",
                    self.state.current,
                )
                self._cancel_idle_reset()

                if self.state.is_processing:
                    self.logger.info("Hotkey de reinicio ignorada porque o app esta processando.")
                    return

                if self._active_native_capture is not None:
                    self.status_ui.show_status(
                        "CAPTURA DE TELA EM ANDAMENTO\nreinicio de voz fica bloqueado enquanto a evidencia estiver ativa",
                        persistent=False,
                        duration_ms=1800,
                        kind="error",
                    )
                    return
                if self._active_voice_gif_capture is not None:
                    self.status_ui.show_status(
                        "GIF INCREMENTAL EM ANDAMENTO\nfinalize ou encerre o relato antes de reiniciar",
                        persistent=False,
                        duration_ms=2400,
                        kind="error",
                    )
                    return

                if self._restart_pending:
                    self.logger.info("Hotkey de reinicio ignorada porque o reinicio ja esta agendado.")
                    return

                if self.state.current in {AppStatus.RECORDING, AppStatus.SILENCE_COUNTDOWN}:
                    if self.recorder.stop(RecorderStopReason.RESTART):
                        self.status_ui.show_status(
                            "RELATO DESCARTADO\niniciando uma nova captura de voz",
                            persistent=True,
                            kind="countdown",
                        )
                        self.sound_notifier.play_discard()
                        self.logger.info("Gravacao atual sera descartada.")
                    return

                self.logger.info("Hotkey de reinicio ignorada fora de uma gravacao ativa.")
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey de reinicio.")
            self._move_to_error("Nao foi possivel processar o atalho de reinicio.")

    def _transition_state(
        self,
        new_state: AppStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        if self.state.current == new_state:
            return
        if not self.state.can_transition(new_state):
            self.logger.debug(
                "Transicao de estado ignorada: %s -> %s",
                self.state.current,
                new_state,
            )
            return
        self.state.transition(new_state, error_message=error_message)

    def _start_voice_recording(self) -> None:
        try:
            self._cancel_restart_countdown()
            self._active_voice_gif_capture = None
            self._pending_voice_audio_result = None
            self._last_native_video_path = None
            self.recorder.start()
            self.state.transition(AppStatus.RECORDING)
            self._set_status_label(AppStatus.RECORDING.value)
            self.status_ui.show_status(
                f"GRAVACAO DE VOZ INICIADA\nfale normalmente e finalize com {self._format_hotkey_label(self.config.hotkey)}",
                persistent=True,
                kind="recording",
            )
            self.sound_notifier.play_start()
            self.logger.info("Gravação iniciada.")
        except Exception as exc:
            self.logger.exception("Não foi possível iniciar a gravação.")
            raise RuntimeError(
                "Não foi possível iniciar a gravação. Verifique o microfone e tente novamente."
            ) from exc

    def _on_speech_started(self) -> None:
        self.logger.info("Primeira fala detectada.")
        self.status_ui.show_status(
            f"VOZ DETECTADA\ncontinue falando; quando terminar, use {self._format_hotkey_label(self.config.hotkey)}",
            persistent=True,
            kind="recording",
        )

    def _on_recording_finished(self, result: RecordingResult) -> None:
        if result.reason == RecorderStopReason.RESTART:
            with self._lock:
                if self.state.current in {AppStatus.RECORDING, AppStatus.SILENCE_COUNTDOWN}:
                    self.state.transition(AppStatus.IDLE)
                    self._set_status_label(AppStatus.IDLE.value)
            self.storage.cleanup_file(result.path)
            self.logger.info(
                "Gravação descartada. Reinício em %s segundos.",
                self.config.restart_delay_seconds,
            )
            self._schedule_restart_countdown()
            return

        if result.reason == RecorderStopReason.FAILED:
            self._move_to_error(
                "Não foi possível concluir a gravação do microfone.",
                cleanup_path=result.path,
            )
            return

        with self._lock:
            if self.state.current in {AppStatus.RECORDING, AppStatus.SILENCE_COUNTDOWN}:
                self.state.transition(AppStatus.PROCESSING_TRANSCRIPTION)
                self._set_status_label(AppStatus.PROCESSING_TRANSCRIPTION.value)
            voice_gif = self._active_voice_gif_capture
            if voice_gif is not None:
                if not voice_gif.stop_requested:
                    voice_gif.stop_requested = True
                    self.native_screen_recorder.stop()
                self._start_voice_gif_prework_locked(voice_gif, result)
                transcription_future = voice_gif.transcription_future
                devtools_future = voice_gif.devtools_future
                screen_result = None
                gif_requested = True
                if voice_gif.screen_result is not None:
                    self._active_voice_gif_capture = None
            else:
                screen_result = None
                transcription_future = None
                devtools_future = None
                gif_requested = self._last_native_video_path is not None

        if result.reason == RecorderStopReason.SILENCE:
            self.logger.info(
                "Silêncio contínuo de %.1f segundos detectado; encerrando gravação.",
                self.config.silence_timeout_seconds,
            )

        self._start_voice_processing(
            result,
            screen_result,
            transcription_future=transcription_future,
            devtools_future=devtools_future,
            gif_requested=gif_requested,
        )

    def _start_voice_processing(
        self,
        result: RecordingResult,
        screen_result: NativeScreenRecordingResult | None = None,
        transcription_future: Future[str] | None = None,
        devtools_future: Future[object | None] | None = None,
        gif_requested: bool = False,
    ) -> None:
        threading.Thread(
            target=self._process_voice_recording,
            args=(
                result,
                screen_result,
                transcription_future,
                devtools_future,
                gif_requested,
            ),
            name="recording-processor",
            daemon=True,
        ).start()

    def _process_voice_recording(
        self,
        result: RecordingResult,
        screen_result: NativeScreenRecordingResult | None = None,
        transcription_future: Future[str] | None = None,
        devtools_future: Future[object | None] | None = None,
        gif_requested: bool = False,
    ) -> None:
        audio_path = result.path
        try:
            self.status_ui.show_status(
                "PROCESSANDO RELATO\ntranscrevendo o audio para montar o bug report",
                persistent=True,
                kind="processing",
            )
            if audio_path is None:
                raise ValueError("Arquivo de audio do relato nao foi gerado.")
            if devtools_future is None:
                devtools_future = self._native_prework_executor.submit(
                    self.devtools_mcp_client.collect_context
                )
            if transcription_future is not None:
                transcription = transcription_future.result()
            else:
                transcription = self._transcribe_native_capture_audio(audio_path)

            with self._lock:
                self.state.transition(AppStatus.PROCESSING_FORMATTING)
                self._set_status_label(AppStatus.PROCESSING_FORMATTING.value)

            self.status_ui.show_status(
                "TRANSCRICAO CONCLUIDA\norganizando o template final do bug report",
                persistent=True,
                kind="processing",
            )

            devtools_context = devtools_future.result()
            if devtools_context is not None:
                self.logger.info(
                    "Contexto tecnico adicional do DevTools MCP anexado ao fluxo de voz."
                )
            evidence_context = devtools_context
            if (
                screen_result is not None
                and screen_result.reason != "failed"
                and screen_result.video_path is not None
            ):
                native_context = screen_result.to_context()
                evidence_context = (
                    combine_evidence_contexts(native_context, devtools_context)
                    or native_context
                )
                self._last_native_video_path = native_context.video_path

            formatted = self.formatter.format_bug_report(
                transcription,
                evidence_context=evidence_context,
            )
            self._last_formatted_report = formatted
            self.storage.save_last_output(formatted)
            file_bundle = (
                evidence_context.evidence_file_paths()
                if evidence_context is not None and self.config.clipboard_include_files
                else []
            )
            clipboard_report = self._clipboard_body_from_report(formatted)
            self.clipboard.copy_payload(clipboard_report, file_bundle)

            with self._lock:
                self.state.transition(AppStatus.COPIED)
                self._set_status_label(AppStatus.COPIED.value)

            final_message = "BUG REPORT PRONTO\nCtrl+\" cola apenas o titulo\nCtrl+V cola o corpo do bug report"
            if gif_requested:
                final_message = (
                    "BUG REPORT PRONTO\nCtrl+\" cola apenas o titulo\n"
                    "Ctrl+V cola o corpo do bug report\nCtrl+Shift+V cola o GIF quando estiver pronto"
                )
            if result.reason == RecorderStopReason.MAX_DURATION:
                final_message = (
                    "TEMPO MAXIMO ATINGIDO\nCtrl+\" cola apenas o titulo\n"
                    "o corpo do bug report ja esta pronto no Ctrl+V"
                )

            self.status_ui.show_status(
                final_message,
                persistent=False,
                duration_ms=5200,
                kind="success",
            )
            self.sound_notifier.play_success()
            self.logger.info("Fluxo concluído com sucesso.")
            self._schedule_idle_reset()
        except (TranscriptionError, FormattingError, ValueError) as exc:
            self._move_to_error(str(exc), cleanup_path=audio_path)
        except Exception:
            self.logger.exception("Falha inesperada durante o processamento.")
            self._move_to_error(
                "Ocorreu um erro inesperado durante o processamento do relato.",
                cleanup_path=audio_path,
            )
        finally:
            self.storage.cleanup_file(audio_path)

    def _move_to_error(self, message: str, cleanup_path: Path | None = None) -> None:
        with self._lock:
            if self._active_native_capture is not None:
                self.native_screen_recorder.stop()
                self.native_capture_audio_recorder.stop()
            if self._active_voice_gif_capture is not None:
                self.native_screen_recorder.stop()
            self._active_native_capture = None
            self._active_voice_gif_capture = None
            self._pending_voice_audio_result = None
            self._last_native_video_path = None
            if self.state.current != AppStatus.ERROR and self.state.can_transition(AppStatus.ERROR):
                self.state.transition(AppStatus.ERROR, error_message=message)
                self._set_status_label(AppStatus.ERROR.value)

        if cleanup_path is not None:
            self.storage.cleanup_file(cleanup_path)

        self.logger.error(message)
        self.sound_notifier.play_error()
        self.status_ui.show_status(
            f"FLUXO INTERROMPIDO\n{message}",
            persistent=False,
            duration_ms=5600,
            kind="error",
        )
        self._schedule_idle_reset()

    def handle_title_paste_hotkey(self) -> None:
        try:
            title = extract_title(self._last_formatted_report).strip()
            if not title:
                self.status_ui.show_status(
                    "SEM TITULO PARA COLAR\ngere um bug report antes de usar esse atalho",
                    persistent=False,
                    duration_ms=2400,
                    kind="error",
                )
                return
            self._write_text_after_hotkey_release(self.config.title_paste_hotkey, title)
            self.status_ui.show_status(
                "TITULO COLADO\nCtrl+V cola o corpo do bug report",
                persistent=False,
                duration_ms=1500,
                kind="hud",
            )
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey de colar titulo.")
            self.status_ui.show_status(
                "FALHA AO COLAR TITULO\ntente novamente no campo de titulo",
                persistent=False,
                duration_ms=2200,
                kind="error",
            )

    def _write_text_after_hotkey_release(self, hotkey: str, text: str) -> None:
        threading.Thread(
            target=self._write_text_worker,
            args=(hotkey, text),
            name="title-paste",
            daemon=True,
        ).start()

    def _write_text_worker(self, hotkey: str, text: str) -> None:
        self._wait_for_hotkey_release(hotkey)
        keyboard.write(text)

    @staticmethod
    def _clipboard_body_from_report(report: str) -> str:
        lines = report.splitlines()
        index = 0
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            return report.strip()

        title = extract_title(report).strip()
        if not title:
            return report.strip()

        first_line = lines[index].strip()
        normalized_first = _normalize_heading(first_line)
        consumed_title = False
        if normalized_first.startswith("titulo:"):
            index += 1
            consumed_title = True
            if not first_line.split(":", 1)[1].strip():
                while index < len(lines) and not lines[index].strip():
                    index += 1
                if index < len(lines):
                    index += 1
        elif normalized_first.rstrip(":") == "titulo":
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
            if index < len(lines):
                index += 1
                consumed_title = True
        elif first_line == title:
            index += 1
            consumed_title = True

        if not consumed_title:
            return report.strip()
        while index < len(lines) and not lines[index].strip():
            index += 1
        return "\n".join(lines[index:]).strip()

    def _schedule_idle_reset(self, delay_seconds: float = 2.8) -> None:
        self._cancel_idle_reset()
        self._idle_reset_timer = threading.Timer(
            delay_seconds,
            self._reset_to_idle_if_terminal,
        )
        self._idle_reset_timer.daemon = True
        self._idle_reset_timer.start()

    def _cancel_idle_reset(self) -> None:
        if self._idle_reset_timer is not None:
            self._idle_reset_timer.cancel()
            self._idle_reset_timer = None

    def _schedule_restart_countdown(self) -> None:
        self._cancel_restart_countdown()
        self._restart_pending = True
        self._restart_cancel_event = threading.Event()
        self._restart_thread = threading.Thread(
            target=self._run_restart_countdown,
            name="restart-countdown",
            daemon=True,
        )
        self._restart_thread.start()

    def _cancel_restart_countdown(self) -> None:
        if self._restart_pending:
            self._restart_cancel_event.set()
        self._restart_pending = False
        self._restart_thread = None

    def _run_restart_countdown(self) -> None:
        self._set_status_label("RESTART_PENDING")
        for remaining in range(self.config.restart_delay_seconds, 0, -1):
            self.status_ui.show_status(
                f"RESET EM {remaining}s",
                persistent=True,
                kind="countdown",
            )
            if self._restart_cancel_event.wait(1):
                return

        with self._lock:
            if self._restart_cancel_event.is_set():
                return
            self._restart_pending = False
            if (
                self.state.is_processing
                or self.recorder.is_recording
                or self._active_native_capture is not None
            ):
                self.logger.info("Reinício automático cancelado por mudança de estado.")
                return
            if self.state.current != AppStatus.IDLE:
                self.logger.info("Reinício automático cancelado porque o estado deixou de ser IDLE.")
                return
            try:
                self._start_voice_recording()
            except Exception:
                self.logger.exception("Falha ao reiniciar a gravação após descarte.")
                self._move_to_error("Não foi possível reiniciar a gravação automaticamente.")

    def _reset_to_idle_if_terminal(self) -> None:
        with self._lock:
            if self.state.current in {AppStatus.COPIED, AppStatus.ERROR}:
                self.state.reset()
                self._set_status_label(AppStatus.IDLE.value)
                self.status_ui.hide()
                self.logger.info("Aplicação voltou para IDLE.")

    def _validate_configuration(self) -> None:
        self._require_provider_key(self.config.transcription_provider)
        self._require_provider_key(self.config.formatter_provider)
        if not self.config.prompt_path.exists():
            raise RuntimeError(
                f"Arquivo de prompt nao encontrado em {self.config.prompt_path}"
            )
        prompt_content = self.config.prompt_path.read_text(encoding="utf-8")
        if "{{TRANSCRICAO}}" not in prompt_content:
            raise RuntimeError(
                "O arquivo de prompt precisa conter o placeholder {{TRANSCRICAO}}."
            )
        capture_hotkey = self._normalize_hotkey(self.config.screen_capture_hotkey)
        attach_hotkey = self._normalize_hotkey(self.config.video_attach_hotkey)
        if capture_hotkey == attach_hotkey:
            raise RuntimeError(
                "Conflito de hotkeys: APP_VIDEO_ATTACH_HOTKEY nao pode ser igual a APP_SCREEN_CAPTURE_HOTKEY."
            )

    def _evaluate_screen_capture_hotkey_configuration(self) -> None:
        self._screen_capture_shortcuts_enabled = True
        self._screen_capture_shortcuts_error = None

        configured = {
            "APP_SCREEN_CAPTURE_HOTKEY": self.config.screen_capture_hotkey,
            "APP_VOICE_GIF_HOTKEY": self.config.voice_gif_hotkey,
            "APP_VIDEO_ATTACH_HOTKEY": self.config.video_attach_hotkey,
            "APP_TITLE_PASTE_HOTKEY": self.config.title_paste_hotkey,
        }
        seen: dict[str, str] = {}
        for key, value in configured.items():
            normalized = self._normalize_hotkey(value)
            if not normalized:
                self._screen_capture_shortcuts_enabled = False
                self._screen_capture_shortcuts_error = (
                    f"{key} esta vazio. Corrija o .env e reinicie o app."
                )
                break
            if normalized in seen:
                other = seen[normalized]
                self._screen_capture_shortcuts_enabled = False
                self._screen_capture_shortcuts_error = (
                    f"Conflito de hotkeys: {other} e {key} usam o mesmo atalho."
                )
                break
            seen[normalized] = key

        if not self._screen_capture_shortcuts_enabled:
            self.logger.error(self._screen_capture_shortcuts_error)
            self.status_ui.show_status(
                f"ATALHOS DE CAPTURA BLOQUEADOS\n{self._screen_capture_shortcuts_error}",
                persistent=False,
                duration_ms=8000,
                kind="error",
            )

    def _set_status_label(self, value: str) -> None:
        self._status_label = value
        self.tray.refresh()

    def _get_status_label(self) -> str:
        return self._status_label

    def _startup_hud_message(self) -> str:
        privacy_note = (
            "modo privado ativado"
            if not self.config.save_last_output
            else "histórico local ativo"
        )
        return (
            "BUGÔMETRO\n"
            f"VOZ = {self._format_hotkey_label(self.config.hotkey)}\n"
            + (
                f"REINICIAR = {self._format_hotkey_label(self.config.restart_hotkey)}\n"
                if self.config.restart_hotkey.strip()
                else ""
            )
            + f"TELA + VOZ = {self._format_hotkey_label(self.config.screen_capture_hotkey)}\n"
            + f"GIF NA VOZ = {self._format_hotkey_label(self.config.voice_gif_hotkey)}\n"
            "SETA NO GIF = SCROLL + ARRASTAR\n"
            f"COLAR TITULO = {self._format_hotkey_label(self.config.title_paste_hotkey)}\n"
            "COLAR TEXTO = CTRL + V\n"
            f"COLAR GIF = {self._format_hotkey_label(self.config.video_attach_hotkey)}\n"
            f"{privacy_note}"
        )

    @staticmethod
    def _normalize_hotkey(hotkey: str) -> str:
        return "+".join(
            segment.strip().lower()
            for segment in hotkey.split("+")
            if segment.strip()
        )

    @staticmethod
    def _format_hotkey_label(hotkey: str) -> str:
        normalized = hotkey.strip().lower()
        replacements = {
            "ctrl": "CTRL",
            "shift": "SHIFT",
            "tab": "TAB",
            "caps lock": "CAPS",
            "'": '"',
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        return normalized.upper().replace("+", " + ")

    def _require_provider_key(self, provider: str) -> None:
        if provider == "openai" and not self.config.openai_api_key:
            raise RuntimeError(
                "A variável OPENAI_API_KEY não foi configurada. Preencha o .env."
            )
        if provider == "gemini" and not self.config.gemini_api_key:
            raise RuntimeError(
                "A variável GEMINI_API_KEY não foi configurada. Preencha o .env."
            )
        if provider not in {"openai", "gemini"}:
            raise RuntimeError("Provider inválido no .env. Use 'openai' ou 'gemini'.")

    def _resolve_api_key(self, provider: str) -> str:
        if provider == "openai":
            return self.config.openai_api_key
        return self.config.gemini_api_key

    def _resolve_timeout(self, provider: str) -> float:
        if provider == "openai":
            return self.config.openai_timeout_seconds
        return self.config.gemini_timeout_seconds

    def _resolve_transcription_model(self, provider: str) -> str:
        if provider == "openai":
            return self.config.openai_transcription_model
        return self.config.gemini_transcription_model

    def _resolve_formatter_model(self, provider: str) -> str:
        if provider == "openai":
            return self.config.openai_formatter_model
        return self.config.gemini_formatter_model

    def _can_open_last_output(self) -> bool:
        return self.config.save_last_output and self.config.last_output_path.exists()

    def _install_signal_handlers(self) -> None:
        if self._signal_installed:
            return
        self._old_sigint_handler = signal.getsignal(signal.SIGINT)

        def handle_sigint(signum, frame) -> None:
            del signum, frame
            self.logger.info("Encerrando aplicação por sinal SIGINT.")
            self._request_shutdown()

        signal.signal(signal.SIGINT, handle_sigint)
        self._install_windows_console_handler()
        self._signal_installed = True

    def _restore_signal_handlers(self) -> None:
        if not self._signal_installed:
            return
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._old_sigint_handler)
        self._restore_windows_console_handler()
        self._signal_installed = False

    def _request_shutdown(self) -> None:
        if self._shutdown_event.is_set():
            return
        threading.Thread(
            target=self.shutdown,
            name="shutdown-request",
            daemon=True,
        ).start()

    @staticmethod
    def _force_exit_soon() -> None:
        time.sleep(0.8)
        os._exit(0)

    def _install_windows_console_handler(self) -> None:
        if os.name != "nt" or self._windows_ctrl_handler is not None:
            return

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def handle_console_event(ctrl_type: int) -> bool:
            if ctrl_type in {0, 1, 2, 5, 6}:
                self.logger.info("Encerrando aplicação por evento do console.")
                self._request_shutdown()
                return True
            return False

        self._windows_ctrl_handler = handler_type(handle_console_event)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(self._windows_ctrl_handler, True)

    def _restore_windows_console_handler(self) -> None:
        if os.name != "nt" or self._windows_ctrl_handler is None:
            return
        ctypes.windll.kernel32.SetConsoleCtrlHandler(self._windows_ctrl_handler, False)
        self._windows_ctrl_handler = None


def _normalize_heading(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip())
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return normalized.lower()


def main() -> None:
    config = AppConfig.load()
    app = BugVoiceReporterApp(config)
    app.run_forever()


if __name__ == "__main__":
    main()
