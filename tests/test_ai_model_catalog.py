import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemii.common.ai.model_catalog import (
    MODEL_METADATA_URL, ZEN_MODELS_URL, ModelCatalogWorker, ZenModelCatalog,
    fetch_public_catalog,
)
from schemii.common.ai import model_catalog as catalog_module
from schemii.common.admin_config import AdminConfig, AiPolicy
from schemii.common.ai.prototype import router
from schemii.common.api.errors import install_api_error_handlers
from schemii.common.metadata.models import Principal, get_current_principal


class CatalogFixture:
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.models = {"plain": {"id": "plain", "name": "Plain", "tool_call": True,
                                "cost": {"input": 0, "output": 0}}}
        self.active = ["plain"]
        self.calls = []
        self.fail = False

    def fetch(self, url):
        self.calls.append(url)
        if self.fail:
            raise RuntimeError("PRIVATE-provider-response")
        if url == ZEN_MODELS_URL:
            return {"data": [{"id": key} for key in self.active]}
        assert url == MODEL_METADATA_URL
        return {"opencode": {"models": self.models}}

    def catalog(self):
        return ZenModelCatalog(fetch_json=self.fetch, clock=lambda: self.now,
                               refresh_seconds=60, max_stale_seconds=120)


def test_no_constructor_network_and_shared_cached_snapshot():
    fixture = CatalogFixture()
    catalog = fixture.catalog()
    assert fixture.calls == []
    assert catalog.snapshot()["models"] == []
    first = catalog.refresh()
    assert first["models"][0]["id"] == "plain"  # No 'free' substring required.
    assert first["models"][0]["toolCall"] is True
    assert first["stale"] is False
    first["models"].clear()
    assert len(catalog.refresh()["models"]) == 1
    assert fixture.calls == [ZEN_MODELS_URL, MODEL_METADATA_URL]


@pytest.mark.parametrize("cost", [None, {}, {"input": 0}, {"input": False, "output": 0},
    {"input": "0", "output": 0}, {"input": 0, "output": 1},
    {"input": 0, "output": float("nan")}, {"input": 0, "output": 0, "cache_read": 1},
    {"input": 0, "output": 0, "tiers": {"large": 1}}])
def test_free_named_models_require_valid_zero_prices(cost):
    fixture = CatalogFixture()
    fixture.active = ["looks-free"]
    fixture.models = {"looks-free": {"cost": cost}}
    assert fixture.catalog().refresh()["models"] == []


def test_refresh_removes_paid_deprecated_and_inactive_and_adds_new_models():
    fixture = CatalogFixture()
    catalog = fixture.catalog()
    catalog.refresh()
    fixture.models["plain"]["cost"]["output"] = 1
    fixture.models["new"] = {"cost": {"input": 0, "output": 0}}
    fixture.models["deprecated"] = {"status": "deprecated", "cost": {"input": 0, "output": 0}}
    fixture.models["not-live"] = {"cost": {"input": 0, "output": 0}}
    fixture.active += ["new", "deprecated"]
    fixture.now += timedelta(seconds=60)
    assert [m["id"] for m in catalog.refresh()["models"]] == ["new"]
    fixture.active = []
    assert catalog.refresh(force=True)["models"] == []


def test_failed_refresh_retains_only_until_max_age_and_never_leaks_errors(caplog):
    fixture = CatalogFixture()
    catalog = fixture.catalog()
    good = catalog.refresh()
    fixture.now += timedelta(seconds=60)
    fixture.fail = True
    stale = catalog.refresh()
    assert stale["models"] == good["models"]
    assert stale["stale"] is True
    assert stale["error"] == "catalog_unavailable"
    assert stale["lastSuccessAt"] == good["lastSuccessAt"]
    fixture.now += timedelta(seconds=60)
    assert catalog.snapshot()["models"] == []
    assert "PRIVATE" not in caplog.text
    assert "PRIVATE" not in str(stale)
    fixture.fail = False
    assert catalog.refresh()["stale"] is False


def test_concurrent_calls_do_not_duplicate_refresh():
    fixture = CatalogFixture()
    entered, release = Event(), Event()

    def fetch(url):
        entered.set()
        assert release.wait(2)
        return fixture.fetch(url)

    catalog = ZenModelCatalog(fetch_json=fetch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        refreshing = executor.submit(catalog.refresh)
        assert entered.wait(2)
        assert catalog.refresh(force=True)["stale"] is True
        release.set()
        assert len(refreshing.result()["models"]) == 1
    assert fixture.calls == [ZEN_MODELS_URL, MODEL_METADATA_URL]


def test_fixed_urls_only():
    with pytest.raises(ValueError, match="Unsupported"):
        fetch_public_catalog("https://attacker.example/catalog")


def test_public_fetch_is_bounded_and_disables_redirects(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == catalog_module.MAX_CATALOG_BYTES + 1
            return b"x" * size

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == ZEN_MODELS_URL
            assert timeout == 10
            assert "Authorization" not in request.headers
            return Response()

    def opener(handler):
        assert handler.redirect_request(None, None, 302, "", {}, "https://elsewhere") is None
        return Opener()

    monkeypatch.setattr(catalog_module, "build_opener", opener)
    with pytest.raises(ValueError, match="response limit"):
        fetch_public_catalog(ZEN_MODELS_URL)


def test_malformed_upstream_does_not_replace_good_snapshot():
    fixture = CatalogFixture()
    catalog = fixture.catalog()
    first = catalog.refresh()
    fixture.models = []
    invalid = catalog.refresh(force=True)
    assert invalid["models"] == first["models"]
    assert invalid["error"] == "catalog_unavailable"


def test_worker_refreshes_and_stops_without_leaking_task():
    async def scenario():
        fixture = CatalogFixture()
        catalog = fixture.catalog()
        worker = ModelCatalogWorker(catalog)
        await worker.start()
        for _ in range(100):
            if catalog.snapshot()["lastSuccessAt"]:
                break
            await asyncio.sleep(.01)
        assert catalog.snapshot()["lastSuccessAt"]
        await worker.stop()
        assert worker._task is None

    asyncio.run(scenario())


@pytest.mark.parametrize("policy", [
    {"catalog_refresh_seconds": 0}, {"catalog_refresh_seconds": True},
    {"catalog_refresh_seconds": 120, "catalog_max_stale_seconds": 60},
    {"catalog_max_stale_seconds": 604801},
])
def test_catalog_config_rejects_invalid_limits(policy):
    with pytest.raises(ValueError, match="ai.catalog"):
        AiPolicy(**policy)


def test_catalog_config_loads_custom_intervals():
    config = AdminConfig.from_document({"ai": {"catalog_refresh_seconds": 120,
                                                "catalog_max_stale_seconds": 3600}})
    assert config.ai.catalog_refresh_seconds == 120
    assert config.ai.catalog_max_stale_seconds == 3600


def test_catalog_route_reads_shared_snapshot_without_sidecar_or_network():
    app = FastAPI()
    app.include_router(router)
    install_api_error_handlers(app)
    identities = []

    def principal():
        identities.append("alice")
        return Principal(user_id="alice", authentication_source="local_prototype")

    app.dependency_overrides[get_current_principal] = principal
    fixture = CatalogFixture()
    app.state.ai_model_catalog = fixture.catalog()
    app.state.ai_model_catalog.refresh()
    fixture.calls.clear()
    with TestClient(app) as client:
        response = client.get("/api/v1/ai/prototype/catalog")
        assert response.status_code == 200
        assert response.json()["models"][0]["id"] == "plain"
        assert identities == ["alice"]
        assert fixture.calls == []
        app.state.ai_model_catalog = None
        assert client.get("/api/v1/ai/prototype/catalog").status_code == 503
