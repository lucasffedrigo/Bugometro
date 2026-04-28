from __future__ import annotations

import ctypes
import logging
import math
import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import keyboard
import numpy as np
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class NativeCaptureContext:
    target_kind: str
    target_title: str
    video_path: Path
    frame_paths: tuple[Path, ...] = ()
    duration_seconds: float = 0.0
    capture_fps: int = 0
    capture_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    annotation_count: int = 0

    def to_prompt_block(self) -> str:
        left, top, width, height = self.capture_rect
        lines = [
            "Captura nativa local do Windows vinculada ao relato:",
            f"- Tipo de captura: {self.target_kind}",
            f"- Alvo capturado: {self.target_title or 'Janela ativa do usuario'}",
            f"- Duracao aproximada: {self.duration_seconds:.1f}s",
            f"- Resolucao capturada: {width}x{height} (origem {left},{top})",
            f"- FPS nominal da gravacao: {self.capture_fps}",
        ]
        if self.annotation_count:
            lines.append(
                f"- O usuario destacou {self.annotation_count} anotacao(oes) temporaria(s) durante a gravacao."
            )
        if self.frame_paths:
            lines.append("- Frames de apoio foram salvos temporariamente para contexto local.")
        lines.extend(
            [
                "- O video final esta disponivel localmente para anexo rapido via CTRL+SHIFT+V.",
                "- Use o video apenas como evidencia complementar ao relato por voz.",
                "- Nao invente dados visuais que nao estejam consistentes com o relato.",
            ]
        )
        return "\n".join(lines)

    def evidence_lines(self) -> list[str]:
        return []

    def evidence_file_paths(self) -> list[Path]:
        return [self.video_path, *self.frame_paths]


@dataclass
class NativeScreenRecordingResult:
    video_path: Path | None
    bundle_dir: Path | None
    reason: str
    target_kind: str
    target_title: str
    capture_rect: tuple[int, int, int, int]
    duration_seconds: float
    capture_fps: int
    frame_paths: tuple[Path, ...] = ()
    annotation_count: int = 0
    error_message: str | None = None

    def to_context(self) -> NativeCaptureContext:
        if self.video_path is None:
            raise RuntimeError("Nao ha video nativo disponivel para gerar contexto.")
        return NativeCaptureContext(
            target_kind=self.target_kind,
            target_title=self.target_title,
            video_path=self.video_path,
            frame_paths=self.frame_paths,
            duration_seconds=self.duration_seconds,
            capture_fps=self.capture_fps,
            capture_rect=self.capture_rect,
            annotation_count=self.annotation_count,
        )


@dataclass(frozen=True)
class _Stroke:
    points: tuple[tuple[int, int], ...]
    expires_at: float | None


class AnnotationModel:
    def __init__(self, hold_seconds: float = 1.5) -> None:
        self.hold_seconds = hold_seconds
        self._lock = threading.Lock()
        self._active_points: list[tuple[int, int]] = []
        self._completed: list[_Stroke] = []
        self._completed_count = 0

    def begin(self, x: int, y: int) -> None:
        with self._lock:
            self._active_points = [(x, y), (x, y)]

    def extend(self, x: int, y: int) -> None:
        with self._lock:
            if not self._active_points:
                return
            if self._active_points[-1] == (x, y):
                return
            self._active_points[-1] = (x, y)

    def end(self, now: float | None = None) -> None:
        expires_at = (now or time.monotonic()) + self.hold_seconds
        with self._lock:
            if not self._active_points:
                return
            points = tuple(self._active_points)
            self._active_points = []
            if len(points) < 2 or points[0] == points[-1]:
                return
            self._completed.append(_Stroke(points=points, expires_at=expires_at))
            self._completed_count += 1

    def cancel(self) -> None:
        with self._lock:
            self._active_points = []

    def snapshot(self, now: float | None = None) -> list[tuple[tuple[int, int], ...]]:
        timestamp = now or time.monotonic()
        with self._lock:
            self._completed = [
                stroke
                for stroke in self._completed
                if stroke.expires_at is None or stroke.expires_at >= timestamp
            ]
            lines = [stroke.points for stroke in self._completed if len(stroke.points) >= 2]
            if len(self._active_points) >= 2:
                lines.append(tuple(self._active_points))
            return lines

    @property
    def completed_count(self) -> int:
        with self._lock:
            return self._completed_count


class CtrlDragArrowController:
    def __init__(
        self,
        hold_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self.logger = logger
        self.model = AnnotationModel(hold_seconds=hold_seconds)
        self._overlay = _AnnotationOverlay(self.model, logger)
        self._listener = None
        self._active = False
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            from pynput import mouse
        except Exception as exc:  # pragma: no cover - import availability depends on env
            self.logger.warning("Anotacoes visuais indisponiveis: %s", exc)
            return

        def on_move(x: int, y: int) -> None:
            if not self._active:
                return
            if not keyboard.is_pressed("ctrl"):
                self.model.end()
                self._active = False
                return
            self.model.extend(int(x), int(y))

        def on_click(x: int, y: int, button, pressed: bool) -> None:
            button_name = getattr(button, "name", str(button))
            if button_name != "left":
                if self._active and not pressed:
                    self.model.end()
                    self._active = False
                return

            if pressed:
                if keyboard.is_pressed("ctrl"):
                    self._overlay.start()
                    self.model.begin(int(x), int(y))
                    self._active = True
                return

            if self._active:
                self.model.extend(int(x), int(y))
                self.model.end()
                self._active = False

        self._listener = mouse.Listener(on_move=on_move, on_click=on_click)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        self._active = False
        self.model.cancel()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._overlay.stop()
        self._started = False

    def draw_on_frame(
        self,
        frame: np.ndarray,
        origin: tuple[int, int],
        now: float | None = None,
    ) -> np.ndarray:
        lines = self.model.snapshot(now=now)
        if not lines:
            return frame

        image = Image.fromarray(frame)
        draw = ImageDraw.Draw(image, "RGBA")
        origin_x, origin_y = origin
        for line in lines:
            translated = [(x - origin_x, y - origin_y) for x, y in line[:2]]
            if len(translated) < 2 or translated[0] == translated[-1]:
                continue
            _draw_arrow_image(draw, translated[0], translated[-1], base_width=5, accent_width=2)
        return np.array(image)

    @property
    def completed_count(self) -> int:
        return self.model.completed_count


class NativeScreenRecorder:
    def __init__(
        self,
        fps: int,
        target_mode: str,
        frame_limit: int,
        annotation_hold_seconds: float,
        bundle_dir_factory: Callable[[], Path],
        logger: logging.Logger,
        on_finished: Callable[[NativeScreenRecordingResult], None] | None = None,
    ) -> None:
        self.fps = max(4, fps)
        self.target_mode = target_mode.strip().lower() or "foreground_window"
        self.frame_limit = max(1, frame_limit)
        self.bundle_dir_factory = bundle_dir_factory
        self.logger = logger
        self.on_finished = on_finished

        self._annotation = CtrlDragArrowController(
            hold_seconds=annotation_hold_seconds,
            logger=logger,
        )
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._bundle_dir: Path | None = None
        self._video_path: Path | None = None
        self._target: _ResolvedTarget | None = None
        self._start_time = 0.0

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                raise RuntimeError("Ja existe uma captura de tela nativa em andamento.")
            self._bundle_dir = self.bundle_dir_factory()
            self._video_path = self._bundle_dir / "native_evidence.mp4"
            self._target = self._resolve_target(self.target_mode)
            self._stop_event.clear()
            self._start_time = time.monotonic()
            self._running = True
            self._thread = threading.Thread(
                target=self._run,
                name="native-screen-recorder",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> bool:
        with self._lock:
            if not self._running:
                return False
            self._stop_event.set()
            return True

    def describe_target(self) -> str:
        target = self._target or self._resolve_target(self.target_mode)
        label = target.title or "Area de trabalho"
        return f"{target.kind}: {label}"

    def _run(self) -> None:
        result = NativeScreenRecordingResult(
            video_path=self._video_path,
            bundle_dir=self._bundle_dir,
            reason="manual",
            target_kind=self._target.kind if self._target else "screen",
            target_title=self._target.title if self._target else "",
            capture_rect=(
                self._target.region["left"],
                self._target.region["top"],
                self._target.region["width"],
                self._target.region["height"],
            )
            if self._target
            else (0, 0, 0, 0),
            duration_seconds=0.0,
            capture_fps=self.fps,
        )
        writer = None
        preview_frames: list[np.ndarray] = []
        last_preview_at = -999.0
        frames_written = 0

        try:
            import imageio.v2 as imageio
            import mss

            if self._video_path is None or self._bundle_dir is None or self._target is None:
                raise RuntimeError("A captura nativa nao foi inicializada corretamente.")

            self._annotation.start()
            writer = imageio.get_writer(
                self._video_path,
                fps=self.fps,
                codec="libx264",
                pixelformat="yuv420p",
                macro_block_size=1,
                ffmpeg_log_level="error",
            )
            frame_interval = 1.0 / float(self.fps)

            with mss.mss() as capturer:
                next_frame_at = time.monotonic()
                while not self._stop_event.is_set():
                    frame_started = time.monotonic()
                    raw = capturer.grab(self._target.region)
                    frame = np.asarray(raw)[..., :3][:, :, ::-1].copy()
                    frame = self._annotation.draw_on_frame(
                        frame,
                        origin=(self._target.region["left"], self._target.region["top"]),
                        now=frame_started,
                    )
                    frame = _ensure_even_dimensions(frame)
                    writer.append_data(frame)
                    frames_written += 1

                    elapsed = frame_started - self._start_time
                    if elapsed - last_preview_at >= 1.5:
                        preview_frames.append(frame.copy())
                        preview_frames = preview_frames[-self.frame_limit :]
                        last_preview_at = elapsed

                    next_frame_at += frame_interval
                    wait_time = next_frame_at - time.monotonic()
                    if wait_time > 0:
                        time.sleep(wait_time)
                    else:
                        next_frame_at = time.monotonic()

            duration_seconds = max(0.1, time.monotonic() - self._start_time)
            frame_paths = self._write_preview_frames(preview_frames)
            result = NativeScreenRecordingResult(
                video_path=self._video_path,
                bundle_dir=self._bundle_dir,
                reason="manual",
                target_kind=self._target.kind,
                target_title=self._target.title,
                capture_rect=(
                    self._target.region["left"],
                    self._target.region["top"],
                    self._target.region["width"],
                    self._target.region["height"],
                ),
                duration_seconds=duration_seconds,
                capture_fps=self.fps,
                frame_paths=tuple(frame_paths),
                annotation_count=self._annotation.completed_count,
            )
            self.logger.info(
                "Captura nativa concluida. alvo=%s video=%s frames=%s duracao=%.1fs",
                self._target.title or self._target.kind,
                self._video_path,
                frames_written,
                duration_seconds,
            )
        except Exception as exc:
            self.logger.exception("Falha na gravacao nativa de tela.")
            result = NativeScreenRecordingResult(
                video_path=self._video_path,
                bundle_dir=self._bundle_dir,
                reason="failed",
                target_kind=self._target.kind if self._target else "screen",
                target_title=self._target.title if self._target else "",
                capture_rect=(
                    self._target.region["left"],
                    self._target.region["top"],
                    self._target.region["width"],
                    self._target.region["height"],
                )
                if self._target
                else (0, 0, 0, 0),
                duration_seconds=max(0.0, time.monotonic() - self._start_time),
                capture_fps=self.fps,
                error_message=str(exc),
                annotation_count=self._annotation.completed_count,
            )
        finally:
            self._annotation.stop()
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            with self._lock:
                self._running = False
            if self.on_finished is not None:
                self.on_finished(result)

    def _write_preview_frames(self, preview_frames: list[np.ndarray]) -> list[Path]:
        if self._bundle_dir is None:
            return []
        saved: list[Path] = []
        for index, frame in enumerate(preview_frames[: self.frame_limit], start=1):
            frame_path = self._bundle_dir / f"frame_{index:02d}.jpg"
            Image.fromarray(frame).save(frame_path, format="JPEG", quality=85)
            saved.append(frame_path)
        return saved

    @staticmethod
    def _resolve_target(target_mode: str) -> "_ResolvedTarget":
        if target_mode == "desktop":
            return _resolve_virtual_screen()
        if target_mode == "foreground_window":
            return _resolve_foreground_window() or _resolve_virtual_screen()
        return _resolve_virtual_screen()


@dataclass(frozen=True)
class _ResolvedTarget:
    kind: str
    title: str
    region: dict[str, int]


class _AnnotationOverlay:
    def __init__(self, model: AnnotationModel, logger: logging.Logger) -> None:
        self.model = model
        self.logger = logger
        self._queue: queue.Queue[None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._visible = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run,
            name="annotation-overlay",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._started = False

    def _run(self) -> None:
        if ctypes.windll is None:  # pragma: no cover - Windows only
            return
        try:
            root = tk.Tk()
            root.withdraw()
            window = tk.Toplevel(root)
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            transparent = "#ff00ff"
            window.configure(bg=transparent)

            left, top, width, height = _virtual_screen_rect()
            window.geometry(f"{width}x{height}+{left}+{top}")

            canvas = tk.Canvas(
                window,
                width=width,
                height=height,
                highlightthickness=0,
                bd=0,
                bg=transparent,
            )
            canvas.pack(fill="both", expand=True)
            window.update_idletasks()
            _make_window_click_through(window.winfo_id(), transparent)

            def repaint() -> None:
                try:
                    item = self._queue.get_nowait()
                    if item is None:
                        root.quit()
                        return
                except queue.Empty:
                    pass

                lines = self.model.snapshot()
                if not lines:
                    canvas.delete("all")
                    if self._visible:
                        window.withdraw()
                        self._visible = False
                    root.after(16, repaint)
                    return

                if not self._visible:
                    window.deiconify()
                    self._visible = True

                canvas.delete("all")
                for line in lines:
                    translated = [(x - left, y - top) for x, y in line[:2]]
                    if len(translated) < 2 or translated[0] == translated[-1]:
                        continue
                    canvas.create_line(
                        translated[0][0],
                        translated[0][1],
                        translated[-1][0],
                        translated[-1][1],
                        fill="#ff2d2d",
                        width=5,
                        capstyle=tk.ROUND,
                        arrow=tk.LAST,
                        arrowshape=(18, 20, 8),
                    )
                    canvas.create_line(
                        translated[0][0],
                        translated[0][1],
                        translated[-1][0],
                        translated[-1][1],
                        fill="#ffd6d6",
                        width=2,
                        capstyle=tk.ROUND,
                        arrow=tk.LAST,
                        arrowshape=(18, 20, 8),
                    )
                root.after(16, repaint)

            root.after(16, repaint)
            root.mainloop()
        except Exception as exc:  # pragma: no cover - UI behavior is hard to assert in tests
            self.logger.warning("Overlay de anotacao indisponivel: %s", exc)


def _ensure_even_dimensions(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    pad_bottom = height % 2
    pad_right = width % 2
    if not pad_bottom and not pad_right:
        return frame
    return np.pad(frame, ((0, pad_bottom), (0, pad_right), (0, 0)), mode="edge")


def _draw_arrow_image(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    base_width: int,
    accent_width: int,
) -> None:
    shadow = (255, 45, 45, 255)
    accent = (255, 214, 214, 180)
    head_length = 18.0
    head_angle = math.radians(28.0)

    draw.line([start, end], fill=shadow, width=base_width)
    draw.line([start, end], fill=accent, width=accent_width)

    for point in _arrow_head_points(start, end, head_length=head_length, head_angle=head_angle):
        draw.line([end, point], fill=shadow, width=base_width)
        draw.line([end, point], fill=accent, width=accent_width)


def _arrow_head_points(
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    head_length: float,
    head_angle: float,
) -> tuple[tuple[int, int], tuple[int, int]]:
    dx = float(end[0] - start[0])
    dy = float(end[1] - start[1])
    angle = math.atan2(dy, dx)
    points: list[tuple[int, int]] = []
    for delta in (-head_angle, head_angle):
        point = (
            int(round(end[0] - head_length * math.cos(angle - delta))),
            int(round(end[1] - head_length * math.sin(angle - delta))),
        )
        points.append(point)
    return points[0], points[1]


def _resolve_foreground_window() -> _ResolvedTarget | None:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        return None

    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None

    width = max(0, rect.right - rect.left)
    height = max(0, rect.bottom - rect.top)
    if width < 64 or height < 64:
        return None

    title_buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
    return _ResolvedTarget(
        kind="janela",
        title=title_buffer.value.strip() or "Janela ativa",
        region={
            "left": int(rect.left),
            "top": int(rect.top),
            "width": int(width),
            "height": int(height),
        },
    )


def _resolve_virtual_screen() -> _ResolvedTarget:
    left, top, width, height = _virtual_screen_rect()
    return _ResolvedTarget(
        kind="desktop",
        title="Area de trabalho",
        region={
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        },
    )


def _virtual_screen_rect() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    left = int(user32.GetSystemMetrics(76))
    top = int(user32.GetSystemMetrics(77))
    width = int(user32.GetSystemMetrics(78))
    height = int(user32.GetSystemMetrics(79))
    return left, top, width, height


def _make_window_click_through(hwnd: int, transparent_color: str) -> None:
    del transparent_color
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    LWA_COLORKEY = 0x00000001

    user32 = ctypes.windll.user32
    current_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(
        hwnd,
        GWL_EXSTYLE,
        current_style
        | WS_EX_LAYERED
        | WS_EX_TRANSPARENT
        | WS_EX_TOOLWINDOW
        | WS_EX_NOACTIVATE,
    )
    color_key = 0x00FF00FF
    user32.SetLayeredWindowAttributes(hwnd, color_key, 255, LWA_COLORKEY)
