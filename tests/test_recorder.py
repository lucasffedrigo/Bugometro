import logging
from pathlib import Path

import numpy as np
import pytest
import sounddevice as sd

from app.recorder import AudioRecorder, RecorderStopReason
from app.silence_detector import SilenceDetector


def _recorder(auto_stop_on_silence: bool) -> AudioRecorder:
    recorder = AudioRecorder(
        sample_rate=16000,
        channels=1,
        block_size=3200,
        max_recording_seconds=180,
        silence_detector=SilenceDetector(
            silence_threshold=0.1,
            silence_timeout_seconds=10,
        ),
        auto_stop_on_silence=auto_stop_on_silence,
        temp_file_factory=lambda: Path("test.wav"),
        logger=logging.getLogger("test-recorder"),
    )
    recorder._start_time = 0.0
    return recorder


def test_silence_timeout_does_not_stop_recording_when_disabled(monkeypatch) -> None:
    recorder = _recorder(auto_stop_on_silence=False)
    speech = np.full((3200, 1), 0.2, dtype=np.float32)
    silence = np.zeros((3200, 1), dtype=np.float32)

    monkeypatch.setattr("app.recorder.time.monotonic", lambda: 1.0)
    recorder._audio_callback(speech, 3200)

    monkeypatch.setattr("app.recorder.time.monotonic", lambda: 2.0)
    recorder._audio_callback(silence, 3200)

    monkeypatch.setattr("app.recorder.time.monotonic", lambda: 12.0)
    recorder._audio_callback(silence, 3200)

    assert recorder._stop_event.is_set() is False
    assert recorder._stop_reason == RecorderStopReason.MANUAL


def test_silence_timeout_can_stop_recording_when_enabled(monkeypatch) -> None:
    recorder = _recorder(auto_stop_on_silence=True)
    speech = np.full((3200, 1), 0.2, dtype=np.float32)
    silence = np.zeros((3200, 1), dtype=np.float32)

    monkeypatch.setattr("app.recorder.time.monotonic", lambda: 1.0)
    recorder._audio_callback(speech, 3200)

    monkeypatch.setattr("app.recorder.time.monotonic", lambda: 2.0)
    recorder._audio_callback(silence, 3200)

    monkeypatch.setattr("app.recorder.time.monotonic", lambda: 12.0)
    with pytest.raises(sd.CallbackStop):
        recorder._audio_callback(silence, 3200)

    assert recorder._stop_event.is_set() is True
    assert recorder._stop_reason == RecorderStopReason.SILENCE
