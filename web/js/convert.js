/* ImageForge Web · 转换核心（浏览器端：解码 / 绘制 / 编码） */
(function (root) {
  "use strict";

  const util = root.IFW;
  const ico = util.ico;
  const bmp = util.bmp;
  const tiff = util.tiff;

  const FORMATS = {
    ico: {
      key: "ico",
      label: "ICO",
      ext: "ico",
      kind: "icon",
      supportsAlpha: true,
      defaultQuality: 100,
      note: "多尺寸 Windows 图标，可用作应用图标或 favicon",
    },
    png: {
      key: "png",
      label: "PNG",
      ext: "png",
      kind: "lossless",
      supportsAlpha: true,
      defaultQuality: 100,
      note: "无损压缩，支持透明",
    },
    jpg: {
      key: "jpg",
      label: "JPG",
      ext: "jpg",
      kind: "lossy",
      supportsAlpha: false,
      defaultQuality: 92,
      note: "有损压缩，体积小；透明区域会用背景色填充",
    },
    webp: {
      key: "webp",
      label: "WebP",
      ext: "webp",
      kind: "lossy",
      supportsAlpha: true,
      defaultQuality: 88,
      note: "高压缩，支持透明",
    },
    bmp: {
      key: "bmp",
      label: "BMP",
      ext: "bmp",
      kind: "lossless",
      supportsAlpha: false,
      defaultQuality: 100,
      note: "无压缩位图，兼容老系统",
    },
    tiff: {
      key: "tiff",
      label: "TIFF",
      ext: "tiff",
      kind: "lossless",
      supportsAlpha: true,
      defaultQuality: 100,
      note: "印刷常用，未压缩保存",
    },
    avif: {
      key: "avif",
      label: "AVIF",
      ext: "avif",
      kind: "lossy",
      supportsAlpha: true,
      defaultQuality: 62,
      note: "新一代压缩；是否可用取决于浏览器",
    },
  };

  const FORMAT_ORDER = ["ico", "png", "jpg", "webp", "bmp", "tiff", "avif"];
  const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256];
  const INPUT_ACCEPT = [
    ".png", ".jpg", ".jpeg", ".jfif", ".jpe", ".webp", ".bmp", ".dib",
    ".gif", ".tif", ".tiff", ".ico", ".avif", ".tga", ".ppm", ".pgm", ".pbm",
  ];

  const support = { webp: false, avif: false, ico: true };

  function detectSupport() {
    if (typeof document === "undefined") return support;
    const canvas = document.createElement("canvas");
    canvas.width = 4;
    canvas.height = 4;
    const canEncode = (type) => {
      try {
        return canvas.toDataURL(type).startsWith("data:" + type);
      } catch (error) {
        return false;
      }
    };
    support.webp = canEncode("image/webp");
    support.avif = canEncode("image/avif");
    return support;
  }

  function isSupportedTarget(key) {
    if (key === "webp") return support.webp;
    if (key === "avif") return support.avif;
    return !!FORMATS[key];
  }

  /** 解码：优先 createImageBitmap（自带 EXIF 方向），失败回退 <img> */
  async function loadBitmap(file) {
    if (typeof createImageBitmap === "function") {
      try {
        return await createImageBitmap(file, { imageOrientation: "from-image" });
      } catch (error) {
        /* 回退 */
      }
    }
    const url = URL.createObjectURL(file);
    try {
      const image = new Image();
      image.decoding = "async";
      await new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = () => reject(new Error("无法解码图片（可能已损坏）"));
        image.src = url;
      });
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth || image.width;
      canvas.height = image.naturalHeight || image.height;
      canvas.getContext("2d").drawImage(image, 0, 0);
      return canvas;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function sourceSize(bitmap) {
    const width = bitmap.width || bitmap.naturalWidth || 0;
    const height = bitmap.height || bitmap.naturalHeight || 0;
    return { width, height };
  }

  /** 把源图绘制到指定画布尺寸；fit = contain | fill | stretch */
  function render(bitmap, size, fit, background) {
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    if (background) {
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, size, size);
    }
    const { width: sw, height: sh } = sourceSize(bitmap);
    if (!sw || !sh) return canvas;

    if (fit === "stretch") {
      ctx.drawImage(bitmap, 0, 0, size, size);
      return canvas;
    }
    const scale = fit === "fill" ? Math.max(size / sw, size / sh) : Math.min(size / sw, size / sh);
    const dw = Math.max(1, Math.round(sw * scale));
    const dh = Math.max(1, Math.round(sh * scale));
    // 高倍降采样时分步缩放，减少锯齿
    const steps = Math.max(0, Math.ceil(Math.log2(scale < 1 ? 1 / scale : 1)) - 1);
    if (steps >= 2) {
      let current = bitmap;
      let cw = sw;
      let ch = sh;
      for (let i = 0; i < steps; i += 1) {
        cw = Math.max(1, Math.round(cw / 2));
        ch = Math.max(1, Math.round(ch / 2));
        const step = document.createElement("canvas");
        step.width = cw;
        step.height = ch;
        step.getContext("2d").drawImage(current, 0, 0, cw, ch);
        current = step;
      }
      ctx.drawImage(current, Math.round((size - dw) / 2), Math.round((size - dh) / 2), dw, dh);
      return canvas;
    }
    ctx.drawImage(bitmap, Math.round((size - dw) / 2), Math.round((size - dh) / 2), dw, dh);
    return canvas;
  }

  /** 把非正方形图按 contain 绘制到 w×h（用于目标尺寸模式） */
  function renderRect(bitmap, w, h, background) {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(w));
    canvas.height = Math.max(1, Math.round(h));
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    if (background) {
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    const { width: sw, height: sh } = sourceSize(bitmap);
    const scale = Math.min(canvas.width / sw, canvas.height / sh);
    const dw = Math.round(sw * scale);
    const dh = Math.round(sh * scale);
    ctx.drawImage(bitmap, Math.round((canvas.width - dw) / 2), Math.round((canvas.height - dh) / 2), dw, dh);
    return canvas;
  }

  function canvasToBytes(canvas, type, quality) {
    return new Promise((resolve, reject) => {
      canvas.toBlob(
        (blob) => {
          if (!blob) {
            reject(new Error("浏览器不支持导出该格式"));
            return;
          }
          blob.arrayBuffer().then((buffer) => resolve(new Uint8Array(buffer))).catch(reject);
        },
        type,
        quality
      );
    });
  }

  function hasTransparency(canvas) {
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 3; i < data.length; i += 4 * 7) {
      if (data[i] < 255) return true;
    }
    return false;
  }

  /** 输出文件名后缀：与桌面版一致，把关键参数写进文件名 */
  function autoSuffix(options) {
    const spec = FORMATS[options.target];
    if (!spec) return "";
    if (spec.key === "ico") {
      const sizes = ico.normalizeSizes(options.icoSizes || ICO_SIZES);
      const max = sizes.length ? sizes[sizes.length - 1] : 256;
      let suffix = "_ico_" + max + "px";
      if (options.icoFit === "fill") suffix += "_fill";
      else if (options.icoFit === "stretch") suffix += "_stretch";
      if (options.icoPayload === "bmp") suffix += "_bmp";
      return suffix;
    }
    const quality = Math.round(options.quality || spec.defaultQuality);
    if (spec.key === "webp") {
      return options.lossless ? "_webp_lossless" : "_webp_q" + quality;
    }
    if (spec.kind === "lossy") return "_" + spec.key + "_q" + quality;
    return "_" + spec.key;
  }

  function outputName(item, options) {
    const stem = util.safeStem(item.name);
    const suffix = (options.suffix || "").trim();
    const spec = FORMATS[options.target];
    return stem + suffix + autoSuffix(options) + "." + spec.ext;
  }

  /**
   * 转换一张图片。
   * 返回 {ok, blob, filename, width, height, bytes, warnings}
   */
  async function convert(item, options) {
    const spec = FORMATS[options.target];
    if (!spec) throw new Error("不支持的输出格式");
    if (!isSupportedTarget(spec.key)) {
      throw new Error("当前浏览器不支持导出 " + spec.label + "，请换用其它格式");
    }
    const warnings = [];
    const bitmap = item.bitmap || (await loadBitmap(item.file));
    const { width, height } = sourceSize(bitmap);
    const quality = util.quality01(options.quality || spec.defaultQuality);
    const background = options.background || "#ffffff";
    let blob;
    let outWidth = width;
    let outHeight = height;

    if (spec.key === "ico") {
      const sizes = ico.normalizeSizes(options.icoSizes || ICO_SIZES);
      if (!sizes.length) throw new Error("请至少勾选一个图标尺寸");
      const frames = [];
      for (const size of sizes) {
        const canvas = render(bitmap, size, options.icoFit || "contain", null);
        const imageData = canvas
          .getContext("2d", { willReadFrequently: true })
          .getImageData(0, 0, size, size);
        frames.push({ size, imageData, canvas });
      }
      const bytes = await ico.encodeAsync(
        frames,
        options.icoPayload === "bmp" ? "bmp" : "png",
        (imageData) => {
          const canvas = document.createElement("canvas");
          canvas.width = imageData.width;
          canvas.height = imageData.height;
          canvas.getContext("2d").putImageData(imageData, 0, 0);
          return canvasToBytes(canvas, "image/png");
        }
      );
      blob = new Blob([bytes], { type: "image/x-icon" });
      outWidth = outHeight = sizes[sizes.length - 1];
    } else if (spec.key === "bmp") {
      const transparent = hasTransparency(render(bitmap, Math.min(width, 512), "contain", null));
      const canvas = render(bitmap, Math.min(Math.max(width, height), 8192), "contain", transparent ? null : background);
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      // 直接用原图尺寸绘制，避免 contain 留白
      const full = document.createElement("canvas");
      full.width = width;
      full.height = height;
      const fctx = full.getContext("2d", { willReadFrequently: true });
      fctx.fillStyle = background;
      fctx.fillRect(0, 0, width, height);
      fctx.drawImage(bitmap, 0, 0);
      const imageData = fctx.getImageData(0, 0, width, height);
      const bytes = bmp.encode(imageData, { bitDepth: 24 });
      blob = new Blob([bytes], { type: "image/bmp" });
      void ctx;
    } else if (spec.key === "tiff") {
      const full = document.createElement("canvas");
      full.width = width;
      full.height = height;
      full.getContext("2d").drawImage(bitmap, 0, 0);
      const imageData = full
        .getContext("2d", { willReadFrequently: true })
        .getImageData(0, 0, width, height);
      const bytes = tiff.encode(imageData);
      blob = new Blob([bytes], { type: "image/tiff" });
    } else if (spec.key === "jpg") {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(bitmap, 0, 0);
      const bytes = await canvasToBytes(canvas, "image/jpeg", quality);
      blob = new Blob([bytes], { type: "image/jpeg" });
    } else {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0);
      const type = spec.key === "webp" ? "image/webp" : "image/avif";
      const bytes = await canvasToBytes(canvas, type, quality);
      blob = new Blob([bytes], { type: type });
    }

    if (spec.key === "jpg" && item.hasAlpha) {
      warnings.push("透明区域已用背景色填充");
    }
    return {
      ok: true,
      blob,
      filename: outputName(item, options),
      width: outWidth,
      height: outHeight,
      bytes: blob.size,
      warnings,
    };
  }

  /** 生成队列缩略图（40px 棋盘格底） */
  async function thumbnail(file, size) {
    const pad = size || 40;
    const bitmap = await loadBitmap(file);
    const canvas = document.createElement("canvas");
    canvas.width = pad;
    canvas.height = pad;
    const ctx = canvas.getContext("2d");
    const cell = 5;
    for (let y = 0; y < pad; y += cell) {
      for (let x = 0; x < pad; x += cell) {
        ctx.fillStyle = (x / cell + y / cell) % 2 === 0 ? "#ffffff" : "#e9ecf2";
        ctx.fillRect(x, y, cell, cell);
      }
    }
    const { width: sw, height: sh } = sourceSize(bitmap);
    const scale = Math.min(pad / sw, pad / sh);
    const dw = Math.max(1, Math.round(sw * scale));
    const dh = Math.max(1, Math.round(sh * scale));
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bitmap, Math.round((pad - dw) / 2), Math.round((pad - dh) / 2), dw, dh);
    return canvas;
  }

  const api = {
    FORMATS,
    FORMAT_ORDER,
    ICO_SIZES,
    INPUT_ACCEPT,
    support,
    detectSupport,
    isSupportedTarget,
    loadBitmap,
    sourceSize,
    render,
    renderRect,
    canvasToBytes,
    autoSuffix,
    outputName,
    convert,
    thumbnail,
  };

  root.IFW = Object.assign(root.IFW || {}, { convert: api });
})(typeof globalThis !== "undefined" ? globalThis : window);
