import assert from "node:assert/strict";
import test from "node:test";
import { createElapsedTimer, formatElapsed } from "../../src/schemii/common/web/assets/elapsed-time.js";

test("elapsed timer continues between network updates and freezes when stopped", () => {
  let time = 100, callback, cleared = 0;
  const values = [];
  const timer = createElapsedTimer({ now: () => time, onTick: value => values.push(value),
    schedule: tick => { callback = tick; return 42; }, clear: id => { assert.equal(id, 42); cleared++; } });
  timer.start(); time += 2350; callback();
  assert.equal(values.at(-1), 2350);
  assert.equal(timer.stop(), 2350);
  time += 8000;
  assert.equal(timer.value(), 2350);
  assert.equal(timer.running, false);
  assert.equal(cleared, 1);
  timer.start(); assert.equal(timer.value(), 0);
  time += 150; assert.equal(timer.stop(), 150);
});

test("elapsed duration stays readable for long jobs", () => {
  assert.equal(formatElapsed(1250), "1.3 s");
  assert.equal(formatElapsed(125250), "2m 5.3s");
  assert.equal(formatElapsed(-1), "0.0 s");
});
