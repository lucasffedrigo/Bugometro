from __future__ import annotations

from app.ui import _bottom_right_bounds


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
