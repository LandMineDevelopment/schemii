import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemii.common.admin_config import AdminConfig, AiPolicy
from schemii.common.ai.credential_lifecycle import CredentialExpiryWorker, router
from schemii.common.metadata.models import Principal, get_current_principal


@pytest.mark.parametrize("days", [1, 30, 3650])
def test_credential_retention_accepts_bounded_days(days):
    assert AiPolicy(credential_inactivity_days=days).credential_inactivity_days == days


def test_credential_retention_loads_disabled_from_config(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("[ai]\ncredential_expiration_enabled = false\ncredential_inactivity_days = 45\n")
    config = AdminConfig.from_env({"SCHEMII_CONFIG_FILE": str(path)})
    assert config.ai.credential_expiration_enabled is False
    assert config.ai.credential_inactivity_days == 45


@pytest.mark.parametrize("days", [0, -1, 3651, True, 1.5, "30"])
def test_credential_retention_rejects_invalid_days(days):
    with pytest.raises(ValueError, match="ai.credential_inactivity_days"):
        AiPolicy(credential_inactivity_days=days)


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_expiration_requires_boolean(value):
    with pytest.raises(ValueError, match="credential_expiration_enabled"):
        AiPolicy(credential_expiration_enabled=value)


def test_disabled_expiration_reaches_both_repository_adapters():
    from schemii.common.metadata.factory import create_metadata_repositories
    from schemii.common.ai.credential_store import PostgresAiCredentialStore
    from schemii.common.metadata.crypto import CredentialCipher

    repositories = create_metadata_repositories(
        {"SCHEMII_STORAGE_MODE": "memory"}, credential_expiration_enabled=False,
        credential_inactivity_days=45,
    )
    assert repositories.ai_credentials._expiration_enabled is False
    assert repositories.ai_credentials._inactivity_days == 45
    def unexpected_connection():
        pytest.fail("disabled sweep must not connect to PostgreSQL")
    store = PostgresAiCredentialStore(unexpected_connection, CredentialCipher(bytes(32)),
                                      expiration_enabled=False)
    assert store.expire_inactive() == 0


def test_activity_uses_authenticated_owner_and_never_get_polling():
    owners = []
    app = FastAPI()
    app.include_router(router)
    app.state.services = SimpleNamespace(metadata=SimpleNamespace(
        ai_credentials=SimpleNamespace(touch_activity=owners.append)))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="alice", authentication_source="local_prototype")
    with TestClient(app) as client:
        response = client.post("/api/v1/activity", json={"owner": "bob"})
        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/activity").status_code == 405
    assert owners == ["alice"]


def test_expiry_worker_startup_retry_and_clean_stop(caplog):
    async def scenario():
        calls = []
        completed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def sweep():
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("SECRET-TOKEN")
            loop.call_soon_threadsafe(completed.set)

        worker = CredentialExpiryWorker(SimpleNamespace(expire_inactive=sweep), interval_seconds=.01)
        await worker.start()
        assert len(calls) == 1
        await asyncio.wait_for(completed.wait(), timeout=2)
        await worker.stop()
        assert worker._task is None
        count = len(calls)
        await asyncio.sleep(.03)
        assert len(calls) == count
        await worker.stop()

    asyncio.run(scenario())
    assert "SECRET-TOKEN" not in caplog.text
    assert "expiration sweep failed" in caplog.text


def test_disabled_expiry_worker_never_touches_store():
    async def scenario():
        def sweep():
            pytest.fail("disabled retention must not expire credentials")

        worker = CredentialExpiryWorker(SimpleNamespace(expire_inactive=sweep), enabled=False)
        await worker.start()
        await worker.stop()
        assert worker._task is None

    asyncio.run(scenario())
