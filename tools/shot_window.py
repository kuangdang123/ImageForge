"""按标题截取一个正在运行的窗口（包括打包后的 exe），用于取证。

    python tools/shot_window.py ImageForge tmp/exe.png
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]


def find_window(keyword: str) -> int:
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if keyword in buffer.value:
            found.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    if not found:
        raise SystemExit(f"没有找到标题包含 {keyword!r} 的窗口")
    # 资源管理器打开同名文件夹时标题里也有关键字，优先挑应用窗口（"X · ImageForge" 形如 "标题 · 应用名"）。
    def score(hwnd: int) -> int:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        points = 0
        if "·" in title:
            points += 4
        if "图片格式转换" in title or "选择图片" in title:
            points += 6
        if title.endswith("ImageForge") or title.startswith("ImageForge"):
            points += 1
        return points

    return max(found, key=score)


def main() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 先声明 DPI 感知，GetWindowRect 才是物理像素
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    keyword = sys.argv[1] if len(sys.argv) > 1 else "ImageForge"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "tmp" / "window.png")
    hwnd = find_window(keyword)
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.3)
    # 目标窗口可能被浏览器等挡着。Windows 限制后台进程直接抢前台，
    # 常规做法是把本线程输入队列临时挂到当前前台线程上，再抢一次。
    foreground = user32.GetForegroundWindow()
    this_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = False
    if target_thread and target_thread != this_thread:
        attached = bool(user32.AttachThreadInput(this_thread, target_thread, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOMOVE_NOSIZE = 0x0001 | 0x0002
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE_NOSIZE)
    time.sleep(0.6)
    if attached:
        user32.AttachThreadInput(this_thread, target_thread, False)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    out.parent.mkdir(parents=True, exist_ok=True)
    bbox = (rect.left, rect.top, rect.right, rect.bottom)
    image = ImageGrab.grab(bbox=bbox, all_screens=True)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE_NOSIZE)
    image.save(out)
    print(f"saved {out} bbox={bbox}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
