export function createDraftAnalysisLifecycle({
  delay = 280,
  isActive = () => true,
  onChange = () => {},
  setTimer = (callback, milliseconds) => globalThis.setTimeout(callback, milliseconds),
  clearTimer = timer => globalThis.clearTimeout(timer),
} = {}) {
  let timer = null;
  let generation = 0;
  let state = { result: null, error: null, loading: false };

  const snapshot = () => ({ ...state });
  const publish = () => onChange(snapshot());
  const current = requestGeneration => requestGeneration === generation && isActive();

  function reset() {
    if (timer !== null) clearTimer(timer);
    timer = null;
    generation += 1;
    state = { result: null, error: null, loading: false };
  }

  async function execute(analyze, requestGeneration) {
    if (!current(requestGeneration)) return null;
    state = { ...state, error: null, loading: true };
    publish();
    try {
      const result = await analyze();
      if (!current(requestGeneration)) return null;
      state = { ...state, result };
      return result;
    } catch (error) {
      if (!current(requestGeneration)) return null;
      state = { ...state, result: null, error };
      return null;
    } finally {
      if (current(requestGeneration)) {
        state = { ...state, loading: false };
        publish();
      }
    }
  }

  function schedule(analyze, { wait = delay, canAnalyze = () => true } = {}) {
    if (timer !== null) clearTimer(timer);
    const requestGeneration = ++generation;
    timer = setTimer(() => {
      timer = null;
      if (!current(requestGeneration)) return;
      if (!canAnalyze()) {
        state = { result: null, error: null, loading: false };
        publish();
        return;
      }
      void execute(analyze, requestGeneration);
    }, wait);
    return requestGeneration;
  }

  return {
    schedule,
    reset,
    snapshot,
  };
}
