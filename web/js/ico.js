/* ImageForge Web · ICO 容器编码（PNG / BMP 两种像素数据，无依赖） */
(function (root) {
  "use strict";

  const util = root.IFW || (typeof require !== "undefined" ? require("./util.js") : {});
  const ByteWriter = util.ByteWriter;

  const MAX_SIZE = 256;

  function normalizeSizes(sizes) {
    const seen = new Set();
    const out = [];
    for (const raw of sizes || []) {
      const value = Math.round(Number(raw));
      if (!Number.isFinite(value) || value <= 0) continue;
      const size = Math.min(MAX_SIZE, Math.max(1, value));
      if (seen.has(size)) continue;
      seen.add(size);
      out.push(size);
    }
    out.sort((a, b) => a - b);
    return out;
  }

  /**
   * 32 位 BGRA 的 DIB（BITMAPINFOHEADER + 自底向上像素 + 1bpp AND 掩码）。
   * imageData: {width, height, data: Uint8ClampedArray|Uint8Array}
   */
  function buildDib(imageData) {
    const width = imageData.width;
    const height = imageData.height;
    const src = imageData.data;
    const maskStride = Math.ceil(width / 32) * 4;
    const pixelBytes = width * height * 4;
    const maskBytes = maskStride * height;
    const writer = new ByteWriter(40 + pixelBytes + maskBytes);

    writer.u32(40); // biSize
    writer.i32(width); // biWidth
    writer.i32(height * 2); // biHeight（XOR + AND 两张位图）
    writer.u16(1); // biPlanes
    writer.u16(32); // biBitCount
    writer.u32(0); // biCompression = BI_RGB
    writer.u32(pixelBytes + maskBytes); // biSizeImage
    writer.i32(0); // biXPelsPerMeter
    writer.i32(0); // biYPelsPerMeter
    writer.u32(0); // biClrUsed
    writer.u32(0); // biClrImportant

    // 自底向上的 BGRA
    for (let y = height - 1; y >= 0; y -= 1) {
      let src_index = y * width * 4;
      for (let x = 0; x < width; x += 1) {
        writer.u8(src[src_index + 2]); // B
        writer.u8(src[src_index + 1]); // G
        writer.u8(src[src_index + 0]); // R
        writer.u8(src[src_index + 3]); // A
        src_index += 4;
      }
    }

    // 1bpp AND 掩码：alpha < 128 记 1（透明）
    const mask = new Uint8Array(maskBytes);
    for (let y = 0; y < height; y += 1) {
      const row = (height - 1 - y) * maskStride;
      for (let x = 0; x < width; x += 1) {
        const alpha = src[(y * width + x) * 4 + 3];
        if (alpha < 128) mask[row + (x >> 3)] |= 0x80 >> (x & 7);
      }
    }
    writer.raw(mask);
    return writer.bytes;
  }

  /**
   * 组装 ICO。frames: [{size, imageData, png?}]
   * payload: "png" 用 frames[i].png；"bmp" 用 imageData 现场生成 DIB。
   */
  function encode(frames, payload) {
    const list = (frames || []).filter(Boolean);
    if (!list.length) throw new Error("没有可写入的图标尺寸");

    const entries = list.map((frame) => {
      const size = frame.size || frame.imageData.width;
      let data;
      if (payload === "bmp") {
        data = frame.dib || buildDib(frame.imageData);
      } else {
        if (!frame.png) throw new Error("PNG 载荷缺少编码数据");
        data = frame.png;
      }
      return { size, data };
    });

    const headerBytes = 6 + entries.length * 16;
    const writer = new ByteWriter(
      headerBytes + entries.reduce((sum, entry) => sum + entry.data.length, 0)
    );
    writer.u16(0); // reserved
    writer.u16(1); // type = icon
    writer.u16(entries.length);

    let offset = headerBytes;
    for (const entry of entries) {
      const size = entry.size >= 256 ? 0 : entry.size;
      writer.u8(size); // width
      writer.u8(size); // height
      writer.u8(0); // colorCount
      writer.u8(0); // reserved
      writer.u16(1); // planes
      writer.u16(32); // bitCount
      writer.u32(entry.data.length);
      writer.u32(offset);
      offset += entry.data.length;
    }
    for (const entry of entries) writer.raw(entry.data);
    return writer.bytes;
  }

  /** 异步版本：PNG 载荷由 toPng(imageData) 提供 */
  async function encodeAsync(frames, payload, toPng) {
    if (payload === "bmp") return encode(frames, "bmp");
    const prepared = [];
    for (const frame of frames) {
      prepared.push({
        size: frame.size,
        imageData: frame.imageData,
        png: frame.png || (await toPng(frame.imageData)),
      });
    }
    return encode(prepared, "png");
  }

  /** 解析 ICO 条目（预览 / 测试用） */
  function readEntries(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (view.getUint16(0, true) !== 0 || view.getUint16(2, true) !== 1) {
      throw new Error("不是有效的 ICO 文件");
    }
    const count = view.getUint16(4, true);
    const entries = [];
    for (let i = 0; i < count; i += 1) {
      const base = 6 + i * 16;
      const width = view.getUint8(base) || 256;
      const height = view.getUint8(base + 1) || 256;
      const bytesInRes = view.getUint32(base + 8, true);
      const offset = view.getUint32(base + 12, true);
      const payload = bytes.subarray(offset, offset + bytesInRes);
      const isPng = payload.length > 8 && payload[0] === 0x89 && payload[1] === 0x50;
      entries.push({
        width,
        height,
        bytes: bytesInRes,
        offset,
        format: isPng ? "png" : "bmp",
        bitCount: view.getUint16(base + 6, true),
      });
    }
    return entries;
  }

  const api = { MAX_SIZE, normalizeSizes, buildDib, encode, encodeAsync, readEntries };
  root.IFW = Object.assign(root.IFW || {}, { ico: api });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
