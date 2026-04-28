from app.config import AppConfig


def test_load_config_has_default_title_paste_hotkey(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("APP_TITLE_PASTE_HOTKEY", raising=False)

    config = AppConfig.load(project_root=tmp_path)

    assert config.title_paste_hotkey == "ctrl+'"
