from __future__ import annotations

from pathlib import Path

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

    def render_prompt(self, transcription: str) -> str:
        if not transcription or not transcription.strip():
            raise FormattingError("A transcrição está vazia; não há conteúdo para formatar.")
        template = self.load_prompt_template()
        prompt = template.replace("{{TRANSCRICAO}}", transcription.strip())
        context_block = ContextExtractor.extract(transcription).to_prompt_block()
        if context_block:
            prompt += (
                "\n\nContexto técnico detectado automaticamente. "
                "Use apenas se estiver consistente com o relato:\n"
                f"{context_block}"
            )
        return prompt

    def format_bug_report(self, transcription: str) -> str:
        prompt = self.render_prompt(transcription)
        content = self._generate_text(prompt, "a formatação do bug report")
        missing = validate_bug_report(content)
        warnings = validate_bug_report_warnings(content)
        if missing or warnings:
            repair_prompt = (
                "Corrija o bug report abaixo para obedecer ao formato desejado. "
                "Não invente fatos, mantenha o conteúdo técnico e retorne apenas o texto final. "
                "Se possível, garanta que a primeira linha do título comece com "
                "[Plataforma/Componente]. A ausência desses colchetes não deve remover "
                "conteúdo nem inventar dados.\n\n"
                f"Bug report atual:\n{content}\n\n"
                f"Pendências impeditivas: {', '.join(missing) or 'nenhuma'}\n"
                f"Ajustes desejados: {', '.join(warnings) or 'nenhum'}"
            )
            content = self._generate_text(repair_prompt, "o reparo do bug report")
        missing = validate_bug_report(content)
        if missing:
            raise FormattingError(
                "A resposta formatada não seguiu o template obrigatório do bug report. "
                f"Pendências: {describe_missing(missing)}."
            )
        return content.strip()

    def format_detailed_bug_report(self, transcription: str, base_report: str) -> str:
        prompt = (
            "Crie uma variante mais detalhada do bug report abaixo para uso interno de QA. "
            "Mantenha o conteúdo fiel ao relato, não invente fatos e escreva em português do Brasil.\n\n"
            f"Relato bruto:\n{transcription}\n\n"
            f"Bug report base:\n{base_report}\n\n"
            "Formato desejado:\n"
            "Título:\n[texto]\n\n"
            "Resumo técnico:\n[texto]\n\n"
            "Contexto identificado:\n- [texto]\n\n"
            "Comportamento atual:\n- [texto]\n\n"
            "Comportamento esperado:\n- [texto]\n\n"
            "Riscos e impacto:\n- [texto]\n\n"
            "Lacunas de evidência:\n- [texto]\n\n"
            "Passos para reprodução:\n1. [texto]\n2. [texto]\n3. [texto]\n"
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
