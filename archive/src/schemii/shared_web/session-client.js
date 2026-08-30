(() => {
  function createSessionClient({ getToken, setToken, sessionPath = "/api/session" } = {}) {
    if (typeof getToken !== "function" || typeof setToken !== "function") throw new TypeError("Token accessors are required");
    let bootstrap = null;
    let session = null;

    function abortError() {
      const error = new Error("The request was aborted");
      error.name = "AbortError";
      return error;
    }

    function waitFor(promise, signal) {
      if (!signal) return promise;
      if (signal.aborted) return Promise.reject(abortError());
      return new Promise((resolve, reject) => {
        const abort = () => reject(abortError());
        signal.addEventListener("abort", abort, { once: true });
        promise.then(
          value => { signal.removeEventListener("abort", abort); resolve(value); },
          error => { signal.removeEventListener("abort", abort); reject(error); },
        );
      });
    }

    async function parseJson(response, message) {
      try {
        return await response.json();
      } catch {
        const error = new Error(message);
        error.code = "invalid_json_response";
        error.status = response.status;
        throw error;
      }
    }

    async function bootstrapSession(options = {}) {
      if (session && getToken() === session.token) return session;
      const token = getToken();
      if (!bootstrap) {
        bootstrap = (async () => {
          const response = await fetch(sessionPath);
          const payload = await parseJson(response, "The local session returned malformed JSON");
          if (!response.ok) throw new Error(payload.error?.message || "Could not start a local session");
          const validated = window.SchemiiShared.validateSessionResponse
            ? window.SchemiiShared.validateSessionResponse(payload)
            : payload;
          if (typeof validated.token !== "string" || !validated.token) throw new Error("Could not start a local session");
           setToken(validated.token);
           session = validated;
           return validated;
        })();
        bootstrap.finally(() => { bootstrap = null; }).catch(() => {});
      }
      return waitFor(bootstrap, options.signal);
    }

    async function ensureToken(options = {}) {
      const token = getToken();
      if (token) return token;
      return (await bootstrapSession(options)).token;
    }

    function validatePath(path, allowPath) {
      if (typeof path !== "string" || (typeof allowPath === "function" && !allowPath(path))) {
        throw new Error("Request must use an allowed local application API");
      }
    }

    async function authenticatedFetch(path, options = {}, requestOptions = {}) {
      const { allowPath, defaultMessage = "Local application request failed", retryInvalidSession = true } = requestOptions;
      validatePath(path, allowPath);
      const token = await ensureToken({ signal: options.signal });
      const response = await fetch(path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...(options.headers || {}),
          "X-Schemii-Token": token,
        },
      });
      if (response.ok) return response;
      const payload = await response.clone().json().catch(() => ({}));
      if (payload.error?.code === "invalid_session" && retryInvalidSession) {
        if (getToken() === token) { setToken(null); session = null; }
        return authenticatedFetch(path, options, { ...requestOptions, retryInvalidSession: false });
      }
      const error = new Error(payload.error?.message || payload.error || defaultMessage);
      error.code = payload.error?.code;
      error.status = response.status;
      error.payload = payload;
      if (window.SchemiiShared.formatApiError) error.message = window.SchemiiShared.formatApiError(error, defaultMessage);
      throw error;
    }

    async function json(path, options = {}, requestOptions = {}) {
      const response = await authenticatedFetch(path, options, requestOptions);
      const payload = await parseJson(response, "The local application returned malformed JSON");
      return typeof requestOptions.validate === "function" ? requestOptions.validate(payload) : payload;
    }

    return Object.freeze({ bootstrap: bootstrapSession, ensureToken, fetch: authenticatedFetch, json });
  }

  window.SchemiiShared = Object.freeze({ ...(window.SchemiiShared || {}), createSessionClient });
})();
