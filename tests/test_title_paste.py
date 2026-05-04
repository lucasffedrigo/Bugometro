from types import SimpleNamespace

from app.main import BugVoiceReporterApp


class _StatusStub:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def show_status(self, message: str, **kwargs) -> None:
        del kwargs
        self.messages.append(message)


class _LoggerStub:
    def exception(self, *args, **kwargs) -> None:
        raise AssertionError(args, kwargs)


def test_title_paste_hotkey_writes_title_without_inline_label(monkeypatch) -> None:
    written: dict[str, str] = {}
    app = BugVoiceReporterApp.__new__(BugVoiceReporterApp)
    app.config = SimpleNamespace(title_paste_hotkey="ctrl+'")
    app.status_ui = _StatusStub()
    app.logger = _LoggerStub()
    app._last_formatted_report = (
        "Titulo: [Checkout] Botao finalizar nao responde\n\n"
        "Resumo:\nFalha ao finalizar."
    )

    monkeypatch.setattr(
        app,
        "_write_text_after_hotkey_release",
        lambda hotkey, text: written.update({"hotkey": hotkey, "text": text}),
    )

    app.handle_title_paste_hotkey()

    assert written == {
        "hotkey": "ctrl+'",
        "text": "[Checkout] Botao finalizar nao responde",
    }
    assert app.status_ui.messages[-1].startswith("TITULO COLADO")
