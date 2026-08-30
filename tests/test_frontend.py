import re
from importlib.resources import files

from fastapi.testclient import TestClient

from schemii.main import create_app


def test_frontend_is_served_with_browser_security_and_cache_headers() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert '<script type="module" src="assets/app.js"></script>' in response.text
    assert "<script>" not in response.text

    head = api.head("/")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"].startswith("text/html")


def test_local_prototype_rejects_untrusted_host_headers() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/api/v1/session", headers={"Host": "rebound.attacker.example"})

    assert response.status_code == 400
    assert response.text == "Invalid host header"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert len(response.headers["x-request-id"]) == 32


def test_api_documentation_receives_general_browser_safety_headers() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/docs")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" not in response.headers


def test_every_packaged_frontend_asset_is_available_and_revalidated() -> None:
    api = TestClient(create_app(), base_url="http://localhost")
    assets = files("schemii.schemii").joinpath("web", "assets")

    for asset in assets.iterdir():
        if not asset.is_file():
            continue
        response = api.get(f"/assets/{asset.name}")
        assert response.status_code == 200, asset.name
        assert response.headers["cache-control"] == "public, max-age=0, must-revalidate"
        assert response.headers["x-content-type-options"] == "nosniff"

    first = api.get("/assets/app.js")
    revalidated = api.get("/assets/app.js", headers={"If-None-Match": first.headers["etag"]})
    assert revalidated.status_code == 304
    assert revalidated.headers["cache-control"] == "public, max-age=0, must-revalidate"


def test_frontend_does_not_replace_unknown_routes_with_the_app_shell() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/not-a-frontend-route")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "not_found"


def test_unavailable_controls_are_backed_by_the_shared_capability_registry() -> None:
    web = files("schemii.schemii").joinpath("web")
    index = web.joinpath("index.html").read_text(encoding="utf-8")
    catalog = web.joinpath("assets", "catalog.js").read_text(encoding="utf-8")
    unavailable = web.joinpath("assets", "unavailable.js").read_text(encoding="utf-8")

    registry_matches = re.findall(
        r'^  (?:(?:"([^"]+)")|([a-z][a-z0-9-]*)):\s*\{ title:',
        unavailable,
        re.MULTILINE,
    )
    registry = {quoted or bare for quoted, bare in registry_matches}
    references = set(re.findall(r'data-unavailable="([^"]+)"', index))
    references.update(re.findall(r'unavailableButton\("([^"]+)"', catalog))

    assert references == registry
    assert "restore-examples" in registry
    assert "The active API does not provide example content" in unavailable


def test_frontend_uses_only_the_active_same_origin_api_contract() -> None:
    api_source = (
        files("schemii.schemii")
        .joinpath("web", "assets", "api.js")
        .read_text(encoding="utf-8")
    )

    assert 'const API_ROOT = "/api/v1";' in api_source
    assert "credentials: \"same-origin\"" in api_source
    assert "cache: \"no-store\"" in api_source
    assert "http://" not in api_source
    assert "https://" not in api_source
