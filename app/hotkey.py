from __future__ import annotations

import threading
import time
from collections.abc import Callable

import keyboard


class GlobalHotkeyManager:
    def __init__(
        self,
        hotkey: str,
        callback: Callable[[], None],
        debounce_ms: int = 400,
    ) -> None:
        self.hotkey = hotkey
        self.callback = callback
        self.debounce_ms = debounce_ms
        self._last_trigger_at = 0.0
        self._lock = threading.Lock()
        self._handler = None

    def start(self) -> None:
        if self._handler is not None:
            return
        self._handler = keyboard.add_hotkey(self.hotkey, self._handle_trigger)

    def stop(self) -> None:
        if self._handler is None:
            return
        keyboard.remove_hotkey(self._handler)
        self._handler = None

    def wait(self) -> None:
        keyboard.wait()

    def _handle_trigger(self) -> None:
        now = time.monotonic()
        with self._lock:
            elapsed_ms = (now - self._last_trigger_at) * 1000
            if elapsed_ms < self.debounce_ms:
                return
            self._last_trigger_at = now
        self.callback()
