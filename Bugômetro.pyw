from __future__ import annotations

import subprocess
from pathlib import Path

from app.main import main


def start_button_bridge() -> None:
    project_root = Path(__file__).resolve().parent
    script_path = project_root / "scripts" / "button_hotkeys.ahk"
    exe_candidates = (
        project_root / "tools" / "AutoHotkey64.exe",
        Path.home()
        / "AppData"
        / "Local"
        / "Programs"
        / "AutoHotkey"
        / "v2"
        / "AutoHotkey64.exe",
        Path.home()
        / "AppData"
        / "Local"
        / "Temp"
        / "codex-ahk-validate"
        / "ahk"
        / "AutoHotkey64.exe",
    )

    exe_path = next((path for path in exe_candidates if path.exists()), None)
    if exe_path is None or not script_path.exists():
        return

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(exe_path), str(script_path)],
        cwd=str(project_root),
        creationflags=creationflags,
        close_fds=True,
    )


if __name__ == "__main__":
    start_button_bridge()
    main()
