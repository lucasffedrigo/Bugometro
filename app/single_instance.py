from __future__ import annotations

import ctypes
from ctypes import wintypes


ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    def __init__(self, name: str) -> None:
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._handle = kernel32.CreateMutexW(None, False, self.name)
        if not self._handle:
            return True
        last_error = kernel32.GetLastError()
        return last_error != ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
