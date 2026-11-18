/* ImageForge Web · TIFF 编码（未压缩 8 位 RGBA，无依赖） */
(function (root) {
  "use strict";

  const util = root.IFW || (typeof require !== "undefined" ? require("./util.js") : {});
  const ByteWriter = util.ByteWriter;

  const TAG = {
    IMAGE_WIDTH: 256,
    IMAGE_LENGTH: 257,
    BITS_PER_SAMPLE: 258,
    COMPRESSION: 259,
    PHOTOMETRIC: 262,
    STRIP_OFFSETS: 273,
    SAMPLES_PER_PIXEL: 277,
    ROWS_PER_STRIP: 278,
    STRIP_BYTE_COUNTS: 279,
    PLANAR_CONFIG: 284,
    EXTRA_SAMPLES: 338,
  };

  const TYPE_SHORT = 3;
  const TYPE_LONG = 4;

  /**
   * 未压缩 TIFF（小端，单 strip，RGBA 8 位）。
   * imageData: {width, height, data}
   */
  function encode(imageData) {
    const width = imageData.width;
    const height = imageData.height;
    const pixelBytes = width * height * 4;
    const headerBytes = 8;
    const bitsOffset = headerBytes;
    const dataOffset = bitsOffset + 8; // 4 个 SHORT
    const ifdOffset = dataOffset + pixelBytes;
    const entryCount = 11;
    const ifdBytes = 2 + entryCount * 12 + 4;
    const writer = new ByteWriter(ifdOffset + ifdBytes);

    // 文件头（小端 "II"）
    writer.u8(0x49).u8(0x49);
    writer.u16(42);
    writer.u32(ifdOffset);

    // BitsPerSample = 8,8,8,8
    writer.u16(8).u16(8).u16(8).u16(8);

    // RGBA 像素
    writer.raw(imageData.data);

    // IFD
    writer.u16(entryCount);
    const shortEntry = (tag, value) => {
      writer.u16(tag).u16(TYPE_SHORT).u32(1).u16(value).u16(0);
    };
    const longEntry = (tag, value) => {
      writer.u16(tag).u16(TYPE_LONG).u32(1).u32(value);
    };

    longEntry(TAG.IMAGE_WIDTH, width);
    longEntry(TAG.IMAGE_LENGTH, height);
    // BitsPerSample 指向数组
    writer.u16(TAG.BITS_PER_SAMPLE).u16(TYPE_SHORT).u32(4).u32(bitsOffset);
    shortEntry(TAG.COMPRESSION, 1); // 未压缩
    shortEntry(TAG.PHOTOMETRIC, 2); // RGB
    longEntry(TAG.STRIP_OFFSETS, dataOffset);
    shortEntry(TAG.SAMPLES_PER_PIXEL, 4);
    longEntry(TAG.ROWS_PER_STRIP, height);
    longEntry(TAG.STRIP_BYTE_COUNTS, pixelBytes);
    shortEntry(TAG.PLANAR_CONFIG, 1); // chunky
    shortEntry(TAG.EXTRA_SAMPLES, 2); // 未预乘 alpha
    writer.u32(0); // 下一个 IFD = 0

    return writer.bytes;
  }

  const api = { encode };
  root.IFW = Object.assign(root.IFW || {}, { tiff: api });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
