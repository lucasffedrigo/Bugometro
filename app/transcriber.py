from __future__ import annotations

import base64
from pathlib import Path

import requests

from app.gemini_utils import extract_text_from_response_json, format_gemini_http_error
from app.openai_utils import format_openai_http_error


class TranscriptionError(RuntimeError):
    """Raised when audio transcription fails."""


class Transcriber:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        retry_limit: int = 1,
        min_transcription_chars: int = 12,
        provider: str = "gemini",
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retry_limit = retry_limit
        self.min_transcription_chars = min_transcription_chars

    def transcribe(self, audio_path: Path) -> str:
        prompts = [
            (
                "Gere apenas a transcrição literal da fala deste áudio em português do Brasil. "
                "Não resuma, não formate, não adicione comentários e não invente conteúdo."
            ),
            (
                "Transcreva palavra por palavra a fala deste áudio em português do Brasil, "
                "preservando termos técnicos, nomes de componentes, versões, navegadores, "
                "ambientes e mensagens de erro. Se houver pouca fala, retorne exatamente o "
                "que foi dito."
            ),
        ]
        last_text = ""
        attempts = max(1, self.retry_limit + 1)
        for attempt in range(attempts):
            prompt = prompts[min(attempt, len(prompts) - 1)]
            text = self._request_transcription(audio_path, prompt)
            normalized = text.strip()
            if self._is_usable(normalized):
                return normalized
            last_text = normalized

        if last_text and self._is_silent_marker(last_text):
            raise TranscriptionError(
                "A transcrição veio vazia. Grave novamente e fale com mais clareza."
            )
        if last_text:
            return last_text
        raise TranscriptionError(
            "A transcrição veio vazia. Grave novamente e fale com mais clareza."
        )

    def _request_transcription(self, audio_path: Path, prompt: str) -> str:
        if self.provider == "openai":
            return self._request_openai_transcription(audio_path, prompt)
        return self._request_gemini_transcription(audio_path, prompt)

    def _request_gemini_transcription(self, audio_path: Path, prompt: str) -> str:
        audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "audio/wav",
                                "data": audio_b64,
                            }
                        },
                    ]
                }
            ]
        }

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TranscriptionError(
                "Falha de rede ao transcrever o áudio com o Gemini. Tente novamente."
            ) from exc

        if response.status_code >= 400:
            raise TranscriptionError(
                format_gemini_http_error("a transcrição do áudio", response)
            )

        return extract_text_from_response_json(response.json())

    def _request_openai_transcription(self, audio_path: Path, prompt: str) -> str:
        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "model": self.model,
            "prompt": prompt,
        }
        files = {
            "file": (audio_path.name, audio_path.read_bytes(), "audio/wav"),
        }

        try:
            response = requests.post(
                url,
                headers=headers,
                data=data,
                files=files,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TranscriptionError(
                "Falha de rede ao transcrever o áudio com a OpenAI. Tente novamente."
            ) from exc

        if response.status_code >= 400:
            raise TranscriptionError(
                format_openai_http_error("a transcrição do áudio", response)
            )

        payload = response.json()
        text = payload.get("text", "")
        return str(text).strip()

    def _is_usable(self, text: str) -> bool:
        if not text or self._is_silent_marker(text):
            return False
        return len(text) >= self.min_transcription_chars

    @staticmethod
    def _is_silent_marker(text: str) -> bool:
        lowered = text.strip().lower()
        return lowered in {"[silêncio]", "silêncio", "[silencio]", "silencio", "[silence]", "silence", "inaudível", "inaudivel"}
