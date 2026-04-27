from pathlib import Path

from app.transcriber import Transcriber


def test_detect_mime_type_for_wav() -> None:
    mime = Transcriber._detect_mime_type(Path("sample.wav"))
    assert mime == "audio/wav"


def test_detect_mime_type_for_webm() -> None:
    mime = Transcriber._detect_mime_type(Path("capture.webm"))
    assert mime == "audio/webm"


def test_detect_mime_type_fallback() -> None:
    mime = Transcriber._detect_mime_type(Path("unknown.bin"))
    assert mime == "application/octet-stream"

