from __future__ import annotations

from collections.abc import Callable
import threading

from PIL import Image, ImageDraw
import pystray


class SystemTrayController:
    def __init__(
        self,
        status_provider: Callable[[], str],
        can_open_last_output: Callable[[], bool],
        on_open_last_output: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self.status_provider = status_provider
        self.can_open_last_output = can_open_last_output
        self.on_open_last_output = on_open_last_output
        self.on_quit = on_quit
        self.icon: pystray.Icon | None = None

    def start(self) -> None:
        if self.icon is not None:
            return
        self.icon = pystray.Icon(
            "bug-voice-reporter",
            self._build_icon(),
            "bug-voice-reporter",
            menu=pystray.Menu(
                pystray.MenuItem(
                    lambda item: f"Status: {self.status_provider()}",
                    None,
                    enabled=False,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Abrir ultimo bug report",
                    self._wrap(self.on_open_last_output),
                    enabled=lambda item: self.can_open_last_output(),
                    default=True,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Sair", self._wrap(self.on_quit)),
            ),
        )

    def run(self) -> None:
        if self.icon is None:
            self.start()
        assert self.icon is not None
        self.icon.run()

    def stop(self) -> None:
        if self.icon is None:
            return
        icon = self.icon
        try:
            icon.visible = False
            icon.update_menu()
        except Exception:
            pass
        try:
            icon.stop()
        except Exception:
            pass
        self.icon = None

    def refresh(self) -> None:
        if self.icon is not None:
            self.icon.update_menu()

    @staticmethod
    def _wrap(callback: Callable[[], None]) -> Callable[[pystray.Icon, pystray.MenuItem], None]:
        def runner(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            del icon, item
            threading.Thread(
                target=callback,
                name="tray-callback",
                daemon=True,
            ).start()

        return runner

    @staticmethod
    def _build_icon() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((10, 10, 54, 54), radius=14, fill=(15, 23, 42, 255))
        draw.rounded_rectangle((18, 16, 46, 48), radius=10, fill=(238, 242, 255, 255))
        draw.ellipse((24, 12, 40, 28), fill=(14, 165, 233, 255))
        draw.rectangle((30, 26, 34, 40), fill=(14, 165, 233, 255))
        draw.ellipse((21, 31, 28, 38), fill=(239, 68, 68, 255))
        draw.ellipse((36, 31, 43, 38), fill=(239, 68, 68, 255))
        draw.rectangle((24, 40, 40, 43), fill=(239, 68, 68, 255))
        return image
