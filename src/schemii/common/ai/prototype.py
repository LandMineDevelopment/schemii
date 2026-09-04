"""Opt-in Pi login boundary. Provider tokens never cross the browser API."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request as HttpRequest, urlopen

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal, get_current_principal


class PiClient:
    def __init__(self, url: str, secret: str):
        self.url = url.rstrip("/")
        self._secret = secret.strip()

    @classmethod
    def from_env(cls):
        url = os.environ.get("SCHEMII_PI_PROTOTYPE_URL")
        if not url:
            return None
        secret = Path(os.environ.get("SCHEMII_PI_PASSWORD_FILE", "/run/secrets/opencode_password")).read_text()
        return cls(url, secret)

    def call(self, path, body=None):
        request = HttpRequest(self.url + path,
                              data=json.dumps(body).encode() if body is not None else None,
                              headers={"Authorization": "Bearer " + self._secret,
                                       "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read(131073)
            if len(raw) > 131072:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except HTTPError as error:
            if error.code == 404:
                raise ApiProblem(404, "pi_login_missing", "This sign-in expired or belongs to another user. Start again.") from None
            if error.code == 429:
                raise ApiProblem(429, "pi_login_capacity", "Too many sign-ins are pending. Cancel one or wait for it to expire.") from None
            raise ApiProblem(502, "pi_login_failed", "The sign-in service could not complete this request.") from None
        except (URLError, TimeoutError, ValueError, OSError):
            raise ApiProblem(503, "pi_unavailable", "The experimental sign-in service is unavailable. Try again shortly.") from None


router = APIRouter(tags=["ai-prototype"])


def _services(request):
    client = request.app.state.pi_client
    if client is None:
        raise ApiProblem(503, "pi_disabled", "The Pi prototype is not enabled on this server.")
    return client, request.app.state.services.metadata.ai_credentials


@router.get("/ai-prototype", include_in_schema=False)
def page(request: Request):
    _services(request)
    return FileResponse(Path(__file__).parent.parent / "web/assets/pi-prototype.html")


@router.get("/api/v1/ai/prototype/credentials")
def credentials(request: Request, principal: Principal = Depends(get_current_principal)):
    client, store = _services(request)
    client.call("/health")
    return {"credentials": store.list(principal.user_id)}


@router.post("/api/v1/ai/prototype/login")
def login(request: Request, principal: Principal = Depends(get_current_principal)):
    client, store = _services(request)
    record = store.begin_login(principal.user_id, "codex-prototype", "openai-codex")
    result = client.call("/logins", {"owner": principal.user_id,
                         "credentialId": "codex-prototype", "generation": record["generation"]})
    return {"id": result["id"], "status": "pending"}


@router.get("/api/v1/ai/prototype/logins/{login_id}")
def poll(login_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    client, store = _services(request)
    result = client.call("/logins/status", {"owner": principal.user_id, "id": login_id})
    if result.get("status") == "succeeded":
        saved = store.save(principal.user_id, result["credentialId"], "openai-codex",
                           result["credential"], result["generation"])
        if not saved:
            raise ApiProblem(409, "pi_login_superseded", "This sign-in was replaced or disconnected. Start again.")
    public = {key: result[key] for key in ("id", "status", "expiresAt", "userCode") if key in result}
    # Never reflect an arbitrary provider-supplied navigation target.
    if result.get("verificationUrl") == "https://auth.openai.com/codex/device":
        public["verificationUrl"] = result["verificationUrl"]
    if result.get("status") == "failed":
        public["message"] = "Sign-in failed or expired. You can start again."
    return public


@router.delete("/api/v1/ai/prototype/logins/{login_id}")
def cancel(login_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    client, store = _services(request)
    current = client.call("/logins/status", {"owner": principal.user_id, "id": login_id})
    store.fence_login(principal.user_id, current["credentialId"], current["generation"])
    client.call("/logins/cancel", {"owner": principal.user_id, "id": login_id})
    return {"status": "cancelled"}


@router.delete("/api/v1/ai/prototype/credentials")
def disconnect(request: Request, principal: Principal = Depends(get_current_principal)):
    _, store = _services(request)
    store.delete(principal.user_id, "codex-prototype")
    return {"status": "disconnected"}
