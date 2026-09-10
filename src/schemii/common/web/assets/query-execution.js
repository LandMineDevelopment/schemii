import { requestJson } from "./http.js";

/** Follow an authorized execution URL; rows remain transient in this caller. */
export async function readExecution(response, { maximumRows = 100, timeoutMs = 900000, signal } = {}) {
  let execution = response.execution;
  const base = response.executionUrl || `/api/v1/common/query-executions/${execution.id}`;
  const deadline = Date.now() + timeoutMs;
  while (["reserved", "running"].includes(execution.status)) {
    if (signal?.aborted || Date.now() > deadline) {
      await requestJson(base, { method: "DELETE" });
      throw new Error(signal?.aborted ? "Query cancelled." : "Preview exceeded 15 minutes; cancellation requested. Narrow the query.");
    }
    await new Promise(resolve => setTimeout(resolve, 400));
    execution = await requestJson(base, { timeoutMs });
  }
  if (execution.status !== "succeeded") throw new Error(execution.errorMessage || `Query ${execution.status}`);
  const result = execution.results[0];
  if (!result) throw new Error("No result was returned.");
  const url = `${base}/results/${result.id}`;
  let page = await requestJson(url, { timeoutMs });
  let rows = [...page.rows];
  while (page.nextCursor && rows.length < maximumRows) {
    page = await requestJson(`${url}?cursor=${encodeURIComponent(page.nextCursor)}`, { timeoutMs });
    rows.push(...page.rows);
  }
  return { columns: page.columns, rows: rows.slice(0, maximumRows), plan: response.plan };
}
