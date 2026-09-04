"""Narrow, authenticated client for the private OpenCode sidecar."""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class OpenCodeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class OpenCodeReply:
    text: str
    tool_calls: tuple[tuple[str, dict[str, Any]], ...]


class OpenCodeClient:
    """Keep OpenCode transport details out of product orchestration."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 300,
        fallback_provider_id: str | None = None,
        fallback_model_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.fallback_provider_id = fallback_provider_id
        self.fallback_model_id = fallback_model_id
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "Accept": "application/json", "X-OpenCode-Directory": "/workspace"}

    @classmethod
    def from_env(cls) -> "OpenCodeClient | None":
        url = os.getenv("SCHEMII_OPENCODE_URL", "").strip()
        if not url:
            return None
        password_file = os.getenv("SCHEMII_OPENCODE_PASSWORD_FILE", "")
        password = Path(password_file).read_text(encoding="utf-8").strip() if password_file else os.getenv("SCHEMII_OPENCODE_PASSWORD", "")
        return cls(
            url,
            os.getenv("SCHEMII_OPENCODE_USERNAME", "opencode"),
            password,
            fallback_provider_id=os.getenv("SCHEMII_AI_FALLBACK_PROVIDER_ID") or None,
            fallback_model_id=os.getenv("SCHEMII_AI_FALLBACK_MODEL_ID") or None,
        )

    def _request(self, method: str, path: str, body: object | None = None) -> Any:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = dict(self._headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(self.base_url + path, data=data, headers=headers, method=method), timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except HTTPError as error:
            error.close()
            raise OpenCodeError(
                "opencode_rejected",
                f"The AI runtime rejected the request ({error.code})",
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise OpenCodeError("opencode_unavailable", "The AI runtime is unavailable") from error
        if len(raw) > 8 * 1024 * 1024:
            raise OpenCodeError("opencode_response_too_large", "The AI runtime returned too much data")
        return json.loads(raw) if raw else None

    def status(self) -> dict[str, Any]:
        health = self._request("GET", "/global/health")
        result = {"healthy": bool(health and health.get("healthy")), "providers": {}}
        try:
            discovered = self._request("GET", "/provider") or {}
            if not isinstance(discovered, dict) or not isinstance(discovered.get("all"), list):
                raise OpenCodeError(
                    "opencode_invalid_response",
                    "The AI runtime returned an invalid provider catalog",
                )
            result["providers"] = {
                "providers": discovered["all"],
                "connected": discovered.get("connected", []),
                "default": discovered.get("default", {}),
                "authMethods": self._request("GET", "/provider/auth") or {},
            }
        except OpenCodeError:
            if not self.fallback_provider_id or not self.fallback_model_id:
                raise
            result["providers"] = {
                "providers": [
                    {
                        "id": self.fallback_provider_id,
                        "name": self.fallback_provider_id,
                        "models": {
                            self.fallback_model_id: {
                                "id": self.fallback_model_id,
                                "name": self.fallback_model_id,
                                "status": "active",
                            }
                        },
                    }
                ]
            }
            result["message"] = (
                "OpenCode provider discovery is unavailable; using the explicitly configured fallback model."
            )
        return result

    @staticmethod
    def _provider_id(provider_id: str) -> str:
        if not isinstance(provider_id, str) or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", provider_id
        ) is None:
            raise OpenCodeError("opencode_invalid_provider", "The AI provider is invalid")
        return provider_id

    def set_api_key(
        self, provider_id: str, key: str, inputs: dict[str, str]
    ) -> None:
        provider_id = self._provider_id(provider_id)
        credential: dict[str, Any] = {"type": "api", "key": key}
        if inputs:
            credential["metadata"] = inputs
        result = self._request(
            "PUT", f"/auth/{quote(provider_id, safe='')}", credential
        )
        if result is not True:
            raise OpenCodeError(
                "opencode_invalid_response",
                "The AI runtime did not confirm the provider credential",
            )

    def delete_provider_auth(self, provider_id: str) -> None:
        provider_id = self._provider_id(provider_id)
        if self._request("DELETE", f"/auth/{quote(provider_id, safe='')}") is not True:
            raise OpenCodeError(
                "opencode_invalid_response",
                "The AI runtime did not confirm credential removal",
            )

    def oauth_authorize(
        self, provider_id: str, method: int, inputs: dict[str, str]
    ) -> dict[str, str]:
        provider_id = self._provider_id(provider_id)
        result = self._request(
            "POST",
            f"/provider/{quote(provider_id, safe='')}/oauth/authorize",
            {"method": method, "inputs": inputs},
        )
        if (
            not isinstance(result, dict)
            or result.get("method") not in {"auto", "code"}
            or not isinstance(result.get("url"), str)
        ):
            raise OpenCodeError(
                "opencode_invalid_response",
                "The AI runtime returned an invalid authorization response",
            )
        return {
            "url": result["url"][:8192],
            "method": result["method"],
            "instructions": str(result.get("instructions", ""))[:4096],
        }

    def oauth_callback(
        self, provider_id: str, method: int, code: str | None
    ) -> None:
        provider_id = self._provider_id(provider_id)
        body: dict[str, Any] = {"method": method}
        if code:
            body["code"] = code
        result = self._request(
            "POST",
            f"/provider/{quote(provider_id, safe='')}/oauth/callback",
            body,
        )
        if result is not True:
            raise OpenCodeError(
                "opencode_invalid_response",
                "The AI runtime did not confirm provider authorization",
            )

    def create_session(self, title: str) -> str:
        value = self._request("POST", "/session", {"title": title[:256]})
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise OpenCodeError("opencode_invalid_response", "The AI runtime returned an invalid session")
        return value["id"]

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", f"/session/{quote(session_id, safe='')}")

    @staticmethod
    def _tool_calls(
        messages: list[dict[str, Any]], known_tools: set[str]
    ) -> list[tuple[str, dict[str, Any]]]:
        calls: list[tuple[str, dict[str, Any]]] = []
        for message in messages:
            for part in message.get("parts", []):
                if not isinstance(part, dict):
                    continue
                name = part.get("tool")
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                if (
                    part.get("type") == "tool"
                    and isinstance(name, str)
                    and name in known_tools
                    and state.get("status") == "completed"
                    and isinstance(state.get("input"), dict)
                ):
                    calls.append((name, state["input"]))
        return calls[:100]

    def _recover_tool_calls(
        self, session_id: str, prompt: str, known_tools: set[str]
    ) -> list[tuple[str, dict[str, Any]]]:
        messages = self._request(
            "GET", f"/session/{quote(session_id, safe='')}/message?limit=20"
        )
        if not isinstance(messages, list):
            return []
        prompt_index = None
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, dict):
                continue
            info = message.get("info") if isinstance(message.get("info"), dict) else {}
            parts = message.get("parts") if isinstance(message.get("parts"), list) else []
            if info.get("role") == "user" and any(
                isinstance(part, dict)
                and part.get("type") == "text"
                and part.get("text") == prompt
                for part in parts
            ):
                prompt_index = index
                break
        if prompt_index is None:
            return []
        assistant_messages = [
            message
            for message in messages[prompt_index + 1 :]
            if isinstance(message, dict)
            and isinstance(message.get("info"), dict)
            and message["info"].get("role") == "assistant"
            and isinstance(message.get("parts"), list)
        ]
        return self._tool_calls(assistant_messages, known_tools)

    def prompt(self, session_id: str, provider_id: str, model_id: str, system: str, prompt: str, tools: dict[str, bool]) -> OpenCodeReply:
        value = self._request("POST", f"/session/{quote(session_id, safe='')}/message", {
            "model": {"providerID": provider_id, "modelID": model_id},
            "system": system,
            "tools": tools,
            "parts": [{"type": "text", "text": prompt}],
        })
        if not isinstance(value, dict):
            raise OpenCodeError("opencode_invalid_response", "The AI runtime returned an invalid message")
        texts: list[str] = []
        known_tools = {
            name
            for name, enabled in tools.items()
            if enabled and name.startswith("schemii_")
        }
        for part in value.get("parts", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
        calls = self._tool_calls([value], known_tools)
        if not calls:
            calls = self._recover_tool_calls(session_id, prompt, known_tools)
        return OpenCodeReply("\n".join(texts).strip(), tuple(calls))
