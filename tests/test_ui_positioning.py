from __future__ import annotations

from types import SimpleNamespace

from app.main import BugVoiceReporterApp
from app.ui import (
    _bottom_right_bounds,
    _hud_body_columns,
    _hud_body_pixel_width,
    _hud_label_color_for_index,
    _hud_title_columns,
    _split_hud_label_line,
    _wrap_hud_body_lines,
    _group_hud_hotkey_tokens,
)


def test_bottom_right_bounds_uses_real_work_area_coordinates() -> None:
    assert _bottom_right_bounds(
        320,
        120,
        work_area=(0, 0, 1920, 1020),
        margin=20,
    ) == (1580, 880, 320, 120)


def test_bottom_right_bounds_keeps_hud_inside_small_work_area() -> None:
    assert _bottom_right_bounds(
        480,
        240,
        work_area=(100, 50, 500, 250),
        margin=20,
    ) == (120, 70, 480, 240)


def test_startup_hud_lines_stay_short_enough_to_avoid_wrapping() -> None:
    app = BugVoiceReporterApp.__new__(BugVoiceReporterApp)
    app.config = SimpleNamespace(
        save_last_output=False,
        hotkey="ctrl+shift",
        restart_hotkey="",
        screen_capture_hotkey="ctrl+shift+space",
        voice_gif_hotkey="ctrl+shift+alt",
        title_paste_hotkey="ctrl+'",
        video_attach_hotkey="ctrl+shift+v",
    )

    lines = app._startup_hud_message().splitlines()

    assert max(len(line) for line in lines) <= 44
    assert "SETA NO GIF = SCROLL + ARRASTAR" in lines
    assert "COLAR TITULO = CTRL + \"" in lines
    assert "COLAR TEXTO = CTRL + V" in lines
    assert "GRAVAR TELA" not in lines
    assert "GIF DURANTE VOZ" not in lines


def test_hud_label_split_includes_equals_in_colored_label() -> None:
    assert _split_hud_label_line("TELA + VOZ = CTRL + SHIFT + SPACE") == (
        "TELA + VOZ =",
        " CTRL + SHIFT + SPACE",
    )
    assert _split_hud_label_line("BUGÔMETRO") == (
        "",
        "BUGÔMETRO",
    )


def test_hud_label_colors_are_distinct_for_startup_actions() -> None:
    colors = [_hud_label_color_for_index(index) for index in range(7)]

    assert colors == [
        "#facc15",
        "#22d3ee",
        "#a78bfa",
        "#34d399",
        "#fb7185",
        "#60a5fa",
        "#f97316",
    ]
    assert len(set(colors)) == 7


def test_hud_body_columns_scale_with_selected_screen_width() -> None:
    lines = [
        "BUGÔMETRO",
        "TELA + VOZ = CTRL + SHIFT + SPACE",
        "SETA NO GIF = SCROLL + ARRASTAR",
    ]

    wide = _hud_body_columns(lines, work_area=(0, 0, 1920, 1080))
    narrow = _hud_body_columns(lines, work_area=(0, 0, 1024, 768))

    assert wide == 35
    assert narrow == 31
    assert narrow < wide
    assert _hud_body_pixel_width(narrow) == 248


def test_hud_title_width_does_not_follow_body_width() -> None:
    assert _hud_title_columns("BUGÔMETRO") == 18
    assert _hud_title_columns("UM TITULO GRANDE DEMAIS PARA O HUD") == 22


def test_hud_body_columns_are_compact_for_non_shortcut_messages() -> None:
    lines = ["continue narrando e finalize a captura com CTRL + SHIFT + SPACE"]

    assert _hud_body_columns(
        lines,
        work_area=(0, 0, 1920, 1080),
        compact=True,
    ) == 30


def test_hud_body_lines_wrap_long_status_messages() -> None:
    assert _wrap_hud_body_lines(
        ["continue narrando e finalize a captura com CTRL + SHIFT + SPACE"],
        columns=30,
    ) == [
        "continue narrando e finalize",
        "a captura com",
        "CTRL + SHIFT + SPACE",
    ]


def test_hud_wrap_keeps_hotkey_sequence_together() -> None:
    assert _group_hud_hotkey_tokens(
        "continue com CTRL + SHIFT + SPACE".split()
    ) == [
        "continue",
        "com",
        "CTRL + SHIFT + SPACE",
    ]
