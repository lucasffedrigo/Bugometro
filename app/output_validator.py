from __future__ import annotations

import re
import unicodedata


SECTION_PATTERNS: dict[str, list[str]] = {
    "summary": [r"(?mi)^(?:\*\*)?resumo:?(?:\*\*)?\s*$"],
    "current_behavior": [r"(?mi)^(?:\*\*)?comportamento atual:(?:\*\*)?\s*$"],
    "expected_behavior": [r"(?mi)^(?:\*\*)?comportamento esperado:(?:\*\*)?\s*$"],
    "evidence": [r"(?mi)^(?:\*\*)?evidencias?:(?:\*\*)?\s*$"],
    "repro_steps": [
        r"(?mi)^(?:\*\*)?passos para reproducao:(?:\*\*)?\s*$",
        r"(?mi)^(?:\*\*)?passo a passo para reproduzir:(?:\*\*)?\s*$",
        r"(?mi)^(?:\*\*)?passos para reproduzir:(?:\*\*)?\s*$",
    ],
}


def validate_bug_report(text: str) -> list[str]:
    normalized = _normalize(text)
    missing = [
        name
        for name, patterns in SECTION_PATTERNS.items()
        if not any(re.search(pattern, normalized) for pattern in patterns)
    ]
    if not _has_title_line(text):
        missing.append("title")
    if not re.search(r"(?m)^(?:-|\*)\s+\S", normalized):
        missing.append("bullet_points")
    if not re.search(r"(?m)^1(?:\.|\))\s+\S", normalized):
        missing.append("numbered_steps")
    return missing


def validate_bug_report_warnings(text: str) -> list[str]:
    warnings: list[str] = []
    title = extract_title(text)
    if title and not re.search(r"^\[[^\]]+\]", title):
        warnings.append("title_component_brackets")
    return warnings


def extract_title(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]

    for index, line in enumerate(lines):
        normalized = _normalize(line).lower()
        if normalized.startswith("titulo:"):
            inline_title = line.split(":", 1)[1].strip()
            if inline_title:
                return inline_title
            for next_line in lines[index + 1 :]:
                if next_line:
                    return next_line
            return ""
        if normalized.rstrip(":") == "titulo":
            for next_line in lines[index + 1 :]:
                if next_line:
                    return next_line
            return ""

    for line in lines:
        if not line:
            continue
        normalized_line = _normalize(line).lower().rstrip(":")
        if normalized_line in _FORBIDDEN_TITLE_LINES:
            return ""
        if line.startswith("**") or line.endswith(":"):
            return ""
        return line
    return ""


def describe_missing(missing: list[str]) -> str:
    labels = {
        "title": "Título",
        "summary": "Resumo",
        "current_behavior": "Comportamento atual",
        "expected_behavior": "Comportamento esperado",
        "evidence": "Evidências",
        "repro_steps": "Passos para reprodução",
        "bullet_points": "tópicos com marcadores",
        "numbered_steps": "passos numerados",
        "title_component_brackets": "título sem [Plataforma/Componente]",
    }
    return ", ".join(labels.get(item, item) for item in missing)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return normalized


def _has_title_line(text: str) -> bool:
    title = extract_title(text)
    if not title:
        return False
    normalized_title = _normalize(title).lower().rstrip(":")
    return normalized_title not in _FORBIDDEN_TITLE_LINES


_FORBIDDEN_TITLE_LINES = {
    "resumo",
    "comportamento atual",
    "comportamento esperado",
    "passos para reproducao",
    "passo a passo para reproduzir",
    "passos para reproduzir",
    "evidencia",
    "evidencias",
    "nao informado",
    "formato obrigatorio da saida final",
}
