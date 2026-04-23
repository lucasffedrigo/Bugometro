from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass


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
            window.attributes("-topmost", True)
            window.attributes("-alpha", 0.97)

            shell = tk.Frame(window, bg="#22d3ee", padx=1, pady=1)
            shell.pack()

            panel = tk.Frame(shell, bg="#020617", padx=16, pady=8)
            panel.pack()

            title_label = tk.Label(
                panel,
                text="",
                bg="#020617",
                fg="#67e8f9",
                font=("Consolas", 16, "bold"),
                justify="center",
                anchor="center",
                width=22,
            )
            title_label.pack(fill="x")

            divider = tk.Canvas(
                panel,
                width=208,
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
                justify="center",
                anchor="center",
                wraplength=285,
            )

            footer_label = tk.Label(
                panel,
                text="",
                bg="#020617",
                fg="#64748b",
                font=("Consolas", 8, "normal"),
                justify="center",
                anchor="center",
                wraplength=285,
            )

            hide_job: str | None = None

            def style_for(kind: str) -> HudStyle:
                if kind == "success":
                    return HudStyle("#03150a", "#bbf7d0", "#22c55e", "#4ade80", 14)
                if kind == "error":
                    return HudStyle("#170606", "#fecaca", "#ef4444", "#fca5a5", 13)
                if kind == "countdown":
                    return HudStyle("#160b03", "#fed7aa", "#fb923c", "#fdba74", 12)
                if kind == "recording":
                    return HudStyle("#03150a", "#bbf7d0", "#22c55e", "#4ade80", 11)
                if kind == "processing":
                    return HudStyle("#020617", "#bae6fd", "#38bdf8", "#7dd3fc", 12)
                if kind == "hud":
                    return HudStyle("#020617", "#dbeafe", "#22d3ee", "#64748b", 14)
                return HudStyle("#0f172a", "#e5e7eb", "#64748b", "#94a3b8", 11)

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
                width = max(divider.winfo_width(), 208)
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

            def position_window() -> None:
                window.update_idletasks()
                screen_width = window.winfo_screenwidth()
                screen_height = window.winfo_screenheight()
                width = window.winfo_reqwidth()
                height = window.winfo_reqheight()
                x = screen_width - width - 28
                y = screen_height - height - 92
                window.geometry(f"+{x}+{y}")

            def hide_window() -> None:
                nonlocal hide_job
                hide_job = None
                window.withdraw()

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
                        )
                        footer_label.configure(
                            bg=style.background,
                            fg=style.muted,
                        )
                        update_optional_labels(body, footer)
                        draw_divider(style.accent, style.background)
                        position_window()
                        window.deiconify()

                        if not message.persistent:
                            hide_job = root.after(message.duration_ms, hide_window)
                except queue.Empty:
                    pass
                root.after(100, process_queue)

            root.after(100, process_queue)
            root.mainloop()
        except Exception:
            return
