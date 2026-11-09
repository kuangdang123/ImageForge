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
    # 先清掉线程里残留的错误码：CreateMutexW 用 GetLastError 区分"新建"和"已存在"，
    # 若前序 Win32 调用恰好留下 183，全新启动的进程会被误判成第二个实例而直接退出。
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle:
        # 创建失败时宁可允许打开，也不要让程序打不开
        return True
    _handle = handle
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def release() -> None:
    """释放互斥体（正常退出时进程结束会自动释放，测试里手动调用）。"""
    global _handle
    if _handle:
        try:
            ctypes.windll.kernel32.CloseHandle(_handle)
        except AttributeError:  # 非 Windows
            pass
        _handle = None


def _window_titles(keyword: str) -> List[int]:
    """收集标题里含关键字的可见顶层窗口，应用窗口排在前面。

    资源管理器打开同名文件夹时标题里也会有关键字（"ImageForge"），
    所以不能"找到第一个就收工"，要挑更像应用窗口的那个。
    """
    user32 = ctypes.windll.user32
    found: List[int] = []
    titles: dict[int, str] = {}

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
            titles[hwnd] = buffer.value
        return True

    user32.EnumWindows(_callback, 0)

    def score(hwnd: int) -> int:
        title = titles.get(hwnd, "")
        points = 0
        if "·" in title:
            points += 4
        if "图片格式转换" in title or "选择图片" in title:
            points += 6
        if title.startswith("ImageForge") or title.endswith("ImageForge"):
            points += 1
        return points

    return sorted(found, key=score, reverse=True)


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