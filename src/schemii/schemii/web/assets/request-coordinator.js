function operationHandle(controller, epoch, signal, finish) {
  let finished = false;
  return Object.freeze({
    epoch,
    signal,
    isCurrent: () => controller.epoch === epoch,
    finish: () => {
      if (finished) return;
      finished = true;
      finish();
    },
  });
}

export function createLatestRequestController() {
  const state = {
    epoch: 0,
    active: null,
  };

  return Object.freeze({
    begin() {
      state.active?.abort();
      const controller = new AbortController();
      const epoch = ++state.epoch;
      state.active = controller;
      return operationHandle(state, epoch, controller.signal, () => {
        if (state.active === controller) state.active = null;
      });
    },
    cancel() {
      state.epoch += 1;
      state.active?.abort();
      state.active = null;
    },
  });
}

export function createWorkspaceOperationController() {
  const state = {
    epoch: 0,
    activeRead: null,
    mutations: new Set(),
  };

  function abortRead() {
    const interrupted = Boolean(state.activeRead);
    state.activeRead?.abort();
    state.activeRead = null;
    return interrupted;
  }

  return Object.freeze({
    beginRead() {
      abortRead();
      const controller = new AbortController();
      const epoch = ++state.epoch;
      state.activeRead = controller;
      return operationHandle(state, epoch, controller.signal, () => {
        if (state.activeRead === controller) state.activeRead = null;
      });
    },
    beginMutation() {
      const interruptedRead = abortRead();
      const controller = new AbortController();
      const epoch = ++state.epoch;
      state.mutations.add(controller);
      const handle = operationHandle(state, epoch, controller.signal, () => {
        state.mutations.delete(controller);
      });
      return Object.freeze({ ...handle, interruptedRead });
    },
    invalidate() {
      state.epoch += 1;
      abortRead();
      for (const controller of state.mutations) controller.abort();
      state.mutations.clear();
    },
    isMutating() {
      return state.mutations.size > 0;
    },
  });
}
