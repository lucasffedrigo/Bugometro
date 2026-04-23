from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ContextHints:
    environments: list[str]
    browsers: list[str]
    operating_systems: list[str]
    devices: list[str]
    versions: list[str]
    user_profiles: list[str]

    def to_prompt_block(self) -> str:
        lines: list[str] = []
        if self.environments:
            lines.append("Ambiente detectado: " + ", ".join(self.environments))
        if self.browsers:
            lines.append("Navegador detectado: " + ", ".join(self.browsers))
        if self.operating_systems:
            lines.append("Sistema operacional detectado: " + ", ".join(self.operating_systems))
        if self.devices:
            lines.append("Dispositivo detectado: " + ", ".join(self.devices))
        if self.versions:
            lines.append("Versões detectadas: " + ", ".join(self.versions))
        if self.user_profiles:
            lines.append("Perfil de usuário citado: " + ", ".join(self.user_profiles))
        return "\n".join(lines)


class ContextExtractor:
    ENV_PATTERNS = {
        "produção": r"\bprodu[cç][aã]o\b|\bproducao\b|\bprod\b",
        "homologação": r"\bhomologa[cç][aã]o\b|\bhomologacao\b",
        "staging": r"\bstaging\b",
        "desenvolvimento": r"\bdev(elopment)?\b|\bdesenvolvimento\b",
    }
    BROWSER_PATTERNS = {
        "Chrome": r"\bchrome\b",
        "Edge": r"\bedge\b",
        "Firefox": r"\bfirefox\b",
        "Safari": r"\bsafari\b",
    }
    OS_PATTERNS = {
        "Windows": r"\bwindows\b",
        "macOS": r"\bmac\b|\bmacos\b",
        "Linux": r"\blinux\b",
        "Android": r"\bandroid\b",
        "iOS": r"\bios\b|\biphone\b|\bipad\b",
    }
    DEVICE_PATTERNS = {
        "desktop": r"\bdesktop\b|\bnotebook\b|\bpc\b",
        "mobile": r"\bcelular\b|\bmobile\b|\bsmartphone\b",
        "tablet": r"\btablet\b|\bipad\b",
    }
    USER_PATTERNS = {
        "administrador": r"\badministrador\b",
        "cliente": r"\bcliente\b",
        "operador": r"\boperador\b",
        "analista": r"\banalista\b",
        "usuário final": r"\busu[aá]rio final\b",
    }
    VERSION_PATTERN = re.compile(r"\b(v?\d+(?:\.\d+){1,3})\b", re.IGNORECASE)

    @classmethod
    def extract(cls, text: str) -> ContextHints:
        lowered = text.lower()
        return ContextHints(
            environments=cls._match_map(lowered, cls.ENV_PATTERNS),
            browsers=cls._match_map(lowered, cls.BROWSER_PATTERNS),
            operating_systems=cls._match_map(lowered, cls.OS_PATTERNS),
            devices=cls._match_map(lowered, cls.DEVICE_PATTERNS),
            versions=cls._match_versions(text),
            user_profiles=cls._match_map(lowered, cls.USER_PATTERNS),
        )

    @staticmethod
    def _match_map(text: str, patterns: dict[str, str]) -> list[str]:
        return [label for label, pattern in patterns.items() if re.search(pattern, text)]

    @classmethod
    def _match_versions(cls, text: str) -> list[str]:
        found = [match.group(1) for match in cls.VERSION_PATTERN.finditer(text)]
        seen: set[str] = set()
        ordered: list[str] = []
        for value in found:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered
