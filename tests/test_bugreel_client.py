from datetime import datetime
from pathlib import Path

from app.bugreel_client import (
    BugReelClient,
    BugReelClientError,
    BugReelDownloadSettings,
    BugReelRecordingSummary,
)
from app.bugreel_context import BugReelContext


def test_bugreel_client_builds_enriched_context_from_payload(tmp_path: Path, monkeypatch) -> None:
    payload = {
        "recording": {
            "id": "rec-1",
            "share_token": "share-1",
            "duration_seconds": 18.2,
            "author": "Lucas",
            "metadata": {
                "browser": "Chrome",
                "os": "Windows 11",
                "url": "https://app.local/dashboard",
            },
            "analysis": {
                "title": "Título BugReel",
                "summary": "Resumo BugReel",
            },
            "transcript": {
                "text": "Ao clicar em salvar a tela fecha e a requisição falha.",
            },
            "console_events": [
                {"type": "error", "message": "TypeError: x is undefined"},
            ],
            "action_events": [
                {"type": "click", "target": "#save-button"},
            ],
            "url_events": [
                {"title": "Dashboard", "url": "https://app.local/dashboard"},
            ],
        },
        "frames": [
            {
                "time_seconds": 3,
                "description": "Modal fecha",
                "filename": "001_3.0s.jpg",
            }
        ],
    }

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr("app.bugreel_client.requests.get", lambda *args, **kwargs: Response())

    client = BugReelClient(
        api_token="",
        timeout_seconds=5,
        download_settings=BugReelDownloadSettings(enabled=False),
        bundle_dir_factory=lambda: tmp_path / "bundle",
    )
    context = BugReelContext(
        url="https://bugreel.local/report/share-1",
        base_url="https://bugreel.local",
        source_kind="report",
        source_id="share-1",
    )

    enriched = client.enrich_context(context)

    assert enriched.recording_id == "rec-1"
    assert enriched.share_token == "share-1"
    assert enriched.title == "Título BugReel"
    assert "Lucas" in enriched.metadata_lines[0]
    assert enriched.frame_filenames == ("001_3.0s.jpg",)
    assert enriched.video_url == "https://bugreel.local/api/recordings/share-1/video"


def test_bugreel_client_downloads_video_and_frames(tmp_path: Path, monkeypatch) -> None:
    payload = {
        "recording": {
            "id": "rec-1",
            "share_token": "share-1",
            "analysis": {},
            "transcript": {"text": ""},
        },
        "frames": [
            {"time_seconds": 3, "description": "Modal fecha", "filename": "001_3.0s.jpg"},
        ],
    }

    class JsonResponse:
        status_code = 200

        @staticmethod
        def json():
            return payload

    class FileResponse:
        status_code = 200

        def __init__(self, content: bytes) -> None:
            self.content = content

    def fake_get(url, *args, **kwargs):
        if "/api/recordings/by-token/" in url:
            return JsonResponse()
        if url.endswith("/video"):
            return FileResponse(b"video")
        if url.endswith(".jpg"):
            return FileResponse(b"frame")
        raise AssertionError(url)

    monkeypatch.setattr("app.bugreel_client.requests.get", fake_get)

    client = BugReelClient(
        api_token="",
        timeout_seconds=5,
        download_settings=BugReelDownloadSettings(enabled=True, frame_limit=1),
        bundle_dir_factory=lambda: tmp_path / "bundle",
    )
    monkeypatch.setattr(Path, "write_bytes", lambda self, data: len(data))

    context = BugReelContext(
        url="https://bugreel.local/report/share-1",
        base_url="https://bugreel.local",
        source_kind="report",
        source_id="share-1",
    )

    enriched = client.enrich_context(context)

    assert enriched.local_bundle_dir == tmp_path / "bundle"
    assert len(enriched.local_files) == 2


def test_bugreel_client_lists_recordings(monkeypatch) -> None:
    payload = {
        "recordings": [
            {
                "id": "rec-1",
                "share_token": "share-1",
                "status": "complete",
                "created_at": "2026-04-24T10:00:00Z",
            }
        ]
    }

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr("app.bugreel_client.requests.get", lambda *args, **kwargs: Response())

    client = BugReelClient(download_settings=BugReelDownloadSettings(enabled=False))
    recordings = client.list_recordings("http://localhost:3500", limit=5)

    assert len(recordings) == 1
    assert recordings[0].recording_id == "rec-1"
    assert recordings[0].to_context("http://localhost:3500").url.endswith("/report/share-1")


def test_bugreel_client_waits_for_new_complete_recording(monkeypatch) -> None:
    responses = iter(
        [
            {"recordings": [{"id": "old-1", "status": "complete", "created_at": "1"}]},
            {
                "recordings": [
                    {"id": "old-1", "status": "complete", "created_at": "1"},
                    {
                        "id": "new-1",
                        "share_token": "share-new-1",
                        "status": "complete",
                        "created_at": "2",
                    },
                ]
            },
        ]
    )

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        "app.bugreel_client.requests.get",
        lambda *args, **kwargs: Response(next(responses)),
    )
    monkeypatch.setattr("app.bugreel_client.time.sleep", lambda *args, **kwargs: None)

    client = BugReelClient(download_settings=BugReelDownloadSettings(enabled=False))
    summary = client.wait_for_new_recording(
        "http://localhost:3500",
        existing_ids={"old-1"},
        timeout_seconds=1,
        poll_interval_seconds=0,
    )

    assert summary.recording_id == "new-1"
    assert summary.share_token == "share-new-1"


def test_bugreel_client_accepts_error_recording_when_artifacts_are_ready(monkeypatch) -> None:
    payload = {
        "recordings": [
            {
                "id": "new-err-1",
                "share_token": "share-err-1",
                "status": "error",
                "created_at": "2026-04-24T10:00:00Z",
                "video_filename": "video.webm",
                "artifacts_ready": True,
                "ai_status": "ai_unavailable",
            }
        ]
    }

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr("app.bugreel_client.requests.get", lambda *args, **kwargs: Response())

    client = BugReelClient(download_settings=BugReelDownloadSettings(enabled=False))
    summary = client.wait_for_new_recording(
        "http://localhost:3500",
        existing_ids=set(),
        timeout_seconds=1,
        poll_interval_seconds=0,
    )

    assert summary.recording_id == "new-err-1"
    assert summary.is_ready_for_import() is True


def test_bugreel_recording_summary_estimates_end_time() -> None:
    summary = BugReelRecordingSummary(
        recording_id="rec-1",
        share_token=None,
        status="error",
        created_at="2026-04-24 10:00:00",
        duration_seconds=12,
    )

    assert summary.estimated_end_at() == datetime(2026, 4, 24, 10, 0, 12)
