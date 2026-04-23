from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SilenceEvent(str, Enum):
    SPEECH_STARTED = "speech_started"
    SILENCE_TIMEOUT = "silence_timeout"


@dataclass
class SilenceDetector:
    silence_threshold: float
    silence_timeout_seconds: float
    has_detected_speech: bool = False
    in_silence_countdown: bool = False
    silence_started_at: float | None = None
    has_timed_out: bool = False

    def reset(self) -> None:
        self.has_detected_speech = False
        self.in_silence_countdown = False
        self.silence_started_at = None
        self.has_timed_out = False

    def update(self, level: float, timestamp: float) -> list[SilenceEvent]:
        events: list[SilenceEvent] = []
        is_speech = level >= self.silence_threshold

        if is_speech:
            if not self.has_detected_speech:
                self.has_detected_speech = True
                events.append(SilenceEvent.SPEECH_STARTED)

            if self.in_silence_countdown:
                self.in_silence_countdown = False
                self.silence_started_at = None
                self.has_timed_out = False

            return events

        if not self.has_detected_speech:
            return events

        if not self.in_silence_countdown:
            self.in_silence_countdown = True
            self.silence_started_at = timestamp
            self.has_timed_out = False
            return events

        if (
            self.silence_started_at is not None
            and not self.has_timed_out
            and (timestamp - self.silence_started_at) >= self.silence_timeout_seconds
        ):
            self.has_timed_out = True
            events.append(SilenceEvent.SILENCE_TIMEOUT)

        return events
