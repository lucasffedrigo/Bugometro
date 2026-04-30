from app.hotkey import GlobalHotkeyManager


def test_global_hotkey_manager_can_suppress_original_keypress(monkeypatch) -> None:
    registered: dict[str, object] = {}

    def fake_add_hotkey(hotkey, callback, suppress=False):
        registered["hotkey"] = hotkey
        registered["callback"] = callback
        registered["suppress"] = suppress
        return "handler-id"

    monkeypatch.setattr("app.hotkey.keyboard.add_hotkey", fake_add_hotkey)
    monkeypatch.setattr("app.hotkey.keyboard.remove_hotkey", lambda handler: None)

    manager = GlobalHotkeyManager(
        hotkey="ctrl+shift+v",
        callback=lambda: None,
        suppress=True,
    )
    manager.start()

    assert registered["hotkey"] == "ctrl+shift+v"
    assert registered["suppress"] is True

    manager.stop()
