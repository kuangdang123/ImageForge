/* ImageForge Web · 最小 ZIP 打包（store 存储，无依赖） */
(function (root) {
  "use strict";

  const util = root.IFW || (typeof require !== "undefined" ? require("./util.js") : {});
  const { ByteWriter, crc32, utf8Bytes } = util;

  function dosDateTime(date) {
    const d = date instanceof Date ? date : new Date();
    const time =
      ((d.getHours() & 0x1f) << 11) |
      ((d.getMinutes() & 0x3f) << 5) |
      ((d.getSeconds() / 2) & 0x1f);
    const day =
      (((d.getFullYear() - 1980) & 0x7f) << 9) |
      (((d.getMonth() + 1) & 0x0f) << 5) |
      (d.getDate() & 0x1f);
    return { time: time & 0xffff, date: day & 0xffff };
  }

  /**
   * files: [{name, data: Uint8Array, date?}]
   * 返回 Uint8Array（可直接 new Blob([...])）
   */
  function build(files) {
    const chunks = [];
    const central = [];
    let offset = 0;

    for (const file of files) {
      const name = utf8Bytes(file.name);
      const data = file.data;
      const crc = crc32(data);
      const stamp = dosDateTime(file.date);

      const local = new ByteWriter(30 + name.length);
      local.u32(0x04034b50);
      local.u16(20); // version needed
      local.u16(0x0800); // UTF-8 文件名
      local.u16(0); // store
      local.u16(stamp.time);
      local.u16(stamp.date);
      local.u32(crc);
      local.u32(data.length);
      local.u32(data.length);
      local.u16(name.length);
      local.u16(0);
      local.raw(name);

      chunks.push(local.bytes, data);

      const centralEntry = new ByteWriter(46 + name.length);
      centralEntry.u32(0x02014b50);
      centralEntry.u16(20); // version made by
      centralEntry.u16(20); // version needed
      centralEntry.u16(0x0800);
      centralEntry.u16(0);
      centralEntry.u16(stamp.time);
      centralEntry.u16(stamp.date);
      centralEntry.u32(crc);
      centralEntry.u32(data.length);
      centralEntry.u32(data.length);
      centralEntry.u16(name.length);
      centralEntry.u16(0);
      centralEntry.u16(0);
      centralEntry.u16(0);
      centralEntry.u16(0);
      centralEntry.u32(0);
      centralEntry.u32(offset);
      centralEntry.raw(name);
      central.push(centralEntry.bytes);

      offset += local.bytes.length + data.length;
    }

    const centralBytes = util.concat(central);
    const end = new ByteWriter(22);
    end.u32(0x06054b50);
    end.u16(0);
    end.u16(0);
    end.u16(files.length);
    end.u16(files.length);
    end.u32(centralBytes.length);
    end.u32(offset);
    end.u16(0);

    return util.concat([...chunks, centralBytes, end.bytes]);
  }

  const api = { build };
  root.IFW = Object.assign(root.IFW || {}, { zip: api });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
