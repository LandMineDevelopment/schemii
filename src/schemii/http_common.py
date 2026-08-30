from __future__ import annotations

import hashlib
import json
import mimetypes
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from .http_access import HttpAccessPolicy, request_is_allowed
from .http_limits import MAX_BODY_SIZE
from .postgres_service import PostgresService, PostgresServiceError


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
SHARED_WEB_DIR = Path(__file__).resolve().parent / "shared_web"


def api_error_payload(status: int, payload: object) -> dict[str, object]:
    """Normalize every HTTP failure without reflecting arbitrary exception text."""
    source = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(source, dict):
        code = source.get("code")
        message = source.get("message")
        error: dict[str, object] = {
            "code": code if isinstance(code, str) and code else "request_failed",
            "message": message if isinstance(message, str) and message else "The request could not be completed",
        }
        if isinstance(source.get("retryable"), bool):
            error["retryable"] = source["retryable"]
        details = source.get("details") if isinstance(source.get("details"), dict) else {
            key: value for key, value in source.items() if key not in {"code", "message", "retryable"}
        }
        if details:
            error["details"] = details
        return {"error": error}
    if isinstance(source, str) and source:
        return {"error": {"code": "request_failed", "message": "The request could not be completed"}}
    if isinstance(payload, dict) and isinstance(payload.get("operation"), dict):
        operation_error = payload["operation"].get("error")
        if isinstance(operation_error, dict):
            return {"error": {
                "code": operation_error.get("code") if isinstance(operation_error.get("code"), str) else "operation_failed",
                "message": operation_error.get("message") if isinstance(operation_error.get("message"), str) else "The operation failed",
                "details": {key: value for key, value in payload.items() if key in {"operation", "approval"}},
            }}
    messages = {400: "The request is invalid", 401: "Authentication is required", 403: "The request is forbidden", 404: "The API route was not found", 405: "The method is not allowed", 413: "The request is too large", 415: "The content type is not supported"}
    return {"error": {"code": "not_found" if status == 404 else "request_failed", "message": messages.get(status, "The request could not be completed"), **({"retryable": True} if status in {429, 502, 503, 504} else {})}}


def metadata_profile_dependencies(authority: object, profile_id: str) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {"activeChats": [], "plans": [], "operations": []}
    metadata = getattr(authority, "store", None)
    if metadata is None:
        return result
    chats = [item for item in metadata.list_chats(states=["active"], limit=1000) if (item.get("target") or {}).get("profileId") == profile_id]
    if len(chats) == 1000:
        raise PostgresServiceError(409, "profile_impact_incomplete", "Profile deletion impact exceeds the safe review limit")
    result["activeChats"] = [{"id": item["chatId"], "resourceKind": item["resourceKind"], "resourceId": item["resourceId"]} for item in chats]
    for chat in chats:
        operations = metadata.list_operations(chat["chatId"], limit=1000)
        if len(operations) == 1000:
            raise PostgresServiceError(409, "profile_impact_incomplete", "Profile deletion impact exceeds the safe review limit")
        for operation in operations:
            if operation.get("state") not in {"succeeded", "failed", "cancelled"}:
                result["operations"].append({"id": operation["operationId"], "state": operation["state"], "chatId": chat["chatId"]})
    plans = metadata.list_migration_plans(limit=1000)
    if len(plans) == 1000:
        raise PostgresServiceError(409, "profile_impact_incomplete", "Profile deletion impact exceeds the safe review limit")
    result["plans"] = [{"id": item["planId"], "state": item["state"]} for item in plans if item.get("target", {}).get("profileId") == profile_id]
    return result


def make_local_app_handler(
    web_dir: Path,
    postgres_service: PostgresService,
    session_token: str,
    *,
    server_id: str,
    access_policy: HttpAccessPolicy = HttpAccessPolicy(),
):
    class LocalAppHandler(SimpleHTTPRequestHandler):
        service = postgres_service
        postgres_server_id = server_id
        postgres_session_binding = hashlib.sha256(f"{server_id}\0{session_token}".encode("utf-8")).hexdigest()

        def __init__(self, *args, **kwargs):
            self._response_started = False
            super().__init__(*args, directory=str(web_dir), **kwargs)

        def handle_one_request(self):
            self._response_started = False
            try:
                super().handle_one_request()
            except Exception:
                if not self._response_started and urlparse(getattr(self, "path", "")).path.startswith("/api/"):
                    self.send_json(500, {"error": {"code": "internal_error", "message": "The request could not be completed", "retryable": True}})
                    return
                raise

        def parse_request(self):
            if not super().parse_request():
                return False
            if request_is_allowed(self.client_address[0], self.headers, access_policy):
                return True
            self.send_json(403, {"error": {"code": "forbidden", "message": "The request is forbidden"}})
            return False

        def send_response(self, code, message=None):
            self._response_started = True
            super().send_response(code, message)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            super().end_headers()

        def send_json(self, status: int, payload, *, normalize_error: bool = True):
            if status >= 400 and normalize_error:
                payload = api_error_payload(status, payload)
            content = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def send_bytes(self, status: int, content: bytes, content_type: str, headers: dict[str, str] | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(content)

        def send_error(self, code, message=None, explain=None):
            if urlparse(self.path).path.startswith("/api/"):
                self.send_json(code, {"error": {"code": "not_found" if code == 404 else "request_failed", "message": "The API route was not found" if code == 404 else "The request could not be completed"}})
                return
            super().send_error(code, message, explain)

        def _send_shared_asset(self, path: str) -> bool:
            prefix = "/shared/"
            if not path.startswith(prefix):
                return False
            name = path[len(prefix):]
            if not name or "/" in name or "\\" in name:
                self.send_error(404, "File not found")
                return True
            asset = SHARED_WEB_DIR / name
            if not asset.is_file():
                self.send_error(404, "File not found")
                return True
            content = asset.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return True

        def _handle_common_get(self, path: str) -> bool:
            if path == "/api/session":
                if not self._is_local_request():
                    self.send_json(403, {"error": {"code": "forbidden", "message": "Session requires a local origin"}})
                else:
                    self.send_json(200, {"token": session_token, "serverId": server_id})
                return True
            return self._send_shared_asset(path)

        def _is_local_request(self) -> bool:
            return request_is_allowed(self.client_address[0], self.headers, access_policy)

        def _authorize_postgres(self) -> bool:
            return self._authorize_local_api("PostgreSQL API", "PostgreSQL session token is missing or invalid")

        def _authorize_local_api(self, scope: str, invalid_session_message: str) -> bool:
            if not self._is_local_request():
                self.send_json(403, {"error": {"code": "forbidden", "message": f"{scope} requires a local origin"}})
                return False
            if self.headers.get("X-Schemii-Token") != session_token:
                self.send_json(403, {"error": {"code": "invalid_session", "message": invalid_session_message}})
                return False
            return True

        def _authorize_shutdown(self) -> bool:
            return self._authorize_local_api("Shutdown", "Shutdown session token is missing or invalid")

        def _read_json(self, maximum: int = MAX_BODY_SIZE):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Invalid content length") from exc
            if length <= 0:
                raise ValueError("Request body is empty")
            if length > maximum:
                raise OverflowError("Request body exceeds the byte limit")
            if self.headers.get_content_type() != "application/json":
                raise TypeError("Content-Type must be application/json")
            try:
                return json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid JSON") from exc

        def _body_or_error(self, maximum: int = MAX_BODY_SIZE):
            try:
                body = self._read_json(maximum)
            except TypeError as error:
                self.send_json(415, {"error": {"code": "invalid_content_type", "message": str(error)}})
                return None
            except OverflowError as error:
                self.send_json(413, {"error": {"code": "request_too_large", "message": str(error), "limit": maximum}})
                return None
            except ValueError as error:
                self.send_json(400, {"error": {"code": "invalid_request", "message": str(error)}})
                return None
            if not isinstance(body, dict):
                self.send_json(400, {"error": {"code": "invalid_request", "message": "Request body must be an object"}})
                return None
            return body

        def _service_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except PostgresServiceError as error:
                self.send_json(error.status, error.to_dict())

    return LocalAppHandler
