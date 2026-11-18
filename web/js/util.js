/* ImageForge Web · 通用工具（无依赖，可在浏览器与 Node 中运行） */
(function (root) {
  "use strict";

  const KB = 1024;
  const MB = KB * 1024;
  const GB = MB * 1024;

  /** 人类可读的文件大小 */
  function formatBytes(bytes) {
    const n = Number(bytes) || 0;
    if (n < KB) return n + " B";
    if (n < MB) {
      const v = n / KB;
      return (v < 10 ? v.toFixed(1) : Math.round(v)) + " KB";
    }
    if (n < GB) {
      const v = n / MB;
      return (v < 10 ? v.toFixed(1) : Math.round(v)) + " MB";
    }
    return (n / GB).toFixed(2) + " GB";
  }

  /** 去掉扩展名，替换文件系统不接受的字符 */
  function safeStem(name) {
    const base = String(name || "image").split(/[\\/]/).pop();
    const dot = base.lastIndexOf(".");
    const stem = dot > 0 ? base.slice(0, dot) : base;
    return stem.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").trim() || "image";
  }

  function extension(name) {
    const base = String(name || "").split(/[\\/]/).pop();
    const dot = base.lastIndexOf(".");
    return dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
  }

  function pad2(n) {
    return n < 10 ? "0" + n : String(n);
  }

  /** 时分秒（用于视频时长，保持与桌面版一致的格式） */
  function formatDuration(seconds) {
    const s = Math.max(0, Math.round(Number(seconds) || 0));
    const m = Math.floor(s / 60);
    return m + ":" + pad2(s % 60);
  }

  const CRC_TABLE = (function () {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i += 1) {
      let c = i;
      for (let k = 0; k < 8; k += 1) {
        c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      }
      table[i] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes, seed) {
    let c = (seed === undefined ? 0 : seed) ^ 0xffffffff;
    for (let i = 0; i < bytes.length; i += 1) {
      c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    }
    return (c ^ 0xffffffff) >>> 0;
  }

  function asciiBytes(text) {
    const out = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i += 1) out[i] = text.charCodeAt(i) & 0xff;
    return out;
  }

  function utf8Bytes(text) {
    if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text);
    return new Uint8Array(Buffer.from(text, "utf8"));
  }

  /** 把多个 Uint8Array 顺序拼接 */
  function concat(chunks) {
    let total = 0;
    for (const chunk of chunks) total += chunk.length;
    const out = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      out.set(chunk, offset);
      offset += chunk.length;
    }
    return out;
  }

  /** 小端写入器，便于构造二进制容器 */
  class ByteWriter {
    constructor(size) {
      this.bytes = new Uint8Array(size);
      this.view = new DataView(this.bytes.buffer);
      this.offset = 0;
    }
    u8(value) {
      this.view.setUint8(this.offset, value & 0xff);
      this.offset += 1;
      return this;
    }
    u16(value) {
      this.view.setUint16(this.offset, value & 0xffff, true);
      this.offset += 2;
      return this;
    }
    u32(value) {
      this.view.setUint32(this.offset, value >>> 0, true);
      this.offset += 4;
      return this;
    }
    i32(value) {
      this.view.setInt32(this.offset, value | 0, true);
      this.offset += 4;
      return this;
    }
    raw(bytes) {
      this.bytes.set(bytes, this.offset);
      this.offset += bytes.length;
      return this;
    }
  }

  function clamp(value, min, max) {
    return value < min ? min : value > max ? max : value;
  }

  /** 质量 1..100 → 0..1（有损编码用） */
  function quality01(quality) {
    return clamp(Number(quality) || 92, 1, 100) / 100;
  }

  const api = {
    formatBytes,
    formatDuration,
    safeStem,
    extension,
    pad2,
    crc32,
    asciiBytes,
    utf8Bytes,
    concat,
    ByteWriter,
    clamp,
    quality01,
  };

  root.IFW = Object.assign(root.IFW || {}, api);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
