/* ImageForge Web · 界面逻辑（纯前端，文件不会离开本机） */
(function () {
  "use strict";

  const { convert, ico, zip, util } = (function () {
    const api = window.IFW;
    return { convert: api.convert, ico: api.ico, zip: api.zip, util: api };
  })();
  const { FORMATS, FORMAT_ORDER } = convert;

  const STORAGE_KEY = "imageforge.web.settings.v1";
  const THEME_KEY = "imageforge.web.theme";

  const DEFAULT_OPTIONS = {
    target: "ico",
    quality: 92,
    icoSizes: [16, 32, 48, 256],
    icoFit: "contain",
    icoPayload: "png",
    background: "#ffffff",
    suffix: "",
    lossless: false,
  };

  const FIT_OPTIONS = [
    { key: "contain", label: "留白" },
    { key: "fill", label: "填充裁切" },
    { key: "stretch", label: "拉伸" },
  ];

  const PAYLOAD_OPTIONS = [
    { key: "png", label: "PNG 压缩" },
    { key: "bmp", label: "BMP 兼容" },
  ];

  const STATUS_TEXT = {
    reading: "读取中…",
    idle: "就绪",
    running: "转换中",
    done: "完成",
    failed: "失败",
  };

  const state = {
    items: [],
    options: Object.assign({}, DEFAULT_OPTIONS),
    converting: false,
    cancelled: false,
    selectedId: null,
    nextId: 1,
    lastPreviewId: null,
    toastTimer: 0,
  };

  const el = {};

  /* ---------- 工具 ---------- */

  function $(id) {
    return document.getElementById(id);
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function toast(message, kind) {
    const node = el.toast;
    node.textContent = message;
    node.dataset.kind = kind || "info";
    node.hidden = false;
    requestAnimationFrame(() => node.classList.add("is-visible"));
    clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => {
      node.classList.remove("is-visible");
      window.setTimeout(() => {
        node.hidden = true;
      }, 200);
    }, 2600);
  }

  function saveOptions() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.options));
    } catch (error) {
      /* 隐私模式可能禁用 localStorage */
    }
  }

  function loadOptions() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved && typeof saved === "object") {
        state.options = Object.assign({}, DEFAULT_OPTIONS, saved);
        if (!Array.isArray(state.options.icoSizes)) state.options.icoSizes = DEFAULT_OPTIONS.icoSizes.slice();
      }
    } catch (error) {
      state.options = Object.assign({}, DEFAULT_OPTIONS);
    }
  }

  /* ---------- 主题 ---------- */

  function currentTheme() {
    return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  }

  function applyTheme(theme) {
    const next = theme === "dark" ? "dark" : theme === "light" ? "light" : "auto";
    if (next === "auto") {
      const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    } else {
      document.documentElement.dataset.theme = next;
    }
    el.themeToggle.setAttribute("aria-label", currentTheme() === "dark" ? "切换到浅色主题" : "切换到深色主题");
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch (error) {
      /* 忽略 */
    }
  }

  function initTheme() {
    let stored = null;
    try {
      stored = localStorage.getItem(THEME_KEY);
    } catch (error) {
      stored = null;
    }
    const params = new URLSearchParams(location.search);
    const fromQuery = params.get("theme");
    applyTheme(fromQuery || stored || "auto");
  }

  /* ---------- 设置面板 ---------- */

  function buildChipGrid(container, options, value, onPick, columns) {
    container.textContent = "";
    if (columns) container.style.gridTemplateColumns = "repeat(" + columns + ", minmax(0, 1fr))";
    options.forEach((option) => {
      const chip = make("button", "chip", option.label);
      chip.type = "button";
      chip.dataset.key = option.key;
      chip.setAttribute("role", "radio");
      chip.setAttribute("aria-checked", String(option.key === value));
      if (option.disabled) {
        chip.disabled = true;
        chip.setAttribute("aria-disabled", "true");
      }
      chip.addEventListener("click", () => {
        if (chip.disabled) return;
        onPick(option.key);
      });
      container.appendChild(chip);
    });
  }

  function buildFormatGrid() {
    const options = FORMAT_ORDER.map((key) => {
      const spec = FORMATS[key];
      const supported = convert.isSupportedTarget(key);
      return { key, label: spec.label, disabled: !supported };
    });
    buildChipGrid(el.formatGrid, options, state.options.target, (key) => {
      state.options.target = key;
      saveOptions();
      syncOptionsUI();
    });
  }

  function buildSizeGrid() {
    el.sizeGrid.textContent = "";
    convert.ICO_SIZES.forEach((size) => {
      const label = make("label", "check-item");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = String(size);
      input.checked = state.options.icoSizes.includes(size);
      input.addEventListener("change", () => {
        const set = new Set(state.options.icoSizes);
        if (input.checked) set.add(size);
        else set.delete(size);
        state.options.icoSizes = Array.from(set).sort((a, b) => a - b);
        saveOptions();
        if (!state.options.icoSizes.length) {
          input.checked = true;
          state.options.icoSizes = [size];
          toast("至少保留一个图标尺寸", "warning");
        }
        syncNamePreview();
      });
      const text = make("span", null, size + "px");
      label.append(input, text);
      el.sizeGrid.appendChild(label);
    });
  }

  function buildFitGrid() {
    buildChipGrid(el.fitGrid, FIT_OPTIONS, state.options.icoFit, (key) => {
      state.options.icoFit = key;
      saveOptions();
      syncOptionsUI();
    });
  }

  function buildPayloadGrid() {
    buildChipGrid(el.payloadGrid, PAYLOAD_OPTIONS, state.options.icoPayload, (key) => {
      state.options.icoPayload = key;
      saveOptions();
      syncOptionsUI();
    });
  }

  function setChipValue(container, value) {
    container.querySelectorAll(".chip").forEach((chip) => {
      const checked = chip.dataset.key === value;
      chip.setAttribute("aria-checked", String(checked));
      chip.tabIndex = checked ? 0 : -1;
    });
  }

  function syncOptionsUI() {
    const spec = FORMATS[state.options.target];
    buildFormatGrid();
    setChipValue(el.fitGrid, state.options.icoFit);
    setChipValue(el.payloadGrid, state.options.icoPayload);

    const isIco = state.options.target === "ico";
    const isLossy = spec.kind === "lossy";
    el.icoGroup.hidden = !isIco;
    el.qualityGroup.hidden = !(isLossy && !(state.options.target === "webp" && state.options.lossless));
    el.bgGroup.hidden = !(state.options.target === "jpg" || state.options.target === "bmp");
    el.losslessRow.hidden = state.options.target !== "webp";

    el.quality.value = String(state.options.quality);
    el.qualityValue.textContent = String(state.options.quality);
    el.bgColor.value = state.options.background;
    el.suffix.value = state.options.suffix;
    el.lossless.checked = !!state.options.lossless;
    el.formatNote.textContent = spec.note + (spec.key === "ico" ? "" : " · 输出 ." + spec.ext);

    const unsupported = FORMAT_ORDER.filter((key) => !convert.isSupportedTarget(key)).map((key) => FORMATS[key].label);
    el.supportNote.textContent = unsupported.length ? "本浏览器不支持：" + unsupported.join(" / ") : "";
    syncNamePreview();
  }

  function syncNamePreview() {
    const sample = state.items[0];
    const stem = util.safeStem(sample ? sample.file.name : "photo.jpg");
    const suffix = (state.options.suffix || "").trim();
    const name = stem + suffix + convert.autoSuffix(state.options) + "." + FORMATS[state.options.target].ext;
    el.namePreview.textContent = "命名示例：" + name;
  }

  /* ---------- 队列 ---------- */

  function renderQueue() {
    el.queue.textContent = "";
    const hasItems = state.items.length > 0;
    el.dropZone.hidden = hasItems;
    el.queue.hidden = !hasItems;
    el.queueCount.textContent = String(state.items.length);
    el.clear.disabled = !hasItems || state.converting;

    for (const item of state.items) {
      const row = make("li", "queue-item");
      row.dataset.id = String(item.id);
      row.tabIndex = 0;
      if (item.id === state.selectedId) row.classList.add("is-selected");

      const thumb = make("span", "thumb");
      if (item.thumbUrl) {
        const img = document.createElement("img");
        img.alt = "";
        img.src = item.thumbUrl;
        thumb.appendChild(img);
      }
      const main = make("div", "queue-main");
      const name = make("p", "queue-name", item.file.name);
      name.title = item.file.name;
      const meta = make("p", "queue-meta", item.meta || "读取中…");
      main.append(name, meta);

      const status = make("span", "queue-status");
      status.dataset.state = item.status;
      status.textContent = item.status === "failed" && item.message ? "失败" : STATUS_TEXT[item.status] || "就绪";
      if (item.status === "failed" && item.message) status.title = item.message;

      const remove = make("button", "queue-remove");
      remove.type = "button";
      remove.title = "移除";
      remove.setAttribute("aria-label", "移除 " + item.file.name);
      remove.innerHTML = '<svg width="14" height="14" aria-hidden="true"><use href="#i-close"/></svg>';
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeItem(item.id);
      });

      row.append(thumb, main, status, remove);
      row.addEventListener("click", () => selectItem(item.id));
      row.addEventListener("dblclick", () => openPreview(item.id));
      el.queue.appendChild(row);
    }
    updateFooter();
  }

  function patchRow(item) {
    const row = el.queue.querySelector('[data-id="' + item.id + '"]');
    if (!row) {
      renderQueue();
      return;
    }
    const meta = row.querySelector(".queue-meta");
    if (meta) meta.textContent = item.meta || "读取中…";
    const status = row.querySelector(".queue-status");
    if (status) {
      status.dataset.state = item.status;
      status.textContent = STATUS_TEXT[item.status] || "就绪";
      if (item.status === "failed" && item.message) status.title = item.message;
    }
    const thumb = row.querySelector(".thumb");
    if (thumb && item.thumbUrl && !thumb.firstChild) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = item.thumbUrl;
      thumb.appendChild(img);
    }
    row.classList.toggle("is-done", item.status === "done");
    updateFooter();
  }

  function selectItem(id) {
    state.selectedId = id;
    el.queue.querySelectorAll(".queue-item").forEach((row) => {
      row.classList.toggle("is-selected", Number(row.dataset.id) === id);
    });
  }

  function removeItem(id) {
    if (state.converting) return;
    const index = state.items.findIndex((item) => item.id === id);
    if (index < 0) return;
    const [removed] = state.items.splice(index, 1);
    if (removed.thumbUrl) URL.revokeObjectURL(removed.thumbUrl);
    if (state.selectedId === id) state.selectedId = null;
    renderQueue();
  }

  function clearItems() {
    if (state.converting) return;
    state.items.forEach((item) => item.thumbUrl && URL.revokeObjectURL(item.thumbUrl));
    state.items = [];
    state.selectedId = null;
    renderQueue();
    setStatus("拖入图片，或按 Ctrl+O 添加");
  }

  function updateFooter() {
    const total = state.items.length;
    const done = state.items.filter((item) => item.status === "done").length;
    const failed = state.items.filter((item) => item.status === "failed").length;
    const pending = state.items.filter((item) => item.status === "idle" || item.status === "failed" || item.status === "done").length;

    el.start.disabled = state.converting || !pending;
    el.start.textContent = pending ? "开始转换 · " + pending : "开始转换";
    el.cancel.disabled = !state.converting;
    el.downloadAll.disabled = state.converting || done === 0;

    if (state.converting) return;
    if (!total) {
      setStatus("拖入图片，或按 Ctrl+O 添加");
    } else if (failed) {
      setStatus("共 " + total + " 张 · " + done + " 张完成 · " + failed + " 张失败");
    } else if (done === total) {
      setStatus("全部完成 · 共 " + total + " 张");
    } else if (!done && !failed) {
      setStatus("共 " + total + " 张 · 就绪");
    } else {
      setStatus("共 " + total + " 张 · 待转换 " + pending + " 张");
    }
  }

  function setStatus(text) {
    el.status.textContent = text;
  }

  function setProgress(ratio) {
    const percent = Math.max(0, Math.min(1, ratio)) * 100;
    el.progressBar.style.width = percent.toFixed(1) + "%";
    el.progress.setAttribute("aria-valuenow", String(Math.round(percent)));
    el.progress.classList.remove("is-indeterminate");
  }

  /* ---------- 添加文件 ---------- */

  function isImageFile(file) {
    const ext = util.extension(file.name);
    if (convert.INPUT_ACCEPT.includes("." + ext)) return true;
    return file.type.startsWith("image/");
  }

  async function addFiles(fileList) {
    const files = Array.from(fileList || []).filter(isImageFile);
    if (!files.length) {
      toast("没有可处理的图片文件", "warning");
      return;
    }
    const added = [];
    for (const file of files) {
      const item = {
        id: state.nextId++,
        file,
        status: "reading",
        meta: "",
        thumbUrl: "",
        bitmap: null,
        result: null,
        message: "",
        hasAlpha: false,
      };
      state.items.push(item);
      added.push(item);
    }
    renderQueue();

    for (const item of added) {
      try {
        const bitmap = await convert.loadBitmap(item.file);
        item.bitmap = bitmap;
        const { width, height } = convert.sourceSize(bitmap);
        const probe = document.createElement("canvas");
        const scale = Math.min(1, 256 / Math.max(width, height));
        probe.width = Math.max(1, Math.round(width * scale));
        probe.height = Math.max(1, Math.round(height * scale));
        const ctx = probe.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(bitmap, 0, 0, probe.width, probe.height);
        const data = ctx.getImageData(0, 0, probe.width, probe.height).data;
        let alpha = false;
        for (let i = 3; i < data.length; i += 4 * 5) {
          if (data[i] < 250) {
            alpha = true;
            break;
          }
        }
        item.hasAlpha = alpha;
        const thumb = await convert.thumbnail(item.file, 40);
        item.thumbUrl = thumb.toDataURL("image/png");
        item.meta = [width + "×" + height, util.extension(item.file.name).toUpperCase(), util.formatBytes(item.file.size), alpha ? "透明" : ""]
          .filter(Boolean)
          .join(" · ");
        item.status = "idle";
      } catch (error) {
        item.status = "failed";
        item.message = (error && error.message) || "无法读取这张图片";
        item.meta = item.message;
      }
      patchRow(item);
    }
    syncNamePreview();
  }

  /* ---------- 转换 ---------- */

  function targets() {
    return state.items.filter((item) => item.status === "idle" || item.status === "failed" || item.status === "done");
  }

  async function startConversion() {
    if (state.converting) return;
    const queue = targets();
    if (!queue.length) {
      toast("先添加需要转换的图片", "warning");
      return;
    }
    if (!state.options.icoSizes.length && state.options.target === "ico") {
      toast("至少勾选一个图标尺寸", "warning");
      return;
    }
    state.converting = true;
    state.cancelled = false;
    el.start.disabled = true;
    el.cancel.disabled = false;
    el.clear.disabled = true;
    document.querySelectorAll(".settings-panel input, .settings-panel .chip, .toolbar .btn").forEach((node) => {
      node.disabled = true;
    });

    let done = 0;
    let failed = 0;
    for (const item of queue) {
      if (state.cancelled) break;
      item.status = "running";
      patchRow(item);
      setProgress(done / queue.length);
      setStatus("正在转换 " + (done + 1) + "/" + queue.length + " · " + Math.round((done / queue.length) * 100) + "% · " + item.file.name);
      try {
        const result = await convert.convert(item, state.options);
        item.result = result;
        item.status = "done";
        item.meta = result.width + "×" + result.height + " · " + util.formatBytes(result.bytes) + " · " + FORMATS[state.options.target].label;
        if (result.warnings && result.warnings.length) item.message = result.warnings.join("；");
        done += 1;
      } catch (error) {
        item.status = "failed";
        item.message = (error && error.message) || "转换失败";
        item.meta = item.message;
        failed += 1;
      }
      patchRow(item);
    }

    const cancelled = state.cancelled;
    state.converting = false;
    state.cancelled = false;
    document.querySelectorAll(".settings-panel input, .settings-panel .chip, .toolbar .btn").forEach((node) => {
      node.disabled = false;
    });
    el.clear.disabled = !state.items.length;
    el.cancel.disabled = true;
    syncOptionsUI();
    updateFooter();
    if (cancelled) {
      setStatus("已取消 · 完成 " + done + " 张");
      setProgress(0);
      toast("已取消转换", "warning");
    } else if (failed) {
      setProgress(1);
      setStatus("完成 " + done + " 张 · 失败 " + failed + " 张");
      toast("完成 " + done + " 张，失败 " + failed + " 张", "error");
    } else {
      setProgress(1);
      setStatus("全部完成 · 共 " + done + " 张");
      toast("已完成 " + done + " 张", "success");
    }
  }

  function cancelConversion() {
    if (!state.converting) return;
    state.cancelled = true;
    el.cancel.disabled = true;
    setStatus("正在取消…");
  }

  /* ---------- 下载 ---------- */

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  function downloadItem(item) {
    if (!item.result) {
      toast("这张还没有转换结果", "warning");
      return;
    }
    downloadBlob(item.result.blob, item.result.filename);
    toast("已保存 " + item.result.filename, "success");
  }

  function downloadAll() {
    const done = state.items.filter((item) => item.status === "done" && item.result);
    if (!done.length) return;
    if (done.length === 1) {
      downloadItem(done[0]);
      return;
    }
    (async () => {
      const files = [];
      for (const item of done) {
        const buffer = new Uint8Array(await item.result.blob.arrayBuffer());
        files.push({ name: item.result.filename, data: buffer });
      }
      const bytes = zip.build(files);
      const blob = new Blob([bytes], { type: "application/zip" });
      const stamp = new Date();
      const name =
        "ImageForge_" +
        stamp.getFullYear() +
        util.pad2(stamp.getMonth() + 1) +
        util.pad2(stamp.getDate()) +
        "_" +
        util.pad2(stamp.getHours()) +
        util.pad2(stamp.getMinutes()) +
        ".zip";
      downloadBlob(blob, name);
      toast("已打包 " + done.length + " 个文件", "success");
    })().catch((error) => toast("打包失败：" + error.message, "error"));
  }

  /* ---------- 预览 ---------- */

  function decodeDib(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const headerSize = view.getUint32(0, true);
    const width = view.getInt32(4, true);
    const height = Math.abs(view.getInt32(8, true)) / 2;
    const bitCount = view.getUint16(14, true);
    if (headerSize < 40 || bitCount !== 32) throw new Error("不支持的 BMP 像素数据");
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    const imageData = ctx.createImageData(width, height);
    const pixels = bytes.subarray(headerSize, headerSize + width * height * 4);
    for (let y = 0; y < height; y += 1) {
      const srcRow = (height - 1 - y) * width * 4;
      const dstRow = y * width * 4;
      for (let x = 0; x < width; x += 1) {
        const s = srcRow + x * 4;
        const d = dstRow + x * 4;
        imageData.data[d] = pixels[s + 2];
        imageData.data[d + 1] = pixels[s + 1];
        imageData.data[d + 2] = pixels[s];
        imageData.data[d + 3] = pixels[s + 3];
      }
    }
    ctx.putImageData(imageData, 0, 0);
    return canvas;
  }

  async function icoFrames(bytes) {
    const entries = ico.readEntries(bytes);
    const frames = [];
    for (const entry of entries) {
      const payload = bytes.subarray(entry.offset, entry.offset + entry.bytes);
      if (entry.format === "png") {
        const bitmap = await createImageBitmap(new Blob([payload], { type: "image/png" }));
        const canvas = document.createElement("canvas");
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        canvas.getContext("2d").drawImage(bitmap, 0, 0);
        frames.push({ size: entry.width, canvas });
      } else {
        const canvas = decodeDib(payload);
        frames.push({ size: entry.width, canvas });
      }
    }
    return frames;
  }

  async function openPreview(id) {
    const item = state.items.find((entry) => entry.id === id);
    if (!item) return;
    state.lastPreviewId = id;
    el.previewTitle.textContent = item.file.name;
    el.previewPath.textContent = item.result ? "输出：" + item.result.filename : "尚未转换";
    el.previewMeta.textContent =
      (item.meta || "") + (item.message ? " · " + item.message : "");
    el.previewSizes.textContent = "";
    el.previewDownload.hidden = !item.result;
    el.preview.hidden = false;
    el.previewClose.focus();

    const canvas = el.previewCanvas;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    try {
      const isIco = item.result && item.result.filename.toLowerCase().endsWith(".ico");
      if (isIco) {
        const bytes = new Uint8Array(await item.result.blob.arrayBuffer());
        const frames = await icoFrames(bytes);
        frames.sort((a, b) => b.size - a.size);
        const largest = frames[0];
        const scale = Math.min(canvas.width / largest.canvas.width, canvas.height / largest.canvas.height, 1);
        ctx.imageSmoothingEnabled = scale < 1;
        ctx.drawImage(
          largest.canvas,
          Math.round((canvas.width - largest.canvas.width * scale) / 2),
          Math.round((canvas.height - largest.canvas.height * scale) / 2),
          Math.round(largest.canvas.width * scale),
          Math.round(largest.canvas.height * scale)
        );
        frames
          .sort((a, b) => a.size - b.size)
          .forEach((frame) => {
            const wrap = make("div", "preview-size");
            const box = document.createElement("canvas");
            const cell = frame.size <= 32 ? 64 : frame.size <= 64 ? 72 : 88;
            box.width = cell;
            box.height = cell;
            const boxCtx = box.getContext("2d");
            const inner = Math.min(cell - 6, Math.max(16, frame.size));
            boxCtx.imageSmoothingEnabled = inner < frame.size;
            boxCtx.drawImage(frame.canvas, (cell - inner) / 2, (cell - inner) / 2, inner, inner);
            wrap.append(box, make("span", null, frame.size + "px"));
            el.previewSizes.appendChild(wrap);
          });
      } else {
        const blob = item.result ? item.result.blob : item.file;
        const bitmap = await createImageBitmap(blob);
        const scale = Math.min(canvas.width / bitmap.width, canvas.height / bitmap.height, 1);
        ctx.imageSmoothingEnabled = scale < 1;
        ctx.drawImage(
          bitmap,
          Math.round((canvas.width - bitmap.width * scale) / 2),
          Math.round((canvas.height - bitmap.height * scale) / 2),
          Math.round(bitmap.width * scale),
          Math.round(bitmap.height * scale)
        );
      }
    } catch (error) {
      el.previewMeta.textContent = "预览失败：" + error.message;
    }
  }

  function closePreview() {
    el.preview.hidden = true;
    state.lastPreviewId = null;
  }

  /* ---------- 拖放 ---------- */

  function readEntries(items) {
    const files = [];
    const walk = (entry) =>
      new Promise((resolve) => {
        if (entry.isFile) {
          entry.file(
            (file) => {
              files.push(file);
              resolve();
            },
            () => resolve()
          );
        } else if (entry.isDirectory) {
          const reader = entry.createReader();
          const readBatch = () => {
            reader.readEntries(
              async (batch) => {
                if (!batch.length) {
                  resolve();
                  return;
                }
                for (const child of batch) await walk(child);
                readBatch();
              },
              () => resolve()
            );
          };
          readBatch();
        } else {
          resolve();
        }
      });
    return (async () => {
      for (const item of items) {
        const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
        if (entry) {
          await walk(entry);
        } else if (item.getAsFile) {
          const file = item.getAsFile();
          if (file) files.push(file);
        }
      }
      return files;
    })();
  }

  function bindDropZone() {
    let depth = 0;
    const zone = el.dropZone;
    const over = (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    };
    ["dragenter"].forEach((type) =>
      document.addEventListener(type, (event) => {
        if (!event.dataTransfer || !Array.from(event.dataTransfer.types || []).includes("Files")) return;
        depth += 1;
        zone.classList.add("is-dragging");
      })
    );
    document.addEventListener("dragover", over);
    document.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (!depth) zone.classList.remove("is-dragging");
    });
    document.addEventListener("drop", async (event) => {
      if (!event.dataTransfer) return;
      event.preventDefault();
      depth = 0;
      zone.classList.remove("is-dragging");
      const files = event.dataTransfer.items && event.dataTransfer.items.length
        ? await readEntries(Array.from(event.dataTransfer.items))
        : Array.from(event.dataTransfer.files || []);
      if (files.length) addFiles(files);
    });
  }

  /* ---------- 粘贴 ---------- */

  async function pasteFromClipboard() {
    try {
      if (navigator.clipboard && navigator.clipboard.read) {
        const items = await navigator.clipboard.read();
        const files = [];
        for (const item of items) {
          const type = item.types.find((t) => t.startsWith("image/"));
          if (!type) continue;
          const blob = await item.getType(type);
          const ext = type.split("/")[1].replace("jpeg", "jpg");
          files.push(new File([blob], "粘贴图片_" + Date.now() + "." + ext, { type }));
        }
        if (files.length) {
          addFiles(files);
          return;
        }
        toast("剪贴板里没有图片", "warning");
        return;
      }
      throw new Error("浏览器不支持读取剪贴板");
    } catch (error) {
      toast("粘贴失败：" + error.message + "（可直接把图片拖进来）", "error");
    }
  }

  /* ---------- 演示数据（文档截图用） ---------- */

  function demoCanvas(width, height, draw) {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    draw(canvas.getContext("2d"), width, height);
    return canvas;
  }

  async function addDemoImages() {
    const gradient = demoCanvas(512, 512, (ctx, w, h) => {
      const g = ctx.createLinearGradient(0, 0, w, h);
      g.addColorStop(0, "#3b82f6");
      g.addColorStop(1, "#0f172a");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = "rgba(255,255,255,0.65)";
      ctx.lineWidth = 14;
      ctx.strokeRect(60, 60, w - 120, h - 120);
      ctx.fillStyle = "rgba(255,255,255,0.92)";
      ctx.font = "600 128px 'Segoe UI', sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("IF", w / 2, h / 2);
    });
    const banner = demoCanvas(960, 360, (ctx, w, h) => {
      const g = ctx.createLinearGradient(0, 0, w, 0);
      g.addColorStop(0, "#f8fafc");
      g.addColorStop(1, "#dbeafe");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#1e3a8a";
      ctx.font = "600 64px 'Microsoft YaHei UI', sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("多尺寸 ICO · 格式互转", w / 2, h / 2);
    });
    const shapes = demoCanvas(400, 400, (ctx, w, h) => {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "rgba(43, 99, 217, 0.9)";
      ctx.beginPath();
      ctx.arc(w / 2, h / 2, w * 0.36, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
      ctx.beginPath();
      ctx.moveTo(w / 2, h * 0.28);
      ctx.lineTo(w * 0.74, h * 0.66);
      ctx.lineTo(w * 0.26, h * 0.66);
      ctx.closePath();
      ctx.fill();
    });

    const blobs = await Promise.all([
      new Promise((resolve) => gradient.toBlob(resolve, "image/png")),
      new Promise((resolve) => banner.toBlob(resolve, "image/png")),
      new Promise((resolve) => shapes.toBlob(resolve, "image/png")),
    ]);
    const files = [
      new File([blobs[0]], "logo-square.png", { type: "image/png" }),
      new File([blobs[1]], "banner-wide.png", { type: "image/png" }),
      new File([blobs[2]], "shapes-transparent.png", { type: "image/png" }),
    ];
    await addFiles(files);
    if (new URLSearchParams(location.search).get("demo") === "done") {
      await startConversion();
    }
  }

  /* ---------- 快捷键 ---------- */

  function bindKeyboard() {
    document.addEventListener("keydown", (event) => {
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.key.toLowerCase() === "o") {
        event.preventDefault();
        if (event.shiftKey) el.folderInput.click();
        else el.fileInput.click();
        return;
      }
      if (mod && event.key === "Enter") {
        event.preventDefault();
        startConversion();
        return;
      }
      if (event.key === "Escape") {
        if (!el.preview.hidden) {
          closePreview();
          return;
        }
        if (state.converting) cancelConversion();
        return;
      }
      if (event.key === "Delete" && state.selectedId && !state.converting) {
        removeItem(state.selectedId);
      }
    });
    document.addEventListener("paste", (event) => {
      const files = Array.from((event.clipboardData && event.clipboardData.files) || []);
      if (files.length) {
        event.preventDefault();
        addFiles(files);
      }
    });
  }

  /* ---------- 初始化 ---------- */

  function cacheDom() {
    el.themeToggle = $("theme-toggle");
    el.queue = $("queue");
    el.queueCount = $("queue-count");
    el.dropZone = $("drop-zone");
    el.addFiles = $("add-files");
    el.addFolder = $("add-folder");
    el.paste = $("paste");
    el.clear = $("clear");
    el.formatGrid = $("format-grid");
    el.formatNote = $("format-note");
    el.supportNote = $("support-note");
    el.qualityGroup = $("quality-group");
    el.quality = $("quality");
    el.qualityValue = $("quality-value");
    el.icoGroup = $("ico-group");
    el.sizeGrid = $("size-grid");
    el.fitGrid = $("fit-grid");
    el.payloadGrid = $("payload-grid");
    el.bgGroup = $("bg-group");
    el.bgColor = $("bg-color");
    el.losslessRow = $("lossless-row");
    el.lossless = $("lossless");
    el.suffix = $("suffix");
    el.namePreview = $("name-preview");
    el.progress = document.querySelector(".progress");
    el.progressBar = $("progress-bar");
    el.status = $("status");
    el.start = $("start");
    el.cancel = $("cancel");
    el.downloadAll = $("download-all");
    el.fileInput = $("file-input");
    el.folderInput = $("folder-input");
    el.preview = $("preview");
    el.previewTitle = $("preview-title");
    el.previewPath = $("preview-path");
    el.previewMeta = $("preview-meta");
    el.previewSizes = $("preview-sizes");
    el.previewCanvas = $("preview-canvas");
    el.previewClose = $("preview-close");
    el.previewDownload = $("preview-download");
    el.previewHint = $("preview-hint");
    el.toast = $("toast");
  }

  function bindEvents() {
    el.themeToggle.addEventListener("click", () => {
      applyTheme(currentTheme() === "dark" ? "light" : "dark");
    });
    el.addFiles.addEventListener("click", () => el.fileInput.click());
    el.addFolder.addEventListener("click", () => el.folderInput.click());
    el.paste.addEventListener("click", pasteFromClipboard);
    el.clear.addEventListener("click", clearItems);
    el.dropZone.addEventListener("click", () => el.fileInput.click());
    el.dropZone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        el.fileInput.click();
      }
    });
    el.fileInput.addEventListener("change", () => {
      if (el.fileInput.files && el.fileInput.files.length) addFiles(el.fileInput.files);
      el.fileInput.value = "";
    });
    el.folderInput.addEventListener("change", () => {
      if (el.folderInput.files && el.folderInput.files.length) addFiles(el.folderInput.files);
      el.folderInput.value = "";
    });
    el.quality.addEventListener("input", () => {
      state.options.quality = Number(el.quality.value);
      el.qualityValue.textContent = el.quality.value;
      saveOptions();
      syncNamePreview();
    });
    el.bgColor.addEventListener("input", () => {
      state.options.background = el.bgColor.value;
      saveOptions();
    });
    el.suffix.addEventListener("input", () => {
      state.options.suffix = el.suffix.value;
      saveOptions();
      syncNamePreview();
    });
    el.lossless.addEventListener("change", () => {
      state.options.lossless = el.lossless.checked;
      saveOptions();
      syncOptionsUI();
    });
    el.start.addEventListener("click", startConversion);
    el.cancel.addEventListener("click", cancelConversion);
    el.downloadAll.addEventListener("click", downloadAll);
    el.previewClose.addEventListener("click", closePreview);
    el.preview.addEventListener("click", (event) => {
      if (event.target === el.preview) closePreview();
    });
    el.previewDownload.addEventListener("click", () => {
      const item = state.items.find((entry) => entry.id === state.lastPreviewId);
      if (item) downloadItem(item);
    });
    bindDropZone();
    bindKeyboard();
  }

  function init() {
    cacheDom();
    initTheme();
    loadOptions();
    convert.detectSupport();
    buildSizeGrid();
    buildFitGrid();
    buildPayloadGrid();
    bindEvents();
    syncOptionsUI();
    renderQueue();
    updateFooter();
    el.previewHint.textContent = "双击队列中的文件可随时预览";
    const params = new URLSearchParams(location.search);
    if (params.get("demo")) {
      addDemoImages()
        .then(() => {
          if (params.get("preview") === "1" && state.items.length) {
            openPreview(state.items[0].id);
          }
        })
        .catch((error) => {
          document.title = "DEMO ERROR: " + (error && error.message);
          el.status.textContent = "演示数据加载失败：" + (error && error.message);
          console.error(error);
        });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
