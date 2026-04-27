from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import threading
import time
import webbrowser
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from app.bugreel_client import (
    BugReelClient,
    BugReelClientError,
    BugReelDownloadSettings,
)
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
import keyboard


@dataclass
class PendingBugReelCapture:
    existing_recording_ids: frozenset[str]
    cancel_event: threading.Event
    armed_at: datetime
    max_existing_recording_order: tuple[int, int] | None = None
    stop_requested: bool = False
    stop_requested_at: float | None = None


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
        self._active_bugreel_capture: PendingBugReelCapture | None = None
        self._last_bugreel_video_path: Path | None = None
        self._bugreel_shortcuts_enabled = True
        self._bugreel_shortcuts_error: str | None = None

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
        self.bugreel_client = BugReelClient(
            api_token=self.config.bugreel_api_token,
            timeout_seconds=self.config.bugreel_timeout_seconds,
            download_settings=BugReelDownloadSettings(
                enabled=self.config.bugreel_download_evidence,
                frame_limit=self.config.bugreel_frame_limit,
            ),
            bundle_dir_factory=self.storage.create_temp_evidence_dir,
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
        self.restart_hotkey = (
            GlobalHotkeyManager(
                hotkey=self.config.restart_hotkey,
                callback=self.handle_restart_hotkey,
                debounce_ms=self.config.hotkey_debounce_ms,
            )
            if self.config.restart_hotkey.strip()
            else None
        )
        self.bugreel_hotkey = GlobalHotkeyManager(
            hotkey=self.config.bugreel_hotkey,
            callback=self.handle_bugreel_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
        )
        self.video_attach_hotkey = GlobalHotkeyManager(
            hotkey=self.config.video_attach_hotkey,
            callback=self.handle_video_attach_hotkey,
            debounce_ms=self.config.hotkey_debounce_ms,
        )

    def start(self) -> None:
        self._validate_configuration()
        if not self._single_instance.acquire():
            raise RuntimeError("Já existe uma instância do bug-voice-reporter em execução.")

        self._install_signal_handlers()
        self.storage.cleanup_sensitive_outputs()
        self.storage.cleanup_stale_temp_audio()
        self.storage.cleanup_stale_temp_evidence()
        if not self.config.log_to_file:
            self.storage.cleanup_file(self.config.log_file_path)

        self.status_ui.start()
        self._evaluate_bugreel_hotkey_configuration()
        self.toggle_hotkey.start()
        if self.restart_hotkey is not None:
            self.restart_hotkey.start()
        if self._bugreel_shortcuts_enabled:
            self.bugreel_hotkey.start()
            self.video_attach_hotkey.start()
        self.tray.start()
        self._started = True
        self._set_status_label(AppStatus.IDLE.value)
        self.logger.info(
            "bug-voice-reporter iniciado. Hotkeys toggle=%s restart=%s bugreel=%s anexar=%s providers transcrição=%s formatação=%s",
            self.config.hotkey,
            self.config.restart_hotkey or "desativado",
            self.config.bugreel_hotkey,
            self.config.video_attach_hotkey,
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

        if self._active_bugreel_capture is not None:
            self._active_bugreel_capture.cancel_event.set()

        self._cancel_restart_countdown()
        self._cancel_idle_reset()
        self.toggle_hotkey.stop()
        if self.restart_hotkey is not None:
            self.restart_hotkey.stop()
        self.bugreel_hotkey.stop()
        self.video_attach_hotkey.stop()
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

    def handle_bugreel_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info(
                    "Hotkey de BugReel acionada no estado %s (captura_ativa=%s stop_requested=%s)",
                    self.state.current,
                    self._active_bugreel_capture is not None,
                    self._active_bugreel_capture.stop_requested
                    if self._active_bugreel_capture is not None
                    else False,
                )
                self._cancel_idle_reset()
                if not self._bugreel_shortcuts_enabled:
                    self.status_ui.show_status(
                        f"BUGREEL INDISPONIVEL\n{self._bugreel_shortcuts_error or 'revise as hotkeys no .env e reinicie o app'}",
                        persistent=False,
                        duration_ms=5200,
                        kind="error",
                    )
                    return

                if self.state.is_processing:
                    self.logger.info("Hotkey de BugReel ignorada porque o app está processando.")
                    return
                if self.state.is_recording_active:
                    self.status_ui.show_status(
                        f"VOZ EM ANDAMENTO\nfinalize o relato com {self._format_hotkey_label(self.config.hotkey)} antes de abrir o BugReel",
                        persistent=False,
                        duration_ms=4200,
                        kind="error",
                    )
                    return
                if self._restart_pending:
                    self._cancel_restart_countdown()

                if self._active_bugreel_capture is None:
                    if self.state.current in {AppStatus.COPIED, AppStatus.ERROR}:
                        self.state.reset()
                        self._set_status_label(AppStatus.IDLE.value)
                    if (
                        self.config.bugreel_auto_trigger
                        and self.config.bugreel_auto_focus_chrome
                        and not self._focus_chrome_window()
                    ):
                        self.status_ui.show_status(
                            "CHROME NAO ENCONTRADO\no navegador precisa estar aberto para iniciar o BugReel",
                            persistent=False,
                            duration_ms=5200,
                            kind="error",
                        )
                        self.sound_notifier.play_error()
                        return
                    self._arm_bugreel_capture()
                    self._auto_trigger_bugreel_capture()
                else:
                    if self._active_bugreel_capture.stop_requested:
                        self.status_ui.show_status(
                            "ENCERRAMENTO EM ANDAMENTO\no video ja esta sendo finalizado; aguarde o processamento",
                            persistent=False,
                            duration_ms=3600,
                            kind="hud",
                        )
                        return
                    self._active_bugreel_capture.stop_requested = True
                    self._active_bugreel_capture.stop_requested_at = time.monotonic()
                    self._transition_state(AppStatus.AWAITING_BUGREEL_UPLOAD)
                    self._set_status_label(AppStatus.AWAITING_BUGREEL_UPLOAD.value)
                    self._trigger_bugreel_stop_shortcut()
                    self.status_ui.show_status(
                        "GRAVACAO ENCERRADA\naguardando o BugReel enviar video e contexto para processamento",
                        persistent=True,
                        kind="processing",
                    )
                    self.logger.info(
                        "Encerramento do BugReel solicitado; aguardando gravacao finalizar."
                    )
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey de BugReel.")
            self._move_to_error("Não foi possível controlar o BugReel.")

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
                    self.logger.info("Reinício agendado cancelado pela hotkey toggle.")
                    return

                if self.state.is_processing:
                    self.logger.info("Hotkey toggle ignorada porque o app está processando.")
                    return

                if self._active_bugreel_capture is not None:
                    self.status_ui.show_status(
                        f"BUGREEL EM ANDAMENTO\ntermine a captura e depois use {self._format_hotkey_label(self.config.bugreel_hotkey)} se precisar encerrar por atalho",
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
                    if self.recorder.stop(RecorderStopReason.MANUAL):
                        self.state.transition(AppStatus.PROCESSING_TRANSCRIPTION)
                        self._set_status_label(AppStatus.PROCESSING_TRANSCRIPTION.value)
                        self.status_ui.show_status(
                            "RELATO ENCERRADO\ntranscrevendo sua fala e montando o bug report",
                            persistent=True,
                            kind="processing",
                        )
                    return
        except Exception:
            self.logger.exception("Falha ao tratar a hotkey toggle.")
            self._move_to_error("Não foi possível processar o atalho global.")

    def handle_video_attach_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info(
                    "Hotkey de anexo de vídeo acionada no estado %s",
                    self.state.current,
                )
                video_path = self._last_bugreel_video_path
                active_capture = self._active_bugreel_capture
                current_state = self.state.current
                processing_now = self.state.is_processing

            if video_path is None or not video_path.exists():
                if active_capture is not None and active_capture.stop_requested:
                    message = (
                        "VIDEO AINDA EM PREPARO\na captura terminou; aguarde o envio e a analise final"
                    )
                elif active_capture is not None:
                    message = (
                        f"VIDEO AINDA NAO DISPONIVEL\na captura esta em andamento; finalize com {self._format_hotkey_label(self.config.bugreel_hotkey)}"
                    )
                elif processing_now:
                    message = (
                        "VIDEO AINDA EM PROCESSAMENTO\naguarde a analise terminar antes de usar CTRL+SHIFT+V"
                    )
                elif current_state == AppStatus.COPIED:
                    message = (
                        "ULTIMO FLUXO SEM VIDEO VALIDO\nrepita a captura do BugReel e confirme o envio do video"
                    )
                else:
                    message = (
                        f"NENHUM VIDEO PARA COLAR\ninicie uma captura BugReel com {self._format_hotkey_label(self.config.bugreel_hotkey)}"
                    )
                self.status_ui.show_status(
                    message,
                    persistent=False,
                    duration_ms=3600,
                    kind="error",
                )
                return

            self.clipboard.copy_files([video_path])
            time.sleep(0.06)
            keyboard.send("ctrl+v")
            self.status_ui.show_status(
                "VIDEO COLADO\na evidencia visual ja esta pronta para a issue",
                persistent=False,
                duration_ms=3600,
                kind="success",
            )
            self.sound_notifier.play_success()
        except Exception:
            self.logger.exception("Falha ao copiar o vídeo do BugReel para o clipboard.")
            self.status_ui.show_status(
                "VIDEO INDISPONIVEL\na evidencia nao foi encontrada para colagem",
                persistent=False,
                duration_ms=2400,
                kind="error",
            )

    def handle_restart_hotkey(self) -> None:
        try:
            with self._lock:
                self.logger.info(
                    "Hotkey de reinício acionada no estado %s",
                    self.state.current,
                )
                self._cancel_idle_reset()

                if self.state.is_processing:
                    self.logger.info("Hotkey de reinício ignorada porque o app está processando.")
                    return

                if self._active_bugreel_capture is not None:
                    self.status_ui.show_status(
                        "BUGREEL PREPARADO\nreinicio de voz fica bloqueado enquanto a captura de tela estiver armada",
                        persistent=False,
                        duration_ms=1800,
                        kind="error",
                    )
                    return

                if self._restart_pending:
                    self.logger.info("Hotkey de reinício ignorada porque o reinício já está agendado.")
                    return

                if self.state.current in {AppStatus.RECORDING, AppStatus.SILENCE_COUNTDOWN}:
                    if self.recorder.stop(RecorderStopReason.RESTART):
                        self.status_ui.show_status(
                            "RELATO DESCARTADO\niniciando uma nova captura de voz",
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

    def _arm_bugreel_capture(self) -> None:
        self._require_bugreel_configuration()
        self._ensure_bugreel_online()
        baseline = self.bugreel_client.list_recordings(
            self.config.bugreel_base_url,
            limit=100,
        )
        max_existing_recording_order = max(
            (
                self._bugreel_recording_order_value(item.recording_id)
                for item in baseline
            ),
            default=None,
        )
        self._last_bugreel_video_path = None
        capture = PendingBugReelCapture(
            existing_recording_ids=frozenset(item.recording_id for item in baseline),
            cancel_event=threading.Event(),
            armed_at=datetime.now(),
            max_existing_recording_order=max_existing_recording_order,
        )
        self._active_bugreel_capture = capture
        self._set_status_label("BUGREEL_ARMED")
        self.status_ui.show_status(
            f"CAPTURA PRONTA\nescolha a janela, clique em Compartilhar e reproduza o bug\npara encerrar, use {self._format_hotkey_label(self.config.bugreel_hotkey)} ou pare na extensao",
            persistent=True,
            kind="hud",
        )
        self.sound_notifier.play_start()
        self.logger.info("Monitoramento do BugReel iniciado.")
        threading.Thread(
            target=self._await_bugreel_capture,
            args=(capture,),
            name="bugreel-await",
            daemon=True,
        ).start()

    def _ensure_bugreel_online(self) -> None:
        try:
            self.status_ui.show_status(
                "VALIDANDO BUGREEL\nchecando se o servico local esta pronto para gravar",
                persistent=False,
                duration_ms=1200,
                kind="hud",
            )
            self.bugreel_client.list_recordings(self.config.bugreel_base_url, limit=1)
            self.status_ui.show_status(
                "BUGREEL ONLINE\nproxima etapa: abrir o seletor de janela",
                persistent=False,
                duration_ms=3000,
                kind="success",
            )
            return
        except BugReelClientError:
            if not self.config.bugreel_auto_start_container:
                raise RuntimeError(
                    "BugReel offline. Inicie o container ou habilite BUGREEL_AUTO_START_CONTAINER=true."
                )

        self.status_ui.show_status(
            "BUGREEL OFFLINE\niniciando Docker, subindo o container e validando a conexao",
            persistent=True,
            kind="processing",
        )
        self._start_bugreel_container()

        deadline = time.monotonic() + self.config.bugreel_start_timeout_seconds
        notified_waiting = False
        while time.monotonic() < deadline:
            try:
                self.bugreel_client.list_recordings(self.config.bugreel_base_url, limit=1)
                self.logger.info("BugReel ficou online após autostart.")
                self.status_ui.show_status(
                    "BUGREEL PRONTO\nservico online e captura liberada",
                    persistent=False,
                    duration_ms=1400,
                    kind="success",
                )
                return
            except BugReelClientError:
                if not notified_waiting:
                    self.status_ui.show_status(
                        "SUBINDO BUGREEL\nassim que o servico responder, o seletor de janela sera aberto",
                        persistent=True,
                        kind="processing",
                    )
                    notified_waiting = True
                time.sleep(1.0)

        raise RuntimeError(
            "BugReel não ficou online a tempo após autostart. Passos: 1) verifique Docker Desktop, 2) confirme BUGREEL_COMPOSE_DIR, 3) execute docker compose up -d."
        )

    def _start_bugreel_container(self) -> None:
        compose_dir = self.config.bugreel_compose_dir.strip()
        if not compose_dir:
            raise RuntimeError("BUGREEL_COMPOSE_DIR não foi configurado.")
        if not Path(compose_dir).exists():
            raise RuntimeError(
                f"BUGREEL_COMPOSE_DIR inválido: {compose_dir}"
            )

        self.status_ui.show_status(
            "INICIANDO CONTAINER\npreparando o BugReel para a captura",
            persistent=True,
            kind="processing",
        )
        try:
            result = subprocess.run(
                ["docker", "compose", "up", "-d"],
                cwd=compose_dir,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except Exception as exc:
            raise RuntimeError(
                "Não foi possível executar 'docker compose up -d'."
            ) from exc

        if result.returncode != 0:
            stderr_line = (result.stderr or "").strip().splitlines()
            details = stderr_line[-1] if stderr_line else "falha ao subir container"
            raise RuntimeError(f"Autostart do BugReel falhou: {details}")

        self.status_ui.show_status(
            "CONTAINER INICIADO\nvalidando a conexao do BugReel",
            persistent=True,
            kind="processing",
        )

    def _auto_trigger_bugreel_capture(self) -> None:
        if not self.config.bugreel_auto_trigger:
            return
        self.status_ui.show_status(
            "ABRINDO SELETOR DO BUGREEL\no painel de compartilhamento vai aparecer agora",
            persistent=False,
            duration_ms=5200,
            kind="hud",
        )
        threading.Thread(
            target=self._dispatch_bugreel_trigger,
            name="bugreel-trigger",
            daemon=True,
        ).start()

    def _dispatch_bugreel_trigger(self) -> None:
        try:
            if self.config.bugreel_boot_url and self._is_safe_bugreel_boot_url(
                self.config.bugreel_boot_url
            ):
                webbrowser.open(self.config.bugreel_boot_url)
            elif self.config.bugreel_boot_url:
                self.logger.info(
                    "BUGREEL_BOOT_URL ignorada por seguranca/localidade: %s",
                    self.config.bugreel_boot_url,
                )
            time.sleep(max(0.2, self.config.bugreel_trigger_delay_seconds))
            command_hotkey = self._effective_bugreel_command_hotkey()
            if not command_hotkey:
                return
            if self.config.bugreel_auto_focus_chrome and not self._is_chrome_foreground():
                if not self._focus_chrome_window():
                    self.status_ui.show_status(
                        "CHROME FORA DE FOCO\ndeixe o Chrome na frente e tente novamente",
                        persistent=False,
                        duration_ms=5200,
                        kind="error",
                    )
                    self.sound_notifier.play_error()
                    return
            self.status_ui.show_status(
                "ESCOLHA A JANELA\nquando o painel abrir, selecione a janela e clique em Compartilhar",
                persistent=False,
                duration_ms=5600,
                kind="hud",
            )
            keyboard.send(command_hotkey)
            self.status_ui.show_status(
                f"AGUARDANDO COMPARTILHAMENTO\ndepois de clicar em Compartilhar, reproduza o bug e finalize com {self._format_hotkey_label(self.config.bugreel_hotkey)}",
                persistent=True,
                kind="hud",
            )
            self.logger.info(
                "Disparo automático do BugReel enviado por hotkey interna (%s).",
                command_hotkey,
            )
        except Exception:
            self.logger.exception("Falha ao disparar inicialização automática do BugReel.")
            self.status_ui.show_status(
                f"NAO FOI POSSIVEL ABRIR O BUGREEL\ndeixe o Chrome visivel e tente {self._format_hotkey_label(self.config.bugreel_hotkey)} novamente",
                persistent=False,
                duration_ms=4200,
                kind="error",
            )

    def _cancel_bugreel_capture(self) -> None:
        capture = self._active_bugreel_capture
        if capture is None:
            return
        capture.cancel_event.set()
        self._active_bugreel_capture = None
        self._last_bugreel_video_path = None
        self._set_status_label(AppStatus.IDLE.value)
        self.status_ui.show_status(
            "BUGREEL CANCELADO\nnenhum video novo sera processado",
            persistent=False,
            duration_ms=3600,
            kind="hud",
        )
        self.logger.info("Monitoramento do BugReel cancelado.")

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

    def _trigger_bugreel_stop_shortcut(self) -> None:
        command_hotkey = self._effective_bugreel_command_hotkey()
        if not command_hotkey:
            return
        try:
            keyboard.send(command_hotkey)
            self.logger.info(
                "Tentativa de parada do BugReel enviada por hotkey interna (%s).",
                command_hotkey,
            )
            self.status_ui.show_status(
                "ENCERRANDO CAPTURA\no comando foi enviado; depois disso o app continua sozinho",
                persistent=False,
                duration_ms=3200,
                kind="hud",
            )
        except Exception:
            self.logger.exception("Falha ao enviar comando de parada para o BugReel.")

    def _await_bugreel_capture(self, capture: PendingBugReelCapture) -> None:
        deadline = time.monotonic() + self.config.bugreel_capture_timeout_seconds
        start_confirmation_deadline = (
            time.monotonic() + self.config.bugreel_start_confirmation_seconds
        )
        stop_confirmation_notice_seconds = 5.0
        announced_started = False
        announced_stopping = False
        announced_unconfirmed_start = False
        announced_waiting_upload = False
        saw_new_recording = False
        observed_statuses: set[str] = set()
        try:
            while time.monotonic() < deadline:
                if capture.cancel_event.wait(1.2):
                    return
                if self._active_bugreel_capture is not capture:
                    return

                summaries = self.bugreel_client.list_recordings(
                    self.config.bugreel_base_url,
                    limit=max(len(capture.existing_recording_ids) + 5, 100),
                )
                for summary in summaries:
                    if summary.recording_id in capture.existing_recording_ids:
                        continue
                    if not self._is_bugreel_recording_newer_than_baseline(summary, capture):
                        continue
                    if not self._summary_belongs_to_current_capture(summary, capture):
                        continue
                    saw_new_recording = True
                    status = summary.status.strip().lower()
                    if status:
                        observed_statuses.add(status)
                    if summary.is_failed_terminal():
                        self._move_to_error(self._build_bugreel_failed_message(summary))
                        return
                    if not summary.is_ready_for_import():
                        with self._lock:
                            self._transition_state(AppStatus.AWAITING_BUGREEL_PUBLICATION)
                            self._set_status_label(AppStatus.AWAITING_BUGREEL_PUBLICATION.value)
                        if capture.stop_requested and not announced_stopping:
                            self.status_ui.show_status(
                                "ENCERRAMENTO CONFIRMADO\naguardando upload do video e inicio do processamento",
                                persistent=True,
                                kind="processing",
                            )
                            announced_stopping = True
                        elif not capture.stop_requested and not announced_started:
                            self.status_ui.show_status(
                                f"GRAVACAO EM ANDAMENTO\nreproduza o bug e depois encerre com {self._format_hotkey_label(self.config.bugreel_hotkey)} ou pela extensao",
                                persistent=True,
                                kind="hud",
                            )
                            announced_started = True
                        continue

                    with self._lock:
                        if self._active_bugreel_capture is not capture:
                            return
                        self._active_bugreel_capture = None
                        self._transition_state(AppStatus.PROCESSING_BUGREEL_ASSETS)
                        self._set_status_label(AppStatus.PROCESSING_BUGREEL_ASSETS.value)

                    self.status_ui.show_status(
                        "PROCESSANDO EVIDENCIAS\nlendo video, audio, logs e contexto da captura",
                        persistent=True,
                        kind="processing",
                    )
                    self._process_bugreel_capture(
                        summary.to_context(self.config.bugreel_base_url)
                    )
                    return

                if (
                    not saw_new_recording
                    and capture.stop_requested
                    and capture.stop_requested_at is not None
                    and time.monotonic()
                    >= capture.stop_requested_at + stop_confirmation_notice_seconds
                ):
                    if not announced_waiting_upload:
                        self.logger.info(
                            "A gravacao local foi encerrada, mas a API do BugReel ainda nao publicou uma nova captura; aguardando upload automatico."
                        )
                        with self._lock:
                            self._transition_state(AppStatus.AWAITING_BUGREEL_PUBLICATION)
                            self._set_status_label(AppStatus.AWAITING_BUGREEL_PUBLICATION.value)
                        self.status_ui.show_status(
                            "AGUARDANDO ENVIO DO VIDEO\na gravacao ja terminou; agora o BugReel precisa publicar os arquivos",
                            persistent=True,
                            kind="processing",
                        )
                        announced_waiting_upload = True
                    continue

                if (
                    not saw_new_recording
                    and not capture.stop_requested
                    and time.monotonic() >= start_confirmation_deadline
                ):
                    if not announced_unconfirmed_start:
                        self.logger.warning(
                            "Nenhum sinal de inicio da gravacao BugReel foi detectado no tempo esperado; mantendo monitoramento porque a extensao pode publicar a captura apenas no encerramento."
                        )
                        self.status_ui.show_status(
                            f"AGUARDANDO INICIO DA GRAVACAO\nse o cronometro da extensao comecou, continue normalmente e finalize com {self._format_hotkey_label(self.config.bugreel_hotkey)}",
                            persistent=True,
                            kind="hud",
                        )
                        announced_unconfirmed_start = True
                    continue

            self._move_to_error(
                self._build_bugreel_timeout_message(
                    capture=capture,
                    saw_new_recording=saw_new_recording,
                    observed_statuses=observed_statuses,
                )
            )
        except BugReelClientError as exc:
            self._move_to_error(str(exc))
        except Exception:
            self.logger.exception("Falha ao aguardar a gravação do BugReel.")
            self._move_to_error(
                "Não foi possível monitorar a captura do BugReel."
            )

    def _summary_belongs_to_current_capture(
        self,
        summary,
        capture: PendingBugReelCapture,
    ) -> bool:
        estimated_end = summary.estimated_end_at()
        if estimated_end is None:
            return True
        return estimated_end >= (capture.armed_at - timedelta(seconds=2))

    @staticmethod
    def _bugreel_recording_order_value(recording_id: str) -> tuple[int, int] | None:
        match = re.search(r"(\d{4})-(\d+)$", recording_id.strip())
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def _is_bugreel_recording_newer_than_baseline(
        self,
        summary,
        capture: PendingBugReelCapture,
    ) -> bool:
        if capture.max_existing_recording_order is None:
            return True
        summary_order = self._bugreel_recording_order_value(summary.recording_id)
        if summary_order is None:
            return True
        return summary_order > capture.max_existing_recording_order

    def _build_bugreel_failed_message(self, summary) -> str:
        status = summary.status.strip().lower() or "error"
        if status in {"canceled", "cancelled", "aborted"}:
            return "CAPTURA CANCELADA\nnenhuma janela foi compartilhada ou o seletor foi fechado"
        if status == "error":
            return (
                "CAPTURA FALHOU\n"
                "o BugReel encerrou a tentativa antes do compartilhamento. Escolha uma janela e clique em Compartilhar."
            )
        return f"CAPTURA INTERROMPIDA\nstatus retornado pelo BugReel: {status}"

    def _find_recent_bugreel_fallback(self, capture: PendingBugReelCapture):
        try:
            summaries = self.bugreel_client.list_recordings(
                self.config.bugreel_base_url,
                limit=5,
            )
        except BugReelClientError:
            return None

        threshold = capture.armed_at - timedelta(seconds=45)
        for summary in summaries:
            if not summary.is_ready_for_import():
                continue
            if summary.recording_id not in capture.existing_recording_ids:
                continue
            estimated_end = summary.estimated_end_at()
            if estimated_end is None:
                continue
            if estimated_end >= threshold:
                self.logger.info(
                    "Usando fallback da gravação recente do BugReel: %s (%s).",
                    summary.recording_id,
                    summary.status,
                )
                return summary
        return None

    def _process_bugreel_capture(self, context) -> None:
        try:
            with self._lock:
                self._transition_state(AppStatus.PROCESSING_BUGREEL_ASSETS)
                self._set_status_label(AppStatus.PROCESSING_BUGREEL_ASSETS.value)
            enriched = self._wait_for_bugreel_assets(context)
            self.logger.info(
                "BugReel publicado: status=%s artifacts_ready=%s ai_status=%s",
                enriched.recording_status or "desconhecido",
                enriched.artifacts_ready,
                enriched.ai_status or "nao_informado",
            )
            if enriched.audio_capture_lines:
                self.logger.info("Audio do BugReel: %s", " | ".join(enriched.audio_capture_lines))
            raw_report = self._build_bugreel_raw_report(enriched)
            evidence_files = enriched.evidence_file_paths()
            self._last_bugreel_video_path = self._pick_video_path(evidence_files)
            if self._last_bugreel_video_path is None or not self._last_bugreel_video_path.exists():
                raise BugReelClientError(
                    "O video do BugReel ainda nao ficou disponivel. Aguarde o upload terminar na extensao e tente novamente."
                )
            with self._lock:
                self._transition_state(AppStatus.PROCESSING_FORMATTING)
                self._set_status_label(AppStatus.PROCESSING_FORMATTING.value)
            formatted = self.formatter.format_bug_report(
                raw_report,
                bugreel_context=enriched,
            )
            self.storage.save_last_output(formatted)
            self.clipboard.copy_text(formatted)

            with self._lock:
                self._transition_state(AppStatus.COPIED)
                self._set_status_label(AppStatus.COPIED.value)

            self.status_ui.show_status(
                "ANALISE PRONTA\nCtrl+V cola o bug report\nCtrl+Shift+V cola o video",
                persistent=False,
                duration_ms=5200,
                kind="success",
            )
            self.sound_notifier.play_success()
            self.logger.info("Fluxo BugReel concluído com sucesso.")
            self._schedule_idle_reset()
        except (BugReelClientError, FormattingError, ValueError) as exc:
            self._move_to_error(str(exc))
        except Exception:
            self.logger.exception("Falha inesperada durante o processamento do BugReel.")
            self._move_to_error(
                "Ocorreu um erro inesperado durante o processamento do BugReel."
            )

    def _wait_for_bugreel_assets(self, context):
        last_enriched = None
        for _ in range(8):
            enriched = self.bugreel_client.enrich_context(context)
            last_enriched = enriched
            video_path = self._pick_video_path(enriched.evidence_file_paths())
            if video_path is not None and video_path.exists():
                return enriched
            status_suffix = ""
            if enriched.ai_status:
                status_suffix = f"\nIA INTERNA DO BUGREEL: {enriched.ai_status}"
            self.status_ui.show_status(
                "FINALIZANDO EVIDENCIAS\naguardando o video final ficar disponivel"
                + status_suffix,
                persistent=True,
                kind="processing",
            )
            time.sleep(1.0)
        if last_enriched is None:
            raise BugReelClientError("Nao foi possivel carregar os artefatos do BugReel.")
        return last_enriched

    def _build_bugreel_raw_report(self, enriched) -> str:
        transcript = enriched.transcript_excerpt.strip()
        if transcript:
            return transcript

        media_audio = self._first_transcribable_media(enriched.local_files)
        if media_audio is not None:
            try:
                with self._lock:
                    self._transition_state(AppStatus.PROCESSING_BUGREEL_FALLBACK)
                    self._set_status_label(AppStatus.PROCESSING_BUGREEL_FALLBACK.value)
                self.status_ui.show_status(
                    "PROCESSANDO MIDIA LOCAL\ntranscrevendo audio e video porque a analise interna do BugReel ficou indisponivel",
                    persistent=True,
                    kind="processing",
                )
                transcribed = self.transcriber.transcribe(media_audio).strip()
                if transcribed:
                    self.logger.info(
                        "Transcrição de fallback do BugReel aplicada a partir de %s.",
                        media_audio.name,
                    )
                    return transcribed
            except (TranscriptionError, ValueError):
                self.logger.info(
                    "Fallback de transcrição do BugReel indisponível para %s.",
                    media_audio.name,
                )

        return (
            enriched.summary.strip()
            or enriched.title.strip()
            or "Use o contexto do BugReel para estruturar este bug report."
        )

    @staticmethod
    def _first_transcribable_media(local_files: Iterable[Path]) -> Path | None:
        accepted = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg"}
        for path in local_files:
            if path.suffix.lower() in accepted and path.exists():
                return path
        return None

    @staticmethod
    def _pick_video_path(local_files: Iterable[Path]) -> Path | None:
        video_suffixes = {".webm", ".mp4", ".mov", ".mkv", ".avi"}
        for path in local_files:
            if path.suffix.lower() in video_suffixes and path.exists():
                return path
        return None

    def _start_voice_recording(self) -> None:
        try:
            self._cancel_restart_countdown()
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

        if result.reason == RecorderStopReason.SILENCE:
            self.logger.info(
                "Silêncio contínuo de %.1f segundos detectado; encerrando gravação.",
                self.config.silence_timeout_seconds,
            )

        threading.Thread(
            target=self._process_voice_recording,
            args=(result,),
            name="recording-processor",
            daemon=True,
        ).start()

    def _process_voice_recording(self, result: RecordingResult) -> None:
        audio_path = result.path
        try:
            self.status_ui.show_status(
                "PROCESSANDO RELATO\ntranscrevendo o audio para montar o bug report",
                persistent=True,
                kind="processing",
            )
            transcription = self.transcriber.transcribe(audio_path)
            self.storage.save_last_transcription(transcription)

            with self._lock:
                self.state.transition(AppStatus.PROCESSING_FORMATTING)
                self._set_status_label(AppStatus.PROCESSING_FORMATTING.value)

            self.status_ui.show_status(
                "TRANSCRICAO CONCLUIDA\norganizando o template final do bug report",
                persistent=True,
                kind="processing",
            )

            formatted = self.formatter.format_bug_report(transcription)
            self.storage.save_last_output(formatted)
            self.clipboard.copy_payload(formatted, [])

            with self._lock:
                self.state.transition(AppStatus.COPIED)
                self._set_status_label(AppStatus.COPIED.value)

            final_message = "BUG REPORT PRONTO\nCtrl+V cola o texto final"
            if result.reason == RecorderStopReason.MAX_DURATION:
                final_message = "TEMPO MAXIMO ATINGIDO\no texto final ja esta pronto no Ctrl+V"

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
            if self._active_bugreel_capture is not None:
                self._active_bugreel_capture.cancel_event.set()
            self._active_bugreel_capture = None
            self._last_bugreel_video_path = None
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
            if self.state.is_processing or self.recorder.is_recording or self._active_bugreel_capture is not None:
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
                f"Arquivo de prompt não encontrado em {self.config.prompt_path}"
            )
        prompt_content = self.config.prompt_path.read_text(encoding="utf-8")
        if "{{TRANSCRICAO}}" not in prompt_content:
            raise RuntimeError(
                "O arquivo de prompt precisa conter o placeholder {{TRANSCRICAO}}."
            )
        command_hotkey = self._normalize_hotkey(self._effective_bugreel_command_hotkey())
        if command_hotkey == self._normalize_hotkey(self.config.video_attach_hotkey):
            raise RuntimeError(
                "Conflito de hotkeys: APP_VIDEO_ATTACH_HOTKEY não pode ser igual a BUGREEL_COMMAND_HOTKEY."
            )

    def _evaluate_bugreel_hotkey_configuration(self) -> None:
        self._bugreel_shortcuts_enabled = True
        self._bugreel_shortcuts_error = None

        configured = {
            "APP_BUGREEL_HOTKEY": self.config.bugreel_hotkey,
            "APP_VIDEO_ATTACH_HOTKEY": self.config.video_attach_hotkey,
            "BUGREEL_COMMAND_HOTKEY": self._effective_bugreel_command_hotkey(),
        }
        seen: dict[str, str] = {}
        for key, value in configured.items():
            normalized = self._normalize_hotkey(value)
            if not normalized:
                self._bugreel_shortcuts_enabled = False
                self._bugreel_shortcuts_error = (
                    f"{key} está vazio. Corrija o .env e reinicie o app."
                )
                break
            if normalized in seen:
                other = seen[normalized]
                self._bugreel_shortcuts_enabled = False
                self._bugreel_shortcuts_error = (
                    f"Conflito de hotkeys: {other} e {key} usam o mesmo atalho."
                )
                break
            seen[normalized] = key

        if not self._bugreel_shortcuts_enabled:
            self.logger.error(self._bugreel_shortcuts_error)
            self.status_ui.show_status(
                f"ATALHOS DO BUGREEL BLOQUEADOS\n{self._bugreel_shortcuts_error}",
                persistent=False,
                duration_ms=8000,
                kind="error",
            )
            return

        expected_command = self._normalize_hotkey("alt+shift+r")
        effective_command_hotkey = self._effective_bugreel_command_hotkey()
        current_command = self._normalize_hotkey(effective_command_hotkey)
        if current_command != expected_command:
            self.status_ui.show_status(
                f"ATALHO INTERNO DO BUGREEL\natual: {self._format_hotkey_label(effective_command_hotkey)}\nrecomendado: ALT + SHIFT + R",
                persistent=False,
                duration_ms=6200,
                kind="hud",
            )
            self.logger.warning(
                "BUGREEL_COMMAND_HOTKEY fora do padrão recomendado (alt+shift+r): %s",
                effective_command_hotkey,
            )

    def _require_bugreel_configuration(self) -> None:
        if not self.config.bugreel_base_url:
            raise RuntimeError("BUGREEL_BASE_URL não foi configurado no .env.")
        if not self.config.bugreel_api_token:
            raise RuntimeError("BUGREEL_API_TOKEN não foi configurado no .env.")

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
            f"GRAVAR VOZ = {self._format_hotkey_label(self.config.hotkey)}\n"
            + (
                f"REINICIAR VOZ = {self._format_hotkey_label(self.config.restart_hotkey)}\n"
                if self.config.restart_hotkey.strip()
                else ""
            )
            + f"GRAVAR TELA = {self._format_hotkey_label(self.config.bugreel_hotkey)}\n"
            "COLAR TEXTO = CTRL + V\n"
            f"COLAR VIDEO = {self._format_hotkey_label(self.config.video_attach_hotkey)}\n"
            f"{privacy_note}"
        )

    def _effective_bugreel_command_hotkey(self) -> str:
        configured = self.config.bugreel_command_hotkey.strip()
        normalized = self._normalize_hotkey(configured)
        if normalized == "ctrl+shift+r":
            return "alt+shift+r"
        return configured

    def _bugreel_toggle_binding_help(self) -> str:
        command_raw = self._effective_bugreel_command_hotkey().strip()
        bugreel_hotkey = self._format_hotkey_label(self.config.bugreel_hotkey)
        if not command_raw:
            return (
                f"proxima acao: configure BUGREEL_COMMAND_HOTKEY no .env, abra o painel e tente "
                f"{bugreel_hotkey} novamente"
            )
        command_hotkey = self._format_hotkey_label(command_raw)
        return (
            f"proxima acao: abra a extensao do BugReel no Chrome, confirme que ela esta conectada, "
            f"vincule {command_hotkey} ao comando 'Toggle BugReel recording', abra o painel, clique em Compartilhar e depois use {bugreel_hotkey}"
        )

    def _build_bugreel_timeout_message(
        self,
        capture: PendingBugReelCapture,
        saw_new_recording: bool,
        observed_statuses: set[str],
    ) -> str:
        if not saw_new_recording:
            return f"CAPTURA NAO INICIOU\n{self._bugreel_toggle_binding_help()}"

        status_text = ", ".join(sorted(observed_statuses)) if observed_statuses else "desconhecido"
        if {"cancelled", "canceled", "aborted", "failed", "error"} & observed_statuses:
            return (
                "CAPTURA INTERROMPIDA\npossiveis causas: painel cancelado, compartilhamento interrompido ou limite de duracao"
            )
        if {"uploading", "processing", "transcribing", "analyzing", "queued", "pending"} & observed_statuses:
            return (
                "PROCESSAMENTO AINDA EM CURSO\n"
                f"status atual: {status_text}. Aguarde a conclusao e tente CTRL+SHIFT+V novamente."
            )
        if "recording" in observed_statuses and not capture.stop_requested:
            return (
                f"CAPTURA AINDA ATIVA\nuse {self._format_hotkey_label(self.config.bugreel_hotkey)} para encerrar e iniciar o processamento"
            )
        if capture.stop_requested:
            return (
                "VIDEO NAO FOI PUBLICADO A TEMPO\n"
                f"status observado: {status_text}. O upload automatico nao concluiu a tempo; revise a extensao e tente novamente."
            )
        return f"NENHUMA NOVA CAPTURA\n{self._bugreel_toggle_binding_help()}"

    @staticmethod
    def _normalize_hotkey(hotkey: str) -> str:
        raw = " ".join(hotkey.strip().lower().split())
        if not raw:
            return ""
        raw = (
            raw.replace("control", "ctrl")
            .replace("capslock", "caps lock")
            .replace("windows", "win")
            .replace("option", "alt")
        )
        aliases = {
            "caps": "caps lock",
            "esc": "escape",
        }
        parts = [aliases.get(part.strip(), part.strip()) for part in raw.split("+") if part.strip()]
        if not parts:
            return ""

        ordered_modifiers: list[str] = []
        main_keys: list[str] = []
        modifier_order = ("ctrl", "alt", "shift", "win", "cmd")
        modifier_set = set(modifier_order)

        for part in parts:
            if part in modifier_set:
                if part not in ordered_modifiers:
                    ordered_modifiers.append(part)
            else:
                main_keys.append(part)

        sorted_modifiers = [modifier for modifier in modifier_order if modifier in ordered_modifiers]
        return "+".join([*sorted_modifiers, *main_keys])

    def _is_chrome_foreground(self) -> bool:
        if os.name != "nt":
            return True
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if "chrome" in class_name.value.lower():
            return True

        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        return "chrome" in title.value.lower()

    def _focus_chrome_window(self) -> bool:
        if os.name != "nt":
            return True
        if self._is_chrome_foreground():
            return True

        user32 = ctypes.windll.user32
        windows: list[int] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, len(class_name))
            if "chrome_widgetwin" not in class_name.value.lower():
                return True
            windows.append(hwnd)
            return False

        callback_fn = enum_proc(callback)
        user32.EnumWindows(callback_fn, 0)
        if not windows:
            return False

        hwnd = windows[0]
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
        return self._is_chrome_foreground()

    @staticmethod
    def _is_safe_bugreel_boot_url(url: str) -> bool:
        normalized = url.strip().lower()
        if not normalized:
            return False
        return normalized.startswith(
            (
                "chrome-extension://",
                "moz-extension://",
                "http://localhost:",
                "https://localhost:",
                "http://127.0.0.1:",
                "https://127.0.0.1:",
            )
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


def main() -> None:
    config = AppConfig.load()
    app = BugVoiceReporterApp(config)
    app.run_forever()


if __name__ == "__main__":
    main()
