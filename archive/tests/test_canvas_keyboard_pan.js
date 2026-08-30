const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const html = fs.readFileSync("src/schemii/web/index.html", "utf8");
const styles = fs.readFileSync("src/schemii/web/styles.css", "utf8");

const helperStart = source.indexOf("const CANVAS_KEYBOARD_PAN_STEP_PX");
const helperEnd = source.indexOf("const COLORS", helperStart);
assert.notEqual(helperStart, -1, "keyboard pan step is missing");
assert.notEqual(helperEnd, -1, "keyboard pan helper end marker is missing");
const context = vm.createContext({});
vm.runInContext(`${source.slice(helperStart, helperEnd)}\nglobalThis.panDelta = canvasKeyboardPanDelta;`, context);
const delta = (key, accelerated = false) => JSON.parse(JSON.stringify(context.panDelta(key, accelerated)));

assert.deepEqual(delta("ArrowLeft"), { x: 24, y: 0 });
assert.deepEqual(delta("ArrowRight"), { x: -24, y: 0 });
assert.deepEqual(delta("ArrowUp"), { x: 0, y: 24 });
assert.deepEqual(delta("ArrowDown"), { x: 0, y: -24 });
assert.deepEqual(delta("ArrowRight", true), { x: -96, y: 0 }, "Shift must accelerate camera panning by four grid cells");
assert.equal(context.panDelta("Enter"), null, "unrelated keys must not pan the canvas");

const handlerStart = source.indexOf('elements.workspace.addEventListener("keydown"');
const handlerEnd = source.indexOf('elements.workspace.addEventListener("pointermove"', handlerStart);
const handler = source.slice(handlerStart, handlerEnd);
assert.notEqual(handlerStart, -1, "workspace keyboard pan handler is missing");
assert.match(handler, /event\.target !== elements\.workspace[\s\S]*activeRailWorkspace\(\) !== "tables"[\s\S]*elements\.workspace\.inert/, "only the focused active Tables canvas may own arrow panning");
assert.match(handler, /event\.ctrlKey \|\| event\.metaKey \|\| event\.altKey[\s\S]*dialog\[open\][\s\S]*dragState \|\| panState \|\| marqueeState/, "keyboard panning must yield to modifiers, dialogs, and active pointer gestures");
assert.match(handler, /event\.preventDefault\(\)[\s\S]*view\.x \+= delta\.x[\s\S]*view\.y \+= delta\.y[\s\S]*applyView\(\)[\s\S]*saveSchema\(LAYOUT_SAVE_DELAY_MS\)/, "accepted arrows must synchronously move and durably debounce the viewport");
assert.doesNotMatch(handler, /event\.repeat/, "native key repeat must provide continuous keyboard panning");
assert.match(source, /pointerdown[\s\S]*elements\.workspace\.focus\(\{ preventScroll: true \}\)/, "canvas pointer interaction must establish keyboard ownership without scrolling");

const workspace = html.match(/<section class="workspace"[^>]*>/)?.[0] || "";
assert.match(workspace, /tabindex="0"/, "the Tables canvas must be keyboard focusable");
for (const shortcut of ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Shift+ArrowUp", "Shift+ArrowDown", "Shift+ArrowLeft", "Shift+ArrowRight"]) assert.ok(workspace.includes(shortcut), `${shortcut} must be exposed through aria-keyshortcuts`);
assert.match(html, /<kbd>Arrows<\/kbd> pan[\s\S]*<kbd>Shift \+ Arrow<\/kbd> faster/, "the canvas must visibly document normal and accelerated keyboard panning");
assert.match(html, /use the arrow keys to pan \(hold Shift to move faster\)/, "onboarding must teach keyboard panning");
assert.match(styles, /\.workspace:focus-visible \{[^}]*outline:/, "keyboard focus on the canvas must be visible");

console.log("Canvas keyboard pan tests passed");
