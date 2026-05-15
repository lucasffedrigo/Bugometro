from __future__ import annotations

import threading
import time
from collections.abc import Callable

import keyboard


def is_modifier_only_hotkey(hotkey: str) -> bool:
    parsed = keyboard.parse_hotkey(hotkey)
    if len(parsed) != 1:
        return False
    return all(
        keyboard.is_modifier(scan_code)
        for group in parsed[0]
        for scan_code in group
    )


def reset_keyboard_runtime_state() -> None:
    # The keyboard package keeps process-wide pressed-key tables. If Windows or
    # another hook misses a key-up event, those tables can stay dirty until the
    # app restarts. Clearing them is safe for this app and restores hotkeys.
    lock = getattr(keyboard, "_pressed_events_lock", None)
    if lock is None:
        return
    with lock:
        for name in (
            "_pressed_events",
            "_physically_pressed_keys",
            "_logically_pressed_keys",
        ):
            value = getattr(keyboard, name, None)
            if hasattr(value, "clear"):
                value.clear()
        listener = getattr(keyboard, "_listener", None)
        active_modifiers = getattr(listener, "active_modifiers", None)
        if hasattr(active_modifiers, "clear"):
            active_modifiers.clear()


class GlobalHotkeyManager:
    def __init__(
        self,
        hotkey: str,
        callback: Callable[[], None],
        debounce_ms: int = 400,
        suppress: bool = False,
        exact: bool = False,
    ) -> None:
        self.hotkey = hotkey
        self.callback = callback
        self.debounce_ms = debounce_ms
        self.suppress = suppress
        self.exact = exact
        self._last_trigger_at = 0.0
        self._lock = threading.Lock()
        self._handler = None
        self._pressed_scan_codes: set[int] = set()
        self._exact_candidate = False
        self._target_groups = self._parse_target_groups(hotkey) if exact else ()
        self._target_scan_codes = {
            code for group in self._target_groups for code in group
        }

    def start(self) -> None:
        with self._lock:
            if self._handler is not None:
                return
            self._reset_exact_state_locked()
            if self.exact:
                # keyboard.hook(suppress=True) suppresses the entire keyboard stream.
                # Exact modifier-only hotkeys must observe events without blocking typing.
                self._handler = keyboard.hook(
                    self._handle_exact_event,
                    suppress=False,
                )
                return
            self._handler = keyboard.add_hotkey(
                self.hotkey,
                self._handle_trigger,
                suppress=self.suppress,
            )

    def stop(self) -> None:
        with self._lock:
            if self._handler is None:
                return
            if self.exact:
                keyboard.unhook(self._handler)
            else:
                keyboard.remove_hotkey(self._handler)
            self._handler = None
            self._reset_exact_state_locked()

    def refresh(self) -> None:
        with self._lock:
            was_started = self._handler is not None
        if not was_started:
            self.reset_state()
            return
        self.stop()
        self.start()

    def reset_state(self) -> None:
        with self._lock:
            self._reset_exact_state_locked()

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

    def _handle_exact_event(self, event) -> None:
        scan_code = int(getattr(event, "scan_code", 0) or 0)
        if scan_code <= 0:
            return
        event_type = getattr(event, "event_type", "")

        with self._lock:
            if scan_code not in self._target_scan_codes:
                if event_type == "down":
                    self._exact_candidate = False
                return

            if event_type == "down":
                self._pressed_scan_codes.add(scan_code)
                if self._matches_exact_target():
                    self._exact_candidate = True
                return

            if event_type == "up":
                should_trigger = self._exact_candidate and self._matches_exact_target()
                self._pressed_scan_codes.discard(scan_code)
                if should_trigger:
                    self._exact_candidate = False

        if event_type == "up" and should_trigger:
            self._handle_trigger()

    def _matches_exact_target(self) -> bool:
        if not self._target_groups:
            return False
        if not self._pressed_scan_codes <= self._target_scan_codes:
            return False
        return all(self._pressed_scan_codes & group for group in self._target_groups)

    @staticmethod
    def _parse_target_groups(hotkey: str) -> tuple[frozenset[int], ...]:
        parsed = keyboard.parse_hotkey(hotkey)
        if len(parsed) != 1:
            raise ValueError("Hotkeys exatas devem ter apenas uma combinacao.")
        return tuple(frozenset(group) for group in parsed[0])

    def _reset_exact_state_locked(self) -> None:
        self._pressed_scan_codes.clear()
        self._exact_candidate = False
