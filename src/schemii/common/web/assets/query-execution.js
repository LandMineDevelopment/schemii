import { requestJson } from "./http.js";

/** Read a bounded, transient preview and release its retained database snapshot. */
export async function readExecution(response, { maximumRows = 100, timeoutMs = 900000, signal, onActivity = () => {} } = {}) {
  let execution = response.execution;
  const base = response.executionUrl || `/api/v1/common/query-executions/${execution.id}`;
  const started = Date.now(), controller = new AbortController();
  let timedOut = false, cancellation, cancellationFailed = false, monitoring = false, activity = null, finished = false;
  const cancel = () => {
    controller.abort();
    cancellation ||= requestJson(base, { method: "DELETE" }).catch(() => { cancellationFailed = true; });
  };
  signal?.addEventListener("abort", cancel, { once: true });
  const timeout = setTimeout(() => { timedOut = true; cancel(); }, timeoutMs);
  const notify = () => { if (!finished) onActivity({ ...activity, elapsedMs: Date.now() - started }); };
  const monitor = async () => {
    if (monitoring || controller.signal.aborted) return;
    monitoring = true;
    try { activity = await requestJson(`${base}/activity`, { signal: controller.signal }); }
    catch { activity = { ...activity, monitorUnavailable: true }; }
    finally { monitoring = false; notify(); }
  };
  const ticker = setInterval(notify, 100);
  const poller = setInterval(() => void monitor(), 1000);
  let result, output;
  try {
    if (signal?.aborted) cancel();
    void monitor();
    while (["reserved", "running"].includes(execution.status)) {
      await new Promise(resolve => setTimeout(resolve, 400));
      execution = await requestJson(base, { timeoutMs, signal: controller.signal });
    }
    if (execution.status !== "succeeded") throw new Error(execution.errorMessage || `Query ${execution.status}`);
    result = execution.results[0];
    if (!result) throw new Error("No result was returned.");
    const url = `${base}/results/${result.id}`;
    let page = await requestJson(url, { timeoutMs, signal: controller.signal });
    const rows = [...page.rows];
    while (page.nextCursor && rows.length < maximumRows) {
      page = await requestJson(`${url}?cursor=${encodeURIComponent(page.nextCursor)}`, { timeoutMs, signal: controller.signal });
      rows.push(...page.rows);
    }
    output = { columns: page.columns, rows: rows.slice(0, maximumRows), plan: response.plan, elapsedMs: Date.now() - started };
    return output;
  } catch (error) {
    cancel();
    await cancellation;
    if (cancellationFailed) throw new Error("The query request stopped, but server cancellation could not be confirmed. Check live queries before running it again.");
    if (timedOut) throw new Error(`Preview exceeded ${(timeoutMs / 60000).toFixed(1)} minutes; cancellation requested. Narrow the query.`);
    if (signal?.aborted) throw new Error("Query cancelled.");
    throw error;
  } finally {
    finished = true;
    clearTimeout(timeout); clearInterval(ticker); clearInterval(poller);
    signal?.removeEventListener("abort", cancel);
    controller.abort();
    await cancellation;
    // A successful receipt can still own a lazy cursor; closing the result
    // releases the connection even when only the first preview page was read.
    for (const retained of execution.results || []) {
      try { await requestJson(`${base}/results/${retained.id}`, { method: "DELETE" }); }
      catch { if (output) output.cleanupWarning = "Preview loaded, but releasing its database snapshot could not be confirmed."; }
    }
  }
}
