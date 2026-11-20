const {
  app,
  BrowserWindow,
  Tray,
  Menu,
  ipcMain,
  nativeImage,
} = require("electron");

const path = require("path");
const { io } = require("socket.io-client");

// Python firewall configuration
const URL = process.env.FIREWALL_WS_URL || "http://127.0.0.1:5000";
const TOKEN = process.env.APPROVAL_TOKEN;

let win = null;
let tray = null;
let socket = null;
let connected = false;
let quitting = false;

const queue = [];

// Prevent multiple Electron instances
const gotLock = app.requestSingleInstanceLock();

if (!gotLock) {
  app.quit();
} else {
  function updateTray() {
    if (!tray) return;

    const count = queue.length;

    tray.setTitle(connected ? (count ? `🛡 ${count}` : "🛡") : "⚠️");

    tray.setContextMenu(
      Menu.buildFromTemplate([
        {
          label: connected
            ? `Connected, ${count} pending`
            : "Disconnected from firewall",
          enabled: false,
        },
        { type: "separator" },
        {
          label: "Show pending approvals",
          enabled: count > 0,
          click: () => showPopup(),
        },
        {
          label: "Quit",
          click: () => {
            quitting = true;
            app.quit();
          },
        },
      ]),
    );
  }

  function showPopup() {
    if (!win || win.isDestroyed()) return;
    if (!queue.length) return;

    const current = queue[0];

    win.webContents.send("state", {
      current,
      pending: queue.length,
    });

    win.center();
    win.show();
    win.setAlwaysOnTop(true, "screen-saver");
    win.focus();

    if (process.platform === "darwin") {
      app.focus({ steal: true });
    }

    console.log("Approval popup displayed:", current.id);
  }

  function refresh() {
    updateTray();

    if (!win || win.isDestroyed()) return;

    if (queue.length === 0) {
      win.hide();
      return;
    }

    showPopup();
  }

  function normalizeRequest(payload) {
    // Support a nested record as well as a direct request object.
    const req = payload?.record || payload?.request || payload;

    if (!req || typeof req !== "object") {
      console.error("Invalid approval payload:", payload);
      return null;
    }

    const id = req.id ?? req.requestId ?? req.request_id;

    if (id === undefined || id === null) {
      console.error("Approval payload has no request ID:", payload);
      return null;
    }

    return {
      ...req,
      id: String(id),
      source: req.source ?? "-",
      serverName: req.serverName ?? req.server_name ?? req.server ?? "-",
      method: req.method ?? "-",
      toolName: req.toolName ?? req.tool_name ?? req.name ?? "-",
      params: req.params ?? req.arguments ?? {},
    };
  }

  function handleApprovalRequest(payload) {
    console.log("Raw approval request:", JSON.stringify(payload, null, 2));

    const request = normalizeRequest(payload);

    if (!request) return;

    if (queue.some((item) => item.id === request.id)) {
      console.log("Duplicate approval ignored:", request.id);
      return;
    }

    queue.push(request);

    console.log("Approval added to queue:", request.id);
    console.log("Pending approvals:", queue.length);

    refresh();
  }

  function connect() {
    if (!TOKEN) {
      console.error("APPROVAL_TOKEN is missing. Cannot connect.");
      return;
    }

    console.log("Connecting to Python firewall:", URL);
    console.log("Socket path: /approvals/socket.io");

    socket = io(URL, {
      path: "/approvals/socket.io",
      auth: {
        token: TOKEN,
      },
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
    });

    socket.on("connect", () => {
      connected = true;

      console.log("Connected to Python MCP Firewall");
      console.log("Socket ID:", socket.id);

      updateTray();
    });

    socket.on("connect_error", (error) => {
      connected = false;

      console.error("Socket connection error:", error.message);

      updateTray();
    });

    socket.on("disconnect", (reason) => {
      connected = false;

      console.warn("Disconnected from firewall:", reason);

      // Keep existing requests until Python replays pending requests.
      updateTray();

      if (reason === "io server disconnect") {
        setTimeout(() => {
          if (socket && !quitting) {
            socket.connect();
          }
        }, 2000);
      }
    });

    socket.on("approval:request", handleApprovalRequest);

    socket.on("approval:resolved", (data) => {
      console.log("Approval resolved:", data);

      const requestId = data?.requestId ?? data?.request_id ?? data?.id;

      if (requestId === undefined || requestId === null) {
        console.error("Resolved event has no request ID:", data);
        return;
      }

      const index = queue.findIndex((item) => item.id === String(requestId));

      if (index !== -1) {
        queue.splice(index, 1);
        refresh();
      }
    });
  }

  // Allow / Block decisions from renderer
  ipcMain.on("decision", (_event, data) => {
    const { requestId, decision } = data || {};

    if (decision !== "allow" && decision !== "block") {
      console.error("Invalid decision:", decision);
      return;
    }

    if (!socket || !socket.connected) {
      console.error("Firewall is disconnected. Decision not sent.");
      return;
    }

    if (!queue.length || queue[0].id !== String(requestId)) {
      console.error("Request ID does not match current approval.");
      return;
    }

    console.log("Sending approval decision:", {
      requestId,
      decision,
    });

    socket.emit("approval:decision", {
      requestId,
      decision,
    });

    // Wait for approval:resolved from Python before removing the item.
    // This prevents losing the request if the decision is not processed.
  });

  function createWindow() {
    win = new BrowserWindow({
      width: 540,
      height: 480,
      show: false,
      frame: false,
      resizable: false,
      skipTaskbar: true,
      fullscreenable: false,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });

    win.setAlwaysOnTop(true, "screen-saver");

    win.setVisibleOnAllWorkspaces(true, {
      visibleOnFullScreen: true,
    });

    win.loadFile(path.join(__dirname, "index.html"));

    win.on("close", (event) => {
      if (!quitting) {
        event.preventDefault();
        win.hide();
      }
    });

    win.webContents.on("did-fail-load", (_event, code, description) => {
      console.error("Failed to load popup:", code, description);
    });

    win.webContents.once("did-finish-load", () => {
      console.log("Approval HTML loaded.");
      connect();
    });
  }

  app.whenReady().then(() => {
    if (process.platform === "darwin") {
      app.dock.hide();
    }

    tray = new Tray(nativeImage.createEmpty());

    updateTray();
    createWindow();
  });

  app.on("second-instance", () => {
    if (queue.length) {
      showPopup();
    }
  });

  app.on("window-all-closed", () => {
    // Keep running in background.
  });

  app.on("before-quit", () => {
    quitting = true;

    if (socket) {
      socket.disconnect();
    }
  });
}
