"""ICO 容器写入器。

为什么不用 Pillow 自带的 ICO 保存：
1. 它用 ``thumbnail`` 等比缩放，非正方形图会存出非正方形帧（Windows 上易出错）；
2. 它跳过所有大于源图尺寸的档位，小图无法补出 128/256；
3. 无法控制留白方式（contain / fill / stretch）。

因此这里自己拼 ICO 容器，像素仍交给 Pillow 缩放：
- ICO 头 + 每个尺寸一条 16 字节目录项；
- 载荷支持 PNG（Vista+，体积小）与 DIB/BMP（旧版兼容，带 AND 掩码）。
"""

from __future__ import annotations

import io
import os
import struct
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image

DEFAULT_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
MAX_SIZE = 256

_ICONDIR = struct.Struct("<HHH")
_ICONDIRENTRY = struct.Struct("<BBBBHHII")
_BITMAPINFOHEADER = struct.Struct("<IiiHHIIiiII")


class IconError(ValueError):
    """ICO 生成参数不合法。"""


def normalize_sizes(sizes: Iterable[int]) -> list[int]:
    clean = sorted({int(s) for s in sizes if 1 <= int(s) <= MAX_SIZE})
    if not clean:
        raise IconError("至少需要选择一个介于 1 与 256 之间的尺寸")
    return clean


def _fit_frame(img: Image.Image, size: int, fit: str) -> Image.Image:
    """把任意尺寸的图放进 size×size 的正方形画布（RGBA）。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    src_w, src_h = img.size
    if fit == "stretch":
        return img.resize((size, size), Image.Resampling.LANCZOS, reducing_gap=2.0)

    if fit == "contain":
        scale = min(size / src_w, size / src_h)
    elif fit == "fill":
        scale = max(size / src_w, size / src_h)
    else:
        raise IconError(f"未知的留白方式: {fit}")

    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS, reducing_gap=2.0)

    if fit == "fill":  # 居中裁切
        left = max(0, (new_w - size) // 2)
        top = max(0, (new_h - size) // 2)
        return resized.crop((left, top, left + size, top + size))

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - new_w) // 2, (size - new_h) // 2), resized)
    return canvas


def render_frames(
    img: Image.Image,
    sizes: Sequence[int],
    *,
    fit: str = "contain",
    upscale: bool = True,
) -> list[tuple[int, Image.Image]]:
    """生成 (边长, RGBA 帧) 列表，按尺寸升序。

    ``upscale=False`` 时跳过大于源图的档位；一个都不剩则抛 IconError。
    """
    sizes = normalize_sizes(sizes)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    longest = max(img.size)

    frames: list[tuple[int, Image.Image]] = []
    for size in sizes:
        if not upscale and size > longest:
            continue
        frames.append((size, _fit_frame(img, size, fit)))
    if not frames:
        raise IconError(f"源图最长边只有 {longest}px，所选尺寸都需要放大；请允许放大或换更大的源图")
    return frames


def _encode_png(frame: Image.Image) -> bytes:
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _encode_dib(frame: Image.Image) -> bytes:
    """32bpp BGRA，行自底向上，附加 1bpp AND 掩码（旧版兼容）。"""
    frame = frame.convert("RGBA")
    width, height = frame.size
    rgba = frame.tobytes()  # 自上而下，每像素 4 字节

    xor_rows = []
    for y in range(height - 1, -1, -1):
        row = rgba[y * width * 4 : (y + 1) * width * 4]
        bgra = bytearray(width * 4)
        bgra[0::4] = row[2::4]
        bgra[1::4] = row[1::4]
        bgra[2::4] = row[0::4]
        bgra[3::4] = row[3::4]
        xor_rows.append(bytes(bgra))
    xor_bitmap = b"".join(xor_rows)

    mask_stride = ((width + 31) // 32) * 4
    mask_rows = []
    for y in range(height - 1, -1, -1):
        bits = bytearray(mask_stride)
        for x in range(width):
            if rgba[(y * width + x) * 4 + 3] < 128:
                bits[x >> 3] |= 0x80 >> (x & 7)
        mask_rows.append(bytes(bits))
    and_mask = b"".join(mask_rows)

    header = _BITMAPINFOHEADER.pack(
        40,          # biSize
        width,       # biWidth
        height * 2,  # biHeight：XOR + AND
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression = BI_RGB
        len(xor_bitmap),  # biSizeImage
        0, 0,        # 分辨率（未知）
        0, 0,        # 调色板
    )
    return header + xor_bitmap + and_mask


def encode_ico(frames: Sequence[tuple[int, Image.Image]], payload: str = "png") -> bytes:
    if not frames:
        raise IconError("没有可写入的帧")
    if payload not in ("png", "bmp"):
        raise IconError(f"未知的 ICO 载荷格式: {payload}")

    encoded: list[tuple[int, bytes]] = []
    for size, frame in frames:
        if frame.size != (size, size):
            raise IconError(f"帧尺寸 {frame.size} 与声明尺寸 {size} 不一致")
        data = _encode_png(frame) if payload == "png" else _encode_dib(frame)
        encoded.append((size, data))

    header = _ICONDIR.pack(0, 1, len(encoded))
    offset = len(header) + len(encoded) * _ICONDIRENTRY.size
    entries, blobs = [], []
    for size, data in encoded:
        entries.append(
            _ICONDIRENTRY.pack(
                size if size < 256 else 0,
                size if size < 256 else 0,
                0,      # 调色板颜色数
                0,      # 保留
                1,      # 平面数
                32,     # 位深
                len(data),
                offset,
            )
        )
        blobs.append(data)
        offset += len(data)
    return header + b"".join(entries) + b"".join(blobs)


def save_ico(
    img: Image.Image,
    dest: str | os.PathLike[str],
    *,
    sizes: Sequence[int] = DEFAULT_SIZES,
    fit: str = "contain",
    payload: str = "png",
    upscale: bool = True,
) -> list[int]:
    """写 .ico，返回实际写入的尺寸列表。先写临时文件再替换，避免半截文件。"""
    frames = render_frames(img, sizes, fit=fit, upscale=upscale)
    data = encode_ico(frames, payload=payload)
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return [size for size, _ in frames]


def read_entries(source: str | os.PathLike[str] | bytes) -> list[dict]:
    """解析 ICO 目录（给测试和 GUI 的"已有 ico 里有什么"用）。"""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if len(data) < _ICONDIR.size:
        raise IconError("不是合法的 ICO：文件太短")
    reserved, kind, count = _ICONDIR.unpack_from(data, 0)
    if reserved != 0 or kind != 1:
        raise IconError("不是合法的 ICO：文件头标识不匹配")
    entries = []
    for index in range(count):
        pos = _ICONDIR.size + index * _ICONDIRENTRY.size
        width, height, colors, _, planes, bit_count, length, offset = _ICONDIRENTRY.unpack_from(data, pos)
        width = width or 256
        height = height or 256
        payload = data[offset : offset + length]
        kind_name = "png" if payload[:8] == b"\x89PNG\r\n\x1a\n" else "bmp"
        entries.append(
            {
                "size": (width, height),
                "bytes": length,
                "offset": offset,
                "planes": planes,
                "bit_count": bit_count,
                "colors": colors,
                "format": kind_name,
            }
        )
    return entries