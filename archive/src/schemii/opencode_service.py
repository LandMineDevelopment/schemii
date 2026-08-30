from __future__ import annotations

import base64
import copy
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .ai_tool_contracts import SCHEMII_TOOL_CONTRACTS


MAX_UPSTREAM_BODY = 8 * 1024 * 1024
MAX_TEXT_SIZE = 64 * 1024
MAX_PROMPT_SIZE = 96 * 1024
MAX_PARTS = 100
MAX_ACTIONS = 20
MAX_ACTION_SIZE = 32 * 1024
MAX_TOOL_OUTPUT_SIZE = 256 * 1024
MAX_ACTIVITY_LINE_SIZE = 256 * 1024
MAX_HISTORY_MESSAGES = 100
MAX_HISTORY_TEXT_SIZE = 512 * 1024
OPENCODE_WORKSPACE = "/workspace"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CUSTOM_TOOLS = set(SCHEMII_TOOL_CONTRACTS)
TOOL_ACTION_TYPES = {name: contract.action_type for name, contract in SCHEMII_TOOL_CONTRACTS.items()}
SAFE_SKILLS = {
    "schemii-help",
    "connection-setup",
    "migration-safety",
    "schema-design-layout",
    "read-only-query-safety",
    "target-selection",
    "postgres-write-safety",
}
PROMPT_TOOLS = {
    **{name: True for name in CUSTOM_TOOLS},
    "skill": True,
    "bash": False,
    "shell": False,
    "read": False,
    "write": False,
    "edit": False,
    "apply_patch": False,
    "glob": False,
    "grep": False,
    "list": False,
    "webfetch": False,
    "websearch": False,
    "task": False,
}


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _open_without_redirects(request, timeout):
    return build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _reject_json_constant(value):
    raise ValueError(value)


class OpenCodeServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = {"error": {"code": code, "message": message}}


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise OpenCodeServiceError(400, "validation_error", f"{label} is invalid")
    return value


def _model(value: Any, *, optional: bool = False) -> dict[str, str] | None:
    if value is None and optional:
        return None
    if not isinstance(value, dict):
        raise OpenCodeServiceError(400, "validation_error", "model must contain providerId and modelId")
    if set(value) == {"providerID", "modelID"}:
        provider_id, model_id = value.get("providerID"), value.get("modelID")
    elif set(value) == {"providerId", "modelId"}:
        provider_id, model_id = value.get("providerId"), value.get("modelId")
    else:
        raise OpenCodeServiceError(400, "validation_error", "model must contain providerId and modelId")
    return {
        "providerID": _identifier(provider_id, "providerId"),
        "modelID": _bounded_text(model_id, 256, "modelId"),
    }


def _bounded_text(value: Any, maximum: int, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or value != value.strip() or len(value.encode("utf-8")) > maximum:
        raise OpenCodeServiceError(400, "validation_error", f"{label} is invalid")
    if "\x00" in value:
        raise OpenCodeServiceError(400, "validation_error", f"{label} is invalid")
    return value


def _normalize_tool_purposes(value: Any) -> Any:
    """Bound model-authored descriptions without changing executable action fields."""
    if isinstance(value, list):
        return [_normalize_tool_purposes(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {}
    for key, item in value.items():
        if key != "purpose" or not isinstance(item, str):
            normalized[key] = _normalize_tool_purposes(item)
            continue
        description = " ".join(item.split())
        encoded = description.encode("utf-8")
        if len(encoded) > 500:
            suffix = "…"
            description = encoded[:500 - len(suffix.encode("utf-8"))].decode("utf-8", "ignore").rstrip() + suffix
        normalized[key] = description
    return normalized


class OpenCodeService:
    """Small, fixed-surface client for the OpenCode 1.18.15 HTTP API."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30,
        *,
        workspace: str = OPENCODE_WORKSPACE,
        custom_tools: set[str] | None = None,
        tool_action_types: dict[str, str] | None = None,
        safe_skills: set[str] | None = None,
        data_tools: set[str] | None = None,
        write_tools: set[str] | None = None,
        structured_data_tools: set[str] | None = None,
        raw_write_tools: set[str] | None = None,
        schema_tools: set[str] | None = None,
        request=Request,
        opener=_open_without_redirects,
    ):
        self.enabled = bool(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._request_factory = request
        self._opener = opener
        if not isinstance(workspace, str) or not re.fullmatch(r"/[A-Za-z0-9/_-]+", workspace) or "//" in workspace or "/../" in f"{workspace}/":
            raise ValueError("OpenCode workspace is invalid")
        self.workspace = workspace.rstrip("/") or "/"
        self.custom_tools = set(CUSTOM_TOOLS if custom_tools is None else custom_tools)
        self.tool_action_types = dict(TOOL_ACTION_TYPES if tool_action_types is None else tool_action_types)
        self.safe_skills = set(SAFE_SKILLS if safe_skills is None else safe_skills)
        self.data_tools = set(({"schema_read_query"} & self.custom_tools) if data_tools is None else data_tools)
        self.write_tools = set(({"schema_insert_rows_preview", "schema_create_view_preview"} & self.custom_tools) if write_tools is None else write_tools)
        self.structured_data_tools = set(({"schema_data_read"} & self.custom_tools) if structured_data_tools is None else structured_data_tools)
        self.raw_write_tools = set(({"schema_raw_write"} & self.custom_tools) if raw_write_tools is None else raw_write_tools)
        default_schema_tools = {
            "schema_project_create", "schema_populate", "schema_add_table", "schema_rename_table", "schema_add_column",
            "schema_update_column", "schema_delete_element", "schema_add_relationship", "schema_migration_preview",
        }
        self.schema_tools = set((default_schema_tools & self.custom_tools) if schema_tools is None else schema_tools)
        if (self.data_tools | self.write_tools | self.structured_data_tools | self.raw_write_tools | self.schema_tools) - self.custom_tools or set(self.tool_action_types) != self.custom_tools or any(not SAFE_ID.fullmatch(name) for name in self.custom_tools | self.safe_skills):
            raise ValueError("OpenCode tool or skill policy is invalid")
        self.prompt_tools = {
            **{name: True for name in self.custom_tools},
            **{name: enabled for name, enabled in PROMPT_TOOLS.items() if name not in CUSTOM_TOOLS},
        }
        if not self.enabled:
            self._authorization = ""
            return
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("OpenCode URL must be an HTTP(S) base URL without credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("OpenCode URL must not contain a query or fragment")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("OpenCode timeout must be from 1 to 300 seconds")
        if not isinstance(username, str) or not username or ":" in username or any(ord(char) < 32 or ord(char) == 127 for char in username):
            raise ValueError("OpenCode username is invalid")
        if not isinstance(password, str):
            raise ValueError("OpenCode credentials must be strings")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._authorization = f"Basic {token}"

    def _request(self, method: str, path: str, payload: Any = None, *, timeout: float | None = None) -> Any:
        if not self.enabled:
            raise OpenCodeServiceError(503, "ai_disabled", "Embedded AI is not configured")
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json", "Authorization": self._authorization, "X-OpenCode-Directory": self.workspace}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = self._request_factory(self.base_url + path, data=data, headers=headers, method=method)
        try:
            response = self._opener(request, timeout=self.timeout if timeout is None else timeout)
            with response:
                raw = response.read(MAX_UPSTREAM_BODY + 1)
        except HTTPError as exc:
            exc.close()
            status = 502 if exc.code >= 500 else 400
            raise OpenCodeServiceError(status, "opencode_error", "OpenCode rejected the request") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise OpenCodeServiceError(502, "opencode_unavailable", "OpenCode is unavailable") from exc
        if len(raw) > MAX_UPSTREAM_BODY:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an oversized response")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid response") from exc

    def health(self) -> dict[str, Any]:
        result = self._request("GET", "/global/health", timeout=min(self.timeout, 2))
        if not isinstance(result, dict) or result.get("healthy") is not True or not isinstance(result.get("version"), str):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid health response")
        return {"healthy": True, "version": result["version"][:64]}

    def providers(self) -> dict[str, Any]:
        result = self._request("GET", "/provider")
        if not isinstance(result, dict):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned invalid providers")
        providers = []
        model_count = 0
        for provider in result.get("all", [])[:200] if isinstance(result.get("all"), list) else []:
            if not isinstance(provider, dict) or not isinstance(provider.get("id"), str) or not SAFE_ID.fullmatch(provider["id"]):
                continue
            models = []
            source_models = provider.get("models", {})
            opencode_authenticated = provider["id"] != "opencode" or any(
                isinstance(model, dict)
                and isinstance(model.get("cost"), dict)
                and isinstance(model["cost"].get("input"), (int, float))
                and model["cost"]["input"] > 0
                for model in source_models.values()
            ) if isinstance(source_models, dict) else provider["id"] != "opencode"
            if isinstance(source_models, dict):
                for model in list(source_models.values())[:200]:
                    if model_count >= 5000:
                        break
                    model_id = model.get("id") if isinstance(model, dict) else None
                    if (
                        not isinstance(model_id, str) or not model_id or model_id != model_id.strip()
                        or len(model_id.encode("utf-8")) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in model_id)
                    ):
                        continue
                    models.append({
                        "id": model_id,
                        "name": str(model.get("name", model_id))[:256],
                        "toolCall": bool(model.get("tool_call")),
                        "status": str(model.get("status", "active"))[:32],
                    })
                    model_count += 1
            normalized_provider = {"id": provider["id"][:128], "name": str(provider.get("name", provider["id"]))[:256], "models": models}
            if provider["id"] == "opencode":
                normalized_provider["authenticated"] = opencode_authenticated
            providers.append(normalized_provider)
        defaults = result.get("default", {})
        safe_defaults = {}
        if isinstance(defaults, dict):
            safe_defaults = {
                key[:128]: value[:256]
                for key, value in list(defaults.items())[:200]
                if isinstance(key, str) and SAFE_ID.fullmatch(key) and isinstance(value, str)
                and value and value == value.strip() and len(value.encode("utf-8")) <= 256
                and not any(ord(char) < 32 or ord(char) == 127 for char in value)
            }
        connected = [item[:128] for item in result.get("connected", [])[:200] if isinstance(item, str)] if isinstance(result.get("connected"), list) else []
        return {"providers": providers, "default": safe_defaults, "connected": connected}

    def auth_methods(self) -> dict[str, list[dict[str, Any]]]:
        result = self._request("GET", "/provider/auth")
        if not isinstance(result, dict):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned invalid authentication methods")
        normalized: dict[str, list[dict[str, Any]]] = {}
        for provider_id, methods in list(result.items())[:200]:
            if not isinstance(provider_id, str) or not isinstance(methods, list):
                continue
            normalized[provider_id[:128]] = []
            for method_index, method in enumerate(methods[:20]):
                if not isinstance(method, dict) or method.get("type") not in {"api", "oauth"}:
                    continue
                if provider_id == "anthropic" and method.get("type") == "oauth":
                    continue
                item: dict[str, Any] = {
                    "id": method_index,
                    "type": method["type"],
                    "label": str(method.get("label", method["type"]))[:256],
                    "name": str(method.get("label", method["type"]))[:256],
                }
                prompts = []
                for prompt in method.get("prompts", [])[:20] if isinstance(method.get("prompts"), list) else []:
                    if not isinstance(prompt, dict) or prompt.get("type") not in {"text", "select"}:
                        continue
                    safe_prompt = {
                        "type": prompt["type"], "key": str(prompt.get("key", ""))[:128],
                        "message": str(prompt.get("message", ""))[:512],
                    }
                    if isinstance(prompt.get("placeholder"), str):
                        safe_prompt["placeholder"] = prompt["placeholder"][:256]
                    when = prompt.get("when")
                    if isinstance(when, dict) and when.get("op") in {"eq", "neq"}:
                        safe_prompt["when"] = {
                            "key": str(when.get("key", ""))[:128], "op": when["op"],
                            "value": str(when.get("value", ""))[:256],
                        }
                    if prompt["type"] == "select" and isinstance(prompt.get("options"), list):
                        safe_prompt["options"] = [
                            {"label": str(option.get("label", ""))[:256], "value": str(option.get("value", ""))[:256], "hint": str(option.get("hint", ""))[:256]}
                            for option in prompt["options"][:50] if isinstance(option, dict)
                        ]
                    prompts.append(safe_prompt)
                if prompts:
                    item["prompts"] = prompts
                    item["inputs"] = [
                        {
                            "id": prompt.get("key", ""),
                            "name": prompt.get("key", ""),
                            "label": prompt.get("message", ""),
                            "type": prompt.get("type", "text"),
                            "required": "when" not in prompt,
                            **({"when": prompt["when"]} if "when" in prompt else {}),
                            **({"options": prompt["options"]} if "options" in prompt else {}),
                        }
                        for prompt in prompts if prompt.get("key")
                    ]
                normalized[provider_id[:128]].append(item)
        return normalized

    def skills(self) -> list[dict[str, str]]:
        result = self._request("GET", "/skill")
        if not isinstance(result, list):
            return []
        return [
            {
                "name": str(item.get("name", ""))[:128],
                "description": str(item.get("description", ""))[:512],
            }
            for item in result[:100]
            if isinstance(item, dict) and item.get("name") in self.safe_skills
        ]

    def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"available": False, "enabled": False, "healthy": False, "providers": [], "authMethods": {}, "skills": []}
        health = self.health()
        discovery = self.providers()
        connected = set(discovery.pop("connected", []))
        auth_methods = self.auth_methods()
        auth_methods.setdefault("opencode", [{
            "id": 0,
            "type": "api",
            "label": "OpenCode Zen API key",
            "name": "OpenCode Zen API key",
            "helpUrl": "https://opencode.ai/auth",
            "helpLabel": "Create a free OpenCode Zen API key",
        }])
        discovery["providers"] = [
            provider for provider in discovery["providers"]
            if provider["id"] in connected or provider["id"] in auth_methods
        ]
        for provider in discovery["providers"]:
            provider["connected"] = provider["id"] in connected
        discovery["default"] = {
            provider_id: model_id for provider_id, model_id in discovery["default"].items()
            if provider_id in connected or provider_id in auth_methods
        }
        return {
            "available": True,
            "enabled": True,
            **health,
            **discovery,
            "authMethods": auth_methods,
            "skills": self.skills(),
        }

    def set_api_key(self, provider_id: Any, key: Any, inputs: Any = None) -> dict[str, bool]:
        provider_id = _identifier(provider_id, "providerId")
        key = _bounded_text(key, 16 * 1024, "key")
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, dict) or len(inputs) > 20 or any(not isinstance(name, str) or not isinstance(value, str) or len(value) > 4096 for name, value in inputs.items()):
            raise OpenCodeServiceError(400, "validation_error", "inputs are invalid")
        credential = {"type": "api", "key": key}
        if inputs:
            credential["metadata"] = inputs
        result = self._request("PUT", f"/auth/{quote(provider_id, safe='')}", credential)
        if result is not True:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid authentication response")
        return {"saved": True}

    def delete_api_key(self, provider_id: Any) -> dict[str, bool]:
        provider_id = _identifier(provider_id, "providerId")
        result = self._request("DELETE", f"/auth/{quote(provider_id, safe='')}")
        if result is not True:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid authentication response")
        return {"deleted": True}

    def oauth_authorize(self, provider_id: Any, method: Any, inputs: Any) -> dict[str, str] | None:
        provider_id = _identifier(provider_id, "providerId")
        if isinstance(method, bool) or not isinstance(method, int) or not 0 <= method <= 100:
            raise OpenCodeServiceError(400, "validation_error", "method is invalid")
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, dict) or len(inputs) > 20 or any(not isinstance(key, str) or not isinstance(value, str) or len(value) > 4096 for key, value in inputs.items()):
            raise OpenCodeServiceError(400, "validation_error", "inputs are invalid")
        result = self._request("POST", f"/provider/{quote(provider_id, safe='')}/oauth/authorize", {"method": method, "inputs": inputs})
        if result is None:
            return None
        if not isinstance(result, dict) or result.get("method") not in {"auto", "code"} or not isinstance(result.get("url"), str):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid OAuth response")
        return {"url": result["url"][:8192], "method": result["method"], "instructions": str(result.get("instructions", ""))[:4096]}

    def oauth_callback(self, provider_id: Any, method: Any, code: Any = None) -> dict[str, bool]:
        provider_id = _identifier(provider_id, "providerId")
        if isinstance(method, bool) or not isinstance(method, int) or not 0 <= method <= 100:
            raise OpenCodeServiceError(400, "validation_error", "method is invalid")
        code = _bounded_text(code, 16 * 1024, "code", optional=True)
        payload = {"method": method}
        if code is not None:
            payload["code"] = code
        if self._request("POST", f"/provider/{quote(provider_id, safe='')}/oauth/callback", payload) is not True:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid OAuth response")
        return {"authenticated": True}

    def create_session(self, title: Any = None, model: Any = None) -> dict[str, Any]:
        title = _bounded_text(title, 256, "title", optional=True)
        _model(model, optional=True)
        payload = {} if title is None else {"title": title}
        result = self._request("POST", "/session", payload)
        if not isinstance(result, dict) or not isinstance(result.get("id"), str) or result.get("directory") != self.workspace:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid session")
        return {"id": result["id"][:128], "title": str(result.get("title", title or ""))[:256]}

    def list_sessions(self) -> dict[str, list[dict[str, Any]]]:
        result = self._request("GET", "/session")
        if not isinstance(result, list):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned invalid sessions")
        sessions = []
        for item in result[:200]:
            if (
                not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not SAFE_ID.fullmatch(item["id"]) or item.get("directory") != self.workspace
                or item.get("parentID") is not None
            ):
                continue
            item_time = item.get("time") if isinstance(item.get("time"), dict) else {}
            session = {
                "id": item["id"],
                "title": " ".join(self._history_text(item.get("title"), 256).split()) or "Untitled chat",
            }
            if isinstance(item_time.get("created"), (int, float)):
                session["createdAt"] = max(0, item_time["created"])
            if isinstance(item_time.get("updated"), (int, float)):
                session["updatedAt"] = max(0, item_time["updated"])
            model = self._history_model(item)
            if model:
                session["model"] = model
            sessions.append(session)
        sessions.sort(key=lambda item: item.get("updatedAt", item.get("createdAt", 0)), reverse=True)
        return {"sessions": sessions}

    def session_messages(self, session_id: Any) -> dict[str, Any]:
        session_id = self.verify_session(session_id)
        result = self._request("GET", f"/session/{quote(session_id, safe='')}/message?limit={MAX_HISTORY_MESSAGES}")
        if not isinstance(result, list):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned invalid session messages")
        messages = []
        total_text = 0
        latest_model = None
        for item in reversed(result[-MAX_HISTORY_MESSAGES:]):
            if not isinstance(item, dict) or not isinstance(item.get("info"), dict) or not isinstance(item.get("parts"), list):
                continue
            info = item["info"]
            role = info.get("role")
            if role not in {"user", "assistant"}:
                continue
            model = self._history_model(info)
            if model and latest_model is None:
                latest_model = model
            item_time = info.get("time") if isinstance(info.get("time"), dict) else {}
            message: dict[str, Any] = {"role": role}
            if isinstance(item_time.get("created"), (int, float)):
                message["createdAt"] = max(0, item_time["created"])
            if role == "user":
                text = "\n".join(
                    part["text"] for part in item["parts"]
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                )
                marker = "\n\nUser request:\n"
                if marker in text:
                    text = text.split(marker, 1)[1]
                remaining = MAX_HISTORY_TEXT_SIZE - total_text
                text = self._history_text(text, min(16 * 1024, remaining))
                if not text:
                    continue
                total_text += len(text.encode("utf-8"))
                message["text"] = text
            else:
                try:
                    normalized = self._normalize_message(item)
                except OpenCodeServiceError:
                    continue
                safe_parts = []
                for part in normalized["parts"]:
                    if part.get("type") in {"text", "reasoning"}:
                        remaining = MAX_HISTORY_TEXT_SIZE - total_text
                        if remaining <= 0:
                            continue
                        text = part.get("text", "").encode("utf-8")[:remaining].decode("utf-8", "ignore")
                        total_text += len(text.encode("utf-8"))
                        safe_parts.append({**part, "text": text})
                    elif part.get("type") == "tool":
                        safe_parts.append({key: part[key] for key in ("type", "tool", "status") if key in part})
                    elif part.get("type") == "skill":
                        safe_parts.append({key: part[key] for key in ("type", "skill", "status") if key in part})
                if not safe_parts:
                    continue
                message["parts"] = safe_parts
                message["text"] = "\n".join(part.get("text", "") for part in safe_parts if part.get("type") == "text")
            messages.append(message)
            if total_text >= MAX_HISTORY_TEXT_SIZE:
                break
        messages.reverse()
        payload: dict[str, Any] = {"messages": messages}
        if latest_model:
            payload["model"] = latest_model
        return payload

    def session_title_seed(self, session_id: Any) -> str | None:
        session_id = self.verify_session(session_id)
        result = self._request("GET", f"/session/{quote(session_id, safe='')}/message?limit={MAX_HISTORY_MESSAGES}")
        if not isinstance(result, list):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned invalid session messages")
        for item in result[-MAX_HISTORY_MESSAGES:]:
            if not isinstance(item, dict) or item.get("info", {}).get("role") != "user" or not isinstance(item.get("parts"), list):
                continue
            text = "\n".join(
                part["text"] for part in item["parts"]
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            )
            marker = "\n\nUser request:\n"
            if marker in text:
                text = text.split(marker, 1)[1]
            text = self._history_text(text, 16 * 1024)
            if text:
                return text
        return None

    @staticmethod
    def _history_text(value: Any, maximum: int) -> str:
        if not isinstance(value, str) or maximum <= 0:
            return ""
        value = "".join(char if ord(char) >= 32 and ord(char) != 127 else " " for char in value).strip()
        return value.encode("utf-8")[:maximum].decode("utf-8", "ignore")

    @staticmethod
    def _history_model(info: dict[str, Any]) -> dict[str, str] | None:
        nested = info.get("model") if isinstance(info.get("model"), dict) else {}
        provider_id = info.get("providerID", nested.get("providerID"))
        model_id = info.get("modelID", nested.get("modelID", nested.get("id")))
        if not isinstance(provider_id, str) or not SAFE_ID.fullmatch(provider_id):
            return None
        if not isinstance(model_id, str) or not model_id or len(model_id.encode("utf-8")) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in model_id):
            return None
        return {"providerId": provider_id, "modelId": model_id}

    def verify_session(self, session_id: Any) -> str:
        return self.session_identity(session_id)["id"]

    def session_identity(self, session_id: Any) -> dict[str, str]:
        session_id = _identifier(session_id, "sessionId")
        result = self._request("GET", f"/session/{quote(session_id, safe='')}")
        if not isinstance(result, dict) or result.get("id") != session_id or result.get("directory") != self.workspace:
            raise OpenCodeServiceError(404, "not_found", "AI session was not found")
        title = self._history_text(result.get("title"), 256)
        return {"id": session_id, "title": title}

    def activity(self, session_id: Any):
        session_id = _identifier(session_id, "sessionId")
        request = self._request_factory(
            self.base_url + "/event",
            headers={"Accept": "text/event-stream", "Authorization": self._authorization, "X-OpenCode-Directory": self.workspace},
            method="GET",
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:
                data_lines = []
                saw_busy = False
                deadline = time.monotonic() + self.timeout + 10
                while time.monotonic() < deadline:
                    raw_line = response.readline(MAX_ACTIVITY_LINE_SIZE + 1)
                    if not raw_line:
                        break
                    if len(raw_line) > MAX_ACTIVITY_LINE_SIZE:
                        raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an oversized activity event")
                    line = raw_line.decode("utf-8", "strict").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        continue
                    if line or not data_lines:
                        continue
                    try:
                        event = json.loads("\n".join(data_lines))
                    except (json.JSONDecodeError, ValueError):
                        data_lines = []
                        continue
                    data_lines = []
                    normalized = self._normalize_activity_event(event, session_id)
                    if normalized is None:
                        continue
                    if normalized.get("type") == "part" and not saw_busy:
                        continue
                    if normalized.get("type") == "session" and normalized.get("state") == "busy":
                        saw_busy = True
                    yield normalized
                    if saw_busy and normalized.get("type") == "session" and normalized.get("state") == "idle":
                        return
        except HTTPError as exc:
            exc.close()
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode rejected the activity stream") from exc
        except (UnicodeDecodeError, URLError, TimeoutError, OSError) as exc:
            raise OpenCodeServiceError(502, "opencode_unavailable", "OpenCode activity is unavailable") from exc

    def _normalize_activity_event(self, event: Any, session_id: str) -> dict[str, Any] | None:
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return None
        event_type = event["type"]
        properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
        if event_type == "server.connected":
            return {"type": "connection", "state": "connected"}
        if properties.get("sessionID") != session_id:
            return None
        if event_type == "session.status":
            status = properties.get("status") if isinstance(properties.get("status"), dict) else {}
            state = status.get("type")
            if state not in {"idle", "busy", "retry"}:
                return None
            normalized = {"type": "session", "state": state}
            if state == "retry":
                attempt = status.get("attempt")
                retry_at = status.get("next")
                if isinstance(attempt, int) and not isinstance(attempt, bool):
                    normalized["attempt"] = max(0, min(attempt, 100))
                if isinstance(retry_at, (int, float)) and not isinstance(retry_at, bool):
                    normalized["retryAt"] = max(0, retry_at)
            return normalized
        if event_type == "session.compacted":
            return {"type": "compaction", "state": "completed"}
        if event_type == "session.error":
            return {"type": "session", "state": "error"}
        if event_type != "message.part.updated":
            return None
        part = properties.get("part") if isinstance(properties.get("part"), dict) else {}
        part_id = part.get("id")
        if not isinstance(part_id, str) or not SAFE_ID.fullmatch(part_id):
            return None
        part_type = part.get("type")
        if part_type in {"reasoning", "text"}:
            part_time = part.get("time") if isinstance(part.get("time"), dict) else {}
            return {
                "type": "part",
                "kind": part_type,
                "key": part_id,
                "state": "completed" if isinstance(part_time.get("end"), (int, float)) else "running",
            }
        if part_type != "tool":
            return None
        tool = part.get("tool")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        tool_state = state.get("status")
        if tool_state not in {"pending", "running", "completed", "error"}:
            return None
        if tool in self.custom_tools:
            return {"type": "part", "kind": "tool", "key": part_id, "tool": tool, "state": tool_state}
        if tool == "skill":
            tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
            skill_name = tool_input.get("name")
            if skill_name in self.safe_skills:
                return {"type": "part", "kind": "skill", "key": part_id, "skill": skill_name, "state": tool_state}
        return None

    def delete_session(self, session_id: Any) -> dict[str, bool]:
        session_id = self.verify_session(session_id)
        if self._request("DELETE", f"/session/{quote(session_id, safe='')}") is not True:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid session response")
        return {"deleted": True}

    def prompt(self, session_id: Any, text: Any, model: Any, system: Any, *, allow_data: bool = False, allow_write: bool = False, allow_structured_data: bool = False, allow_raw_write: bool = False, allow_schema: bool = True) -> dict[str, Any]:
        session_id = self.verify_session(session_id)
        text = _bounded_text(text, MAX_PROMPT_SIZE, "text")
        model = _model(model)
        system = _bounded_text(system, MAX_TEXT_SIZE, "system")
        if any(not isinstance(value, bool) for value in (allow_data, allow_write, allow_structured_data, allow_raw_write, allow_schema)):
            raise OpenCodeServiceError(400, "validation_error", "AI tool permissions are invalid")
        prompt_tools = dict(self.prompt_tools)
        for tool in self.data_tools:
            prompt_tools[tool] = allow_data
        for tool in self.write_tools:
            prompt_tools[tool] = allow_write
        for tool in self.structured_data_tools:
            prompt_tools[tool] = allow_structured_data
        for tool in self.raw_write_tools:
            prompt_tools[tool] = allow_raw_write
        for tool in self.schema_tools:
            prompt_tools[tool] = allow_schema
        payload = {
            "model": model,
            "system": system,
            "tools": prompt_tools,
            "parts": [{"type": "text", "text": text}],
        }
        try:
            result = self._request("POST", f"/session/{quote(session_id, safe='')}/message", payload)
        except OpenCodeServiceError as error:
            try:
                self._request("POST", f"/session/{quote(session_id, safe='')}/abort", timeout=min(self.timeout, 5))
            except OpenCodeServiceError:
                pass
            if error.code == "opencode_unavailable":
                raise OpenCodeServiceError(
                    504,
                    "provider_timeout",
                    "The AI provider did not respond. Connect another provider or try a different model.",
                ) from error
            raise
        allowed_tools = set(self.custom_tools)
        if not allow_data:
            allowed_tools -= self.data_tools
        if not allow_write:
            allowed_tools -= self.write_tools
        if not allow_structured_data:
            allowed_tools -= self.structured_data_tools
        if not allow_raw_write:
            allowed_tools -= self.raw_write_tools
        if not allow_schema:
            allowed_tools -= self.schema_tools
        normalized = self._normalize_message(result, allowed_tools)
        if not normalized["actions"]:
            normalized["actions"] = self._recover_prompt_actions(session_id, text, allowed_tools)
        return normalized

    def _recover_prompt_actions(self, session_id: str, prompt_text: str, allowed_tools: set[str]) -> list[dict[str, Any]]:
        try:
            messages = self._request("GET", f"/session/{quote(session_id, safe='')}/message?limit=20")
        except OpenCodeServiceError:
            return []
        if not isinstance(messages, list):
            return []
        prompt_index = None
        for index in range(len(messages) - 1, -1, -1):
            item = messages[index]
            if not isinstance(item, dict) or not isinstance(item.get("info"), dict) or item["info"].get("role") != "user":
                continue
            parts = item.get("parts") if isinstance(item.get("parts"), list) else []
            if any(isinstance(part, dict) and part.get("type") == "text" and part.get("text") == prompt_text for part in parts):
                prompt_index = index
                break
        if prompt_index is None:
            return []
        actions = []
        for item in messages[prompt_index + 1:]:
            if not isinstance(item, dict) or not isinstance(item.get("info"), dict) or item["info"].get("role") != "assistant":
                continue
            try:
                item_actions = self._normalize_message(item, allowed_tools)["actions"]
            except OpenCodeServiceError:
                continue
            actions.extend(item_actions[:MAX_ACTIONS - len(actions)])
            if len(actions) >= MAX_ACTIONS:
                break
        return actions

    def _normalize_message(self, result: Any, allowed_tools: set[str] | None = None) -> dict[str, Any]:
        if not isinstance(result, dict) or not isinstance(result.get("parts"), list):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid message")
        allowed_tools = set(self.custom_tools) if allowed_tools is None else allowed_tools & self.custom_tools
        parts = []
        actions = []
        text_items = []
        text_size = 0
        tool_output_size = 0
        for part in result["parts"][:MAX_PARTS]:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"text", "reasoning"} and isinstance(part.get("text"), str):
                remaining = MAX_TEXT_SIZE - text_size
                value = part["text"].encode("utf-8")[:remaining].decode("utf-8", "ignore")
                text_size += len(value.encode("utf-8"))
                safe_part = {"type": part["type"], "text": value}
                part_time = part.get("time") if isinstance(part.get("time"), dict) else {}
                if part["type"] == "reasoning" and isinstance(part_time.get("start"), (int, float)) and isinstance(part_time.get("end"), (int, float)):
                    safe_part["durationMs"] = max(0, min(round(part_time["end"] - part_time["start"]), 60 * 60 * 1000))
                parts.append(safe_part)
                if part["type"] == "text":
                    text_items.append(value)
                continue
            if part.get("type") == "tool" and part.get("tool") == "skill":
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
                if tool_input.get("name") in self.safe_skills:
                    parts.append({"type": "skill", "skill": tool_input["name"], "status": str(state.get("status", "unknown"))[:32]})
                continue
            if part.get("type") != "tool" or part.get("tool") not in allowed_tools:
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            output = state.get("output")
            safe_part = {"type": "tool", "tool": part["tool"], "status": str(state.get("status", "unknown"))[:32]}
            if isinstance(output, str):
                remaining = MAX_TOOL_OUTPUT_SIZE - tool_output_size
                safe_output = output.encode("utf-8")[:min(MAX_ACTION_SIZE, remaining)].decode("utf-8", "ignore")
                tool_output_size += len(safe_output.encode("utf-8"))
                safe_part["output"] = safe_output
            tool_input = state.get("input")
            if state.get("status") == "completed" and isinstance(tool_input, dict) and len(actions) < MAX_ACTIONS:
                action = self._adapt_tool_call(part["tool"], tool_input)
                if action is not None:
                    actions.append(action)
            parts.append(safe_part)
        text = "\n".join(text_items).encode("utf-8")[:MAX_TEXT_SIZE].decode("utf-8", "ignore")
        if not text and not actions:
            raise OpenCodeServiceError(
                502,
                "provider_empty_response",
                "The AI provider returned an empty response. Try another free model.",
            )
        return {"text": text, "parts": parts, "actions": actions}

    def _adapt_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        action_type = self.tool_action_types.get(tool_name)
        if action_type is None:
            return None
        action = _normalize_tool_purposes(copy.deepcopy(arguments))
        if any(key in action for key in ("type", "action", "requiresApproval", "requiresConfirmation", "readOnly", "destructive", "requiresPasswordEntry")):
            return None
        action["type"] = action_type
        action["requiresConfirmation"] = True
        if action_type in {"schema_read_query", "data_read", "migration_preview", "insert_rows_preview", "create_view_preview", "read_query"}:
            action["readOnly"] = True
        if action_type == "connection_setup":
            action["requiresPasswordEntry"] = True
        if action_type in {"delete_element", "widget_delete"}:
            action["destructive"] = True
        return action
