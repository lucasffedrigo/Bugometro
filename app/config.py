from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    prompt_path: Path
    last_output_path: Path
    last_transcription_path: Path
    log_file_path: Path
    hotkey: str
    restart_hotkey: str
    hotkey_debounce_ms: int
    restart_delay_seconds: int
    sample_rate: int
    channels: int
    block_duration_ms: int
    silence_threshold: float
    silence_timeout_seconds: float
    max_recording_seconds: float
    transcription_provider: str
    formatter_provider: str
    gemini_api_key: str
    gemini_timeout_seconds: float
    gemini_transcription_model: str
    gemini_formatter_model: str
    openai_api_key: str
    openai_timeout_seconds: float
    openai_transcription_model: str
    openai_formatter_model: str
    debug_save_transcription: bool
    save_last_output: bool
    clipboard_clear_seconds: int
    log_to_file: bool

    @property
    def block_size(self) -> int:
        return int(self.sample_rate * (self.block_duration_ms / 1000))

    @classmethod
    def load(cls, project_root: Path | None = None) -> "AppConfig":
        root = project_root or Path(__file__).resolve().parents[1]
        load_dotenv(root / ".env", override=True)

        return cls(
            project_root=root,
            prompt_path=root / "prompts" / "bug_prompt.txt",
            last_output_path=root / "last_output.txt",
            last_transcription_path=root / "last_transcription.txt",
            log_file_path=root / "bug_voice_reporter.log",
            hotkey=os.getenv("APP_HOTKEY", "ctrl+tab"),
            restart_hotkey=os.getenv("APP_RESTART_HOTKEY", "ctrl+caps lock"),
            hotkey_debounce_ms=int(os.getenv("HOTKEY_DEBOUNCE_MS", "400")),
            restart_delay_seconds=int(os.getenv("RESTART_DELAY_SECONDS", "5")),
            sample_rate=int(os.getenv("AUDIO_SAMPLE_RATE", "16000")),
            channels=int(os.getenv("AUDIO_CHANNELS", "1")),
            block_duration_ms=int(os.getenv("AUDIO_BLOCK_DURATION_MS", "200")),
            silence_threshold=float(os.getenv("SILENCE_THRESHOLD", "0.015")),
            silence_timeout_seconds=float(os.getenv("SILENCE_TIMEOUT_SECONDS", "10")),
            max_recording_seconds=float(os.getenv("MAX_RECORDING_SECONDS", "180")),
            transcription_provider=os.getenv("TRANSCRIPTION_PROVIDER", "gemini").strip().lower(),
            formatter_provider=os.getenv("FORMATTER_PROVIDER", "gemini").strip().lower(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_timeout_seconds=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "45")),
            gemini_transcription_model=os.getenv(
                "GEMINI_TRANSCRIPTION_MODEL",
                "gemini-2.5-flash",
            ).strip(),
            gemini_formatter_model=os.getenv(
                "GEMINI_FORMATTER_MODEL",
                "gemini-2.5-flash",
            ).strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
            openai_transcription_model=os.getenv(
                "OPENAI_TRANSCRIPTION_MODEL",
                "gpt-4o-mini-transcribe",
            ).strip(),
            openai_formatter_model=os.getenv(
                "OPENAI_FORMATTER_MODEL",
                "gpt-4.1-mini",
            ).strip(),
            debug_save_transcription=_as_bool(
                os.getenv("DEBUG_SAVE_TRANSCRIPTION", "false")
            ),
            save_last_output=_as_bool(
                os.getenv("SAVE_LAST_OUTPUT", "false")
            ),
            clipboard_clear_seconds=int(
                os.getenv("CLIPBOARD_CLEAR_SECONDS", "120")
            ),
            log_to_file=_as_bool(
                os.getenv("LOG_TO_FILE", "false")
            ),
        )


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
