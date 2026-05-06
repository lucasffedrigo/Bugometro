from __future__ import annotations

from PIL import Image

from app.tray import SystemTrayController, TRAY_ICON_PATH, _build_tray_icon


def test_tray_icon_uses_bug_hunter_logo_asset() -> None:
    icon = SystemTrayController._build_icon()

    assert TRAY_ICON_PATH.exists()
    assert icon.size == (64, 64)
    assert icon.getbbox() is not None


def test_tray_icon_zooms_logo_body_for_small_tray_slot() -> None:
    source = Image.open(TRAY_ICON_PATH)
    full_logo = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    focused_icon = _build_tray_icon(source)

    assert focused_icon.getbbox() is not None
    assert focused_icon.getbbox()[3] - focused_icon.getbbox()[1] > (
        full_logo.getbbox()[3] - full_logo.getbbox()[1]
    )
