from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
import threading
from types import SimpleNamespace

from app.main import BugVoiceReporterApp, PendingNativeCapture
from app.state import AppStatus, StateMachine
from app.native_capture import NativeScreenRecordingResult
from app.recorder import RecorderStopReason, RecordingResult


class _RecordingExecutor:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, tuple[object, ...]]] = []

    def submit(self, fn, *args):
        future: Future[object] = Future()
        self.submissions.append((getattr(fn, "__name__", repr(fn)), args))
        return future


class _StorageStub:
    def cleanup_file(self, path: Path | None) -> None:
        del path


class _LoggerStub:
    def info(self, *args, **kwargs) -> None:
        del args, kwargs

    def exception(self, *args, **kwargs) -> None:
        raise AssertionError(args, kwargs)


class _StatusStub:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def show_status(self, message: str, **kwargs) -> None:
        del kwargs
        self.messages.append(message)


class _SoundStub:
    def __init__(self) -> None:
        self.starts = 0

    def play_start(self) -> None:
        self.starts += 1


class _RecorderStub:
    def __init__(self, recording: bool = True) -> None:
        self.is_recording = recording
        self.stops: list[RecorderStopReason] = []

    def stop(self, reason: RecorderStopReason = RecorderStopReason.MANUAL) -> bool:
        self.stops.append(reason)
        self.is_recording = False
        return True


class _ScreenRecorderStub:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> bool:
        self.stops += 1
        return True


def _minimal_voice_app() -> BugVoiceReporterApp:
    app = BugVoiceReporterApp.__new__(BugVoiceReporterApp)
    app._lock = threading.RLock()
    app.config = SimpleNamespace(
        hotkey="ctrl+f2",
        voice_gif_hotkey="ctrl+f4",
    )
    app.state = StateMachine()
    app.state.transition(AppStatus.RECORDING)
    app.recorder = _RecorderStub(recording=True)
    app.native_screen_recorder = _ScreenRecorderStub()
    app._native_prework_executor = _RecordingExecutor()
    app.devtools_mcp_client = SimpleNamespace(collect_context=lambda: None)
    app._active_native_capture = None
    app._active_voice_gif_capture = None
    app._pending_voice_audio_result = None
    app._last_native_video_path = None
    app._restart_pending = False
    app.status_ui = _StatusStub()
    app.sound_notifier = _SoundStub()
    app.logger = _LoggerStub()
    app._set_status_label = lambda value: None
    app._cancel_idle_reset = lambda: None
    return app


def test_native_capture_starts_transcription_before_screen_finishes(tmp_path: Path) -> None:
    audio_path = tmp_path / "capture.wav"
    audio_path.write_bytes(b"audio")
    executor = _RecordingExecutor()

    app = BugVoiceReporterApp.__new__(BugVoiceReporterApp)
    app._lock = threading.RLock()
    app._native_prework_executor = executor
    app._active_native_capture = PendingNativeCapture(
        started_at=datetime.now(),
        stop_requested=False,
    )
    app.storage = _StorageStub()
    app.logger = _LoggerStub()
    app.devtools_mcp_client = SimpleNamespace(collect_context=lambda: None)

    app._on_native_capture_audio_finished(
        RecordingResult(path=audio_path, reason=RecorderStopReason.MANUAL)
    )

    assert app._active_native_capture is not None
    assert app._active_native_capture.transcription_future is not None
    assert executor.submissions == [
        ("_transcribe_native_capture_audio", (audio_path,))
    ]


def test_native_capture_publishes_gif_path_when_screen_finishes(tmp_path: Path) -> None:
    video_path = tmp_path / "native_evidence.gif"
    video_path.write_bytes(b"gif")

    app = BugVoiceReporterApp.__new__(BugVoiceReporterApp)
    app._lock = threading.RLock()
    app._native_prework_executor = _RecordingExecutor()
    app._active_native_capture = PendingNativeCapture(
        started_at=datetime.now(),
        stop_requested=False,
    )
    app._last_native_video_path = None
    app.devtools_mcp_client = SimpleNamespace(collect_context=lambda: None)

    app._on_native_capture_screen_finished(
        NativeScreenRecordingResult(
            video_path=video_path,
            bundle_dir=tmp_path,
            reason="manual",
            target_kind="janela",
            target_title="Benchmark",
            capture_rect=(0, 0, 800, 600),
            duration_seconds=2.0,
            capture_fps=10,
        )
    )

    assert app._last_native_video_path == video_path


def test_hotkey_release_wait_uses_short_post_release_delay(monkeypatch) -> None:
    sleeps: list[float] = []

    monkeypatch.setattr("app.main.keyboard.is_pressed", lambda key: False)
    monkeypatch.setattr("app.main.time.sleep", lambda seconds: sleeps.append(seconds))

    BugVoiceReporterApp._wait_for_hotkey_release("ctrl+shift+v")

    assert sleeps == [0.02]


def test_voice_gif_hotkey_starts_and_stops_screen_capture() -> None:
    app = _minimal_voice_app()

    app.handle_voice_gif_hotkey()
    app.handle_voice_gif_hotkey()

    assert app.native_screen_recorder.starts == 1
    assert app.native_screen_recorder.stops == 1
    assert app._active_voice_gif_capture is not None
    assert app._active_voice_gif_capture.stop_requested is True


def test_voice_recording_prioritizes_report_before_running_gif_finishes(tmp_path: Path) -> None:
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"audio")
    app = _minimal_voice_app()
    started: list[
        tuple[
            RecordingResult,
            NativeScreenRecordingResult | None,
            Future[str] | None,
            Future[object] | None,
            bool,
        ]
    ] = []
    app.handle_voice_gif_hotkey()
    app._start_voice_processing = (
        lambda result,
        screen_result=None,
        transcription_future=None,
        devtools_future=None,
        gif_requested=False: started.append(
            (
                result,
                screen_result,
                transcription_future,
                devtools_future,
                gif_requested,
            )
        )
    )

    audio_result = RecordingResult(path=audio_path, reason=RecorderStopReason.MANUAL)
    app._on_recording_finished(audio_result)

    assert len(started) == 1
    assert started[0][0] == audio_result
    assert started[0][1] is None
    assert started[0][2] is not None
    assert started[0][3] is not None
    assert started[0][4] is True
    assert app._pending_voice_audio_result is None
    assert app.native_screen_recorder.stops == 1
    assert app._active_voice_gif_capture is not None
    assert [
        name for name, _args in app._native_prework_executor.submissions
    ] == ["_transcribe_native_capture_audio", "<lambda>"]

    video_path = tmp_path / "native_evidence.gif"
    video_path.write_bytes(b"gif")
    screen_result = NativeScreenRecordingResult(
        video_path=video_path,
        bundle_dir=tmp_path,
        reason="manual",
        target_kind="desktop",
        target_title="Area de trabalho",
        capture_rect=(0, 0, 1920, 1080),
        duration_seconds=2.0,
        capture_fps=10,
    )
    app._on_native_capture_screen_finished(screen_result)

    assert len(started) == 1
    assert app._pending_voice_audio_result is None
    assert app._active_voice_gif_capture is None
    assert app._last_native_video_path == video_path
