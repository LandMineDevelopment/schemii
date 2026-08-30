const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const start = source.indexOf("const MIN_ZOOM");
const end = source.indexOf("const COLORS", start);
assert.notEqual(start, -1, "zoom constants marker is missing");
assert.notEqual(end, -1, "zoom constants end marker is missing");

const context = vm.createContext({ Math });
vm.runInContext(`${source.slice(start, end)}\nglobalThis.clampZoom = clampZoom;`, context);

assert.equal(context.clampZoom(0.01), 0.1);
assert.equal(context.clampZoom(0.24), 0.24);
assert.equal(context.clampZoom(2), 1.7);
assert.equal(context.clampZoom(1.5, 1.25), 1.25);

const wheelStart = source.indexOf('elements.workspace.addEventListener("wheel"');
const wheelEnd = source.indexOf('elements.inspectorContent.addEventListener("input"', wheelStart);
const wheelSource = source.slice(wheelStart, wheelEnd);
assert.match(wheelSource, /setZoom\([^\n]+, true\)/, "wheel zoom must use the transient compositor-only path");
assert.match(wheelSource, /if \(!changed\) return;/, "clamped wheel zoom must not schedule a no-op save");
assert.match(wheelSource, /setTimeout\(finishWheelZoom, WHEEL_ZOOM_IDLE_MS\)/, "wheel zoom must synchronize once after input becomes idle");
assert.doesNotMatch(wheelSource, /applyView\(\)/, "wheel events must not repaint the complete view directly");
assert.match(source, /function finishWheelZoom\(\)[\s\S]*?applyView\(\);[\s\S]*?saveSchema\(LAYOUT_SAVE_DELAY_MS\)/, "zoom completion must synchronize and save once");
assert.match(source, /const WHEEL_ZOOM_IDLE_MS = 140/, "wheel zoom idle delay must remain explicit");
assert.match(source, /if \(newZoom === oldZoom\) return false;/, "clamped zoom controls must not save an unchanged viewport");
assert.match(fs.readFileSync("src/schemii/web/styles.css", "utf8"), /\.workspace\.zooming \.connections\s*\{[^}]*visibility:\s*hidden;/, "relationship painting must be disabled during wheel zoom");

console.log("Zoom limit tests passed");
