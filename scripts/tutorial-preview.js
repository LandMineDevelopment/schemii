#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const schemiiWeb = path.join(root, "src/schemii/web");
const schemerWeb = path.join(root, "src/schemii/schemer_web");
const sharedWeb = path.join(root, "src/schemii/shared_web");
const host = "127.0.0.1";
const configuredPort = process.env.SCHEMII_TUTORIAL_PREVIEW_PORT || "18080";

if (!/^\d+$/.test(configuredPort) || Number(configuredPort) > 65535) {
  console.error("SCHEMII_TUTORIAL_PREVIEW_PORT must be an integer from 0 through 65535.");
  process.exit(2);
}

const schema = JSON.parse(fs.readFileSync(path.join(root, "examples/schema_starter.json"), "utf8"));
schema.revision = 1;
schema.layoutToken = "1".repeat(64);

let dashboard = {
  id: "dashboard_tutorial_preview",
  version: 3,
  revision: 1,
  updatedAt: "2026-08-25T00:00:00Z",
  dashboard: {
    title: "Tutorial preview",
    archived: false,
    widgets: [
      { id: "widget_preview_one", kind: "placeholder", title: "Revenue overview", configuration: {} },
      { id: "widget_preview_two", kind: "placeholder", title: "Orders by status", configuration: {} },
    ],
    slicers: [],
    viewport: { desktop: { y: 0 }, mobile: { y: 0 } },
  },
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function sendJson(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(body),
    "Content-Type": "application/json; charset=utf-8",
  });
  response.end(body);
}

function readJson(request, response, callback) {
  let body = "";
  request.setEncoding("utf8");
  request.on("data", chunk => {
    body += chunk;
    if (Buffer.byteLength(body) > 1024 * 1024) request.destroy();
  });
  request.on("end", () => {
    try {
      callback(JSON.parse(body || "{}"));
    } catch {
      sendJson(response, 400, { error: { code: "invalid_json", message: "Preview request body is not valid JSON" } });
    }
  });
}

function sharedFile(requestPath) {
  const relative = requestPath.slice("/shared/".length);
  const candidate = path.resolve(sharedWeb, relative);
  return candidate.startsWith(`${sharedWeb}${path.sep}`) ? candidate : null;
}

function staticFile(requestPath) {
  if (requestPath === "/" || requestPath === "/index.html") return path.join(schemerWeb, "index.html");
  if (requestPath === "/app.js" || requestPath === "/styles.css") return path.join(schemerWeb, requestPath.slice(1));
  if (["/schemii", "/schemii/", "/schemii/index.html"].includes(requestPath)) return path.join(schemiiWeb, "index.html");
  if (requestPath === "/schemii/app.js" || requestPath === "/schemii/styles.css") return path.join(schemiiWeb, requestPath.slice("/schemii/".length));
  if (requestPath.startsWith("/shared/")) return sharedFile(requestPath);
  return null;
}

function contentType(file) {
  return {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
  }[path.extname(file)] || "application/octet-stream";
}

const server = http.createServer((request, response) => {
  const requestPath = new URL(request.url, `http://${host}`).pathname;
  if (requestPath === "/favicon.ico") {
    response.writeHead(204, { "Cache-Control": "no-store" });
    return response.end();
  }
  if (requestPath === "/api/session") {
    return sendJson(response, 200, { token: "tutorial-preview-token", serverId: "tutorial-preview-server" });
  }
  if (requestPath === "/api/schemas" && request.method === "GET") {
    return sendJson(response, 200, { schemas: [clone(schema)] });
  }
  if (requestPath === "/api/postgres/profiles" && request.method === "GET") {
    return sendJson(response, 200, { profiles: [] });
  }
  if (requestPath === "/api/dashboards/summary" && request.method === "GET") {
    return sendJson(response, 200, {
      summaries: [{
        id: dashboard.id,
        title: dashboard.dashboard.title,
        archived: dashboard.dashboard.archived,
        revision: dashboard.revision,
        widgetCount: dashboard.dashboard.widgets.length,
      }],
      page: { pageSize: 100, returned: 1, hasMore: false, nextCursor: null },
    });
  }
  if (requestPath === `/api/dashboards/${dashboard.id}` && request.method === "GET") {
    return sendJson(response, 200, clone(dashboard));
  }
  if (requestPath === `/api/dashboards/${dashboard.id}` && request.method === "PUT") {
    return readJson(request, response, payload => {
      if (!payload.record || payload.record.id !== dashboard.id) {
        return sendJson(response, 400, { error: { code: "invalid_dashboard", message: "Preview dashboard identity is invalid" } });
      }
      dashboard = {
        ...clone(payload.record),
        revision: dashboard.revision + 1,
        updatedAt: new Date().toISOString(),
      };
      return sendJson(response, 200, clone(dashboard));
    });
  }

  const file = staticFile(requestPath);
  if (file && fs.existsSync(file) && fs.statSync(file).isFile()) {
    response.writeHead(200, { "Cache-Control": "no-store", "Content-Type": contentType(file) });
    fs.createReadStream(file).pipe(response);
    return;
  }
  return sendJson(response, 404, { error: { code: "not_found", message: "Tutorial preview route not found" } });
});

server.on("error", error => {
  console.error(`Tutorial preview failed: ${error.message}`);
  process.exitCode = 1;
});

server.listen(Number(configuredPort), host, () => {
  const port = server.address().port;
  console.log("Tutorial preview is ready with synthetic, in-memory data.");
  console.log(`Schemer: http://${host}:${port}/`);
  console.log(`Schemii: http://${host}:${port}/schemii/`);
  console.log("Press Ctrl+C to stop. No Docker, PostgreSQL, OpenCode, or saved application data is used.");
});

function shutdown() {
  server.close(error => {
    if (error) {
      console.error(`Tutorial preview shutdown failed: ${error.message}`);
      process.exitCode = 1;
    }
  });
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
