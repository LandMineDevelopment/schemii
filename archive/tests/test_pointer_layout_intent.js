const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");

assert.match(source, /const POINTER_MOVE_THRESHOLD_PX = 3;/, "layout gestures must use an explicit movement threshold");
assert.match(
  source,
  /Math\.hypot\(event\.clientX - dragState\.startX, event\.clientY - dragState\.startY\) > POINTER_MOVE_THRESHOLD_PX/,
  "table jitter must not begin a layout drag"
);
assert.match(
  source,
  /Math\.hypot\(event\.clientX - panState\.startX, event\.clientY - panState\.startY\) <= POINTER_MOVE_THRESHOLD_PX\) return;[\s\S]*?panState\.moved = true;/,
  "touch jitter must not begin a canvas pan"
);
assert.match(
  source,
  /if \(panState\?\.pointerId !== event\.pointerId\) return;\s+view\.x = panState\.viewX;\s+view\.y = panState\.viewY;/,
  "a cancelled pan must restore the original viewport"
);
assert.match(source, /const moved = panState\.moved;/, "only an intentional pan may persist the viewport");

console.log("Pointer layout intent tests passed");
