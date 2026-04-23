from __future__ import annotations

import threading

try:
    import winsound
except Exception:  # pragma: no cover - Windows-only fallback
    winsound = None


class SoundNotifier:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def update_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def play_start(self) -> None:
        self._play(880, 90)

    def play_discard(self) -> None:
        self._play(440, 120)

    def play_success(self) -> None:
        self._play_pattern([(880, 80), (1046, 120)])

    def play_error(self) -> None:
        self._play_pattern([(330, 120), (220, 180)])

    def _play(self, frequency: int, duration_ms: int) -> None:
        if not self.enabled or winsound is None:
            return
        threading.Thread(
            target=lambda: winsound.Beep(frequency, duration_ms),
            daemon=True,
        ).start()

    def _play_pattern(self, pattern: list[tuple[int, int]]) -> None:
        if not self.enabled or winsound is None:
            return

        def runner() -> None:
            for frequency, duration_ms in pattern:
                winsound.Beep(frequency, duration_ms)

        threading.Thread(target=runner, daemon=True).start()
