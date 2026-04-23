from __future__ import annotations

import ctypes
import os
import signal
import threading
import time
from ctypes import wintypes
from pathlib import Path

from app.clipboard import ClipboardService
from app.config import AppConfig
from app.formatter import Formatter, FormattingError
from app.hotkey import GlobalHotkeyManager
from app.logger import setup_logging
from app.recorder import AudioRecorder, RecorderStopReason, RecordingResult
from app.silence_detector import SilenceDetector
from app.single_instance import SingleInstanceGuard
from app.sounds import SoundNotifier
from app.state import AppStatus, StateMachine
from app.storage import LocalStorage
from app.tray import SystemTrayController
from app.transcriber import Transcriber, TranscriptionError
from app.ui import StatusNotifier


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
        self._single_instance = SingleInstanceGuard("Local\\bug-voice-reporter")
        self._signal_installed = False
        self._old_sigint_handler = None
        self._windows_ctrl_handler = None
        self._shutdown_event = threading.Event()

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
        self.toggle_hotkey = GlobalHotkeyManager(
            hotkey=self.config.hotkey,
            callback=self.handle_toggle_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
        )
        self.restart_hotkey = GlobalHotkeyManager(
            hotkey=self.config.restart_hotkey,
            callback=self.handle_restart_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
        )

    def start(self) -> None:
        self._validate_configuration()
        if not self._single_instance.acquire():
            raise RuntimeError("Já existe uma instância do bug-voice-reporter em execução.")
        self._install_signal_handlers()
        self.storage.cleanup_sensitive_outputs()
        self.storage.cleanup_stale_temp_audio()
        if not self.config.log_to_file:
            self.storage.cleanup_file(self.config.log_file_path)
        self.status_ui.start()
        self.toggle_hotkey.start()
        self.restart_hotkey.start()
        self.tray.start()
        self._started = True
        self.logger.info(
            "bug-voice-reporter iniciado. Hotkeys toggle=%s restart=%s providers transcrição=%s formatação=%s",
            self.config.hotkey,
            self.config.restart_hotkey,
            self.config.transcription_provider,
            self.config.formatter_provider,
        )
        self._set_status_label("IDLE")
        self.status_ui.show_status(
            self._startup_hud_message(),
            persistent=False,
            duration_ms=10000,
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
        if not self._started and self.state.current == AppStatus.IDLE:
            self.toggle_hotkey.stop()
            self.restart_hotkey.stop()
            self.tray.stop()
            self.clipboard.stop()
            self.status_ui.stop()
            self._restore_signal_handlers()
            return
        self._cancel_restart_countdown()
        self._cancel_idle_reset()
        self.toggle_hotkey.stop()
        self.restart_hotkey.stop()
        self.tray.stop()
        self.clipboard.stop()
        self.status_ui.stop()
        self._started = False
        self._single_instance.release()
        self._restore_signal_handlers()
        threading.Thread(target=self._force_exit_soon, daemon=True).start()

    def open_last_output(self) -> None:
        if not self.config.save_last_output:
            self.status_ui.show_status(
                "Histórico local desativado por segurança",
                persistent=False,
                duration_ms=2200,
                kind="error",
            )
            return
        if not self.storage.open_file(self.config.last_output_path):
            self.status_ui.show_status(
                "Última saída não encontrada",
                persistent=False,
                duration_ms=1800,
                kind="error",
            )

    def handle_toggle_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info("Hotkey toggle acionada no estado %s", self.state.current)
                self._cancel_idle_reset()

                if self._restart_pending:
                    self._cancel_restart_countdown()
                    self.status_ui.show_status(
                        "REINÍCIO CANCELADO",
                        persistent=False,
                        duration_ms=1800,
                        kind="hud",
                    )
                    self.logger.info("Reinício agendado cancelado pela hotkey toggle.")
                    return

                if self.state.is_processing:
                    self.logger.info("Hotkey toggle ignorada porque o app está processando.")
                    return

                if self.state.current in {AppStatus.COPIED, AppStatus.ERROR}:
                    self.state.reset()

                if self.state.current == AppStatus.IDLE:
                    self._start_recording()
                    return

                if self.state.current in {AppStatus.RECORDING, AppStatus.SILENCE_COUNTDOWN}:
                    if self.recorder.stop(RecorderStopReason.MANUAL):
                        self.state.transition(AppStatus.PROCESSING_TRANSCRIPTION)
                        self._set_status_label(AppStatus.PROCESSING_TRANSCRIPTION.value)
                        self.status_ui.show_status(
                            "PROCESSANDO\ntranscrição em andamento",
                            persistent=True,
                            kind="processing",
                        )
                    return
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey toggle.")
            self._move_to_error("Não foi possível processar o atalho global.")

    def handle_restart_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info(
                    "Hotkey de reinício acionada no estado %s",
                    self.state.current,
                )
                self._cancel_idle_reset()

                if self.state.is_processing:
                    self.logger.info(
                        "Hotkey de reinício ignorada porque o app está processando."
                    )
                    return

                if self._restart_pending:
                    self.logger.info(
                        "Hotkey de reinício ignorada porque o reinício já está agendado."
                    )
                    return

                if self.state.current in {AppStatus.RECORDING, AppStatus.SILENCE_COUNTDOWN}:
                    if self.recorder.stop(RecorderStopReason.RESTART):
                        self.status_ui.show_status(
                            "RESET DO RELATO\nreiniciando captura",
                            persistent=True,
                            kind="countdown",
                        )
                        self.sound_notifier.play_discard()
                        self.logger.info("Gravação atual será descartada.")
                    return

                self.logger.info("Hotkey de reinício ignorada fora de uma gravação ativa.")
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey de reinício.")
            self._move_to_error("Não foi possível processar o atalho de reinício.")

    def _start_recording(self) -> None:
        try:
            self._cancel_restart_countdown()
            self.recorder.start()
            self.state.transition(AppStatus.RECORDING)
            self._set_status_label(AppStatus.RECORDING.value)
            self.status_ui.show_status("GRAVANDO", persistent=True, kind="recording")
            self.sound_notifier.play_start()
            self.logger.info("Gravação iniciada.")
        except Exception as exc:
            self.logger.exception("Não foi possível iniciar a gravação.")
            raise RuntimeError(
                "Não foi possível iniciar a gravação. Verifique o microfone e tente novamente."
            ) from exc

    def _on_speech_started(self) -> None:
        self.logger.info("Primeira fala detectada.")

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
        if result.reason == RecorderStopReason.SILENCE:
            self.logger.info(
                "Silêncio contínuo de %.1f segundos detectado; encerrando gravação.",
                self.config.silence_timeout_seconds,
            )

        worker = threading.Thread(
            target=self._process_recording,
            args=(result,),
            name="recording-processor",
            daemon=True,
        )
        worker.start()

    def _process_recording(self, result: RecordingResult) -> None:
        audio_path = result.path
        try:
            self.status_ui.show_status(
                "PROCESSANDO\ntranscrição em andamento",
                persistent=True,
                kind="processing",
            )
            transcription = self.transcriber.transcribe(audio_path)
            self.storage.save_last_transcription(transcription)

            with self._lock:
                self.state.transition(AppStatus.PROCESSING_FORMATTING)
                self._set_status_label(AppStatus.PROCESSING_FORMATTING.value)
            self.status_ui.show_status(
                "ANALISANDO BUG\nformatando relatório",
                persistent=True,
                kind="processing",
            )

            formatted = self.formatter.format_bug_report(transcription)
            self.storage.save_last_output(formatted)
            self.clipboard.copy_text(formatted)

            with self._lock:
                self.state.transition(AppStatus.COPIED)
                self._set_status_label(AppStatus.COPIED.value)

            final_message = "BUG REPORT COPIADO\npronto para colar"
            if result.reason == RecorderStopReason.MAX_DURATION:
                final_message = "TEMPO MÁXIMO\nconteúdo copiado"

            self.status_ui.show_status(
                final_message,
                persistent=False,
                duration_ms=3200,
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
            if self.state.current != AppStatus.ERROR:
                current = self.state.current
                if not self.state.can_transition(AppStatus.ERROR):
                    self.logger.warning(
                        "Transição para ERROR não permitida a partir de %s; mantendo estado.",
                        current,
                    )
                else:
                    self.state.transition(AppStatus.ERROR, error_message=message)
                    self._set_status_label(AppStatus.ERROR.value)

        if cleanup_path is not None:
            self.storage.cleanup_file(cleanup_path)

        self.logger.error(message)
        self.sound_notifier.play_error()
        self.status_ui.show_status(
            f"ERRO DETECTADO\n{message}",
            persistent=False,
            duration_ms=3400,
            kind="error",
        )
        self._schedule_idle_reset()

    def _schedule_idle_reset(self, delay_seconds: float = 2.8) -> None:
        self._cancel_idle_reset()
        self._idle_reset_timer = threading.Timer(delay_seconds, self._reset_to_idle_if_terminal)
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

            if self.state.is_processing or self.recorder.is_recording:
                self.logger.info("Reinício automático cancelado por mudança de estado.")
                return

            if self.state.current != AppStatus.IDLE:
                self.logger.info(
                    "Reinício automático cancelado porque o estado deixou de ser IDLE."
                )
                return

            try:
                self._start_recording()
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
                f"Arquivo de prompt não encontrado em {self.config.prompt_path}"
            )
        prompt_content = self.config.prompt_path.read_text(encoding="utf-8")
        if "{{TRANSCRICAO}}" not in prompt_content:
            raise RuntimeError(
                "O arquivo de prompt precisa conter o placeholder {{TRANSCRICAO}}."
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
            "BUG HUNTER OFFLINE\n"
            "GRAVAR = CTRL + TAB\n"
            "REINICIAR = CTRL + CAPS\n"
            f"{privacy_note}"
        )

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
            raise RuntimeError(
                "Provider inválido no .env. Use 'openai' ou 'gemini'."
            )

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
        signal.signal(signal.SIGINT, self._old_sigint_handler)
        self._restore_windows_console_handler()
        self._signal_installed = False

    def _request_shutdown(self) -> None:
        if self._shutdown_event.is_set():
            return
        threading.Thread(target=self.shutdown, name="shutdown-request", daemon=True).start()

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


def main() -> None:
    config = AppConfig.load()
    app = BugVoiceReporterApp(config)
    app.run_forever()


if __name__ == "__main__":
    main()
