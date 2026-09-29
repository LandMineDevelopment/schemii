"""Raw resource expiry runs through the assembled application lifecycle."""

import asyncio
import threading

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
