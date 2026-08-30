const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const body = {};
const style = { textOverflow: "ellipsis", webkitLineClamp: "none" };
const context = vm.createContext({
  document: { body },
  window: {},
  HTMLElement: class {},
  getComputedStyle: () => style,
  setTimeout,
  clearTimeout,
  requestAnimationFrame: callback => callback()
});
vm.runInContext(fs.readFileSync("src/schemii/shared_web/ui-components.js", "utf8"), context);
const { elementHasTruncatedText, automaticTooltipText, findTooltipTarget } = context.window.SchemiiShared;

const element = {
  hidden: false,
  scrollWidth: 160,
  clientWidth: 80,
  scrollHeight: 20,
  clientHeight: 20,
  textContent: "  A long   database object name  ",
  dataset: {},
  parentElement: body,
  getAttribute: () => null
};

assert.equal(elementHasTruncatedText(element), true);
assert.equal(automaticTooltipText(element), "A long database object name");
assert.equal(findTooltipTarget(element, { automaticTruncation: true, boundary: body }), element);
assert.equal(element.dataset.tooltip, "A long database object name");
assert.equal(element.dataset.tooltipAutomatic, "true");

element.textContent = "Updated truncated text";
assert.equal(findTooltipTarget(element, { automaticTruncation: true, boundary: body }), element);
assert.equal(element.dataset.tooltip, "Updated truncated text");

element.scrollWidth = 80;
assert.equal(findTooltipTarget(element, { automaticTruncation: true, boundary: body }), null);
assert.equal("tooltip" in element.dataset, false);
assert.equal("tooltipAutomatic" in element.dataset, false);

element.dataset.tooltip = "Explicit tooltip";
assert.equal(findTooltipTarget(element, { automaticTruncation: true, boundary: body }), element);
assert.equal(element.dataset.tooltip, "Explicit tooltip");

style.textOverflow = "clip";
element.dataset = {};
element.scrollWidth = 160;
assert.equal(elementHasTruncatedText(element), false);

element.value = "Truncated input value";
style.textOverflow = "ellipsis";
assert.equal(automaticTooltipText(element), "Truncated input value");

console.log("Automatic tooltip tests passed");
