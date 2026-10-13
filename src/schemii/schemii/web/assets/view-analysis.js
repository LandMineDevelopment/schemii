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
// not cancel another visible view's analysis. Retain only the current design's
// contexts and bound the requests initiated by a large object browser.
export function createViewAnalysisCollection({ load, onChange = () => {}, maximumConcurrent = 4 }) {
  const entries = new Map();
  const queue = [];
  let active = 0;

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
      entry.controller?.clear();
    }
    for (const context of contexts) {
      if (!entries.has(context.key)) entries.set(context.key, { context, controller: null });
      else entries.get(context.key).context = context;
    }
  }

  function snapshot(context) {
    return entries.get(context?.key)?.controller?.snapshot()
      || { key: context?.key || null, status: "idle", analysis: null, error: null };
  }

  function ensure(context, { force = false, notify = false } = {}) {
    const entry = entries.get(context?.key);
    if (!entry) return Promise.resolve(snapshot(null));
    if (!entry.controller) {
      entry.controller = createViewAnalysisController({
        keyOf: value => value.key,
        load: schedule,
        maximumEntries: 1,
        onChange: value => {
          if (entries.get(context.key) === entry) onChange(value, entry.context);
        },
      });
    }
    const current = entry.controller.snapshot();
    // Rendering a failed row does not retry it. Recovery is an explicit action.
    if (!force && current.status === "error") return Promise.resolve(current);
    return entry.controller.select(entry.context, { force, notify });
  }

  return Object.freeze({
    sync,
    snapshot,
    ensure,
    clear() { sync([]); },
  });
}

export function desiredViewColumnSummary(state) {
  const analysis = state?.analysis;
  const outputs = analysis?.outputs;
  const incomplete = analysis?.warnings?.some(warning => (
    warning === "unresolved_wildcard" || warning === "too_many_outputs"
  ));
  if (Array.isArray(outputs) && !incomplete && (outputs.length || analysis.status !== "partial")) {
    const count = outputs.length;
    const summary = `${count} ${count === 1 ? "column" : "columns"}`;
    if (state.status === "loading") return `${summary} · refreshing…`;
    if (state.status === "error") return `${summary} · analysis unavailable`;
    return summary;
  }
  if (state?.status === "loading") return "Analyzing columns…";
  if (state?.status === "ready" || state?.status === "error") return "Column count unavailable";
  return "Columns not analyzed";
}
