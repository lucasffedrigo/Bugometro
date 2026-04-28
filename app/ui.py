from __future__ import annotations

import ctypes
import queue
import threading
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass

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

            panel = tk.Frame(shell, bg="#020617", padx=16, pady=10)
            panel.pack()

            title_label = tk.Label(
                panel,
                text="",
                bg="#020617",
                fg="#67e8f9",
                font=("Consolas", 16, "bold"),
                justify="left",
                anchor="w",
                width=24,
            )
            title_label.pack(fill="x")

            divider = tk.Canvas(
                panel,
                width=270,
                height=6,
                bg="#020617",
                highlightthickness=0,
                bd=0,
            )
            divider.pack(fill="x", pady=(4, 5))

            body_label = tk.Label(
                panel,
                text="",
                bg="#020617",
                fg="#dbeafe",
                font=("Consolas", 10, "bold"),
                justify="left",
                anchor="w",
                wraplength=300,
            )

            footer_label = tk.Label(
                panel,
                text="",
                bg="#020617",
                fg="#64748b",
                font=("Consolas", 8, "normal"),
                justify="left",
                anchor="w",
                wraplength=300,
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
                width = max(divider.winfo_width(), 270)
                y = 3
                divider.create_line(16, y, width - 16, y, fill=accent, width=2)
                divider.create_rectangle(13, y - 2, 17, y + 2, fill=accent, outline=accent)
                divider.create_rectangle(width - 17, y - 2, width - 13, y + 2, fill=accent, outline=accent)

            def update_optional_labels(body: str, footer: str) -> None:
                if body:
                    body_label.configure(text=body)
                    if not body_label.winfo_ismapped():
                        body_label.pack(fill="x")
                else:
                    body_label.pack_forget()

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
                left, top, right, bottom = _work_area_rect()
                x = right - width - 20
                y = bottom - height - 20
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

                        window.configure(bg=style.background)
                        shell.configure(bg=style.accent)
                        panel.configure(bg=style.background)
                        title_label.configure(
                            text=title,
                            bg=style.background,
                            fg=style.accent,
                            font=("Consolas", style.title_size, "bold"),
                        )
                        body_label.configure(
                            bg=style.background,
                            fg=style.foreground,
                            font=("Consolas", style.body_size, "bold"),
                        )
                        footer_label.configure(
                            bg=style.background,
                            fg=style.muted,
                            font=("Consolas", max(10, style.body_size - 2), "normal"),
                        )
                        update_optional_labels(body, footer)
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
