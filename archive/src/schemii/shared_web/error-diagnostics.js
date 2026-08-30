(() => {
  const text = value => typeof value === "string" && value.trim() ? value.trim().slice(0, 1000) : "";

  function errorPayload(error) {
    if (error?.payload?.error && typeof error.payload.error === "object") return error.payload.error;
    if (error?.error && typeof error.error === "object") return error.error;
    return null;
  }

  function formatPostgresDiagnostic(postgres, fallback = "") {
    if (!postgres || typeof postgres !== "object" || Array.isArray(postgres)) return text(fallback);
    const primary = text(postgres.message);
    const lines = [primary || text(fallback)];
    const locationText = [
      /^[0-9A-Z]{5}$/.test(postgres.sqlstate || "") ? `SQLSTATE ${postgres.sqlstate}` : "",
      Number.isInteger(postgres.position) && postgres.position > 0 ? `position ${postgres.position}` : "",
    ].filter(Boolean).join(" · ");
    if (locationText) lines.push(locationText);
    if (text(postgres.detail)) lines.push(`Detail: ${text(postgres.detail)}`);
    if (text(postgres.hint)) lines.push(`Hint: ${text(postgres.hint)}`);
    if (text(postgres.context)) lines.push(`Context: ${text(postgres.context)}`);
    return lines.filter(Boolean).join("\n");
  }

  function formatApiError(error, fallback = "The request could not be completed") {
    const payload = errorPayload(error);
    const message = text(payload?.message) || text(error?.message) || fallback;
    const details = payload?.details && typeof payload.details === "object" && !Array.isArray(payload.details) ? payload.details : {};
    const postgres = details.postgres;
    if (postgres && typeof postgres === "object" && !Array.isArray(postgres)) {
      const diagnostic = formatPostgresDiagnostic(postgres, message);
      const primary = text(postgres.message);
      if (!primary || primary.toLowerCase() === message.toLowerCase() || /(?:failed|could not be (?:read|connected)|rejected)$/.test(message.toLowerCase())) return diagnostic;
      return `${message}\n${diagnostic}`;
    }
    if (!["capability_unavailable", "application_limitation"].includes(payload?.code || error?.code)) return message;
    const lines = [message];
    const requirement = text(details.requiredCapability || details.capability || details.requiredSurface);
    if (requirement) lines.push(`Required: ${requirement}`);
    if (text(details.reason)) lines.push(`Reason: ${text(details.reason)}`);
    const alternative = text(details.safeAlternative || details.guidance);
    if (alternative) lines.push(`Alternative: ${alternative}`);
    return [...new Set(lines)].join("\n");
  }

  function allowedLocalErrorAction(error) {
    const payload = errorPayload(error);
    const action = payload?.details?.settingsAction;
    if (!["capability_unavailable", "application_limitation"].includes(payload?.code || error?.code)
        || !action || typeof action !== "object" || Array.isArray(action)
        || Object.keys(action).sort().join(",") !== "path,type"
        || action.type !== "open_local_settings" || action.path !== "/api/ai/settings") return null;
    return Object.freeze({ type: action.type, path: action.path });
  }

  window.SchemiiShared = Object.freeze({
    ...(window.SchemiiShared || {}),
    allowedLocalErrorAction,
    formatApiError,
    formatPostgresDiagnostic,
  });
})();
