(() => {
  const DEFAULTS = Object.freeze({
    name: "",
    host: "127.0.0.1",
    port: 5432,
    database: "",
    user: "",
    sslmode: "prefer",
    timeout: 10,
  });

  function createProfileForm({ fields, defaults = {} } = {}) {
    const required = ["name", "host", "port", "database", "user", "password", "sslmode", "timeout"];
    if (!fields || required.some(name => !fields[name])) throw new TypeError("All PostgreSQL profile fields are required");
    const initial = { ...DEFAULTS, ...defaults };

    function read() {
      return {
        name: fields.name.value.trim(),
        host: fields.host.value.trim(),
        port: Number(fields.port.value),
        dbname: fields.database.value.trim(),
        user: fields.user.value.trim(),
        password: fields.password.value,
        sslmode: fields.sslmode.value,
        timeout: Number(fields.timeout.value),
      };
    }

    function fill(profile = null) {
      if (fields.id) fields.id.value = profile?.id ?? "";
      fields.name.value = profile?.name ?? initial.name;
      fields.host.value = profile?.host ?? initial.host;
      fields.port.value = profile?.port ?? initial.port;
      fields.database.value = profile?.dbname ?? initial.database;
      fields.user.value = profile?.user ?? initial.user;
      fields.password.value = "";
      fields.sslmode.value = profile?.sslmode ?? initial.sslmode;
      fields.timeout.value = profile?.timeout ?? initial.timeout;
    }

    function clearPassword() {
      fields.password.value = "";
    }

    return Object.freeze({ read, fill, clearPassword, profileId: () => fields.id?.value ?? "" });
  }

  function createProfileRepository({ postgresClient } = {}) {
    if (!postgresClient || typeof postgresClient.request !== "function") throw new TypeError("A PostgreSQL client is required");
    return Object.freeze({
      async list() {
        const result = await postgresClient.request("/api/postgres/profiles");
        return Array.isArray(result.profiles) ? result.profiles : [];
      },
      save(profileId, payload) {
        const path = profileId ? `/api/postgres/profiles/${encodeURIComponent(profileId)}` : "/api/postgres/profiles";
        return postgresClient.request(path, {
          method: profileId ? "PUT" : "POST",
          body: JSON.stringify(payload),
        });
      },
      test(profileId) {
        return postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}/test`, {
          method: "POST", body: "{}",
        });
      },
      deletionImpact(profileId) {
        return postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}/deletion-impact`);
      },
      remove(profileId, preview) {
        return postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}`, {
          method: "DELETE",
          body: JSON.stringify({ profileFingerprint: preview.profileFingerprint, impactFingerprint: preview.impactFingerprint }),
        });
      },
      async namespaceCatalog(profileId, database, { scope = "user", pageSize = 100 } = {}) {
        const entries = [];
        let cursor = null;
        let identity = null;
        do {
          const query = new URLSearchParams({ database, scope, pageSize: String(pageSize), ...(cursor ? { cursor } : {}) });
          const result = await postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}/namespaces?${query}`);
          if (identity && (result.profileId !== identity.profileId || result.database !== identity.database || result.scope !== identity.scope || result.catalogFingerprint !== identity.catalogFingerprint)) throw new Error("Namespace catalog changed while loading; refresh it");
          identity = result;
          entries.push(...result.entries);
          cursor = result.page.hasMore ? result.page.nextCursor : null;
        } while (cursor);
        return { ...identity, entries, namespaces: entries.map(entry => entry.name), page: { ...identity.page, returned: entries.length, hasMore: false, nextCursor: null } };
      },
      async namespaces(profileId, database, options) {
        const result = await this.namespaceCatalog(profileId, database, options);
        return result.namespaces;
      },
      async relationCatalog(profileId, database, namespace, { kind = null, search = "", pageSize = 100, onPage = null } = {}) {
        const entries = [];
        let cursor = null;
        let identity = null;
        do {
          const query = new URLSearchParams({ database, namespace, pageSize: String(pageSize), ...(kind ? { kind } : {}), ...(search ? { search } : {}), ...(cursor ? { cursor } : {}) });
          const result = await postgresClient.request(`/api/postgres/profiles/${encodeURIComponent(profileId)}/relations?${query}`);
          if (identity && (result.profileId !== identity.profileId || result.database !== identity.database || result.namespace !== identity.namespace || result.catalogFingerprint !== identity.catalogFingerprint)) throw new Error("Relation catalog changed while loading; refresh it");
          identity = result;
          entries.push(...result.entries);
          if (typeof onPage === "function") onPage(entries.length, result.page.hasMore);
          cursor = result.page.hasMore ? result.page.nextCursor : null;
        } while (cursor);
        return { ...identity, entries, relations: entries, page: { ...identity.page, returned: entries.length, hasMore: false, nextCursor: null } };
      },
    });
  }

  function initializeNamespaceSelect(select, namespaces, { preferred = null, emptyLabel = "No user namespaces found" } = {}) {
    const entries = Array.isArray(namespaces) ? namespaces.map(item => typeof item === "string" ? { name: item, classification: "user" } : item) : [];
    const values = entries.map(entry => entry.name);
    select.replaceChildren(...(values.length ? entries.map(entry => new Option(entry.classification === "user" ? entry.name : `${entry.name} (${entry.classification.replaceAll("_", " ")})`, entry.name)) : [new Option(emptyLabel, "")]));
    const selected = values.includes(preferred) ? preferred : values[0] ?? null;
    if (selected) select.value = selected;
    select.disabled = !values.length;
    return selected;
  }

  const TARGET_STATE_LABELS = Object.freeze({
    suggested: "Suggested",
    selected: "Selected",
    linked: "Linked",
    verified: "Verified",
  });

  function targetPresentation({ state, profileName, profileId, database, namespace, relation = null, verifiedAt = null, verificationSource = null } = {}) {
    if (!Object.hasOwn(TARGET_STATE_LABELS, state)) throw new TypeError("A supported target presentation state is required");
    const identity = [profileName ? `${profileName}${profileId ? ` (${profileId})` : ""}` : profileId, database && namespace ? `${database}.${namespace}${relation ? `.${relation}` : ""}` : database].filter(Boolean).join(" · ");
    const freshness = state === "verified"
      ? `${verificationSource || "PostgreSQL"}${verifiedAt ? ` · ${new Date(verifiedAt).toLocaleString()}` : " · current response"}`
      : verificationSource || "Browser workspace state";
    return Object.freeze({ state, label: TARGET_STATE_LABELS[state], identity: identity || "No complete PostgreSQL target", freshness });
  }

  function formatTargetPresentation(target) {
    return `${target.label}: ${target.identity} · Source: ${target.freshness}`;
  }

  function profileDeletionConfirmation(profile, preview) {
    const impact = preview?.impact;
    if (!impact || typeof impact !== "object" || Array.isArray(impact)) throw new TypeError("Profile deletion impact is required");
    const affected = Object.entries(impact).flatMap(([kind, items]) => {
      if (!Array.isArray(items)) throw new TypeError("Profile deletion impact is invalid");
      const label = kind.replaceAll("_", " ");
      return items.map(item => `${label}: ${typeof item === "string" ? item : JSON.stringify(item)}`);
    });
    const identity = `${profile?.name || preview.profileId} (${preview.profileId}) · ${profile?.dbname || "unknown database"}`;
    const details = affected.length ? affected.map(item => `- ${item}`).join("\n") : "- No saved or active dependencies reported";
    return `Permanently delete PostgreSQL connection ${identity}?\n\nImpact (${affected.length}):\n${details}\n\nDependent resources are not deleted, but their connection will be unavailable. This cannot be undone.`;
  }

  window.SchemiiShared = Object.freeze({
    ...(window.SchemiiShared || {}), createProfileForm, createProfileRepository, initializeNamespaceSelect,
    targetPresentation, formatTargetPresentation, profileDeletionConfirmation,
  });
})();
