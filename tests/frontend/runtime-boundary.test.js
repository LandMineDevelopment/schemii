import assert from "node:assert/strict";
import test from "node:test";

import RuntimeBoundary from "../../.opencode/plugins/runtime-boundary.js";

test("repository runtime boundary allows only the supported launcher", async () => {
  const plugin = await RuntimeBoundary();
  const before = plugin["tool.execute.before"];

  await assert.doesNotReject(
    before({ tool: "bash" }, { args: { command: "./start.sh" } }),
  );
  await assert.doesNotReject(
    before({ tool: "bash" }, { args: { command: "bash -n start.sh" } }),
  );

  for (const command of [
    "docker compose up",
    "sudo docker info",
    "/usr/bin/docker ps",
    "git status && docker compose ps",
    "env DOCKER_HOST=unix:///var/run/docker.sock docker info",
    "curl --unix-socket /var/run/docker.sock http://localhost/version",
    "nohup env PYTHONPATH=src .venv/bin/uvicorn schemii.main:app --port 8011 &",
    "python -m uvicorn schemii.main:app",
    "python3 -m http.server 8011",
    "fastapi dev src/schemii/main.py",
  ]) {
    await assert.rejects(
      before({ tool: "bash" }, { args: { command } }),
      /Use \.\/start\.sh/,
      command,
    );
  }
});

test("runtime boundary ignores non-Bash tools", async () => {
  const plugin = await RuntimeBoundary();
  const before = plugin["tool.execute.before"];

  await assert.doesNotReject(
    before({ tool: "read" }, { args: { command: "docker compose ps" } }),
  );
});
