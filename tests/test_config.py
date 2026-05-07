from app.config import AppConfig
from pathlib import Path


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

    assert config.voice_gif_hotkey == "ctrl+shift+alt"


def test_env_example_documents_all_runtime_env_vars() -> None:
    config_path = Path("app/config.py")
    example_path = Path(".env.example")
    source = config_path.read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip()
        for line in example_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }

    expected = set()
    marker = 'os.getenv("'
    for chunk in source.split(marker)[1:]:
        expected.add(chunk.split('"', 1)[0])

    assert expected <= documented
