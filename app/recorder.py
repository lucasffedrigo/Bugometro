from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from app.silence_detector import SilenceDetector, SilenceEvent


class RecorderStopReason(str, Enum):
    MANUAL = "manual"
    SILENCE = "silence"
    MAX_DURATION = "max_duration"
    RESTART = "restart"
    FAILED = "failed"


@dataclass
class RecordingResult:
    path: Path | None
    reason: RecorderStopReason
    error_message: str | None = None


class AudioRecorder:
    def __init__(
        self,
        sample_rate: int,
        channels: int,
        block_size: int,
        max_recording_seconds: float,
        silence_detector: SilenceDetector,
        temp_file_factory: Callable[[], Path],
        logger: logging.Logger,
        on_speech_started: Callable[[], None] | None = None,
        on_finished: Callable[[RecordingResult], None] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = block_size
        self.max_recording_seconds = max_recording_seconds
        self.silence_detector = silence_detector
        self.temp_file_factory = temp_file_factory
        self.logger = logger
        self.on_speech_started = on_speech_started
        self.on_finished = on_finished

        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._stop_reason = RecorderStopReason.MANUAL
        self._start_time = 0.0
        self._temp_path: Path | None = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                raise RuntimeError("Já existe uma gravação em andamento.")

            self._running = True
            self._frames = []
            self._stop_event.clear()
            self._stop_reason = RecorderStopReason.MANUAL
            self._start_time = time.monotonic()
            self._temp_path = self.temp_file_factory()
            self.silence_detector.reset()
            self._thread = threading.Thread(
                target=self._run,
                name="audio-recorder",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._running = False
                raise

    def stop(self, reason: RecorderStopReason = RecorderStopReason.MANUAL) -> bool:
        with self._lock:
            if not self._running:
                return False
            self._stop_reason = reason
            self._stop_event.set()
            return True

    def _run(self) -> None:
        result = RecordingResult(path=self._temp_path, reason=self._stop_reason)
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                blocksize=self.block_size,
                callback=self._audio_callback,
            ):
                while not self._stop_event.wait(0.1):
                    pass

            audio_data = (
                np.concatenate(self._frames, axis=0)
                if self._frames
                else np.zeros((0, self.channels), dtype=np.float32)
            )
            if self._temp_path is None:
                raise RuntimeError("Arquivo temporário de áudio não foi criado.")

            sf.write(self._temp_path, audio_data, self.sample_rate, format="WAV")
            result = RecordingResult(path=self._temp_path, reason=self._stop_reason)
            self.logger.info(
                "Gravação finalizada. Motivo=%s arquivo=%s",
                result.reason,
                result.path,
            )
        except Exception as exc:
            self.logger.exception("Falha na gravação do microfone.")
            result = RecordingResult(
                path=self._temp_path,
                reason=RecorderStopReason.FAILED,
                error_message=str(exc),
            )
        finally:
            with self._lock:
                self._running = False
            if self.on_finished is not None:
                self.on_finished(result)

    def _audio_callback(self, indata: np.ndarray, frames: int, *_args) -> None:
        if self._stop_event.is_set():
            raise sd.CallbackStop

        self._frames.append(indata.copy())

        now = time.monotonic()
        level = float(np.sqrt(np.mean(np.square(indata)))) if indata.size else 0.0
        events = self.silence_detector.update(level=level, timestamp=now)

        for event in events:
            if event == SilenceEvent.SPEECH_STARTED and self.on_speech_started is not None:
                self.on_speech_started()
            elif event == SilenceEvent.SILENCE_TIMEOUT:
                self._stop_reason = RecorderStopReason.SILENCE
                self._stop_event.set()
                raise sd.CallbackStop

        if (now - self._start_time) >= self.max_recording_seconds:
            self._stop_reason = RecorderStopReason.MAX_DURATION
            self._stop_event.set()
            raise sd.CallbackStop
