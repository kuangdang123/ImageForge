/* ImageForge Web · 无依赖截图驱动（Chrome DevTools Protocol）
 * 用法：node tools/web_shot.js <url> <out.png> [width] [height] [waitExpr]
 * 例：node tools/web_shot.js "file:///D:/.../index.html?demo=1&theme=light" tmp/shots/web_light.png 1440 1080
 */
"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CHROME =
  process.env.CHROME_PATH ||
  (fs.existsSync("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
    ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    : "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`HTTP ${response.status} ${url}`);
  return response.json();
}

async function waitFor(fn, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await sleep(150);
  }
  throw new Error(`超时等待 ${label}${lastError ? "：" + lastError.message : ""}`);
}

function connect(wsUrl) {
  const socket = new WebSocket(wsUrl);
  let nextId = 1;
  const pending = new Map();
  const listeners = new Map();

  const ready = new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve);
    socket.addEventListener("error", (event) => reject(new Error("WebSocket 错误: " + (event.message || ""))));
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
      return;
    }
    if (message.method && listeners.has(message.method)) {
      for (const handler of listeners.get(message.method)) handler(message.params);
    }
  });

  function send(method, params) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }

  function once(method) {
    return new Promise((resolve) => {
      const handler = (params) => {
        listeners.get(method).delete(handler);
        resolve(params);
      };
      if (!listeners.has(method)) listeners.set(method, new Set());
      listeners.get(method).add(handler);
    });
  }

  function on(method, handler) {
    if (!listeners.has(method)) listeners.set(method, new Set());
    listeners.get(method).add(handler);
  }

  return { ready, send, once, on, close: () => socket.close() };
}

async function main() {
  const [url, outPath, widthArg, heightArg, waitExprArg] = process.argv.slice(2);
  if (!url || !outPath) {
    console.error("用法：node tools/web_shot.js <url> <out.png> [width] [height] [waitExpr]");
    return 2;
  }
  const width = Number(widthArg) || 1440;
  const height = Number(heightArg) || 1080;
  fs.mkdirSync(path.dirname(path.resolve(outPath)), { recursive: true });

  const port = 9200 + Math.floor(Math.random() * 600);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ifw-chrome-"));
  const child = spawn(
    CHROME,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-extensions",
      "--hide-scrollbars",
      "--force-device-scale-factor=1",
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profile}`,
      "about:blank",
    ],
    { stdio: ["ignore", "ignore", "ignore"] }
  );

  try {
    await waitFor(() => fetchJson(`http://127.0.0.1:${port}/json/version`), 15000, "DevTools 端口");
    const target = await fetchJson(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" });
    const client = connect(target.webSocketDebuggerUrl);
    await client.ready;

    const errors = [];
    client.on("Runtime.exceptionThrown", (params) => {
      const details = params.exceptionDetails || {};
      const frames = ((details.stackTrace && details.stackTrace.callFrames) || [])
        .slice(0, 6)
        .map((frame) => `    at ${frame.functionName || "<anonymous>"} (${frame.url}:${frame.lineNumber + 1}:${frame.columnNumber + 1})`)
        .join("\n");
      errors.push("异常：" + details.text + (details.exception && details.exception.description ? "\n" + details.exception.description : "") + (frames ? "\n" + frames : ""));
    });
    client.on("Runtime.consoleAPICalled", (params) => {
      if (params.type === "error") {
        errors.push("console.error：" + params.args.map((arg) => arg.value || arg.description || "").join(" "));
      }
    });

    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await client.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: false,
    });
    const loaded = client.once("Page.loadEventFired");
    await client.send("Page.navigate", { url });
    await loaded;

    const waitExpr = waitExprArg || "document.querySelectorAll('.queue-item').length > 0";
    await waitFor(async () => {
      const result = await client.send("Runtime.evaluate", { expression: waitExpr, returnByValue: true });
      return !!result.result.value;
    }, 20000, `条件 ${waitExpr}`);
    await sleep(700);

    const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    fs.writeFileSync(path.resolve(outPath), Buffer.from(shot.data, "base64"));

    const title = await client.send("Runtime.evaluate", { expression: "document.title", returnByValue: true });
    const status = await client.send("Runtime.evaluate", {
      expression: "(document.getElementById('status')||{}).textContent || ''",
      returnByValue: true,
    });
    console.log(`saved ${outPath} ${fs.statSync(path.resolve(outPath)).size} B`);
    console.log(`title: ${title.result.value}`);
    console.log(`status: ${status.result.value}`);
    if (errors.length) {
      console.log("页面错误：");
      errors.forEach((line) => console.log("  " + line));
    }
    client.close();
    return errors.length ? 1 : 0;
  } finally {
    child.kill();
    await sleep(200);
    try {
      fs.rmSync(profile, { recursive: true, force: true });
    } catch (error) {
      /* 忽略清理失败 */
    }
  }
}

main().then(
  (code) => process.exit(code || 0),
  (error) => {
    console.error(error);
    process.exit(1);
  }
);
