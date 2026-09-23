"""Owner-scoped Pi transport. Credentials cross only the private sidecar boundary."""

from dataclasses import dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from urllib.request import Request, urlopen

from schemii.common.admin_config import AiPolicy
from schemii.common.ai.context import compact_tool_context
from schemii.common.metadata.limit_events import LimitEventNotice


class PiError(RuntimeError):
    def __init__(self, code, message=None, status=502, *, limit_event=None):
        self.code = code
        self.status = status
        self.limit_event = limit_event
        super().__init__(message or _MESSAGES.get(code, _MESSAGES["provider_failed"]))


_MESSAGES = {
    "provider_failed": "The AI provider request failed. Retry or reconnect your account.",
    "provider_request_rejected": "The provider rejected the AI request format. Reconnecting will not fix this; the request needs to be corrected.",
    "rate_limited": "The selected provider has reached its usage or rate limit. Wait and retry, or explicitly select another available model. No fallback model was used.",
    "billing_required": "The selected provider requires billing or credits. Check that provider account or explicitly select another model. No fallback model was used.",
    "provider_access_denied": "The provider denied access. Check your API key, billing setup, or model access, then retry. No fallback model was used.",
    "credentials_required": "Connect your own provider account before running this request.",
    "instance_access_denied": "An administrator has not enabled this AI provider for your account, app, and database.",
    "instance_policy_changed": "The administrator changed this connection's model or reasoning. Start a new response with the current settings.",
    "credentials_changed": "Your AI credentials were disconnected, replaced, or expired. Reconnect and try again.",
    "reasoning_unsupported": "The selected model does not support this reasoning effort. Choose an available level or Model default.",
    "model_unavailable": "This model is not available through the connected provider account. Choose another model; your conversation is kept. No fallback model was used.",
    "cancelled": "The AI request was cancelled.",
    "timeout": "The AI request exceeded the configured time limit. Ask a narrower question and retry.",
    "busy": "AI capacity is currently in use. Wait for a running request to finish.",
    "context_too_large": "The AI context exceeds the configured size limit. Start a new chat or request fewer rows or a smaller model scope.",
    "response_too_large": "The AI response exceeds the configured size limit. Ask for a shorter answer or split the work into smaller requests.",
    "permission_changed": "Assistant permissions changed. Request a new response.",
    "tool_denied": "The model requested a tool that is not enabled for this turn.",
    "invalid_response": "The AI runtime returned an invalid response. Try again.",
    "unavailable": "The AI runtime is temporarily unavailable. Try again shortly.",
}


@dataclass(frozen=True)
class PiReply:
    text: str
    tool_calls: tuple[tuple[str, dict], ...]
    assistant_message: dict | None = None
    tool_call_ids: tuple[str, ...] = ()


class PiRuntime:
    SHARED_CODEX_ID = "instance-codex"

    def __init__(self, client, store, catalog, policy=None, *, opener=urlopen,
                 instance_store=None, auth=None):
        self.client, self.store, self.catalog = client, store, catalog
        self.instance_store, self.auth = instance_store, auth
        self.policy = policy or AiPolicy()
        self._open = opener
        self._identities = set()
        self._identity_lock = threading.Lock()
        self._catalog_lock = threading.Lock()
        self._supported = None
        self._supported_until = 0
        # Adapter catalogs describe supported models, not account entitlements.
        # Keep only short-lived, generation-scoped denials, never provider text.
        self._model_denials = {}
        # Account discovery retains only supported model IDs, timestamps and safe
        # errors in memory. Credential generations isolate reconnects and owners.
        self._account_catalogs = {}
        self._instance_codex_catalog = None

    def _account_catalog(self, owner, provider_id, generation):
        now = time.monotonic()
        with self._catalog_lock:
            self._account_catalogs = {key: value for key, value in self._account_catalogs.items()
                if now - value["updated"] < self.policy.catalog_max_stale_seconds
                and (key[:2] != (owner, provider_id) or key[2] == generation)}
            return self._account_catalogs.get((owner, provider_id, generation), {})

    def _refresh_account_catalog(self, owner, metadata):
        provider_id, credential_id = metadata["provider_id"], metadata["credential_id"]
        generation = metadata["generation"]
        key = (owner, provider_id, generation)
        identity = (owner, credential_id)
        previous = self._account_catalog(owner, provider_id, generation)
        acquired = False
        result = {**previous, "updated": time.monotonic(), "checkedAt": datetime.now(timezone.utc).isoformat()}
        try:
            with self._identity_lock:
                if identity in self._identities:
                    raise PiError("busy")
                self._identities.add(identity)
                acquired = True
            record = self.store.get(owner, credential_id)
            if record is None or record["generation"] != generation:
                raise PiError("credentials_changed")
            reply = self.client.call("/models/refresh", {
                "owner": owner, "providerId": provider_id, "credentialId": credential_id,
                "generation": generation, "credential": record["credential"],
            })
            current = self.store.get(owner, credential_id)
            if current is None or current["generation"] != generation:
                raise PiError("credentials_changed")
            if reply.get("generation") != generation:
                raise PiError("credentials_changed")
            if "credential" in reply:
                if not self.store.save(
                    owner, credential_id, provider_id, reply["credential"], generation,
                ):
                    raise PiError("credentials_changed")
            if reply.get("type") == "error":
                raise PiError(reply.get("code", "unavailable"))
            models = reply.get("models")
            if reply.get("type") != "result" or not isinstance(models, list) or any(
                not isinstance(item, dict) or item.get("providerId") != provider_id
                or not isinstance(item.get("id"), str) for item in models
            ):
                raise PiError("invalid_response")
            result.update(ids=frozenset(item["id"] for item in models), success=time.monotonic(), error=None)
        except Exception as error:
            code = error.code if isinstance(error, PiError) else "unavailable"
            result["error"] = (
                "Model availability could not be checked while this account is in use. Try again after the current request finishes."
                if code == "busy" else
                "Connect your provider account again to check model availability."
                if code in {"credentials_required", "credentials_changed"} else
                "Could not refresh this provider's model list. Previously listed models may be outdated; reopen the list to retry."
            )
        finally:
            if acquired:
                with self._identity_lock:
                    self._identities.discard(identity)
        # Late discovery must not restore a disconnected/replaced account's list.
        current = next((row for row in self.store.list(owner)
                        if row["credential_id"] == credential_id and row.get("connected")), None)
        if acquired and current and current["generation"] == generation:
            with self._catalog_lock:
                self._account_catalogs[key] = result
        return result

    def _denied_models(self, owner, credentials):
        current = time.monotonic()
        generations = {row["provider_id"]: row["generation"] for row in credentials if row.get("connected")}
        with self._catalog_lock:
            self._model_denials = {key: until for key, until in self._model_denials.items()
                if until > current and (key[0] != owner or generations.get(key[1], 0) == key[2])}
            return {(key[1], key[3]) for key in self._model_denials if key[0] == owner}

    def _deny_model(self, owner, provider_id, model_id, generation):
        with self._catalog_lock:
            current = time.monotonic()
            self._model_denials = {key: until for key, until in self._model_denials.items() if until > current}
            self._model_denials[owner, provider_id, generation, model_id] = current + self.policy.catalog_refresh_seconds

    def _supported_models(self):
        with self._catalog_lock:
            if self._supported is None or time.monotonic() >= self._supported_until:
                models = self.client.call("/models")["models"]
                if not isinstance(models, list):
                    raise PiError("invalid_response")
                self._supported = models
                self._supported_until = time.monotonic() + self.policy.runtime_status_cache_seconds
            return self._supported

    @staticmethod
    def credential_id(provider_id):
        return {"openai-codex": "codex-prototype", "openai": "openai"}.get(provider_id)

    def status(self, owner, *, refresh=False, zen_scope=None):
        try:
            supported = self._supported_models()
            credentials = self.store.list(owner)
            refresh_results = {}
            if refresh and self.policy.enabled:
                records = [row for row in credentials if row.get("connected")
                           and row["provider_id"] in {"openai-codex", "openai"}]
                # Providers are independent, but each identity shares the turn's
                # credential lease so OAuth rotation cannot race a running chat.
                if records:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(lambda row: self._refresh_account_catalog(owner, row), records))
                        refresh_results = {row["provider_id"]: result for row, result in zip(records, results)}
                credentials = self.store.list(owner)
            connected = {row["provider_id"] for row in credentials if row.get("connected")}
            instance_grants = {}
            if self.instance_store:
                for actual_provider, public_provider in (("opencode", "opencode"),
                                                         ("openai-codex", self.SHARED_CODEX_ID)):
                    if not self.instance_store.status(actual_provider)["connected"]:
                        continue
                    if zen_scope is None:
                        if any(grant["userId"] == owner for grant in self.instance_store.list_grants(actual_provider)):
                            connected.add(public_provider)
                    else:
                        try:
                            scope = self.require_instance_access(owner, zen_scope, actual_provider)
                        except Exception:
                            pass  # An inaccessible resource is never selectable.
                        else:
                            connected.add(public_provider)
                            instance_grants[public_provider] = self.instance_store.get_grant(
                                owner, *scope, provider_id=actual_provider)
            generations = {row["provider_id"]: row["generation"] for row in credentials if row.get("connected")}
            denied = self._denied_models(owner, credentials)
            zen = self.catalog.snapshot() if self.catalog else {"models": [], "error": None, "checkedAt": None}
            verified_zen = {model["id"] for model in zen["models"]}
            providers = []
            for provider_id, name in (("openai-codex", "ChatGPT Codex"),
                                      ("openai", "OpenAI"), ("opencode", "OpenCode Zen"),
                                      (self.SHARED_CODEX_ID, "Shared ChatGPT Codex")):
                account = self._account_catalog(owner, provider_id, generations.get(provider_id, 0))
                refresh_error = refresh_results.get(provider_id, {}).get("error")
                ids = account.get("ids")
                if time.monotonic() - account.get("success", 0) >= self.policy.catalog_max_stale_seconds:
                    ids = None
                models = [{"id": item["id"], "name": item.get("name", item["id"]),
                           "reasoningLevels": item.get("reasoningLevels", ["default"]),
                           "status": "unavailable" if (provider_id, item["id"]) in denied
                               or (ids is not None and item["id"] not in ids) else "active"}
                          for item in supported if item.get("providerId") == (
                              "openai-codex" if provider_id == self.SHARED_CODEX_ID else provider_id)
                          and (provider_id != "opencode" or item["id"] in verified_zen)]
                if provider_id == self.SHARED_CODEX_ID:
                    grant = instance_grants.get(provider_id)
                    if grant:
                        models = [model for model in models if model["id"] == grant["modelId"]]
                        for model in models:
                            model["reasoningLevels"] = [grant["reasoningEffort"]]
                authenticated = provider_id in connected
                available = any(model["status"] == "active" for model in models) and authenticated
                item = {"id": provider_id, "name": name, "available": available,
                        "authenticated": authenticated, "models": models,
                        "authMethods": [], "catalogError": refresh_error or account.get("error"),
                        "catalogCheckedAt": account.get("checkedAt")}
                if provider_id == "opencode":
                    item["privacy"] = "Free Zen models may use prompts and selected context for training. Do not send confidential or personal data."
                    item["catalogCheckedAt"] = zen.get("checkedAt")
                    if zen.get("error"):
                        item["catalogError"] = "Could not refresh the free-model catalog. The list uses its last unexpired snapshot."
                if provider_id == self.SHARED_CODEX_ID:
                    item["adminManaged"] = True
                    if instance_grants.get(provider_id):
                        item["selectedModelId"] = instance_grants[provider_id]["modelId"]
                        item["selectedReasoningEffort"] = instance_grants[provider_id]["reasoningEffort"]
                providers.append(item)
            errors = [f'{provider["name"]}: {provider["catalogError"]}' for provider in providers if provider["catalogError"]]
            return {"enabled": self.policy.enabled, "healthy": True, "providers": providers,
                    "message": " ".join(errors) or None}
        except Exception:
            return {"enabled": self.policy.enabled, "healthy": False, "providers": [],
                    "message": _MESSAGES["unavailable"]}

    def cancel(self, owner, turn_id):
        try:
            self.client.call("/turns/cancel", {"owner": owner, "turnId": turn_id})
        except Exception:
            pass  # Cancellation is best effort; terminal acceptance checks authority again.

    def require_available_model(self, owner, provider_id, model_id):
        """Validate the current owner/provider selection before admitting work."""
        if not self.policy.enabled:
            raise PiError("unavailable", status=503)
        status = self.status(owner)
        provider = next((p for p in status["providers"] if p["id"] == provider_id), None)
        if not provider or not provider["available"] or model_id not in {
            m["id"] for m in provider["models"] if m["status"] == "active"
        }:
            raise PiError("model_unavailable", status=409)

    def require_instance_access(self, owner, zen_scope, provider_id="opencode"):
        if self.instance_store is None or not callable(zen_scope):
            raise PiError("instance_access_denied", status=403)
        scope = zen_scope()
        if not isinstance(scope, tuple) or len(scope) != 3:
            raise PiError("instance_access_denied", status=403)
        product, connection_owner_id, connection_id = scope
        if self.auth and self.auth.enabled and (
            not self.auth.user(owner) or f"{product}:access" not in self.auth.capabilities(owner)
        ):
            raise PiError("instance_access_denied", status=403)
        if not self.instance_store.status(provider_id)["connected"] or not self.instance_store.has_grant(
            owner, product, connection_owner_id, connection_id, provider_id=provider_id
        ):
            raise PiError("instance_access_denied", status=403)
        return scope

    def require_instance_policy(self, owner, provider_id, model_id, reasoning_effort, scope):
        if provider_id != self.SHARED_CODEX_ID:
            return None
        actual_scope = self.require_instance_access(owner, scope, "openai-codex")
        grant = self.instance_store.get_grant(owner, *actual_scope, provider_id="openai-codex")
        if not grant:
            raise PiError("instance_access_denied", status=403)
        if grant["modelId"] != model_id or grant["reasoningEffort"] != reasoning_effort:
            raise PiError("instance_policy_changed", status=409)
        return grant

    def require_reasoning_effort(self, owner, provider_id, model_id, reasoning_effort="default"):
        if reasoning_effort == "default":
            return
        provider = next((item for item in self.status(owner)["providers"] if item["id"] == provider_id), {})
        model = next((item for item in provider.get("models", []) if item["id"] == model_id), {})
        if reasoning_effort not in model.get("reasoningLevels", ["default"]):
            raise PiError("reasoning_unsupported", status=422)

    def test_instance_codex(self):
        """Verify the installation credential against Codex's live model endpoint."""
        if self.instance_store is None:
            raise PiError("unavailable", status=503)
        identity = ("schemii-instance", "instance-codex")
        with self._identity_lock:
            if identity in self._identities:
                raise PiError("busy", status=429)
            self._identities.add(identity)
        record = None
        try:
            record = self.instance_store.credential("openai-codex")
            if record is None:
                raise PiError("credentials_required", status=409)
            generation = record["generation"]
            reply = self.client.call("/models/refresh", {
                "owner": identity[0], "providerId": "openai-codex",
                "credentialId": identity[1], "generation": generation,
                "credential": record["credential"],
            })
            if (reply.get("generation") != generation or
                    self.instance_store.generation("openai-codex") != generation):
                raise PiError("credentials_changed", status=409)
            if "credential" in reply and not self.instance_store.save_credential(
                    "openai-codex", reply["credential"], generation):
                raise PiError("credentials_changed", status=409)
            if reply.get("type") == "error":
                code = reply.get("code")
                raise PiError(code if code in _MESSAGES else "provider_failed")
            models = reply.get("models")
            if reply.get("type") != "result" or not isinstance(models, list) or any(
                    not isinstance(item, dict) or item.get("providerId") != "openai-codex"
                    or not isinstance(item.get("id"), str) for item in models):
                raise PiError("invalid_response")
            available_ids = {item["id"] for item in models}
            result = {"connected": True, "checkedAt": datetime.now(timezone.utc).isoformat(),
                      "models": [item for item in self._supported_models()
                                 if item.get("providerId") == "openai-codex"
                                 and item.get("id") in available_ids]}
            with self._catalog_lock:
                self._instance_codex_catalog = {**result, "generation": generation,
                                                "updated": time.monotonic()}
            return result
        except Exception:
            with self._catalog_lock:
                self._instance_codex_catalog = None
            raise
        finally:
            if record is not None:
                record["credential"].clear()
            with self._identity_lock:
                self._identities.discard(identity)

    def instance_codex_catalog(self):
        generation = self.instance_store.generation("openai-codex") if self.instance_store else 0
        with self._catalog_lock:
            cached = self._instance_codex_catalog
            if cached and cached["generation"] == generation and (
                    time.monotonic() - cached["updated"] < self.policy.catalog_max_stale_seconds):
                return {"verifiedModels": cached["models"], "catalogCheckedAt": cached["checkedAt"]}
        return {"verifiedModels": [], "catalogCheckedAt": None}

    def disconnect(self, owner, credential_id="codex-prototype"):
        self.store.delete(owner, credential_id)
        self._denied_models(owner, self.store.list(owner))
        provider_id = {"codex-prototype": "openai-codex", "openai": "openai"}.get(credential_id)
        if provider_id:
            self._account_catalog(owner, provider_id, 0)
        try:
            self.client.call("/credentials/remove", {"owner": owner, "credentialId": credential_id})
        except Exception:
            pass  # Persisted generation fencing still rejects every late completion.

    def run(self, owner, turn_id, provider_id, model_id, system, prompt, tools,
            on_text=lambda text: None, is_authorized=lambda: True, messages=None,
            reasoning_effort="default", zen_scope=None):
        identity = (("schemii-instance", "instance-codex") if provider_id == self.SHARED_CODEX_ID else
                    (owner, "instance-opencode" if provider_id == "opencode" else self.credential_id(provider_id)))
        # TODO(multi-replica-ai): Before enabling multiple API workers/replicas,
        # acquire a durable owner/credential lease and read credentials AFTER it
        # is acquired; hold it through token-refresh persistence. This process-local
        # guard and sidecar overlap rejection cannot prevent stale cross-worker reads.
        # Retain the shared sidecar, not a process per user. Deferred for local dev.
        with self._identity_lock:
            if identity[1] and identity in self._identities:
                raise PiError("busy", status=429, limit_event=LimitEventNotice(
                    "ai_provider_credential", "credential_overlap_guard", 1, 1))
            if identity[1]:
                self._identities.add(identity)
        try:
            return self._run(owner, turn_id, provider_id, model_id, system, prompt,
                             tools, on_text, is_authorized, messages, reasoning_effort, zen_scope)
        finally:
            with self._identity_lock:
                if identity[1]:
                    self._identities.discard(identity)

    def _run(self, owner, turn_id, provider_id, model_id, system, prompt, tools,
             on_text, is_authorized, messages, reasoning_effort="default", zen_scope=None):
        self.require_available_model(owner, provider_id, model_id)
        self.require_reasoning_effort(owner, provider_id, model_id, reasoning_effort)
        credential_id = self.credential_id(provider_id)
        record = self.store.get(owner, credential_id) if credential_id else None
        instance_scope = None
        grant_policy = None
        if provider_id in {"opencode", self.SHARED_CODEX_ID}:
            actual_provider = "openai-codex" if provider_id == self.SHARED_CODEX_ID else "opencode"
            instance_scope = self.require_instance_access(owner, zen_scope, actual_provider)
            product, connection_owner_id, connection_id = instance_scope
            record = self.instance_store.resolve(owner, product, connection_owner_id, connection_id,
                                                 provider_id=actual_provider)
            if record is None:
                raise PiError("instance_access_denied", status=403)
            if provider_id == self.SHARED_CODEX_ID:
                grant_policy = self.require_instance_policy(owner, provider_id, model_id,
                                                            reasoning_effort, zen_scope)
                credential_id = "instance-codex"
            else:
                record = {"generation": record["generation"],
                          "credential": {"type": "api_key", "key": record["credential"]}}
                credential_id = "instance-opencode"
        if credential_id and record is None:
            raise PiError("credentials_required", status=401)
        if messages is not None and (not isinstance(messages, list) or not messages
                                    or any(not isinstance(message, dict) or message.get("role") not in
                                           {"user", "assistant", "toolResult"} for message in messages)):
            raise PiError("invalid_response")
        context = {"systemPrompt": system,
                   "messages": messages if messages is not None else [
                       {"role": "user", "content": prompt, "timestamp": int(time.time() * 1000)}],
                   "tools": tools}
        context["messages"] = compact_tool_context(system, context["messages"], tools,
                                                    self.policy.context_bytes)
        context_bytes = len(json.dumps(context, ensure_ascii=False).encode())
        if context_bytes > self.policy.context_bytes:
            raise PiError("context_too_large", status=413, limit_event=LimitEventNotice(
                "ai_turn", "ai.context_bytes", self.policy.context_bytes, context_bytes))
        body = {"owner": owner, "turnId": turn_id,
                "providerId": "openai-codex" if provider_id == self.SHARED_CODEX_ID else provider_id,
                "modelId": model_id, "context": context,
                "limits": {"maxConcurrent": self.policy.maximum_concurrent_turns,
                           "maxPerOwner": self.policy.maximum_concurrent_turns_per_user,
                           "timeoutMs": self.policy.provider_timeout_seconds * 1000,
                           "contextBytes": self.policy.context_bytes,
                           "responseBytes": self.policy.response_bytes}}
        if reasoning_effort != "default":
            body["reasoningEffort"] = reasoning_effort
        if record:
            body.update(credentialId=credential_id, generation=record["generation"], credential=record["credential"])

        def authority():
            if record:
                if instance_scope is not None:
                    if zen_scope() != instance_scope or (self.auth and self.auth.enabled and (
                        not self.auth.user(owner) or f"{instance_scope[0]}:access" not in self.auth.capabilities(owner)
                    )) or self.instance_store.generation(actual_provider) != record["generation"] or not self.instance_store.has_grant(
                        owner, *instance_scope, provider_id=actual_provider
                    ):
                        raise PiError("permission_changed", status=409)
                    if grant_policy and self.instance_store.get_grant(
                        owner, *instance_scope, provider_id=actual_provider) != grant_policy:
                        raise PiError("permission_changed", status=409)
                else:
                    current = self.store.get(owner, credential_id)
                    if current is None or current["generation"] != record["generation"]:
                        raise PiError("credentials_changed", status=409)
            if not is_authorized():
                raise PiError("permission_changed", status=409)

        def persist(event):
            if record and instance_scope is not None and provider_id == self.SHARED_CODEX_ID and "credential" in event:
                if event.get("generation") != record["generation"] or not self.instance_store.save_credential(
                    "openai-codex", event["credential"], record["generation"]):
                    raise PiError("credentials_changed", status=409)
            if record and instance_scope is not None and provider_id == "opencode" and "credential" in event:
                # The sidecar returns the credential in its private terminal
                # envelope. Instance API keys are immutable during a turn;
                # accept only an unchanged value and never write it to a user store.
                if event.get("generation") != record["generation"] or event["credential"] != record["credential"]:
                    raise PiError("credentials_changed", status=409)
            if record and instance_scope is None and "credential" in event:
                if event.get("generation") != record["generation"] or not self.store.save(
                    owner, credential_id, provider_id, event["credential"], record["generation"],
                ):
                    raise PiError("credentials_changed", status=409)

        timer = threading.Timer(self.policy.provider_timeout_seconds, lambda: self.cancel(owner, turn_id))
        timer.daemon = True
        try:
            authority()
            request = Request(self.client.url + "/turns", data=json.dumps(body).encode(),
                              headers={"Authorization": "Bearer " + self.client._secret,
                                       "Content-Type": "application/json", "Accept": "application/x-ndjson"})
            timer.start()
            deadline = time.monotonic() + self.policy.provider_timeout_seconds + 10
            # Native replay context has its own bound. Terminal JSON also repeats
            # public content and carries refreshed credentials. NDJSON envelopes
            # must not penalize providers streaming one character at a time.
            event_limit = self.policy.response_bytes * 6 + self.policy.context_bytes + 131072
            total_limit = event_limit + self.policy.response_bytes * 32 + 65536
            total = 0
            text_bytes = 0
            next_authority_check = time.monotonic() + 1
            with self._open(request, timeout=self.policy.provider_timeout_seconds + 10) as response:
                while True:
                    if time.monotonic() >= deadline:
                        raise PiError("timeout", status=504)
                    raw = response.readline(event_limit + 1)
                    if not raw:
                        raise PiError("invalid_response")
                    total += len(raw)
                    if len(raw) > event_limit or total > total_limit:
                        raise PiError("response_too_large", status=413)
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise PiError("invalid_response")
                    kind = event.get("type")
                    if kind == "text":
                        if time.monotonic() >= next_authority_check:
                            authority()
                            next_authority_check = time.monotonic() + 1
                        text = event.get("text")
                        if not isinstance(text, str):
                            raise PiError("invalid_response")
                        text_bytes += len(text.encode())
                        if text_bytes > self.policy.response_bytes:
                            raise self._size_error("response_bytes", text_bytes, "response_stream")
                        on_text(text)
                    elif kind in ("result", "error"):
                        persist(event)
                        authority()
                        if kind == "error":
                            code = event.get("code")
                            if code == "model_unavailable":
                                self._deny_model(owner, provider_id, model_id, record["generation"] if record else 0)
                            raise PiError(code if code in _MESSAGES else "provider_failed",
                                          limit_event=self._runtime_notice(event.get("limit")))
                        text, calls = event.get("text"), event.get("toolCalls", [])
                        if not isinstance(text, str) or not isinstance(calls, list):
                            raise PiError("invalid_response")
                        content_bytes = len(text.encode()) + self._json_bytes(calls)
                        if content_bytes > self.policy.response_bytes:
                            raise self._size_error("response_bytes", content_bytes, "response_content")
                        result = []
                        for call in calls:
                            # Transport carries untrusted calls; only the server
                            # dispatcher authorizes execution and returns useful
                            # permission denials to the model.
                            if (not isinstance(call, dict) or not isinstance(call.get("name"), str)
                                    or not call["name"].strip()):
                                raise PiError("invalid_response")
                            if not isinstance(call.get("arguments"), dict):
                                raise PiError("invalid_response")
                            result.append((call["name"], call["arguments"]))
                        message = event.get("assistantMessage")
                        ids = tuple(call.get("id", "") for call in calls)
                        if message is not None:
                            native_bytes = self._json_bytes(message)
                            if native_bytes > self.policy.context_bytes:
                                raise self._size_error("context_bytes", native_bytes, "native_response")
                            if (not isinstance(message, dict) or message.get("role") != "assistant"
                                    or not isinstance(message.get("content"), list)):
                                raise PiError("invalid_response")
                            parts = message["content"]
                            if any(not isinstance(part, dict) or part.get("type") not in
                                   {"text", "thinking", "toolCall"} for part in parts):
                                raise PiError("invalid_response")
                            replay_calls = [{"id": part.get("id"), "name": part.get("name"),
                                             "arguments": part.get("arguments")}
                                            for part in parts if part["type"] == "toolCall"]
                            replay_text = [part.get("text") for part in parts if part["type"] == "text"]
                            if (replay_calls != calls or any(not isinstance(item, str) for item in replay_text)
                                    or "".join(replay_text) != text
                                    or any(not isinstance(item, str) or not item for item in ids)
                                    or len(set(ids)) != len(ids)):
                                raise PiError("invalid_response")
                        return PiReply(text, tuple(result), message, ids)
                    else:
                        raise PiError("invalid_response")
        except PiError:
            self.cancel(owner, turn_id)
            raise
        except Exception:
            self.cancel(owner, turn_id)
            raise PiError("provider_failed") from None
        finally:
            timer.cancel()
            body.pop("credential", None)
            if record:
                record["credential"].clear()

    @staticmethod
    def _json_bytes(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())

    def _size_error(self, name, observed, source):
        return PiError("context_too_large" if name == "context_bytes" else "response_too_large",
                       status=413, limit_event=LimitEventNotice(
                           "ai_" + source, "ai." + name, getattr(self.policy, name), observed))

    def _runtime_notice(self, value):
        """Trust only numeric, known private-runtime limit descriptors."""
        if not isinstance(value, dict):
            return None
        name = value.get("name")
        allowed = {"maximum_concurrent_turns": self.policy.maximum_concurrent_turns,
                   "maximum_concurrent_turns_per_user": self.policy.maximum_concurrent_turns_per_user,
                   "credential_overlap_guard": 1,
                   "response_bytes": self.policy.response_bytes,
                   "context_bytes": self.policy.context_bytes}
        configured, observed = value.get("configured"), value.get("observed")
        if (name not in allowed or type(configured) is not int or configured != allowed[name]
                or type(observed) is not int or observed < 0):
            return None
        if name in {"response_bytes", "context_bytes"}:
            source = value.get("source")
            if source not in {"request_context", "native_response", "response_stream", "response_content"}:
                return None
            return LimitEventNotice("ai_" + source, "ai." + name, configured, observed)
        credential = name == "credential_overlap_guard"
        return LimitEventNotice("ai_provider_credential" if credential else "ai_turn",
                                name if credential else "ai." + name, configured, observed)
