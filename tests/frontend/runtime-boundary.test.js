import assert from "node:assert/strict";
import test from "node:test";

import { RuntimeBoundary, runtimeBoundaryViolation } from "../../.opencode/plugins/runtime-boundary.js";

test("repository runtime boundary allows only the supported launcher", () => {
  assert.equal(runtimeBoundaryViolation("./start.sh"), null);
  assert.equal(runtimeBoundaryViolation("bash -n start.sh"), null);

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
    assert.notEqual(runtimeBoundaryViolation(command), null, command);
  }
});

test("runtime boundary aborts forbidden Bash tool calls", async () => {
  const plugin = await RuntimeBoundary();
  const before = plugin["tool.execute.before"];

  await assert.rejects(
    before({ tool: "bash" }, { args: { command: "docker compose ps" } }),
    /Use \.\/start\.sh/,
  );
  await assert.doesNotReject(
    before({ tool: "bash" }, { args: { command: "./start.sh" } }),
  );
});
