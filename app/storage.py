from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


class LocalStorage:
    def __init__(
        self,
        last_output_path: Path,
        last_transcription_path: Path,
        debug_save_transcription: bool = False,
        save_last_output: bool = False,
    ) -> None:
        self.last_output_path = last_output_path
        self.last_transcription_path = last_transcription_path
        self.debug_save_transcription = debug_save_transcription
        self.persist_last_output = save_last_output

    def create_temp_wav_path(self) -> Path:
        handle = tempfile.NamedTemporaryFile(
            delete=False,
            prefix="bugometro_",
            suffix=".wav",
        )
        path = Path(handle.name)
        handle.close()
        return path

    def create_temp_evidence_dir(self) -> Path:
        directory = tempfile.mkdtemp(prefix="bugometro_capture_")
        return Path(directory)

    def save_last_output(self, content: str) -> Path | None:
        if not self.persist_last_output:
            return None
        self.last_output_path.write_text(content, encoding="utf-8")
        return self.last_output_path

    def save_last_transcription(self, content: str) -> Path | None:
        if not self.debug_save_transcription:
            return None
        self.last_transcription_path.write_text(content, encoding="utf-8")
        return self.last_transcription_path

    def cleanup_file(self, file_path: Path | None) -> None:
        if file_path and file_path.exists():
            try:
                file_path.unlink(missing_ok=True)
            except PermissionError:
                return
            except OSError:
                return

    def cleanup_sensitive_outputs(self) -> None:
        if not self.persist_last_output:
            self.cleanup_file(self.last_output_path)
        if not self.debug_save_transcription:
            self.cleanup_file(self.last_transcription_path)

    def cleanup_stale_temp_audio(self, max_age_hours: float = 12) -> None:
        temp_dir = Path(tempfile.gettempdir())
        cutoff = time.time() - (max_age_hours * 3600)
        for path in temp_dir.glob("bugometro_*.wav"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def cleanup_stale_temp_evidence(self, max_age_hours: float = 24) -> None:
        temp_dir = Path(tempfile.gettempdir())
        cutoff = time.time() - (max_age_hours * 3600)
        for path in temp_dir.glob("bugometro_capture_*"):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                if path.is_dir():
                    for nested in sorted(path.rglob("*"), reverse=True):
                        if nested.is_file():
                            nested.unlink(missing_ok=True)
                        elif nested.is_dir():
                            nested.rmdir()
                    path.rmdir()
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    @staticmethod
    def open_file(file_path: Path) -> bool:
        if not file_path.exists():
            return False
        try:
            os.startfile(file_path)  # type: ignore[attr-defined]
            return True
        except Exception:
            return False
