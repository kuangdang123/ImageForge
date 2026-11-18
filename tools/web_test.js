/* ImageForge Web · 编码器离线测试（Node 运行，产物交给 Python + Pillow 校验）
 * 用法：node tools/web_test.js [outdir]
 */
"use strict";

const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const ROOT = path.resolve(__dirname, "..");
const util = require(path.join(ROOT, "web", "js", "util.js"));
const ico = require(path.join(ROOT, "web", "js", "ico.js"));
const bmp = require(path.join(ROOT, "web", "js", "bmp.js"));
const tiff = require(path.join(ROOT, "web", "js", "tiff.js"));
const zip = require(path.join(ROOT, "web", "js", "zip.js"));

const outDir = path.resolve(process.argv[2] || path.join(ROOT, "tmp", "web"));
fs.mkdirSync(outDir, { recursive: true });

/** 造一张 RGBA 图：透明底 + 红矩形 + 蓝方块（与桌面版测试一致） */
function makeImage(width, height, opts) {
  const options = opts || {};
  const data = new Uint8ClampedArray(width * height * 4);
  const inset = options.inset === undefined ? 6 : options.inset;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      const inside = x >= inset && x < width - inset && y >= inset && y < height - inset;
      if (!inside) {
        data[i] = 0;
        data[i + 1] = 0;
        data[i + 2] = 0;
        data[i + 3] = 0;
        continue;
      }
      const halfX = x >= width / 2 - width / 8 && x < width / 2 + width / 8;
      const halfY = y >= height / 2 - height / 8 && y < height / 2 + height / 8;
      if (halfX && halfY) {
        data[i] = 20;
        data[i + 1] = 120;
        data[i + 2] = 220;
      } else {
        data[i] = 220;
        data[i + 1] = 40;
        data[i + 2] = 40;
      }
      data[i + 3] = 255;
    }
  }
  return { width, height, data };
}

/** 最近邻缩放（测试用） */
function scaleImage(image, size) {
  const out = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const sx = Math.min(image.width - 1, Math.floor((x + 0.5) * (image.width / size)));
      const sy = Math.min(image.height - 1, Math.floor((y + 0.5) * (image.height / size)));
      const s = (sy * image.width + sx) * 4;
      const d = (y * size + x) * 4;
      out[d] = image.data[s];
      out[d + 1] = image.data[s + 1];
      out[d + 2] = image.data[s + 2];
      out[d + 3] = image.data[s + 3];
    }
  }
  return { width: size, height: size, data: out };
}

/** 最小 PNG 编码器（RGBA 8 位，无滤波），只用于测试 ICO 的 PNG 载荷 */
function encodePng(image) {
  const chunk = (type, payload) => {
    const typeBytes = util.asciiBytes(type);
    const out = new Uint8Array(12 + payload.length);
    const view = new DataView(out.buffer);
    view.setUint32(0, payload.length);
    out.set(typeBytes, 4);
    out.set(payload, 8);
    view.setUint32(8 + payload.length, util.crc32(util.concat([typeBytes, payload])));
    return out;
  };

  const ihdr = new Uint8Array(13);
  const ihdrView = new DataView(ihdr.buffer);
  ihdrView.setUint32(0, image.width);
  ihdrView.setUint32(4, image.height);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  const stride = image.width * 4;
  const raw = new Uint8Array((stride + 1) * image.height);
  for (let y = 0; y < image.height; y += 1) {
    raw[y * (stride + 1)] = 0; // filter: none
    raw.set(image.data.subarray(y * stride, y * stride + stride), y * (stride + 1) + 1);
  }
  const idat = zlib.deflateSync(Buffer.from(raw));

  const signature = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  return util.concat([
    signature,
    chunk("IHDR", ihdr),
    chunk("IDAT", new Uint8Array(idat)),
    chunk("IEND", new Uint8Array(0)),
  ]);
}

function write(name, bytes) {
  const file = path.join(outDir, name);
  fs.writeFileSync(file, Buffer.from(bytes));
  return { name, bytes: bytes.length, file };
}

const report = { outDir, files: [], checks: [] };

function check(name, condition, detail) {
  report.checks.push({ name, ok: !!condition, detail: detail || "" });
  if (!condition) throw new Error("检查失败: " + name + (detail ? " · " + detail : ""));
}

const source = makeImage(64, 64);

// --- ICO（BMP 载荷）---
const bmpFrames = [16, 32, 48, 256].map((size) => {
  const image = scaleImage(source, size);
  return { size, imageData: image, dib: ico.buildDib(image) };
});
const icoBmp = ico.encode(bmpFrames, "bmp");
report.files.push(write("test_bmp.ico", icoBmp));
{
  const entries = ico.readEntries(icoBmp);
  check("ICO(BMP) 条目数", entries.length === 4, JSON.stringify(entries.map((e) => e.width)));
  check("ICO(BMP) 载荷类型", entries.every((e) => e.format === "bmp"));
  check("ICO(BMP) 256 记 0", entries[3].width === 256);
  check("ICO(BMP) 位深 32", entries.every((e) => e.bitCount === 32));
}

// --- ICO（PNG 载荷）---
const pngFrames = [16, 32, 48, 256].map((size) => {
  const image = scaleImage(source, size);
  return { size, imageData: image, png: encodePng(image) };
});
const icoPng = ico.encode(pngFrames, "png");
report.files.push(write("test_png.ico", icoPng));
{
  const entries = ico.readEntries(icoPng);
  check("ICO(PNG) 条目数", entries.length === 4);
  check("ICO(PNG) 载荷类型", entries.every((e) => e.format === "png"));
  check("ICO(PNG) 文件头", icoPng[0] === 0 && icoPng[2] === 1);
}

// --- BMP 24 位 ---
const bmp24 = bmp.encode(source, { bitDepth: 24 });
report.files.push(write("test_24.bmp", bmp24));
check("BMP 文件头", bmp24[0] === 0x42 && bmp24[1] === 0x4d);
check("BMP 大小字段", new DataView(bmp24.buffer).getUint32(2, true) === bmp24.length);
check("BMP 位深", new DataView(bmp24.buffer).getUint16(28, true) === 24);

// --- TIFF ---
const tiffBytes = tiff.encode(source);
report.files.push(write("test.tiff", tiffBytes));
check("TIFF 文件头", tiffBytes[0] === 0x49 && tiffBytes[1] === 0x49);
check("TIFF 魔数 42", new DataView(tiffBytes.buffer).getUint16(2, true) === 42);

// --- ZIP ---
const zipBytes = zip.build([
  { name: "a.txt", data: util.asciiBytes("hello") },
  { name: "中文.txt", data: util.utf8Bytes("你好") },
]);
report.files.push(write("test.zip", zipBytes));
check("ZIP 结束标记", new DataView(zipBytes.buffer).getUint32(zipBytes.length - 22, true) === 0x06054b50);

fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2), "utf8");
console.log(JSON.stringify(report, null, 2));
console.log("\n全部 " + report.checks.length + " 项编码检查通过");
