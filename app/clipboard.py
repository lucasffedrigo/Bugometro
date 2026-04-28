from __future__ import annotations

import struct
import threading
from pathlib import Path

import pyperclip

try:
    import win32clipboard  # type: ignore[import-not-found]
    import win32con  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional on non-Windows test envs
    win32clipboard = None
    win32con = None


class ClipboardService:
    def __init__(self, clear_after_seconds: int = 120) -> None:
        self.clear_after_seconds = clear_after_seconds
        self._lock = threading.Lock()
        self._clear_timer: threading.Timer | None = None
        self._last_copied_text: str | None = None

    def copy_text(self, content: str) -> None:
        if not content or not content.strip():
            raise ValueError("Nao e possivel copiar conteudo vazio para a area de transferencia.")
        pyperclip.copy(content)
        self._register_owned_text(content)

    def copy_payload(self, content: str, file_paths: list[Path] | None = None) -> None:
        if not content or not content.strip():
            raise ValueError("Nao e possivel copiar conteudo vazio para a area de transferencia.")

        normalized_paths = [Path(path) for path in file_paths or [] if Path(path).exists()]
        if normalized_paths and self._copy_windows_bundle(content, normalized_paths):
            self._register_owned_text(content)
            return

        pyperclip.copy(content)
        self._register_owned_text(content)

    def copy_files(self, file_paths: list[Path]) -> None:
        normalized_paths = [Path(path) for path in file_paths if Path(path).exists()]
        if not normalized_paths:
            raise ValueError("Nao ha arquivos validos para copiar no clipboard.")

        with self._lock:
            self._cancel_timer_locked()
            self._last_copied_text = None

        if not self._copy_windows_files(normalized_paths):
            raise RuntimeError(
                "Nao foi possivel copiar arquivos para o clipboard neste ambiente."
            )

    @staticmethod
    def paste_text() -> str:
        try:
            return pyperclip.paste()
        except Exception:
            return ""

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

    def _register_owned_text(self, content: str) -> None:
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

    def _cancel_timer_locked(self) -> None:
        if self._clear_timer is not None:
            self._clear_timer.cancel()
            self._clear_timer = None

    @staticmethod
    def _copy_windows_bundle(content: str, file_paths: list[Path]) -> bool:
        if win32clipboard is None or win32con is None:
            return False

        try:
            dropfiles = struct.pack("IiiII", 20, 0, 0, 0, 1)
            file_data = ("\0".join(str(path) for path in file_paths) + "\0\0").encode("utf-16le")

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, content)
            win32clipboard.SetClipboardData(win32con.CF_HDROP, dropfiles + file_data)
            return True
        except Exception:
            return False
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def _copy_windows_files(file_paths: list[Path]) -> bool:
        if win32clipboard is None or win32con is None:
            return False

        try:
            dropfiles = struct.pack("IiiII", 20, 0, 0, 0, 1)
            file_data = ("\0".join(str(path) for path in file_paths) + "\0\0").encode("utf-16le")

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, dropfiles + file_data)
            return True
        except Exception:
            return False
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
