"""单实例守卫的回归测试。"""

from __future__ import annotations

import ctypes
import os
import unittest

from imgforge import single_instance


@unittest.skipUnless(os.name == "nt", "互斥体只在 Windows 上有意义")
class SingleInstanceTest(unittest.TestCase):
    def tearDown(self) -> None:
        single_instance.release()

    def test_stale_last_error_is_not_a_duplicate(self) -> None:
        """前序 Win32 调用留下 183 时，全新进程不能被误判成第二个实例。

        之前就踩过：ctypes 调用链里残留的错误码让启动的进程直接走
        "把已有窗口叫到前台" 分支然后退出，表现为双击没反应。
        """
        name = f"Local\\ImageForge.Test.{os.getpid()}.stale"
        ctypes.windll.kernel32.SetLastError(single_instance.ERROR_ALREADY_EXISTS)
        self.assertTrue(single_instance.acquire(name))

    def test_second_acquire_is_rejected(self) -> None:
        name = f"Local\\ImageForge.Test.{os.getpid()}.second"
        self.assertTrue(single_instance.acquire(name))
        self.assertFalse(single_instance.acquire(name))

    def test_release_allows_acquire_again(self) -> None:
        name = f"Local\\ImageForge.Test.{os.getpid()}.release"
        self.assertTrue(single_instance.acquire(name))
        single_instance.release()
        self.assertTrue(single_instance.acquire(name))


if __name__ == "__main__":
    unittest.main()