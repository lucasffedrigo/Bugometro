from app.config import AppConfig


def test_load_config_has_default_title_paste_hotkey(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("APP_TITLE_PASTE_HOTKEY", raising=False)

    config = AppConfig.load(project_root=tmp_path)

    assert config.title_paste_hotkey == "ctrl+'"


def test_load_config_captures_desktop_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("NATIVE_CAPTURE_TARGET", raising=False)

    config = AppConfig.load(project_root=tmp_path)

    assert config.native_capture_target == "desktop"


def test_load_config_has_default_voice_gif_hotkey(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("APP_VOICE_GIF_HOTKEY", raising=False)

    config = AppConfig.load(project_root=tmp_path)

    assert config.voice_gif_hotkey == "ctrl+f4"
