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
