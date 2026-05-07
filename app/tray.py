from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading

from PIL import Image
import pystray


TRAY_ICON_PATH = Path(__file__).resolve().parent / "assets" / "bug_hunter_logo.png"
TRAY_ICON_FOCUS_BOX = (45, 35, 211, 246)


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
            "bugometro",
            self._build_icon(),
            "Bugômetro",
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
        if TRAY_ICON_PATH.exists():
            return _build_tray_icon(Image.open(TRAY_ICON_PATH))
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        return image


def _build_tray_icon(source: Image.Image) -> Image.Image:
    image = source.convert("RGBA")
    crop = image.crop(TRAY_ICON_FOCUS_BOX)
    side = max(crop.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    return square.resize((64, 64), Image.Resampling.LANCZOS)
