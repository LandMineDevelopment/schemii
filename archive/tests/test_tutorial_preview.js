const assert = require("node:assert/strict");
const path = require("node:path");
const { spawn } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const preview = spawn(process.execPath, ["scripts/tutorial-preview.js"], {
  cwd: root,
  env: { ...process.env, SCHEMII_TUTORIAL_PREVIEW_PORT: "0" },
  stdio: ["ignore", "pipe", "pipe"],
});

let output = "";
let errors = "";
preview.stdout.setEncoding("utf8");
preview.stderr.setEncoding("utf8");
preview.stdout.on("data", chunk => { output += chunk; });
preview.stderr.on("data", chunk => { errors += chunk; });

function waitForUrl() {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`Preview did not start: ${output}${errors}`)), 5000);
    const inspect = () => {
      const match = output.match(/Schemer: (http:\/\/127\.0\.0\.1:\d+\/)/);
      if (!match) return;
      clearTimeout(timeout);
      preview.stdout.off("data", inspect);
      resolve(match[1]);
    };
    preview.stdout.on("data", inspect);
    inspect();
  });
}

async function stopPreview() {
  if (preview.exitCode !== null) return;
  preview.kill("SIGTERM");
  await new Promise(resolve => preview.once("exit", resolve));
}

async function main() {
  try {
    const origin = await waitForUrl();
    const [schemer, schemii, session, dashboards, schemas] = await Promise.all([
      fetch(origin).then(response => response.text()),
      fetch(`${origin}schemii/`).then(response => response.text()),
      fetch(`${origin}api/session`).then(response => response.json()),
      fetch(`${origin}api/dashboards/summary`).then(response => response.json()),
      fetch(`${origin}api/schemas`).then(response => response.json()),
    ]);
    assert.equal((schemer.match(/data-onboarding-page=/g) || []).length, 7);
    assert.equal((schemii.match(/data-onboarding-page=/g) || []).length, 7);
    assert.equal(session.serverId, "tutorial-preview-server");
    assert.equal(dashboards.summaries[0].title, "Tutorial preview");
    assert.equal(schemas.schemas.length, 1);
    assert.match(output, /No Docker, PostgreSQL, OpenCode, or saved application data is used/);
    console.log("Tutorial preview server contracts passed");
  } finally {
    await stopPreview();
  }
}

main().catch(async error => {
  await stopPreview();
  console.error(error);
  process.exitCode = 1;
});
