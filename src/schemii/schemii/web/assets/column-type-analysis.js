// This is an optimization for inspector requests, never migration authorization.
// Only recognize unqualified builtins; custom/quoted types retain exact identity.
export function columnTypeKey(value) {
  const exact = String(value ?? "").trim();
  const source = exact.toLowerCase().replace(/\s+/g, " ");
  const aliases = {
    int2: "smallint", int: "integer", int4: "integer", int8: "bigint",
    float4: "real", float8: "double precision", bool: "boolean",
    decimal: "numeric", varchar: "character varying",
    timestamptz: "timestamp with time zone", timetz: "time with time zone",
  };
  const match = /^(smallint|int2|integer|int|int4|bigint|int8|real|float4|double precision|float8|boolean|bool|numeric|decimal|varchar|character varying|text|uuid|json|jsonb|bytea|date|timestamp|timestamptz|time|timetz)(\s*\(\s*\d+\s*(?:,\s*-?\d+\s*)?\))?( with(?:out)? time zone)?((?:\s*\[\s*\])*)$/.exec(source);
  if (!match) return `exact:${exact}`;
  let base = aliases[match[1]] || match[1];
  let modifier = (match[2] || "").replace(/\s/g, "");
  if (base === "numeric" && /^\(\d+\)$/.test(modifier)) modifier = modifier.replace(")", ",0)");
  if (["timestamp", "time"].includes(base)) base += match[3] || " without time zone";
  else if (match[3]) return `exact:${exact}`;
  if (/^(timestamp|time) (with|without) time zone$/.test(base) && !modifier) modifier = "(6)";
  return `builtin:${base}${modifier}${match[4].replace(/\s/g, "")}`;
}

/** One inspector population owns baseline discovery, a serial queue and errors. */
export function createColumnTypeAnalysis({
  workspaceId, design, loadHistory, analyze, isActive, onStatus = () => {},
  setTimer = callback => setTimeout(callback, 300), clearTimer = clearTimeout,
}) {
  const entries = new Set();
  const controller = new AbortController();
  let historyLoaded = false;
  let matchesBaseline = false;
  let timer = null;
  let running = false;
  let disposed = false;
  let failure = null;
  const active = () => !disposed && isActive();
  const publish = entry => {
    if (active() && entry.isActive()) entry.onChange(entry.state);
  };
  const status = () => {
    if (active()) onStatus({ error: [...entries].some(entry => entry.queued && entry.isActive()) ? failure : null });
  };
  const setState = (entry, state) => { entry.state = state; publish(entry); };
  const needsCheck = entry => entry.sourceType === undefined
    || (entry.sourceType !== null && columnTypeKey(entry.sourceType) !== columnTypeKey(entry.targetType));

  function schedule() {
    if (!active() || running || failure || timer !== null) return;
    timer = setTimer(() => { timer = null; void drain(); });
  }

  async function drain() {
    if (!active() || running || failure) return;
    running = true;
    try {
      if (!historyLoaded) {
        const history = await loadHistory(workspaceId, { signal: controller.signal });
        if (!active()) return;
        historyLoaded = true;
        matchesBaseline = history.designRevision === design?.revision
          && history.baseline?.kind === "postgresql"
          && Boolean(design?.fingerprint)
          && history.baseline.fingerprint === design.fingerprint;
        if (matchesBaseline) {
          for (const entry of entries) entry.sourceType = entry.originalType;
        }
      }
      for (const entry of entries) {
        if (!active()) return;
        if (!entry.isActive() || !entry.queued) continue;
        entry.queued = false;
        if (!needsCheck(entry)) {
          setState(entry, { status: "ready" });
          continue;
        }
        const version = entry.version;
        const targetType = entry.targetType;
        const request = new AbortController();
        entry.request = request;
        const abort = () => request.abort();
        controller.signal.addEventListener("abort", abort, { once: true });
        setState(entry, { status: "pending" });
        try {
          const analysis = await analyze(workspaceId, { columnId: entry.columnId, targetType }, { signal: request.signal });
          if (!active() || !entry.isActive() || version !== entry.version) continue;
          entry.sourceType = analysis.sourceType;
          setState(entry, { status: analysis.requiresConversion ? "conversion-required" : "ready", analysis });
        } catch (error) {
          if (!active() || !entry.isActive() || version !== entry.version) continue;
          // Cancellation is neutral, including transports that use native AbortError.
          if (request.signal.aborted || error?.code === "request_cancelled" || error?.name === "AbortError") {
            setState(entry, { status: "idle" });
            continue;
          }
          entry.queued = true;
          throw error;
        } finally {
          controller.signal.removeEventListener("abort", abort);
          entry.request = null;
        }
      }
    } catch (error) {
      if (!active()) return;
      failure = error;
      for (const entry of entries) {
        if (entry.queued) setState(entry, { status: "unavailable" });
      }
      status();
    } finally {
      running = false;
      if (active() && !failure && [...entries].some(entry => entry.queued && entry.isActive())) schedule();
    }
  }

  function add({ columnId, targetType, isActive: rowActive, onChange }) {
    const entry = { columnId, originalType: targetType, targetType, sourceType: matchesBaseline ? targetType : undefined,
      isActive: rowActive, onChange, state: { status: "idle" }, version: 0, queued: false, request: null };
    entries.add(entry);
    function select(value) {
      if (entry.queued && columnTypeKey(value) === columnTypeKey(entry.targetType)) return;
      if (entry.state.status !== "idle" && columnTypeKey(value) === columnTypeKey(entry.targetType)) return;
      entry.version += 1;
      entry.request?.abort();
      entry.targetType = value;
      entry.queued = needsCheck(entry);
      setState(entry, { status: entry.queued ? (failure ? "unavailable" : "pending") : "ready" });
      if (failure) status();
      schedule();
    }
    select(targetType);
    return {
      select,
      dispose() {
        entry.version += 1;
        entry.request?.abort();
        entry.queued = false;
        entries.delete(entry);
        if (!entries.size) dispose();
      },
    };
  }

  function dispose() {
    disposed = true;
    controller.abort();
    if (timer !== null) clearTimer(timer);
    timer = null;
    for (const entry of entries) entry.request?.abort();
    entries.clear();
  }

  return {
    add, dispose,
    retry() {
      if (!active() || !failure) return;
      failure = null;
      status();
      for (const entry of entries) if (entry.queued) setState(entry, { status: "pending" });
      schedule();
    },
  };
}
