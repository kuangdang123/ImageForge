"""模型包定位、安装与下载校验的测试。"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from imgforge.upscale import download, engine


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _fake_model_zip(path: Path) -> Path:
    return _make_zip(
        path,
        {
            "ncnn/realcugan-ncnn-vulkan.exe": b"MZ fake exe",
            "ncnn/models-se/up2x-conservative-x2.param": b"param",
            "ncnn/models-se/up2x-conservative-x2.bin": b"bin",
        },
    )


class ModelsPathTest(unittest.TestCase):
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

    def test_install_archive_installs_ncnn_dir(self) -> None:
        archive = _fake_model_zip(self.tmp / "models.zip")
        root = self.tmp / "installed"
        result = download.install_archive(archive, root)
        self.assertEqual(result, root)
        self.assertTrue((root / "ncnn" / "realcugan-ncnn-vulkan.exe").is_file())
        engine.set_models_root(root)
        self.assertTrue(engine.models_ready())

    def test_install_rejects_traversal(self) -> None:
        archive = _make_zip(
            self.tmp / "evil.zip",
            {"ncnn/../../evil.txt": b"x", "ncnn/realcugan-ncnn-vulkan.exe": b"MZ"},
        )
        with self.assertRaises(download.DownloadError):
            download.install_archive(archive, self.tmp / "target")

    def test_install_rejects_absolute_path(self) -> None:
        archive = _make_zip(self.tmp / "abs.zip", {"C:/Windows/evil.dll": b"x"})
        with self.assertRaises(download.DownloadError):
            download.install_archive(archive, self.tmp / "target")

    def test_install_rejects_missing_ncnn(self) -> None:
        archive = _make_zip(self.tmp / "bad.zip", {"readme.txt": b"x"})
        with self.assertRaises(download.DownloadError):
            download.install_archive(archive, self.tmp / "target")

    def test_download_checksum_mismatch(self) -> None:
        archive = _fake_model_zip(self.tmp / "fake.zip")
        url = archive.resolve().as_uri()
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_models(root=self.tmp / "dl", url=url)
        self.assertIn("校验", str(ctx.exception))

    def test_download_ok_when_sha_matches(self) -> None:
        archive = _fake_model_zip(self.tmp / "fake.zip")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        old_sha = download.MODELS_SHA256
        download.MODELS_SHA256 = digest
        try:
            root = download.download_models(
                root=self.tmp / "dl", url=archive.resolve().as_uri()
            )
        finally:
            download.MODELS_SHA256 = old_sha
        self.assertEqual(root, self.tmp / "dl")
        engine.set_models_root(root)
        self.assertTrue(engine.models_ready())


if __name__ == "__main__":
    unittest.main()
