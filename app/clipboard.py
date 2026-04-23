from __future__ import annotations

import threading

import pyperclip


class ClipboardService:
    def __init__(self, clear_after_seconds: int = 120) -> None:
        self.clear_after_seconds = clear_after_seconds
        self._lock = threading.Lock()
        self._clear_timer: threading.Timer | None = None
        self._last_copied_text: str | None = None

    def copy_text(self, content: str) -> None:
        if not content or not content.strip():
            raise ValueError("Não é possível copiar conteúdo vazio para a área de transferência.")
        pyperclip.copy(content)
        with self._lock:
            self._last_copied_text = content
            self._cancel_timer_locked()
            if self.clear_after_seconds > 0:
                self._clear_timer = threading.Timer(
                    self.clear_after_seconds,
                    self._clear_if_unchanged,
                )
                self._clear_timer.daemon = True
                self._clear_timer.start()

    def stop(self) -> None:
        with self._lock:
            self._cancel_timer_locked()
            self._last_copied_text = None

    def _clear_if_unchanged(self) -> None:
        with self._lock:
            expected = self._last_copied_text
            self._clear_timer = None
        if not expected:
            return
        try:
            current = pyperclip.paste()
        except Exception:
            return
        if current != expected:
            return
        try:
            pyperclip.copy("")
        except Exception:
            return
        with self._lock:
            if self._last_copied_text == expected:
                self._last_copied_text = None

    def _cancel_timer_locked(self) -> None:
        if self._clear_timer is not None:
            self._clear_timer.cancel()
            self._clear_timer = None
