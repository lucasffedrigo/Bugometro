#!/usr/bin/env node

const DEFAULT_BROWSER_URL = "http://127.0.0.1:9222";

function argValue(name, fallback = "") {
  const prefix = `${name}=`;
  const inline = process.argv.find((item) => item.startsWith(prefix));
  if (inline) return inline.slice(prefix.length);
  const index = process.argv.indexOf(name);
  if (index >= 0 && process.argv[index + 1]) return process.argv[index + 1];
  return fallback;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function requestJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ao consultar ${url}`);
  }
  return response.json();
}

async function findPage(browserUrl) {
  const pages = await requestJson(`${browserUrl.replace(/\/$/, "")}/json/list`);
  const page =
    pages.find(
      (item) =>
        item.type === "page" &&
        item.webSocketDebuggerUrl &&
        item.url &&
        !item.url.startsWith("about:") &&
        !item.url.startsWith("devtools://")
    ) || pages.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
  if (!page) {
    throw new Error("Nenhuma pagina depuravel encontrada no Chrome observado.");
  }
  return page;
}

async function browserVersion(browserUrl) {
  try {
    const version = await requestJson(`${browserUrl.replace(/\/$/, "")}/json/version`);
    return version.Browser || version["User-Agent"] || "";
  } catch {
    return "";
  }
}

class CdpSession {
  constructor(wsUrl) {
    this.nextId = 1;
    this.pending = new Map();
    this.events = {
      console: [],
      exceptions: [],
      requests: new Map(),
    };
    this.ws = new WebSocket(wsUrl);
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
    this.ws.addEventListener("message", (event) => this.handleMessage(event.data));
  }

  handleMessage(raw) {
    const message = JSON.parse(raw);
    if (message.id && this.pending.has(message.id)) {
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message || "CDP command failed"));
      else resolve(message.result || {});
      return;
    }
    if (message.method) this.handleEvent(message.method, message.params || {});
  }

  handleEvent(method, params) {
    if (method === "Runtime.consoleAPICalled") {
      this.events.console.push({
        level: params.type || "log",
        message: (params.args || []).map((arg) => arg.value ?? arg.description ?? "").join(" "),
      });
    }
    if (method === "Log.entryAdded") {
      const entry = params.entry || {};
      this.events.console.push({
        level: entry.level || "log",
        message: entry.text || entry.url || "",
      });
    }
    if (method === "Runtime.exceptionThrown") {
      const details = params.exceptionDetails || {};
      this.events.exceptions.push({
        name: details.exception?.className || "exception",
        message: details.text || details.exception?.description || details.exception?.value || "",
        url: details.url || "",
        lineNumber: details.lineNumber,
        columnNumber: details.columnNumber,
      });
    }
    if (method === "Network.requestWillBeSent") {
      const request = params.request || {};
      this.events.requests.set(params.requestId, {
        method: request.method,
        url: request.url,
        type: params.type,
        resourceType: params.type,
        priority: request.initialPriority,
        initiatorType: params.initiator?.type,
        startTime: params.timestamp,
      });
    }
    if (method === "Network.responseReceived") {
      const current = this.events.requests.get(params.requestId) || {};
      const response = params.response || {};
      this.events.requests.set(params.requestId, {
        ...current,
        status: response.status,
        resourceType: params.type || current.type,
        url: response.url || current.url,
        mimeType: response.mimeType,
        protocol: response.protocol,
        fromDiskCache: response.fromDiskCache,
        fromServiceWorker: response.fromServiceWorker,
        remoteIPAddress: response.remoteIPAddress,
        encodedDataLength: response.encodedDataLength,
      });
    }
    if (method === "Network.loadingFinished") {
      const current = this.events.requests.get(params.requestId) || {};
      this.events.requests.set(params.requestId, {
        ...current,
        endTime: params.timestamp,
        encodedDataLength: params.encodedDataLength ?? current.encodedDataLength,
      });
    }
    if (method === "Network.loadingFailed") {
      const current = this.events.requests.get(params.requestId) || {};
      this.events.requests.set(params.requestId, {
        ...current,
        endTime: params.timestamp,
        errorText: params.errorText,
        blockedReason: params.blockedReason,
      });
    }
  }

  command(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (!this.pending.has(id)) return;
        this.pending.delete(id);
        reject(new Error(`Timeout em ${method}`));
      }, 5000);
    });
  }

  close() {
    this.ws.close();
  }
}

const pageSnapshotScript = `() => {
  const nav = performance.getEntriesByType("navigation")[0];
  const resources = performance.getEntriesByType("resource").slice(-80).map((entry) => ({
    method: "GET",
    url: entry.name,
    resourceType: entry.initiatorType,
    durationMs: entry.duration,
    transferSize: entry.transferSize,
    encodedDataLength: entry.encodedBodySize,
    decodedBodySize: entry.decodedBodySize
  }));
  const longTasks = performance.getEntriesByType("longtask").map((entry) => ({
    durationMs: entry.duration,
    startTime: entry.startTime
  }));
  const lcpEntries = performance.getEntriesByType("largest-contentful-paint");
  const layoutShifts = performance.getEntriesByType("layout-shift")
    .filter((entry) => !entry.hadRecentInput);
  const cls = layoutShifts.reduce((sum, entry) => sum + entry.value, 0);
  const active = document.activeElement;
  const selector = active
    ? [
        active.id ? "#" + active.id : "",
        active.getAttribute("name") ? '[name="' + active.getAttribute("name") + '"]' : "",
        active.className && typeof active.className === "string"
          ? active.tagName.toLowerCase() + "." + active.className.trim().split(/\\s+/).slice(0, 3).join(".")
          : active.tagName?.toLowerCase()
      ].find(Boolean)
    : "";
  const viewport = {
    width: window.innerWidth,
    height: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio
  };
  return {
    page_url: location.href,
    page_title: document.title,
    user_agent: navigator.userAgent,
    page_state: document.readyState,
    viewport,
    connection: navigator.connection ? {
      effectiveType: navigator.connection.effectiveType,
      downlink: navigator.connection.downlink,
      rtt: navigator.connection.rtt,
      saveData: navigator.connection.saveData
    } : undefined,
    selected_element: {
      selector,
      text: active?.innerText || active?.value || active?.ariaLabel || ""
    },
    performance: {
      domContentLoaded: nav ? nav.domContentLoadedEventEnd : undefined,
      load: nav ? nav.loadEventEnd : undefined,
      ttfb: nav ? nav.responseStart : undefined,
      fcp: performance.getEntriesByName("first-contentful-paint")[0]?.startTime,
      lcp: lcpEntries[lcpEntries.length - 1]?.startTime,
      cls,
      jsHeapUsedSize: performance.memory?.usedJSHeapSize,
      longTasks
    },
    network: {
      entries: resources
    }
  };
}`;

async function main() {
  const browserUrl = argValue("--browser-url", process.env.DEVTOOLS_BROWSER_URL || DEFAULT_BROWSER_URL);
  const waitMs = Number(argValue("--wait-ms", process.env.DEVTOOLS_WAIT_MS || "1500"));
  const [page, browser] = await Promise.all([findPage(browserUrl), browserVersion(browserUrl)]);
  const session = new CdpSession(page.webSocketDebuggerUrl);
  await session.open();
  await Promise.allSettled([
    session.command("Runtime.enable"),
    session.command("Log.enable"),
    session.command("Network.enable"),
    session.command("Performance.enable"),
  ]);

  await sleep(Math.max(0, waitMs));

  const evaluation = await session.command("Runtime.evaluate", {
    expression: `(${pageSnapshotScript})()`,
    returnByValue: true,
    awaitPromise: true,
  });
  const snapshot = evaluation.result?.value || {};
  const performanceMetrics = await session.command("Performance.getMetrics").catch(() => ({ metrics: [] }));
  session.close();

  const requests = [
    ...(snapshot.network?.entries || []),
    ...Array.from(session.events.requests.values()),
  ];

  const output = {
    context: {
      ...snapshot,
      browser,
      console: { entries: session.events.console },
      exceptions: session.events.exceptions,
      network: { entries: requests },
      performance: {
        ...(snapshot.performance || {}),
        metrics: performanceMetrics.metrics || [],
      },
    },
  };
  process.stdout.write(JSON.stringify(output));
}

main().catch((error) => {
  console.error(error.message || String(error));
  process.exit(1);
});
