from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Protocol

import requests

from app.context_extractor import ContextExtractor
from app.gemini_utils import extract_text_from_response_json, format_gemini_http_error
from app.openai_utils import (
    extract_text_from_openai_response_json,
    format_openai_http_error,
)
from app.output_validator import (
    describe_missing,
    validate_bug_report,
    validate_bug_report_warnings,
)


class FormattingError(RuntimeError):
    """Raised when the formatter cannot build the final bug report."""


class EvidenceContext(Protocol):
    def to_prompt_block(self) -> str: ...

    def evidence_lines(self) -> list[str]: ...


class Formatter:
    def __init__(
        self,
        api_key: str,
        model: str,
        prompt_path: Path,
        timeout_seconds: float,
        provider: str = "gemini",
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.prompt_path = prompt_path
        self.timeout_seconds = timeout_seconds

    def load_prompt_template(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def render_prompt(
        self,
        transcription: str,
        evidence_context: EvidenceContext | None = None,
    ) -> str:
        if not transcription or not transcription.strip():
            raise FormattingError("A transcricao esta vazia; nao ha conteudo para formatar.")

        template = self.load_prompt_template()
        prompt = template.replace("{{TRANSCRICAO}}", transcription.strip())
        context_block = ContextExtractor.extract(transcription).to_prompt_block()
        if context_block:
            prompt += (
                "\n\nContexto tecnico detectado automaticamente. "
                "Use apenas se estiver consistente com o relato:\n"
                f"{context_block}"
            )
        if evidence_context is not None:
            prompt += (
                "\n\nContexto adicional da captura:\n"
                f"{evidence_context.to_prompt_block()}"
            )
        return prompt

    def format_bug_report(
        self,
        transcription: str,
        evidence_context: EvidenceContext | None = None,
    ) -> str:
        prompt = self.render_prompt(transcription, evidence_context=evidence_context)
        content = self._generate_text(prompt, "a formatacao do bug report")
        missing = validate_bug_report(content)
        warnings = validate_bug_report_warnings(content)

        if missing or warnings:
            repair_prompt = (
                "Corrija o bug report abaixo para obedecer ao formato desejado. "
                "Nao invente fatos, mantenha o conteudo tecnico e retorne apenas o texto final. "
                "Se possivel, garanta que a primeira linha do titulo comece com "
                "[Plataforma/Componente]. A ausencia desses colchetes nao deve remover "
                "conteudo nem inventar dados.\n\n"
                f"Bug report atual:\n{content}\n\n"
                f"Pendencias impeditivas: {', '.join(missing) or 'nenhuma'}\n"
                f"Ajustes desejados: {', '.join(warnings) or 'nenhum'}"
            )
            content = self._generate_text(repair_prompt, "o reparo do bug report")

        content = self._inject_evidence_lines(content, evidence_context)
        missing = validate_bug_report(content)
        if missing:
            raise FormattingError(
                "A resposta formatada nao seguiu o template obrigatorio do bug report. "
                f"Pendencias: {describe_missing(missing)}."
            )
        return content.strip()

    def format_detailed_bug_report(self, transcription: str, base_report: str) -> str:
        prompt = (
            "Crie uma variante mais detalhada do bug report abaixo para uso interno de QA. "
            "Mantenha o conteudo fiel ao relato, nao invente fatos e escreva em portugues do Brasil.\n\n"
            f"Relato bruto:\n{transcription}\n\n"
            f"Bug report base:\n{base_report}\n\n"
            "Formato desejado:\n"
            "Titulo:\n[texto]\n\n"
            "Resumo tecnico:\n[texto]\n\n"
            "Contexto identificado:\n- [texto]\n\n"
            "Comportamento atual:\n- [texto]\n\n"
            "Comportamento esperado:\n- [texto]\n\n"
            "Riscos e impacto:\n- [texto]\n\n"
            "Lacunas de evidencia:\n- [texto]\n\n"
            "Passos para reproducao:\n1. [texto]\n2. [texto]\n3. [texto]\n"
        )
        return self._generate_text(prompt, "a variante detalhada do bug report").strip()

    def _generate_text(self, prompt: str, context: str) -> str:
        if self.provider == "openai":
            return self._generate_text_openai(prompt, context)
        return self._generate_text_gemini(prompt, context)

    def _generate_text_gemini(self, prompt: str, context: str) -> str:
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
                        {
                            "text": prompt,
                        }
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
            raise FormattingError(
                "Falha de rede ao formatar o bug report. Tente novamente."
            ) from exc

        if response.status_code >= 400:
            raise FormattingError(format_gemini_http_error(context, response))

        content = extract_text_from_response_json(response.json())
        if not content.strip():
            raise FormattingError("A resposta formatada veio vazia.")
        return content.strip()

    def _generate_text_openai(self, prompt: str, context: str) -> str:
        url = "https://api.openai.com/v1/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": prompt,
        }

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise FormattingError(
                "Falha de rede ao formatar o bug report com a OpenAI. Tente novamente."
            ) from exc

        if response.status_code >= 400:
            raise FormattingError(format_openai_http_error(context, response))

        content = extract_text_from_openai_response_json(response.json())
        if not content.strip():
            raise FormattingError("A resposta formatada veio vazia.")
        return content.strip()

    @staticmethod
    def extract_output_text(response: object) -> str:
        if isinstance(response, dict):
            return (
                extract_text_from_openai_response_json(response)
                or extract_text_from_response_json(response)
            )

        text = getattr(response, "text", None)
        if text:
            return str(text).strip()

        output_text = getattr(response, "output_text", None)
        if output_text:
            return str(output_text).strip()

        return ""

    @staticmethod
    def _inject_evidence_lines(
        content: str,
        evidence_context: EvidenceContext | None,
    ) -> str:
        if evidence_context is None:
            return content.strip()

        lines = content.strip().splitlines()
        evidence_index: int | None = None
        for index, line in enumerate(lines):
            normalized = Formatter._normalize_evidence_text(line)
            if normalized in {"evidencia:", "evidencias:", "evidncia:", "evidncias:"}:
                evidence_index = index
                break

        if evidence_index is None:
            return content.strip()

        evidence_lines = evidence_context.evidence_lines()
        next_section_index = len(lines)
        for index in range(evidence_index + 1, len(lines)):
            stripped = lines[index].strip()
            normalized = Formatter._normalize_evidence_text(stripped)
            if not stripped:
                continue
            if stripped.startswith("- ") or stripped.startswith("* "):
                continue
            if stripped[:2].isdigit() and stripped[1:2] in {".", ")"}:
                continue
            if normalized.endswith(":"):
                next_section_index = index
                break

        existing_lines = lines[evidence_index + 1 : next_section_index]
        trailing_lines = lines[next_section_index:]
        existing_lines = Formatter._sanitize_evidence_lines(existing_lines)
        existing_block = "\n".join(existing_lines).strip()
        if all(item in existing_block for item in evidence_lines):
            return content.strip()

        additions = [f"- {item}" for item in evidence_lines if item not in existing_block]

        normalized_existing_block = Formatter._normalize_evidence_text(existing_block)
        if not existing_block or normalized_existing_block in {"nao informado", "no informado"}:
            merged_lines = additions
        else:
            merged_lines = existing_lines + additions

        separator = [""] if merged_lines and trailing_lines and trailing_lines[0].strip() else []
        rebuilt = lines[: evidence_index + 1] + merged_lines + separator + trailing_lines
        return "\n".join(rebuilt).strip()

    @staticmethod
    def _normalize_evidence_text(value: str) -> str:
        normalized = value.strip().lower().replace("*", "")
        normalized = unicodedata.normalize("NFD", normalized)
        normalized = "".join(
            char for char in normalized if unicodedata.category(char) != "Mn"
        )
        return normalized.replace("?", "").replace("\ufffd", "")

    @staticmethod
    def _sanitize_evidence_lines(lines: list[str]) -> list[str]:
        blocked_tokens = (
            "appdata\\local\\temp",
            "bug_voice_reporter_capture",
            "pacote local de evid",
            "arquivos locais",
            "video da captura:",
            "captura privada:",
        )
        cleaned: list[str] = []
        for line in lines:
            lowered = line.lower()
            if any(token in lowered for token in blocked_tokens):
                continue
            cleaned.append(line)
        return cleaned
