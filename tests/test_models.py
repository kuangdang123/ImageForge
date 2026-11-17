"""模型包元数据、安装与下载校验的测试。

全部离线可跑：下载相关测试只用 ``file://`` 或本机回环的临时 HTTP 服务。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from imgforge.upscale import download, engine


# ------------------------------------------------------------- 测试夹具 --
def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _fake_ncnn_zip(path: Path) -> Path:
    return _make_zip(
        path,
        {
            "ncnn/realcugan-ncnn-vulkan.exe": b"MZ fake exe",
            "ncnn/models-se/up2x-conservative-x2.param": b"param",
            "ncnn/models-se/up2x-conservative-x2.bin": b"bin",
        },
    )


def _fake_apisr_zip(path: Path) -> Path:
    return _make_zip(
        path,
        {
            "apisr/grl4.onnx": b"fake grl4 onnx",
            "apisr/rrdb4.onnx": b"fake rrdb4 onnx",
        },
    )


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _QuietHandler(SimpleHTTPRequestHandler):
    """静默的静态文件服务（不在测试输出里刷请求日志）。"""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - 覆盖基类签名
        pass


@contextmanager
def _http_server(directory: Path):
    """起一个只服务本机回环的临时 HTTP 服务，yield 出 base URL。"""
    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, name="imgforge-test-http", daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class _TmpTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="imgforge-models-test-")
        self.tmp = Path(self._tmp.name)
        self._old_env = os.environ.get("IMAGEFORGE_MODELS_DIR")
        engine.set_models_root(None)

    def tearDown(self) -> None:
        engine.set_models_root(None)
        if self._old_env is None:
            os.environ.pop("IMAGEFORGE_MODELS_DIR", None)
        else:
            os.environ["IMAGEFORGE_MODELS_DIR"] = self._old_env
        self._tmp.cleanup()

    def patch_pack(self, key: str, **changes) -> download.ModelPack:
        """临时替换 PACK_BY_KEY 里的某个包（测试用假 zip 的 size/sha）。"""
        old = download.PACK_BY_KEY[key]
        pack = replace(old, **changes)
        download.PACK_BY_KEY[key] = pack
        self.addCleanup(download.PACK_BY_KEY.__setitem__, key, old)
        return pack

    def patch_legacy(self, *, size: int, sha256: str) -> None:
        """临时替换旧 API 的 ncnn 常量。"""
        old_size, old_sha = download.MODELS_BYTES, download.MODELS_SHA256
        download.MODELS_BYTES = int(size)
        download.MODELS_SHA256 = sha256
        self.addCleanup(setattr, download, "MODELS_BYTES", old_size)
        self.addCleanup(setattr, download, "MODELS_SHA256", old_sha)


# ------------------------------------------------------- 模型目录定位 --
class ModelsPathTest(_TmpTestCase):
    def test_env_var_is_authoritative(self) -> None:
        os.environ["IMAGEFORGE_MODELS_DIR"] = str(self.tmp / "env-root")
        roots = engine._candidate_roots()
        self.assertEqual(roots, [self.tmp / "env-root"])

    def test_explicit_override_beats_env(self) -> None:
        os.environ["IMAGEFORGE_MODELS_DIR"] = str(self.tmp / "env-root")
        override = self.tmp / "override"
        engine.set_models_root(override)
        self.assertEqual(engine._candidate_roots(), [override])
        self.assertEqual(engine.weights_root(), override)

    def test_models_ready_requires_exe_and_params(self) -> None:
        root = self.tmp / "root"
        engine.set_models_root(root)
        self.assertFalse(engine.models_ready())
        (root / "ncnn" / "models-se").mkdir(parents=True)
        (root / "ncnn" / "realcugan-ncnn-vulkan.exe").write_bytes(b"MZ")
        self.assertFalse(engine.models_ready())
        (root / "ncnn" / "models-se" / "up2x-conservative-x2.param").write_bytes(b"p")
        self.assertTrue(engine.models_ready())


# ----------------------------------------------------------- 元数据 --
class ModelPackTest(_TmpTestCase):
    def test_packs_metadata_sane(self) -> None:
        self.assertGreaterEqual(len(download.PACKS), 2)
        keys = [pack.key for pack in download.PACKS]
        self.assertEqual(len(keys), len(set(keys)), "pack key 必须唯一")
        self.assertTrue(
            any(pack.key == "ncnn" and pack.required for pack in download.PACKS),
            "ncnn 必须是 required",
        )
        for pack in download.PACKS:
            with self.subTest(pack=pack.key):
                self.assertGreater(pack.size, 0)
                self.assertRegex(pack.sha256, r"^[0-9a-f]{64}$")
                self.assertNotIn("..", pack.subdir)
                self.assertTrue(pack.subdir)
                self.assertTrue(pack.archive.endswith(".zip"))
                self.assertEqual(download.PACK_BY_KEY[pack.key], pack)

    def test_legacy_and_apisr_constants_match_packs(self) -> None:
        ncnn = download.PACK_BY_KEY["ncnn"]
        apisr = download.PACK_BY_KEY["apisr"]
        self.assertEqual(download.MODELS_ARCHIVE, ncnn.archive)
        self.assertEqual(download.MODELS_RELEASE_TAG, ncnn.tag)
        self.assertEqual(download.MODELS_BYTES, ncnn.size)
        self.assertEqual(download.MODELS_SHA256, ncnn.sha256)
        self.assertEqual(download.MODELS_URL, download.pack_url(ncnn))
        self.assertEqual(download.APISR_ARCHIVE, apisr.archive)
        self.assertEqual(download.APISR_TAG, apisr.tag)
        self.assertEqual(download.APISR_BYTES, apisr.size)
        self.assertEqual(download.APISR_SHA256, apisr.sha256)
        self.assertEqual(apisr.tag, "models-v2")
        self.assertFalse(apisr.required)

    def test_pack_url(self) -> None:
        self.assertEqual(
            download.models_url("models-v1", "realcugan-ncnn-models.zip"),
            "https://github.com/kuangdang123/ImageForge/releases/download/"
            "models-v1/realcugan-ncnn-models.zip",
        )
        for pack in download.PACKS:
            self.assertTrue(download.pack_url(pack).startswith("https://"))
            self.assertIn(f"/{pack.tag}/{pack.archive}", download.pack_url(pack))

    def test_pack_root(self) -> None:
        root = self.tmp / "models"
        for pack in download.PACKS:
            self.assertEqual(download.pack_root(pack, root), root / pack.subdir)
            self.assertEqual(download.pack_root(pack.key, root), root / pack.subdir)
        engine.set_models_root(root)
        self.assertEqual(download.pack_root("ncnn"), root / "ncnn")

    def test_installed_requires_expected_files(self) -> None:
        root = self.tmp / "root"
        (root / "apisr").mkdir(parents=True)
        (root / "ncnn").mkdir()
        self.assertFalse(download.installed("apisr", root))
        self.assertFalse(download.installed("ncnn", root))

        (root / "apisr" / "x.onnx").write_bytes(b"onnx")
        self.assertTrue(download.installed("apisr", root))
        self.assertFalse(download.installed("ncnn", root))

        (root / "ncnn" / "realcugan-ncnn-vulkan.exe").write_bytes(b"MZ")
        self.assertTrue(download.installed("ncnn", root))

    def test_installed_unknown_key_uses_nonempty_dir(self) -> None:
        root = self.tmp / "root"
        (root / "custom").mkdir(parents=True)
        self.assertFalse(download.installed("custom", root))
        (root / "custom" / "anything.bin").write_bytes(b"x")
        self.assertTrue(download.installed("custom", root))

    def test_installed_packs_and_missing_packs(self) -> None:
        root = self.tmp / "root"
        self.assertEqual(download.installed_packs(root), [])
        self.assertEqual(list(download.missing_packs(root)), list(download.PACKS))

        download.install_archive(_fake_ncnn_zip(self.tmp / "ncnn.zip"), root)
        self.assertEqual(download.installed_packs(root), ["ncnn"])
        self.assertEqual([pack.key for pack in download.missing_packs(root)], ["apisr"])

    def test_unknown_pack_key_raises(self) -> None:
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_pack("nope", self.tmp / "dl", url="file:///nonexistent.zip")
        self.assertIn("未知模型包", str(ctx.exception))


# ------------------------------------------------------------- 安装 --
class InstallArchiveTest(_TmpTestCase):
    def test_install_archive_installs_ncnn_dir(self) -> None:
        archive = _fake_ncnn_zip(self.tmp / "models.zip")
        root = self.tmp / "installed"
        keys = download.install_archive(archive, root)
        self.assertEqual(keys, ["ncnn"])
        self.assertIsInstance(keys, list)
        self.assertTrue((root / "ncnn" / "realcugan-ncnn-vulkan.exe").is_file())
        self.assertTrue(download.installed("ncnn", root))
        engine.set_models_root(root)
        self.assertTrue(engine.models_ready())

    def test_install_archive_installs_apisr_dir(self) -> None:
        archive = _fake_apisr_zip(self.tmp / "apisr.zip")
        root = self.tmp / "installed"
        keys = download.install_archive(archive, root)
        self.assertEqual(keys, ["apisr"])
        self.assertTrue((root / "apisr" / "grl4.onnx").is_file())
        self.assertTrue(download.installed("apisr", root))
        self.assertFalse(download.installed("ncnn", root))

    def test_install_archive_installs_both_packs(self) -> None:
        archive = _make_zip(
            self.tmp / "both.zip",
            {
                "ncnn/realcugan-ncnn-vulkan.exe": b"MZ",
                "apisr/grl4.onnx": b"onnx",
            },
        )
        root = self.tmp / "installed"
        self.assertEqual(download.install_archive(archive, root), ["ncnn", "apisr"])
        self.assertEqual(download.installed_packs(root), ["ncnn", "apisr"])

    def test_install_archive_ignores_unknown_prefix(self) -> None:
        archive = _make_zip(
            self.tmp / "mixed.zip",
            {
                "ncnn/realcugan-ncnn-vulkan.exe": b"MZ",
                "docs/readme.txt": b"junk",
                "top-level.txt": b"junk",
            },
        )
        root = self.tmp / "installed"
        self.assertEqual(download.install_archive(archive, root), ["ncnn"])
        self.assertFalse((root / "docs").exists())
        self.assertFalse((root / "top-level.txt").exists())

    def test_install_archive_replaces_existing_dir(self) -> None:
        root = self.tmp / "installed"
        stale = root / "ncnn" / "stale.bin"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"old")
        download.install_archive(_fake_ncnn_zip(self.tmp / "models.zip"), root)
        self.assertFalse(stale.exists())
        self.assertTrue((root / "ncnn" / "realcugan-ncnn-vulkan.exe").is_file())

    def test_install_rejects_traversal(self) -> None:
        archive = _make_zip(
            self.tmp / "evil.zip",
            {"ncnn/../../evil.txt": b"x", "ncnn/realcugan-ncnn-vulkan.exe": b"MZ"},
        )
        with self.assertRaises(download.DownloadError):
            download.install_archive(archive, self.tmp / "target")
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_install_rejects_absolute_path(self) -> None:
        for index, name in enumerate(("C:/Windows/evil.dll", "/etc/passwd", "\\\\share\\evil")):
            with self.subTest(name=name):
                archive = _make_zip(
                    self.tmp / f"abs{index}.zip",
                    {name: b"x", "ncnn/realcugan-ncnn-vulkan.exe": b"MZ"},
                )
                with self.assertRaises(download.DownloadError):
                    download.install_archive(archive, self.tmp / "target")

    def test_install_rejects_missing_recognized_dir(self) -> None:
        archive = _make_zip(self.tmp / "bad.zip", {"readme.txt": b"x"})
        with self.assertRaises(download.DownloadError) as ctx:
            download.install_archive(archive, self.tmp / "target")
        self.assertIn("没有可识别的模型目录", str(ctx.exception))

    def test_install_rejects_missing_file(self) -> None:
        with self.assertRaises(download.DownloadError):
            download.install_archive(self.tmp / "nope.zip", self.tmp / "target")


# ------------------------------------------------------------- 下载 --
class DownloadTest(_TmpTestCase):
    def _fake_apisr(self, name: str = "apisr.zip") -> Path:
        return _fake_apisr_zip(self.tmp / name)

    def test_download_pack_sha_matches(self) -> None:
        archive = self._fake_apisr()
        self.patch_pack("apisr", size=archive.stat().st_size, sha256=_sha256_of(archive))
        progress: list[tuple[int, int]] = []
        root = self.tmp / "dl"
        keys = download.download_pack(
            "apisr",
            root,
            progress=lambda done, total: progress.append((done, total)),
            url=archive.resolve().as_uri(),
        )
        self.assertEqual(keys, ["apisr"])
        self.assertTrue(download.installed("apisr", root))
        self.assertGreater(len(progress), 0)
        done, total = progress[-1]
        self.assertEqual(total, archive.stat().st_size)
        self.assertEqual(done, archive.stat().st_size)

    def test_download_pack_sha_mismatch(self) -> None:
        archive = self._fake_apisr()
        self.patch_pack("apisr", size=archive.stat().st_size, sha256="0" * 64)
        root = self.tmp / "dl"
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_pack("apisr", root, url=archive.resolve().as_uri())
        self.assertIn("校验", str(ctx.exception))
        self.assertFalse(download.installed("apisr", root))

    def test_download_pack_size_mismatch(self) -> None:
        archive = self._fake_apisr()
        self.patch_pack(
            "apisr", size=archive.stat().st_size + 1, sha256=_sha256_of(archive)
        )
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_pack("apisr", self.tmp / "dl", url=archive.resolve().as_uri())
        self.assertIn("大小", str(ctx.exception))

    def test_download_pack_cancel(self) -> None:
        archive = self._fake_apisr()
        self.patch_pack("apisr", size=archive.stat().st_size, sha256=_sha256_of(archive))
        root = self.tmp / "dl"
        with self.assertRaises(download.DownloadCancelled) as ctx:
            download.download_pack(
                "apisr", root, cancel=lambda: True, url=archive.resolve().as_uri()
            )
        self.assertEqual(str(ctx.exception), "已取消")
        self.assertFalse(download.installed("apisr", root))

    def test_download_pack_accepts_modelpack_and_file_url(self) -> None:
        archive = self._fake_apisr("direct.zip")
        pack = replace(
            download.PACK_BY_KEY["apisr"],
            size=archive.stat().st_size,
            sha256=_sha256_of(archive),
        )
        root = self.tmp / "dl"
        self.assertEqual(
            download.download_pack(pack, root, url=archive.resolve().as_uri()), ["apisr"]
        )
        self.assertTrue(download.installed(pack, root))

    def test_download_models_checksum_mismatch(self) -> None:
        archive = _fake_ncnn_zip(self.tmp / "fake.zip")
        self.patch_legacy(size=archive.stat().st_size, sha256="0" * 64)
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_models(root=self.tmp / "dl", url=archive.resolve().as_uri())
        self.assertIn("校验", str(ctx.exception))

    def test_download_models_ok_when_sha_matches(self) -> None:
        archive = _fake_ncnn_zip(self.tmp / "fake.zip")
        self.patch_legacy(size=archive.stat().st_size, sha256=_sha256_of(archive))
        root = download.download_models(root=self.tmp / "dl", url=archive.resolve().as_uri())
        self.assertEqual(root, self.tmp / "dl")
        self.assertIsInstance(root, Path)
        engine.set_models_root(root)
        self.assertTrue(engine.models_ready())


class HttpDownloadTest(_TmpTestCase):
    """本机回环 HTTP 服务：验证真实 HTTP 路径（不需要外网）。"""

    def test_download_pack_over_http(self) -> None:
        www = self.tmp / "www"
        www.mkdir()
        archive = _fake_apisr_zip(www / "apisr.zip")
        self.patch_pack("apisr", size=archive.stat().st_size, sha256=_sha256_of(archive))
        root = self.tmp / "dl"
        try:
            with _http_server(www) as base:
                keys = download.download_pack(
                    "apisr", root, url=f"{base}/apisr.zip", timeout=10
                )
        except OSError as exc:  # 某些受限环境不允许绑定端口
            self.skipTest(f"本机不能绑定回环端口：{exc}")
        self.assertEqual(keys, ["apisr"])
        self.assertTrue(download.installed("apisr", root))


if __name__ == "__main__":
    unittest.main()
