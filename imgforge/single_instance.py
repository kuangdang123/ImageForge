"""单实例守卫。

双击启动器（或 exe）多次时，只留一个进程：后来的进程发现互斥体已存在，
就把已经在跑的那个窗口叫到前台，然后自己安静退出，而不是再开一个界面。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import List

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\ImageForge.SingleInstance.1"

# 句柄保存在模块级，避免被回收后互斥体提前释放
_handle = None


def acquire(name: str = MUTEX_NAME) -> bool:
    """拿到互斥体返回 True；已有实例在跑返回 False。"""
    global _handle
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:  # 非 Windows 平台不拦
        return True
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle:
        # 创建失败时宁可允许打开，也不要让程序打不开
        return True
    _handle = handle
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def _window_titles(keyword: str) -> List[int]:
    user32 = ctypes.windll.user32
    found: List[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):  # pragma: no cover - 由系统回调
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if keyword in buffer.value:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(_callback, 0)
    return found


def focus_existing(keyword: str = "ImageForge", timeout: float = 3.0) -> bool:
    """把已存在的主窗口恢复并切到前台；找不到就返回 False。

    刚被双击启动时，第一个进程可能还在初始化窗口，所以这里轮询一小会儿。
    """
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        windows = _window_titles(keyword)
        if windows:
            hwnd = windows[0]
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.1)
    return False