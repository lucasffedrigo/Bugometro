from app.silence_detector import SilenceDetector, SilenceEvent


def test_does_not_timeout_before_first_speech() -> None:
    detector = SilenceDetector(silence_threshold=0.1, silence_timeout_seconds=10)

    assert detector.update(level=0.01, timestamp=0.0) == []
    assert detector.update(level=0.01, timestamp=15.0) == []
    assert detector.has_detected_speech is False


def test_starts_countdown_only_after_speech_and_times_out() -> None:
    detector = SilenceDetector(silence_threshold=0.1, silence_timeout_seconds=10)

    assert detector.update(level=0.2, timestamp=1.0) == [SilenceEvent.SPEECH_STARTED]
    assert detector.update(level=0.01, timestamp=2.0) == []
    assert detector.update(level=0.01, timestamp=11.9) == []
    assert detector.update(level=0.01, timestamp=12.0) == [SilenceEvent.SILENCE_TIMEOUT]


def test_returns_to_recording_when_speech_resumes() -> None:
    detector = SilenceDetector(silence_threshold=0.1, silence_timeout_seconds=10)

    detector.update(level=0.2, timestamp=1.0)
    detector.update(level=0.01, timestamp=2.0)

    events = detector.update(level=0.2, timestamp=3.0)

    assert events == []
    assert detector.in_silence_countdown is False
