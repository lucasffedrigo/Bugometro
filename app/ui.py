from __future__ import annotations

import ctypes
import queue
import threading
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass


_HUD_LABEL_COLORS = (
    "#facc15",  # amber
    "#22d3ee",  # cyan
    "#a78bfa",  # violet
    "#34d399",  # emerald
    "#fb7185",  # rose
    "#60a5fa",  # blue
    "#f97316",  # orange
)
_HUD_MIN_COLUMNS = 30
_HUD_MAX_COLUMNS = 48
_HUD_SCREEN_WIDTH_RATIO = 0.28
_HUD_CHAR_PIXEL_WIDTH = 8
_HUD_HORIZONTAL_CHROME_PX = 34


def _enable_process_dpi_awareness() -> None:
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_dpi_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if set_dpi_context is not None:
            set_dpi_context.argtypes = [ctypes.c_void_p]
            set_dpi_context.restype = wintypes.BOOL
            per_monitor_v2 = ctypes.c_void_p(-4)
            if set_dpi_context(per_monitor_v2):
                return
    except Exception:
        pass

    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(2) == 0:
            return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_process_dpi_awareness()

_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_USER32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
_USER32.GetAncestor.restype = wintypes.HWND
_USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_USER32.GetCursorPos.restype = wintypes.BOOL
_USER32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
_USER32.GetMonitorInfoW.restype = wintypes.BOOL
_USER32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_USER32.GetWindowLongW.restype = ctypes.c_long
_USER32.IsWindowVisible.argtypes = [wintypes.HWND]
_USER32.IsWindowVisible.restype = wintypes.BOOL
_USER32.MonitorFromPoint.argtypes = [wintypes.POINT, ctypes.c_uint]
_USER32.MonitorFromPoint.restype = wintypes.HMONITOR
_USER32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND,
    wintypes.COLORREF,
    wintypes.BYTE,
    wintypes.DWORD,
]
_USER32.SetLayeredWindowAttributes.restype = wintypes.BOOL
_USER32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_USER32.SetWindowLongW.restype = ctypes.c_long
_USER32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
_USER32.SetWindowPos.restype = wintypes.BOOL
_USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_USER32.ShowWindow.restype = wintypes.BOOL
_USER32.SystemParametersInfoW.argtypes = [
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_uint,
]
_USER32.SystemParametersInfoW.restype = wintypes.BOOL


@dataclass
class UiMessage:
    text: str
    persistent: bool = False
    duration_ms: int = 2500
    kind: str = "normal"


@dataclass(frozen=True)
class HudStyle:
    background: str
    foreground: str
    accent: str
    muted: str
    title_size: int
    body_size: int


class StatusNotifier:
    def __init__(self) -> None:
        self._queue: queue.Queue[UiMessage | None] = queue.Queue()
        self._started = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._run, name="ui-thread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._started = False

    def show_status(
        self,
        text: str,
        persistent: bool = False,
        duration_ms: int = 2500,
        kind: str = "normal",
    ) -> None:
        if not self._started:
            return
        self._queue.put(
            UiMessage(
                text=text,
                persistent=persistent,
                duration_ms=duration_ms,
                kind=kind,
            )
        )

    def hide(self) -> None:
        self.show_status("", persistent=False, duration_ms=1)

    def _run(self) -> None:
        try:
            root = tk.Tk()
            root.withdraw()
            window = tk.Toplevel(root)
            window.withdraw()
            window.overrideredirect(True)
            window.geometry("10x10+0+0")

            shell = tk.Frame(window, bg="#22d3ee", padx=1, pady=1)
            shell.pack()

            panel = tk.Frame(shell, bg="#020617", padx=14, pady=10)
            panel.pack()

            title_label = tk.Label(
                panel,
                text="",
                bg="#020617",
                fg="#67e8f9",
                font=("Consolas", 16, "bold"),
                justify="center",
                anchor="center",
                width=0,
            )
            title_label.pack(fill="x")

            divider = tk.Canvas(
                panel,
                width=260,
                height=6,
                bg="#020617",
                highlightthickness=0,
                bd=0,
            )
            divider.pack(fill="x", pady=(4, 5))

            body_text = tk.Text(
                panel,
                bg="#020617",
                fg="#dbeafe",
                font=("Consolas", 10, "bold"),
                width=34,
                height=1,
                wrap="none",
                borderwidth=0,
                highlightthickness=0,
                padx=6,
                pady=2,
                cursor="arrow",
                takefocus=0,
            )
            body_text.configure(state="disabled")

            footer_label = tk.Label(
                panel,
                text="",
                bg="#020617",
                fg="#64748b",
                font=("Consolas", 8, "normal"),
                justify="left",
                anchor="w",
                wraplength=260,
            )

            hide_job: str | None = None
            visible = False
            hwnd: int | None = None

            def style_for(kind: str) -> HudStyle:
                if kind == "success":
                    return HudStyle("#03150a", "#bbf7d0", "#22c55e", "#4ade80", 16, 10)
                if kind == "error":
                    return HudStyle("#170606", "#fecaca", "#ef4444", "#fca5a5", 15, 10)
                if kind == "countdown":
                    return HudStyle("#160b03", "#fed7aa", "#fb923c", "#fdba74", 14, 10)
                if kind == "recording":
                    return HudStyle("#03150a", "#bbf7d0", "#22c55e", "#4ade80", 14, 10)
                if kind == "processing":
                    return HudStyle("#020617", "#bae6fd", "#38bdf8", "#7dd3fc", 14, 10)
                if kind == "hud":
                    return HudStyle("#020617", "#dbeafe", "#22d3ee", "#64748b", 16, 10)
                return HudStyle("#0f172a", "#e5e7eb", "#64748b", "#94a3b8", 13, 10)

            def split_message(text: str) -> tuple[str, str, str]:
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                if not lines:
                    return "", "", ""
                title = lines[0]
                footer = ""
                body_lines = lines[1:]
                if len(lines) >= 3 and lines[-1].lower().startswith(("modo ", "histórico ")):
                    footer = lines[-1]
                    body_lines = lines[1:-1]
                return title, "\n".join(body_lines), footer

            def draw_divider(accent: str, bg: str) -> None:
                divider.configure(bg=bg)
                divider.delete("all")
                width = max(divider.winfo_width(), 220)
                y = 3
                divider.create_line(16, y, width - 16, y, fill=accent, width=2)
                divider.create_rectangle(13, y - 2, 17, y + 2, fill=accent, outline=accent)
                divider.create_rectangle(width - 17, y - 2, width - 13, y + 2, fill=accent, outline=accent)

            def apply_responsive_width(
                title: str,
                lines: list[str],
                *,
                shortcut_layout: bool,
            ) -> int:
                columns = _hud_body_columns(
                    lines,
                    work_area=_work_area_rect(),
                    compact=not shortcut_layout,
                )
                pixel_width = _hud_body_pixel_width(columns)
                title_label.configure(width=_hud_title_columns(title))
                divider.configure(width=pixel_width)
                body_text.configure(width=columns)
                footer_label.configure(wraplength=pixel_width)
                return columns

            def update_body_text(
                body: str,
                style: HudStyle,
                *,
                shortcut_layout: bool,
                columns: int,
            ) -> None:
                body_lines = body.splitlines()
                display_lines = body_lines if shortcut_layout else _wrap_hud_body_lines(
                    body_lines,
                    columns=columns,
                )
                body_text.configure(
                    state="normal",
                    bg=style.background,
                    fg=style.foreground,
                    font=("Consolas", style.body_size, "bold"),
                    wrap="none",
                    height=max(1, len(display_lines)),
                )
                body_text.delete("1.0", "end")
                body_text.tag_configure("hud_body", foreground=style.foreground)
                for index, line in enumerate(display_lines):
                    label, value = _split_hud_label_line(line)
                    if label:
                        label_tag = f"hud_label_{index}"
                        body_text.tag_configure(
                            label_tag,
                            foreground=_hud_label_color_for_index(index),
                        )
                        body_text.insert("end", label, (label_tag,))
                        body_text.insert("end", value, ("hud_body",))
                    else:
                        body_text.insert("end", line, ("hud_body",))
                    if index < len(display_lines) - 1:
                        body_text.insert("end", "\n", ("hud_body",))
                body_text.configure(state="disabled")

            def update_optional_labels(
                body: str,
                footer: str,
                style: HudStyle,
                *,
                shortcut_layout: bool,
                columns: int,
            ) -> None:
                if body:
                    update_body_text(
                        body,
                        style,
                        shortcut_layout=shortcut_layout,
                        columns=columns,
                    )
                    if not body_text.winfo_ismapped():
                        body_text.pack(fill="x")
                else:
                    body_text.pack_forget()

                if footer:
                    footer_label.configure(text=footer)
                    if not footer_label.winfo_ismapped():
                        footer_label.pack(fill="x", pady=(5, 0))
                else:
                    footer_label.pack_forget()

            def position_window(*, show: bool = False) -> None:
                window.update_idletasks()
                width = window.winfo_reqwidth()
                height = window.winfo_reqheight()
                x, y, width, height = _bottom_right_bounds(width, height)
                if hwnd is None:
                    window.geometry(f"{width}x{height}+{x}+{y}")
                    if show:
                        window.deiconify()
                    return
                _set_window_bounds(hwnd, x, y, width, height, show=show or visible)

            def hide_window() -> None:
                nonlocal hide_job, visible
                hide_job = None
                visible = False
                if hwnd is not None:
                    _set_window_alpha(hwnd, 0)
                    _hide_native_window(hwnd)

            def show_window() -> None:
                nonlocal visible
                if hwnd is None:
                    position_window(show=True)
                    visible = True
                    return
                if visible:
                    position_window()
                    return
                _set_window_alpha(hwnd, 0)
                position_window(show=True)
                visible = True
                root.after_idle(lambda: _set_window_alpha(hwnd, 247))

            def process_queue() -> None:
                nonlocal hide_job
                try:
                    while True:
                        message = self._queue.get_nowait()
                        if message is None:
                            root.quit()
                            return

                        if hide_job is not None:
                            root.after_cancel(hide_job)
                            hide_job = None

                        if not message.text:
                            hide_window()
                            continue

                        style = style_for(message.kind)
                        title, body, footer = split_message(message.text)
                        shortcut_layout = message.kind == "hud"
                        columns = apply_responsive_width(
                            title,
                            [*body.splitlines(), footer],
                            shortcut_layout=shortcut_layout,
                        )

                        window.configure(bg=style.background)
                        shell.configure(bg=style.accent)
                        panel.configure(bg=style.background)
                        title_label.configure(
                            text=title,
                            bg=style.background,
                            fg=style.accent,
                            font=("Consolas", style.title_size, "bold"),
                        )
                        footer_label.configure(
                            bg=style.background,
                            fg=style.muted,
                            font=("Consolas", max(10, style.body_size - 2), "normal"),
                        )
                        update_optional_labels(
                            body,
                            footer,
                            style,
                            shortcut_layout=shortcut_layout,
                            columns=columns,
                        )
                        draw_divider(style.accent, style.background)
                        show_window()

                        if not message.persistent:
                            hide_job = root.after(message.duration_ms, hide_window)
                except queue.Empty:
                    pass
                root.after(100, process_queue)

            window.update_idletasks()
            hwnd = _resolve_top_level_hwnd(window.winfo_id())
            _configure_native_hud_window(hwnd)
            _set_window_alpha(hwnd, 0)
            _hide_native_window(hwnd)
            root.after(100, process_queue)
            root.mainloop()
        except Exception:
            return


def _work_area_rect() -> tuple[int, int, int, int]:
    try:
        cursor = wintypes.POINT()
        MONITOR_DEFAULTTONEAREST = 2
        if _USER32.GetCursorPos(ctypes.byref(cursor)):
            monitor = _USER32.MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST)
            if monitor:
                monitor_info = _MonitorInfo()
                monitor_info.cbSize = ctypes.sizeof(_MonitorInfo)
                if _USER32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
                    work = monitor_info.rcWork
                    return int(work.left), int(work.top), int(work.right), int(work.bottom)
    except Exception:
        pass
    try:
        rect = wintypes.RECT()
        SPI_GETWORKAREA = 0x0030
        if _USER32.SystemParametersInfoW(
            SPI_GETWORKAREA,
            0,
            ctypes.byref(rect),
            0,
        ):
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        pass
    return 0, 0, 1920, 1080


def _bottom_right_bounds(
    width: int,
    height: int,
    *,
    work_area: tuple[int, int, int, int] | None = None,
    margin: int = 20,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = work_area or _work_area_rect()
    safe_width = max(1, int(width))
    safe_height = max(1, int(height))
    safe_margin = max(0, int(margin))
    x = max(left + safe_margin, right - safe_width - safe_margin)
    y = max(top + safe_margin, bottom - safe_height - safe_margin)
    return x, y, safe_width, safe_height


def _split_hud_label_line(line: str) -> tuple[str, str]:
    if "=" not in line:
        return "", line
    label, value = line.split("=", 1)
    return f"{label.rstrip()} =", f" {value.lstrip()}"


def _hud_label_color_for_index(index: int) -> str:
    return _HUD_LABEL_COLORS[index % len(_HUD_LABEL_COLORS)]


def _hud_body_columns(
    lines: list[str],
    *,
    work_area: tuple[int, int, int, int],
    compact: bool = False,
) -> int:
    left, _top, right, _bottom = work_area
    screen_width = max(1, right - left)
    proportional_pixels = int(screen_width * _HUD_SCREEN_WIDTH_RATIO)
    available_columns = max(
        _HUD_MIN_COLUMNS,
        min(
            _HUD_MAX_COLUMNS,
            (proportional_pixels - _HUD_HORIZONTAL_CHROME_PX)
            // _HUD_CHAR_PIXEL_WIDTH,
        ),
    )
    if compact:
        desired_columns = _HUD_MIN_COLUMNS
    else:
        longest_line = max((len(line) for line in lines if line), default=_HUD_MIN_COLUMNS)
        desired_columns = max(_HUD_MIN_COLUMNS, longest_line + 2)
    return min(desired_columns, available_columns)


def _hud_body_pixel_width(columns: int) -> int:
    return max(220, int(columns) * _HUD_CHAR_PIXEL_WIDTH)


def _hud_title_columns(title: str) -> int:
    return min(22, max(18, len(title.strip()) + 1))


def _wrap_hud_body_lines(lines: list[str], *, columns: int) -> list[str]:
    safe_columns = max(12, int(columns) - 2)
    wrapped: list[str] = []
    for line in lines:
        words = _group_hud_hotkey_tokens(line.split())
        if not words:
            wrapped.append("")
            continue
        current = words[0]
        for word in words[1:]:
            if len(current) + 1 + len(word) <= safe_columns:
                current = f"{current} {word}"
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)
    return wrapped


def _group_hud_hotkey_tokens(words: list[str]) -> list[str]:
    grouped: list[str] = []
    index = 0
    hotkey_words = {"CTRL", "SHIFT", "ALT", "SPACE", "CAPS"}
    while index < len(words):
        word = words[index]
        if word.upper() not in hotkey_words:
            grouped.append(word)
            index += 1
            continue

        hotkey = [word]
        index += 1
        while (
            index + 1 < len(words)
            and words[index] == "+"
            and words[index + 1].upper() in hotkey_words
        ):
            hotkey.extend([words[index], words[index + 1]])
            index += 2
        grouped.append(" ".join(hotkey))
    return grouped


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _resolve_top_level_hwnd(widget_hwnd: int) -> int:
    GA_ROOT = 2
    resolved = int(_USER32.GetAncestor(widget_hwnd, GA_ROOT))
    return resolved or int(widget_hwnd)


def _configure_native_hud_window(hwnd: int) -> None:
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000

    current_style = _USER32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _USER32.SetWindowLongW(
        hwnd,
        GWL_EXSTYLE,
        current_style
        | WS_EX_LAYERED
        | WS_EX_TRANSPARENT
        | WS_EX_TOOLWINDOW
        | WS_EX_NOACTIVATE,
    )


def _set_window_alpha(hwnd: int, alpha: int) -> None:
    LWA_ALPHA = 0x00000002
    safe_alpha = max(0, min(255, int(alpha)))
    _USER32.SetLayeredWindowAttributes(hwnd, 0, safe_alpha, LWA_ALPHA)


def _set_window_bounds(hwnd: int, x: int, y: int, width: int, height: int, *, show: bool) -> None:
    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_NOACTIVATE = 0x0010
    SWP_NOOWNERZORDER = 0x0200
    SWP_NOSENDCHANGING = 0x0400
    SWP_SHOWWINDOW = 0x0040

    flags = SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING
    if show:
        flags |= SWP_SHOWWINDOW
    _USER32.SetWindowPos(
        hwnd,
        HWND_TOPMOST,
        int(x),
        int(y),
        int(width),
        int(height),
        flags,
    )


def _hide_native_window(hwnd: int) -> None:
    SW_HIDE = 0
    _USER32.ShowWindow(hwnd, SW_HIDE)


def _show_native_window(hwnd: int) -> None:
    SW_SHOWNOACTIVATE = 4
    _USER32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
