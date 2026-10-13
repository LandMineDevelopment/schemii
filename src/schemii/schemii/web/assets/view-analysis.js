function aborted(error, signal) {
  return signal.aborted || error?.name === "AbortError" || error?.code === "request_cancelled";
}

export function createViewAnalysisController({
  keyOf,
  load,
  onChange = () => {},
  maximumEntries = 48,
}) {
  const cache = new Map();
  let current = { key: null, status: "idle", analysis: null, error: null };
  let request = null;
  let token = 0;

  function publish(next, notify) {
    current = next;
    if (notify) onChange({ ...current });
    return { ...current };
  }

  function remember(key, analysis) {
    cache.delete(key);
    cache.set(key, analysis);
    while (cache.size > maximumEntries) cache.delete(cache.keys().next().value);
  }

  function cancelRequest() {
    token += 1;
    request?.controller.abort();
    request = null;
  }

  function clear({ notify = false } = {}) {
    cancelRequest();
    cache.clear();
    publish({ key: null, status: "idle", analysis: null, error: null }, notify);
  }

  function select(context, { force = false, notify = true } = {}) {
    const key = context ? keyOf(context) : null;
    if (!key) {
      cancelRequest();
      return Promise.resolve(publish({ key: null, status: "idle", analysis: null, error: null }, notify));
    }
    if (!force && request?.key === key) return request.promise;

    const cached = cache.get(key) || null;
    if (!force && cached) {
      cancelRequest();
      return Promise.resolve(publish({ key, status: "ready", analysis: cached, error: null }, notify));
    }

    cancelRequest();
    const requestToken = token;
    const controller = new AbortController();
    publish({ key, status: "loading", analysis: cached, error: null }, notify);
    const promise = Promise.resolve()
      .then(() => load(context, { signal: controller.signal }))
      .then(analysis => {
        if (requestToken !== token || controller.signal.aborted) return { ...current };
        remember(key, analysis);
        request = null;
        return publish({ key, status: "ready", analysis, error: null }, true);
      })
      .catch(error => {
        if (requestToken !== token || aborted(error, controller.signal)) return { ...current };
        request = null;
        return publish({ key, status: "error", analysis: cached, error }, true);
      });
    request = { key, controller, promise };
    return promise;
  }

  return Object.freeze({
    select,
    retry(context, options = {}) {
      return select(context, { ...options, force: true });
    },
    snapshot() {
      return { ...current };
    },
    clear,
    cancel() {
      cancelRequest();
    },
  });
}

// The inspector and object browser share results, but selecting one view must
// not cancel another visible view's analysis. Keep lightweight shape summaries
// per current view, and bound both requests and the aggregate rich result cache.
export function createViewAnalysisCollection({
  load,
  onChange = () => {},
  maximumConcurrent = 4,
  maximumEntries = 48,
}) {
  if (!Number.isInteger(maximumEntries) || maximumEntries < 1) {
    throw new RangeError("View analysis cache must retain at least one result.");
  }
  if (!Number.isInteger(maximumConcurrent) || maximumConcurrent < 1) {
    throw new RangeError("View analysis must permit at least one active request.");
  }
  const entries = new Map();
  const cache = new Map();
  const queue = [];
  let active = 0;
  let selectedKey = null;

  function drain() {
    while (active < maximumConcurrent && queue.length) {
      const job = queue.shift();
      job.signal.removeEventListener("abort", job.abort);
      if (job.signal.aborted) {
        job.abort();
        continue;
      }
      active += 1;
      Promise.resolve()
        .then(() => load(job.context, { signal: job.signal }))
        .then(job.resolve, job.reject)
        .finally(() => {
          active -= 1;
          drain();
        });
    }
  }

  function schedule(context, { signal }) {
    return new Promise((resolve, reject) => {
      const job = { context, signal, resolve, reject };
      job.abort = () => {
        const index = queue.indexOf(job);
        if (index >= 0) queue.splice(index, 1);
        reject(new DOMException("View analysis cancelled", "AbortError"));
      };
      if (signal.aborted) {
        job.abort();
        return;
      }
      signal.addEventListener("abort", job.abort, { once: true });
      queue.push(job);
      drain();
    });
  }

  function sync(contexts) {
    const keys = new Set(contexts.map(context => context.key));
    for (const [key, entry] of entries) {
      if (keys.has(key)) continue;
      entries.delete(key);
      cache.delete(key);
      entry.request?.controller.abort();
    }
    if (!keys.has(selectedKey)) selectedKey = null;
    for (const context of contexts) {
      if (!entries.has(context.key)) entries.set(context.key, {
        context, status: "idle", columnCount: null, request: null,
      });
      else entries.get(context.key).context = context;
    }
  }

  function snapshot(context) {
    const entry = entries.get(context?.key);
    const cached = cache.get(context?.key);
    return {
      key: context?.key || null,
      status: entry?.status || "idle",
      columnCount: entry?.columnCount ?? null,
      analysis: cached?.analysis || null,
      error: cached?.error || null,
    };
  }

  function summary(context) {
    const entry = entries.get(context?.key);
    return {
      key: context?.key || null,
      status: entry?.status || "idle",
      columnCount: entry?.columnCount ?? null,
    };
  }

  function remember(key, analysis, error = null) {
    cache.delete(key);
    cache.set(key, { analysis, error });
    while (cache.size > maximumEntries) {
      const oldest = [...cache.keys()].find(candidate => candidate !== selectedKey);
      cache.delete(oldest);
    }
  }

  function ensure(context, { force = false, notify = false } = {}) {
    const entry = entries.get(context?.key);
    if (!entry) return Promise.resolve(snapshot(null));
    if (!force && entry.request) return entry.request.promise;
    const cached = cache.get(context.key);
    if (!force && cached) {
      remember(context.key, cached.analysis, cached.error);
      return Promise.resolve(snapshot(context));
    }

    entry.request?.controller.abort();
    const controller = new AbortController();
    const request = { controller, promise: null };
    entry.request = request;
    entry.status = "loading";
    if (cached) cached.error = null;
    const current = () => entries.get(context.key) === entry && entry.request === request;
    request.promise = Promise.resolve()
      .then(() => schedule(entry.context, { signal: controller.signal }))
      .then(analysis => {
        if (!current() || controller.signal.aborted) return snapshot(context);
        entry.request = null;
        entry.status = "ready";
        entry.columnCount = analysisColumnCount(analysis);
        remember(context.key, analysis);
        const next = { ...snapshot(context), analysis };
        onChange(next, entry.context);
        return next;
      })
      .catch(error => {
        if (!current() || aborted(error, controller.signal)) return snapshot(context);
        entry.request = null;
        entry.status = "error";
        const analysis = cache.get(context.key)?.analysis || null;
        remember(context.key, analysis, error);
        const next = { ...snapshot(context), analysis, error };
        onChange(next, entry.context);
        return next;
      });
    if (notify) onChange(snapshot(context), entry.context);
    return request.promise;
  }

  return Object.freeze({
    sync,
    snapshot,
    summary,
    ensure,
    select(context, options = {}) {
      selectedKey = entries.has(context?.key) ? context.key : null;
      return ensure(context, options);
    },
    clear() { selectedKey = null; sync([]); },
  });
}

function analysisColumnCount(analysis) {
  const outputs = analysis?.outputs;
  const incomplete = analysis?.warnings?.some(warning => (
    warning === "unresolved_wildcard" || warning === "too_many_outputs"
  ));
  if (Array.isArray(outputs) && !incomplete && (outputs.length || analysis.status !== "partial")) {
    return outputs.length;
  }
  return null;
}

export function desiredViewColumnSummary(state) {
  const count = state?.columnCount !== undefined ? state.columnCount : analysisColumnCount(state?.analysis);
  if (count !== null) {
    const summary = `${count} ${count === 1 ? "column" : "columns"}`;
    if (state.status === "loading") return `${summary} · refreshing…`;
    if (state.status === "error") return `${summary} · analysis unavailable`;
    return summary;
  }
  if (state?.status === "loading") return "Analyzing columns…";
  if (state?.status === "ready" || state?.status === "error") return "Column count unavailable";
  return "Columns not analyzed";
}
