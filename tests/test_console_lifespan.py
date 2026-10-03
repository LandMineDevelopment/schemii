"""Raw resource expiry runs through the assembled application lifecycle."""

import asyncio
import threading

import pytest

from schemii.schemii.console import raw_session
from tests.test_console import console_client
from tests.test_raw_console import policy_fixture


def test_lifespan_reaps_abandoned_sessions_and_joins_maintenance(monkeypatch):
    api, _ = console_client()
    service = api.app.state.raw_console
    _, idle = policy_fixture()
    _, running = policy_fixture()
    idle.update(id="raw_idle", used=0)
    running.update(id="raw_running", used=0)
    closed = threading.Event()
    idle["raw"].close = closed.set
    running["raw"].close = lambda: None
    running["raw"].cancel = lambda: None
    service.sessions.update({idle["id"]: idle, running["id"]: running})
    service.claim(running)
    # No wall-clock idle wait: the service deadline clock is advanced directly.
    monkeypatch.setattr(service, "_clock", lambda: 2000)
    monkeypatch.setattr(raw_session, "RAW_SESSION_SWEEP_SECONDS", 0.001)

    async def check():
        async with api.app.router.lifespan_context(api.app):
            assert await asyncio.to_thread(closed.wait, 2)
            assert idle["id"] not in service.sessions
            assert running["id"] in service.sessions
            assert running["status"] == "running"
            tasks = [
                task
                for task in asyncio.all_tasks()
                if task.get_name() == "raw-console-maintenance"
            ]
            assert len(tasks) == 1
            service.release(running)
        assert tasks[0].done()
        assert service.sessions == {}
        service.close()

    asyncio.run(check())


def test_maintenance_failure_is_logged_and_next_sweep_runs(monkeypatch, caplog):
    service, _ = policy_fixture()
    monkeypatch.setattr(raw_session, "RAW_SESSION_SWEEP_SECONDS", 0.001)

    async def check():
        stop = asyncio.Event()
        calls = []

        def reap():
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("owned sweep failure")
            loop.call_soon_threadsafe(stop.set)

        service.reap = reap
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(service.maintain(stop), 2)
        assert len(calls) == 2

    asyncio.run(check())
    assert "owned sweep failure" in caplog.text


def test_lifespan_shutdown_joins_the_native_sweep_thread(monkeypatch):
    api, _ = console_client()
    entered = threading.Event()
    finish = threading.Event()
    exited = threading.Event()

    def reap():
        entered.set()
        assert finish.wait(2)
        exited.set()

    api.app.state.raw_console.reap = reap
    monkeypatch.setattr(raw_session, "RAW_SESSION_SWEEP_SECONDS", 0.001)

    async def check():
        context = api.app.router.lifespan_context(api.app)
        await context.__aenter__()
        assert await asyncio.to_thread(entered.wait, 1)
        shutdown = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert not exited.is_set()
        finish.set()
        await asyncio.wait_for(shutdown, 1)
        assert exited.is_set()
        assert not any(
            task.get_name() == "raw-console-maintenance" for task in asyncio.all_tasks()
        )

    asyncio.run(check())


def test_catalog_worker_failure_cannot_skip_raw_or_metadata_cleanup(monkeypatch):
    from types import SimpleNamespace
    from schemii import main
    from schemii.common.metadata.factory import MetadataRepositories

    api, _ = console_client()
    state = api.app.state
    refresh_finished = threading.Event()
    order = []

    def failed_refresh():
        refresh_finished.set()
        raise RuntimeError("owned catalog refresh failed")

    state.ai_model_catalog = SimpleNamespace(refresh=failed_refresh, refresh_seconds=60)
    original_raw_close = state.raw_console.close
    original_migration_stop = main.MigrationExecutionWorker.stop
    original_metadata_close = MetadataRepositories.close

    def close_raw():
        original_raw_close()
        order.append("raw-closed")

    async def stop_migrations(worker):
        await original_migration_stop(worker)
        assert worker._task is None
        order.append("migration-stopped")

    def close_metadata(metadata):
        assert state.raw_console.closed
        assert state.bulk_jobs.closed
        order.append("metadata-closed")
        original_metadata_close(metadata)

    monkeypatch.setattr(state.raw_console, "close", close_raw)
    monkeypatch.setattr(main.MigrationExecutionWorker, "stop", stop_migrations)
    monkeypatch.setattr(MetadataRepositories, "close", close_metadata)

    async def check():
        context = api.app.router.lifespan_context(api.app)
        await context.__aenter__()
        assert await asyncio.to_thread(refresh_finished.wait, 1)
        with pytest.raises(RuntimeError, match="owned catalog refresh failed"):
            await context.__aexit__(None, None, None)
        assert order == ["raw-closed", "migration-stopped", "metadata-closed"]
        assert not any(
            task.get_name() == "ai-credential-expiry" for task in asyncio.all_tasks()
        )

    asyncio.run(check())


@pytest.mark.parametrize("maintenance", ["raw", "chat"])
def test_repeated_shutdown_cancellation_joins_native_consumers_before_metadata(
    monkeypatch, maintenance
):
    from schemii import main
    from schemii.common.metadata.factory import MetadataRepositories

    api, _ = console_client()
    state = api.app.state
    entered, finish, exited, metadata_closed = (threading.Event() for _ in range(4))
    stopped = asyncio.Event()
    original_metadata_close = MetadataRepositories.close
    original_maintain = state.raw_console.maintain

    def blocking_native():
        entered.set()
        assert finish.wait(2)
        exited.set()

    async def record_stop(stop):
        async def observe():
            await stop.wait()
            stopped.set()

        observer = asyncio.create_task(observe())
        try:
            await original_maintain(stop)
        finally:
            if stop.is_set():
                await observer
            else:
                observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)

    state.raw_console.maintain = record_stop
    if maintenance == "raw":
        state.raw_console.reap = blocking_native
        monkeypatch.setattr(raw_session, "RAW_SESSION_SWEEP_SECONDS", 0.001)
    else:
        state.schemoo_ai.maintain = blocking_native
        monkeypatch.setattr(main, "CHAT_MAINTENANCE_SECONDS", 0.001)

    def close_metadata(metadata):
        assert exited.is_set(), (
            "metadata ownership must outlive the real native consumer"
        )
        assert state.raw_console.closed
        assert state.bulk_jobs.closed
        original_metadata_close(metadata)
        metadata_closed.set()

    monkeypatch.setattr(MetadataRepositories, "close", close_metadata)

    async def check():
        context = api.app.router.lifespan_context(api.app)
        await context.__aenter__()
        assert await asyncio.to_thread(entered.wait, 1)
        shutdown = asyncio.create_task(context.__aexit__(None, None, None))
        try:
            await asyncio.wait_for(stopped.wait(), 1)
            shutdown.cancel()
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert not metadata_closed.is_set()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(shutdown, 1)
        assert exited.is_set() and metadata_closed.is_set()
        assert not any(
            task.get_name()
            in {
                "raw-console-maintenance",
                "product-chat-maintenance",
                "schemii-shutdown",
                "schemii-migration-worker",
                "ai-credential-expiry",
            }
            for task in asyncio.all_tasks()
        )

    asyncio.run(check())


def test_failed_raw_maintenance_still_stops_consumers_and_metadata(monkeypatch):
    from schemii.common.metadata.factory import MetadataRepositories

    api, _ = console_client()
    state = api.app.state
    metadata_closed = threading.Event()
    original_metadata_close = MetadataRepositories.close

    async def failed_maintenance(stop):
        raise RuntimeError("owned raw maintenance failure")

    def close_metadata(metadata):
        assert state.raw_console.closed
        assert state.bulk_jobs.closed
        original_metadata_close(metadata)
        metadata_closed.set()

    monkeypatch.setattr(state.raw_console, "maintain", failed_maintenance)
    monkeypatch.setattr(MetadataRepositories, "close", close_metadata)

    async def check():
        with pytest.raises(RuntimeError, match="owned raw maintenance failure"):
            async with api.app.router.lifespan_context(api.app):
                await asyncio.sleep(0)
        assert metadata_closed.is_set()
        assert not any(
            task.get_name()
            in {
                "product-chat-maintenance",
                "schemii-migration-worker",
                "ai-credential-expiry",
            }
            for task in asyncio.all_tasks()
        )

    asyncio.run(check())
