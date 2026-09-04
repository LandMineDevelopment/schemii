"""Owner-scoped Pi transport. Credentials cross only the private sidecar boundary."""

from dataclasses import dataclass
import json
import threading
import time
from urllib.request import Request, urlopen

from schemii.common.admin_config import AiPolicy


class PiError(RuntimeError):
    def __init__(self, code, message=None, status=502):
        self.code = code
        self.status = status
        super().__init__(message or _MESSAGES.get(code, _MESSAGES["provider_failed"]))


_MESSAGES = {
    "provider_failed": "The AI provider request failed. Retry or reconnect your account.",
    "rate_limited": "The selected provider has reached its usage or rate limit. Wait and retry, or explicitly select another available model. No fallback model was used.",
    "billing_required": "The selected provider requires billing or credits. Check that provider account or explicitly select another model. No fallback model was used.",
    "credentials_required": "Connect your own provider account before running this request.",
    "credentials_changed": "Your AI credentials were disconnected, replaced, or expired. Reconnect and try again.",
    "model_unavailable": "This model is not currently available. Select an available model.",
    "cancelled": "The AI request was cancelled.",
    "timeout": "The AI request exceeded the configured time limit.",
    "busy": "AI capacity is currently in use. Wait for a running request to finish.",
    "context_too_large": "The AI context exceeds the configured size limit.",
    "response_too_large": "The AI response exceeds the configured size limit.",
    "permission_changed": "Assistant permissions changed. Request a new response.",
    "tool_denied": "The model requested a tool that is not enabled for this turn.",
    "invalid_response": "The AI runtime returned an invalid response. Try again.",
    "unavailable": "The AI runtime is temporarily unavailable. Try again shortly.",
}


@dataclass(frozen=True)
class PiReply:
    text: str
    tool_calls: tuple[tuple[str, dict], ...]


class PiRuntime:
    def __init__(self, client, store, catalog, policy=None, *, opener=urlopen):
        self.client, self.store, self.catalog = client, store, catalog
        self.policy = policy or AiPolicy()
        self._open = opener
        self._identities = set()
        self._identity_lock = threading.Lock()
        self._catalog_lock = threading.Lock()
        self._supported = None
        self._supported_until = 0

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

    def status(self, owner):
        try:
            supported = self._supported_models()
            verified = {m["id"] for m in self.catalog.snapshot()["models"]} if self.catalog else set()
            connected = {row["provider_id"] for row in self.store.list(owner) if row.get("connected")}
            providers = []
            for provider_id, name in (("openai-codex", "ChatGPT Codex"), ("openai", "OpenAI"), ("opencode", "OpenCode Zen")):
                models = [{"id": item["id"], "name": item.get("name", item["id"]), "status": "active"}
                          for item in supported if item.get("providerId") == provider_id
                          and (provider_id != "opencode" or item["id"] in verified)]
                authenticated = provider_id in connected
                available = bool(models) and (authenticated or provider_id == "opencode")
                item = {"id": provider_id, "name": name, "available": available,
                        "authenticated": authenticated, "models": models,
                        "authMethods": []}
                if provider_id == "opencode":
                    item["privacy"] = "Free trial models may use prompts and selected context for training. Do not send confidential or personal data."
                providers.append(item)
            return {"enabled": self.policy.enabled, "healthy": True, "providers": providers}
        except Exception:
            return {"enabled": self.policy.enabled, "healthy": False, "providers": [],
                    "message": _MESSAGES["unavailable"]}

    def cancel(self, owner, turn_id):
        try:
            self.client.call("/turns/cancel", {"owner": owner, "turnId": turn_id})
        except Exception:
            pass  # Cancellation is best effort; terminal acceptance checks authority again.

    def disconnect(self, owner, credential_id="codex-prototype"):
        self.store.delete(owner, credential_id)
        try:
            self.client.call("/credentials/remove", {"owner": owner, "credentialId": credential_id})
        except Exception:
            pass  # Persisted generation fencing still rejects every late completion.

    def run(self, owner, turn_id, provider_id, model_id, system, prompt, tools,
            on_text=lambda text: None, is_authorized=lambda: True):
        identity = (owner, self.credential_id(provider_id))
        with self._identity_lock:
            if identity[1] and identity in self._identities:
                raise PiError("busy", status=429)
            if identity[1]:
                self._identities.add(identity)
        try:
            return self._run(owner, turn_id, provider_id, model_id, system, prompt,
                             tools, on_text, is_authorized)
        finally:
            with self._identity_lock:
                if identity[1]:
                    self._identities.discard(identity)

    def _run(self, owner, turn_id, provider_id, model_id, system, prompt, tools,
             on_text, is_authorized):
        if not self.policy.enabled:
            raise PiError("unavailable", status=503)
        status = self.status(owner)
        provider = next((p for p in status["providers"] if p["id"] == provider_id), None)
        if not provider or not provider["available"] or model_id not in {m["id"] for m in provider["models"]}:
            raise PiError("model_unavailable", status=409)
        credential_id = self.credential_id(provider_id)
        record = self.store.get(owner, credential_id) if credential_id else None
        if credential_id and record is None:
            raise PiError("credentials_required", status=401)
        allowed = {tool["name"] for tool in tools}
        context = {"systemPrompt": system,
                   "messages": [{"role": "user", "content": prompt, "timestamp": int(time.time() * 1000)}],
                   "tools": tools}
        context_bytes = len(json.dumps(context, ensure_ascii=False).encode())
        if context_bytes > self.policy.context_bytes:
            raise PiError("context_too_large", status=413)
        body = {"owner": owner, "turnId": turn_id, "providerId": provider_id,
                "modelId": model_id, "context": context,
                "limits": {"maxConcurrent": self.policy.maximum_concurrent_turns,
                           "maxPerOwner": self.policy.maximum_concurrent_turns_per_user,
                           "timeoutMs": self.policy.provider_timeout_seconds * 1000,
                           "contextBytes": self.policy.context_bytes,
                           "responseBytes": self.policy.response_bytes}}
        if record:
            body.update(credentialId=credential_id, generation=record["generation"], credential=record["credential"])
        elif provider_id == "opencode":
            # Explicit public auth marker, never ambient server credentials. Only
            # verified free models reach this path through the catalog gate above.
            body.update(credentialId="zen-public", generation=1,
                        credential={"type": "api_key", "key": "public"})

        def authority():
            if record:
                current = self.store.get(owner, credential_id)
                if current is None or current["generation"] != record["generation"]:
                    raise PiError("credentials_changed", status=409)
            if not is_authorized():
                raise PiError("permission_changed", status=409)

        def persist(event):
            if record and "credential" in event:
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
            # Terminal JSON repeats response text and can include refreshed tokens.
            event_limit = self.policy.response_bytes * 6 + 131072
            total_limit = event_limit * 2
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
                            raise PiError("response_too_large", status=413)
                        on_text(text)
                    elif kind in ("result", "error"):
                        persist(event)
                        authority()
                        if kind == "error":
                            code = event.get("code")
                            raise PiError(code if code in _MESSAGES else "provider_failed")
                        text, calls = event.get("text"), event.get("toolCalls", [])
                        if not isinstance(text, str) or not isinstance(calls, list):
                            raise PiError("invalid_response")
                        if len(text.encode()) + len(json.dumps(calls, ensure_ascii=False).encode()) > self.policy.response_bytes:
                            raise PiError("response_too_large", status=413)
                        result = []
                        for call in calls:
                            if not isinstance(call, dict) or call.get("name") not in allowed:
                                raise PiError("tool_denied", status=403)
                            if not isinstance(call.get("arguments"), dict):
                                raise PiError("invalid_response")
                            result.append((call["name"], call["arguments"]))
                        return PiReply(text, tuple(result))
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
