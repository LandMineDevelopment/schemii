import assert from "node:assert/strict";
import test from "node:test";
import { streamCopyDownload } from "../../src/schemii/schemii/web/assets/console-copy.js";

test("COPY download preserves binary chunks and reports streamed bytes without a Blob", async () => {
  const written = [], counts = [];
  let closed = false;
  const response = new Response(new ReadableStream({ start(controller) {
    controller.enqueue(new Uint8Array([0, 255, 10]));
    controller.enqueue(new Uint8Array([13, 128]));
    controller.close();
  } }));
  response.blob = () => { throw new Error("COPY must not buffer into a Blob"); };
  const output = new WritableStream({ write(chunk) { written.push(...chunk); }, close() { closed = true; } });
  assert.equal(await streamCopyDownload(response, output, count => counts.push(count)), 5);
  assert.deepEqual(written, [0, 255, 10, 13, 128]);
  assert.deepEqual(counts, [3, 5]);
  assert.ok(closed);
});

test("COPY download surfaces database errors before writing an error page into the file", async () => {
  let wrote = false;
  const response = new Response(JSON.stringify({ error: { message: "permission denied for table accounts", code: "postgres_error" } }), { status: 403 });
  const output = new WritableStream({ write() { wrote = true; } });
  await assert.rejects(streamCopyDownload(response, output), /permission denied for table accounts/);
  assert.equal(wrote, false);
});

test("COPY download aborts its destination when the response stream fails", async () => {
  let aborted = false;
  const response = new Response(new ReadableStream({ pull(controller) { controller.error(new Error("network disconnected")); } }));
  const output = new WritableStream({ abort() { aborted = true; } });
  await assert.rejects(streamCopyDownload(response, output), /network disconnected/);
  assert.ok(aborted);
});
