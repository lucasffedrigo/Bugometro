from app.hotkey import GlobalHotkeyManager


class FakeKeyboardEvent:
    def __init__(self, scan_code: int, event_type: str) -> None:
        self.scan_code = scan_code
        self.event_type = event_type


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


def test_exact_hotkey_waits_for_release_and_ignores_longer_chord(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.hotkey.keyboard.parse_hotkey",
        lambda hotkey: (((1,), (2,)),),
    )
    calls: list[str] = []
    manager = GlobalHotkeyManager(
        hotkey="ctrl+shift",
        callback=lambda: calls.append("called"),
        exact=True,
    )

    manager._handle_exact_event(FakeKeyboardEvent(1, "down"))
    manager._handle_exact_event(FakeKeyboardEvent(2, "down"))

    assert calls == []

    manager._handle_exact_event(FakeKeyboardEvent(3, "down"))
    manager._handle_exact_event(FakeKeyboardEvent(3, "up"))
    manager._handle_exact_event(FakeKeyboardEvent(2, "up"))
    manager._handle_exact_event(FakeKeyboardEvent(1, "up"))

    assert calls == []

    manager._handle_exact_event(FakeKeyboardEvent(1, "down"))
    manager._handle_exact_event(FakeKeyboardEvent(2, "down"))
    manager._handle_exact_event(FakeKeyboardEvent(2, "up"))

    assert calls == ["called"]


def test_exact_hotkey_never_suppresses_the_keyboard_stream(monkeypatch) -> None:
    registered: dict[str, object] = {}

    monkeypatch.setattr(
        "app.hotkey.keyboard.parse_hotkey",
        lambda hotkey: (((1,), (2,), (3,)),),
    )

    def fake_hook(callback, suppress=False):
        registered["callback"] = callback
        registered["suppress"] = suppress
        return "hook-id"

    monkeypatch.setattr("app.hotkey.keyboard.hook", fake_hook)
    monkeypatch.setattr("app.hotkey.keyboard.unhook", lambda handler: None)

    manager = GlobalHotkeyManager(
        hotkey="ctrl+shift+space",
        callback=lambda: None,
        suppress=True,
        exact=True,
    )
    manager.start()

    assert registered["suppress"] is False

    manager.stop()
