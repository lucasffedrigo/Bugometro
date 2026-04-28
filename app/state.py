from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AppStatus(str, Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    SILENCE_COUNTDOWN = "SILENCE_COUNTDOWN"
    PROCESSING_TRANSCRIPTION = "PROCESSING_TRANSCRIPTION"
    PROCESSING_FORMATTING = "PROCESSING_FORMATTING"
    COPIED = "COPIED"
    ERROR = "ERROR"


ALLOWED_TRANSITIONS: dict[AppStatus, set[AppStatus]] = {
    AppStatus.IDLE: {
        AppStatus.RECORDING,
        AppStatus.PROCESSING_TRANSCRIPTION,
        AppStatus.PROCESSING_FORMATTING,
        AppStatus.ERROR,
    },
    AppStatus.RECORDING: {
        AppStatus.IDLE,
        AppStatus.SILENCE_COUNTDOWN,
        AppStatus.PROCESSING_TRANSCRIPTION,
        AppStatus.ERROR,
    },
    AppStatus.SILENCE_COUNTDOWN: {
        AppStatus.IDLE,
        AppStatus.RECORDING,
        AppStatus.PROCESSING_TRANSCRIPTION,
        AppStatus.ERROR,
    },
    AppStatus.PROCESSING_TRANSCRIPTION: {
        AppStatus.PROCESSING_FORMATTING,
        AppStatus.ERROR,
    },
    AppStatus.PROCESSING_FORMATTING: {AppStatus.COPIED, AppStatus.ERROR},
    AppStatus.COPIED: {
        AppStatus.IDLE,
        AppStatus.RECORDING,
        AppStatus.ERROR,
    },
    AppStatus.ERROR: {
        AppStatus.IDLE,
        AppStatus.RECORDING,
        AppStatus.PROCESSING_TRANSCRIPTION,
        AppStatus.PROCESSING_FORMATTING,
    },
}


@dataclass
class StateMachine:
    current: AppStatus = AppStatus.IDLE
    last_error: str | None = None
    history: list[AppStatus] = field(default_factory=lambda: [AppStatus.IDLE])

    def can_transition(self, new_state: AppStatus) -> bool:
        if new_state == self.current:
            return True
        return new_state in ALLOWED_TRANSITIONS[self.current]

    def transition(self, new_state: AppStatus, error_message: str | None = None) -> None:
        if not self.can_transition(new_state):
            raise ValueError(f"Invalid state transition: {self.current} -> {new_state}")

        self.current = new_state
        if new_state == AppStatus.ERROR:
            self.last_error = error_message or "Erro desconhecido."
        elif new_state != AppStatus.ERROR:
            self.last_error = None
        self.history.append(new_state)

    def reset(self) -> None:
        if self.current != AppStatus.IDLE:
            self.transition(AppStatus.IDLE)

    @property
    def is_processing(self) -> bool:
        return self.current in {
            AppStatus.PROCESSING_TRANSCRIPTION,
            AppStatus.PROCESSING_FORMATTING,
        }

    @property
    def is_recording_active(self) -> bool:
        return self.current in {
            AppStatus.RECORDING,
            AppStatus.SILENCE_COUNTDOWN,
        }
