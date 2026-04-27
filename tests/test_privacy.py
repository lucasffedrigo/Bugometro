from pathlib import Path

from app.clipboard import ClipboardService
from app.storage import LocalStorage


def test_storage_does_not_persist_output_when_disabled(tmp_path: Path) -> None:
    output_path = tmp_path / "last_output.txt"
    transcription_path = tmp_path / "last_transcription.txt"
    storage = LocalStorage(
        last_output_path=output_path,
        last_transcription_path=transcription_path,
        debug_save_transcription=False,
        save_last_output=False,
    )

    result = storage.save_last_output("conteúdo sensível")

    assert result is None
    assert not output_path.exists()


def test_storage_cleanup_sensitive_outputs_removes_stale_files(tmp_path: Path) -> None:
    output_path = tmp_path / "last_output.txt"
    transcription_path = tmp_path / "last_transcription.txt"
    output_path.write_text("relatório", encoding="utf-8")
    transcription_path.write_text("transcrição", encoding="utf-8")

    storage = LocalStorage(
        last_output_path=output_path,
        last_transcription_path=transcription_path,
        debug_save_transcription=False,
        save_last_output=False,
    )

    storage.cleanup_sensitive_outputs()

    assert not output_path.exists()
    assert not transcription_path.exists()


def test_clipboard_auto_clear_does_not_override_newer_copy(monkeypatch) -> None:
    clipboard_state = {"value": ""}

    def fake_copy(value: str) -> None:
        clipboard_state["value"] = value

    def fake_paste() -> str:
        return clipboard_state["value"]

    monkeypatch.setattr("app.clipboard.pyperclip.copy", fake_copy)
    monkeypatch.setattr("app.clipboard.pyperclip.paste", fake_paste)

    clipboard = ClipboardService(clear_after_seconds=1)
    clipboard.copy_text("primeiro texto")
    clipboard.copy_text("segundo texto")
    fake_copy("conteúdo do usuário")
    clipboard._clear_if_unchanged()

    assert clipboard_state["value"] == "conteúdo do usuário"
    clipboard.stop()


def test_clipboard_auto_clear_removes_own_content(monkeypatch) -> None:
    clipboard_state = {"value": ""}

    def fake_copy(value: str) -> None:
        clipboard_state["value"] = value

    def fake_paste() -> str:
        return clipboard_state["value"]

    monkeypatch.setattr("app.clipboard.pyperclip.copy", fake_copy)
    monkeypatch.setattr("app.clipboard.pyperclip.paste", fake_paste)

    clipboard = ClipboardService(clear_after_seconds=1)
    clipboard.copy_text("bug report")
    clipboard._clear_if_unchanged()

    assert clipboard_state["value"] == ""
    clipboard.stop()


def test_clipboard_copy_files_requires_valid_paths(tmp_path: Path) -> None:
    clipboard = ClipboardService(clear_after_seconds=1)
    missing = tmp_path / "missing.webm"
    try:
        try:
            clipboard.copy_files([missing])
            assert False, "expected ValueError"
        except ValueError:
            pass
    finally:
        clipboard.stop()


def test_clipboard_copy_files_uses_windows_transport(monkeypatch, tmp_path: Path) -> None:
    copied: dict[str, object] = {}
    video = tmp_path / "video.webm"
    video.write_bytes(b"1")

    def fake_copy_windows_files(paths):
        copied["paths"] = paths
        return True

    monkeypatch.setattr(
        "app.clipboard.ClipboardService._copy_windows_files",
        staticmethod(fake_copy_windows_files),
    )

    clipboard = ClipboardService(clear_after_seconds=1)
    try:
        clipboard.copy_files([video])
        assert copied["paths"] == [video]
    finally:
        clipboard.stop()
