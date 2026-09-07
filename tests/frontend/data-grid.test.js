import assert from "node:assert/strict";
import test from "node:test";
import { pagedRowsStatus } from "../../src/schemii/schemii/web/assets/relation-browser.js";

import {
  installAutoPageLoader,
  isNearScrollEnd,
  mergeDataGridPages,
} from "#common/data-grid.js";

class ScrollContainer {
  constructor() {
    this.scrollHeight = 1000;
    this.scrollTop = 0;
    this.clientHeight = 200;
    this.attributes = new Map();
    this.listeners = new Map();
  }

  addEventListener(type, callback) { this.listeners.set(type, callback); }
  removeEventListener(type, callback) {
    if (this.listeners.get(type) === callback) this.listeners.delete(type);
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  scroll() { this.listeners.get("scroll")?.(); }
}

test("inspector and full preview share paging status for empty, loading and failed pages", () => {
  const page = { rows: [[1], [2]], columns: [{ name: "id" }], nextCursor: "next" };
  assert.equal(pagedRowsStatus(null, { loading: true }), "Loading rows…");
  assert.equal(pagedRowsStatus(null), "Up to 100 rows");
  assert.equal(pagedRowsStatus(page), "2 rows · 1 columns · scroll for more");
  assert.equal(pagedRowsStatus(page, { loading: true }), "2 rows · 1 columns · loading more…");
  assert.equal(pagedRowsStatus(page, { pagingError: new Error("offline") }), "2 rows · 1 columns · more rows could not load · refresh to retry");
  assert.equal(pagedRowsStatus({ rows: [], columns: [], nextCursor: null }), "0 rows · 0 columns");
});

test("scroll-end detection uses the remaining vertical distance", () => {
  const container = new ScrollContainer();
  container.scrollTop = 679;
  assert.equal(isNearScrollEnd(container, 120), false);
  container.scrollTop = 680;
  assert.equal(isNearScrollEnd(container, 120), true);
});

test("page merging retains loaded rows and advances the server cursor", () => {
  const columns = [{ name: "id", dataType: "bigint" }];
  const current = { columns, rows: [[1], [2]], nextCursor: "cursor-2", truncated: true };
  const next = { columns, rows: [[3], [4]], nextCursor: null, truncated: false };

  assert.deepEqual(mergeDataGridPages(current, next), {
    columns,
    rows: [[1], [2], [3], [4]],
    nextCursor: null,
    truncated: false,
  });
});

test("automatic paging loads once near the boundary and ignores duplicate scroll events", async () => {
  const container = new ScrollContainer();
  container.scrollTop = 700;
  let remainingPages = 2;
  let resolveLoad;
  let loads = 0;
  const pager = installAutoPageLoader({
    container,
    canLoad: () => remainingPages > 0,
    loadNext: () => {
      loads += 1;
      return new Promise(resolve => { resolveLoad = resolve; });
    },
    schedule: callback => callback(),
  });

  container.scroll();
  container.scroll();
  assert.equal(loads, 1);
  assert.equal(container.attributes.get("aria-busy"), "true");

  remainingPages -= 1;
  container.scrollHeight = 1400;
  resolveLoad();
  await Promise.resolve();
  assert.equal(container.attributes.has("aria-busy"), false);
  assert.equal(loads, 1);

  container.scrollTop = 1100;
  container.scroll();
  assert.equal(loads, 2);
  remainingPages -= 1;
  resolveLoad();
  await Promise.resolve();
  pager.destroy();
  assert.equal(container.listeners.has("scroll"), false);
});
