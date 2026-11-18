/* ImageForge Web · BMP 编码（24/32 位，无依赖） */
(function (root) {
  "use strict";

  const util = root.IFW || (typeof require !== "undefined" ? require("./util.js") : {});
  const ByteWriter = util.ByteWriter;

  /**
   * 编码为 BMP。
   * imageData: {width, height, data}
   * options: {bitDepth: 24 | 32}
   */
  function encode(imageData, options) {
    const opts = options || {};
    const width = imageData.width;
    const height = imageData.height;
    const src = imageData.data;
    const bitDepth = opts.bitDepth === 24 ? 24 : 32;
    const bytesPerPixel = bitDepth / 8;
    const stride = Math.ceil((width * bytesPerPixel) / 4) * 4;
    const pixelBytes = stride * height;
    const offset = 14 + 40;
    const writer = new ByteWriter(offset + pixelBytes);

    // BITMAPFILEHEADER
    writer.u8(0x42).u8(0x4d); // "BM"
    writer.u32(offset + pixelBytes); // bfSize
    writer.u16(0); // bfReserved1
    writer.u16(0); // bfReserved2
    writer.u32(offset); // bfOffBits

    // BITMAPINFOHEADER
    writer.u32(40);
    writer.i32(width);
    writer.i32(height);
    writer.u16(1);
    writer.u16(bitDepth);
    writer.u32(0); // BI_RGB
    writer.u32(pixelBytes);
    writer.i32(2835); // 72 DPI
    writer.i32(2835);
    writer.u32(0);
    writer.u32(0);

    // 自底向上，BGR(A)
    for (let y = height - 1; y >= 0; y -= 1) {
      const rowStart = writer.offset;
      let index = y * width * 4;
      for (let x = 0; x < width; x += 1) {
        writer.u8(src[index + 2]);
        writer.u8(src[index + 1]);
        writer.u8(src[index + 0]);
        if (bitDepth === 32) writer.u8(src[index + 3]);
        index += 4;
      }
      // 行填充
      const padding = stride - (writer.offset - rowStart);
      for (let p = 0; p < padding; p += 1) writer.u8(0);
    }
    return writer.bytes;
  }

  const api = { encode };
  root.IFW = Object.assign(root.IFW || {}, { bmp: api });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
