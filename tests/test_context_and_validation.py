from app.context_extractor import ContextExtractor
from app.output_validator import (
    extract_title,
    validate_bug_report,
    validate_bug_report_warnings,
)


def test_context_extractor_detects_environment_browser_and_version() -> None:
    hints = ContextExtractor.extract(
        "No Chrome em produção no Windows 11 versão 2.4.1 o administrador não consegue salvar."
    )

    assert "produção" in hints.environments
    assert "Chrome" in hints.browsers
    assert "Windows" in hints.operating_systems
    assert "2.4.1" in hints.versions
    assert "administrador" in hints.user_profiles


def test_output_validator_detects_missing_sections_and_title() -> None:
    invalid = "Resumo:\nTeste"

    missing = validate_bug_report(invalid)

    assert "title" in missing
    assert "current_behavior" in missing
    assert extract_title("Título:\nMeu título\n\nResumo:\nX") == "Meu título"


def test_output_validator_accepts_title_with_component_brackets() -> None:
    valid = (
        "[Login Web] usuário envia credenciais válidas e sistema retorna erro genérico\n\n"
        "Resumo:\nResumo curto.\n\n"
        "**Comportamento atual:**\n"
        "- O sistema falha ao salvar.\n\n"
        "**Comportamento esperado:**\n"
        "- O sistema deve salvar com sucesso.\n\n"
        "**Passos para reprodução:**\n"
        "1. Abrir o formulário.\n"
        "2. Preencher os campos.\n"
        "3. Salvar.\n\n"
        "**Evidências:**\nNão informado\n"
    )

    assert validate_bug_report(valid) == []
    assert validate_bug_report_warnings(valid) == []


def test_title_without_component_brackets_is_warning_only() -> None:
    valid_without_brackets = (
        "Login Web usuário envia credenciais válidas e sistema retorna erro genérico\n\n"
        "Resumo:\nResumo curto.\n\n"
        "**Comportamento atual:**\n"
        "- O sistema falha ao salvar.\n\n"
        "**Comportamento esperado:**\n"
        "- O sistema deve salvar com sucesso.\n\n"
        "**Passos para reprodução:**\n"
        "1. Abrir o formulário.\n"
        "2. Preencher os campos.\n"
        "3. Salvar.\n\n"
        "**Evidências:**\nNão informado\n"
    )

    assert validate_bug_report(valid_without_brackets) == []
    assert validate_bug_report_warnings(valid_without_brackets) == [
        "title_component_brackets"
    ]


def test_extract_title_reads_first_plain_line() -> None:
    text = (
        "[Checkout] usuário finaliza compra e sistema retorna erro de pagamento\n\n"
        "Resumo:\nFalha ao finalizar pedido."
    )

    assert extract_title(text) == (
        "[Checkout] usuário finaliza compra e sistema retorna erro de pagamento"
    )


def test_extract_title_removes_inline_title_label() -> None:
    text = "Titulo: [Checkout] Botao finalizar nao responde\n\nResumo:\nFalha."

    assert extract_title(text) == "[Checkout] Botao finalizar nao responde"
