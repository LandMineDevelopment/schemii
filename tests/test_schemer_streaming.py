import asyncio
import pytest
from types import SimpleNamespace as NS

from schemii.schemer import streaming
from schemii.common.postgres.console.models import ConsoleResultColumn


class Console:
    def __init__(self, pages):
        self.pages = list(pages)
        self.closed = []
        self.cancelled = False
        self.execution = NS(id="execution", status="succeeded", results=[NS(id="result")])

    def reserve_read_target(self, *args, **kwargs):
        return self.execution

    def run(self, *args):
        pass

    def get_owned(self, *args):
        return self.execution

    def page(self, *args):
        rows = self.pages.pop(0)
        return NS(rows=rows, columns=[ConsoleResultColumn(name="output_1", data_type="text")],
                  next_cursor="next" if self.pages else None, truncated=False)

    def close_result(self, *args):
        self.closed.append(args[-1])

    def cancel(self, *args):
        self.cancelled = True


def setup(pages):
    console = Console(pages)
    return console, NS(console=console), NS(connection_id="c", database="d", namespace="public")


def collect(services, model, **kwargs):
    async def run():
        return [event async for event in streaming.events(services, "owner", model,
                [{"tileId": "tile", "plan": {"sql": "select 1", "outputLabels": ["Full name"]}}], **kwargs)]
    return asyncio.run(run())


def test_stream_eagerly_drains_and_closes_before_completion():
    console, services, model = setup([[[1]], [[2]]])
    events = collect(services, model)
    assert [e["rows"] for e in events if e["type"] == "rows"] == [[[1]], [[2]]]
    assert next(e for e in events if e["type"] == "rows")["columns"][0]["name"] == "Full name"
    assert console.closed == ["result"]
    assert events[-2]["type"] == "complete"
    assert events[-1]["type"] == "end"
    assert not console.cancelled


def test_row_cap_and_byte_cap(monkeypatch):
    monkeypatch.setitem(streaming.LIMITS, "rows", 2)
    console, services, model = setup([[[1], [2]], [[3]]])
    events = collect(services, model)
    complete = events[-2]
    assert complete["rowCount"] == 2
    assert complete["reason"] == "row_limit"
    assert console.closed == ["result"]
    monkeypatch.setitem(streaming.LIMITS, "bytes", 1)
    console, services, model = setup([[["wide"]]])
    events = collect(services, model)
    assert events[-2]["rowCount"] == 0
    assert events[-2]["reason"] == "byte_limit"
    assert console.closed == ["result"]


def test_export_ignores_preview_caps(monkeypatch):
    monkeypatch.setitem(streaming.LIMITS, "rows", 1)
    console, services, model = setup([[[1], [2]], [[3]]])
    events = collect(services, model, export=True)
    assert events[-2]["rowCount"] == 3
    assert not events[-2]["limitReached"]
    assert console.closed == ["result"]


def test_disconnect_closes_source():
    console, services, model = setup([[[1]], [[2]]])
    async def run():
        source = streaming.events(services, "owner", model, [{"tileId": "tile", "plan": {"sql": "select 1"}}])
        async for event in source:
            if event["type"] == "rows":
                break
        await source.aclose()
    asyncio.run(run())
    assert console.cancelled
    assert console.closed == ["result"]


def test_csv_formula_protection():
    assert streaming._csv_value("=1+1") == "'=1+1"
    assert streaming._csv_value(-42) == -42


def test_failed_fetch_cleans_up_before_error_is_delivered():
    console, services, model = setup([])
    def fail(*args):
        raise RuntimeError("secret database value")
    console.page = fail
    async def run():
        async for event in streaming.events(services, "owner", model,
                [{"tileId": "tile", "plan": {"sql": "select 1"}}]):
            if event["type"] == "error":
                assert console.closed == ["result"]
                assert "secret" not in event["message"]
    asyncio.run(run())


def test_cancellation_during_fetch_joins_worker_and_closes():
    import threading
    console, services, model = setup([])
    entered, release = threading.Event(), threading.Event()
    def page(*args):
        entered.set()
        assert release.wait(2)
        return NS(rows=[], columns=[], next_cursor=None, truncated=False)
    def cancel(*args):
        console.cancelled = True
        release.set()
    console.page, console.cancel = page, cancel
    async def run():
        work = asyncio.create_task(_consume())
        while not entered.is_set():
            await asyncio.sleep(.001)
        work.cancel()
        try:
            await work
        except asyncio.CancelledError:
            pass
    async def _consume():
        async for _ in streaming.events(services, "owner", model,
                [{"tileId": "tile", "plan": {"sql": "select 1"}}]):
            pass
    asyncio.run(run())
    assert console.cancelled and console.closed == ["result"]


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("failure", ["send_error", "timeout", "disconnect"])
def test_asgi_transport_failures_release_source(monkeypatch, spec_version, failure):
    console, services, model = setup([[[1]], [[2]]])
    monkeypatch.setattr(streaming, "STREAM_SECONDS", .05)

    async def run():
        rows_sent = asyncio.Event()
        report = streaming.response(services, "owner", model,
            [{"tileId": "tile", "plan": {"sql": "select 1"}}])

        async def send(message):
            if message["type"] == "http.response.body" and b'"type":"rows"' in message["body"]:
                rows_sent.set()
                if failure == "send_error":
                    raise OSError("browser disconnected")
                await asyncio.Event().wait()

        async def receive():
            if failure == "disconnect" and spec_version == "2.3":
                await rows_sent.wait()
                return {"type": "http.disconnect"}
            await asyncio.Event().wait()

        # ASGI2.4 reports disconnect through send(), not receive(). A blocked
        # send still falls under the same overall response deadline.
        try:
            await report({"type": "http", "asgi": {"spec_version": spec_version}}, receive, send)
        except (OSError, TimeoutError):
            pass
        except Exception as error:
            from starlette.requests import ClientDisconnect
            assert isinstance(error, ClientDisconnect)
        assert rows_sent.is_set()
        assert console.cancelled
        assert console.closed == ["result"]

    asyncio.run(run())


def test_revoked_report_stops_before_delivering_pending_rows():
    from schemii.common.api.errors import ApiProblem
    console, services, model = setup([[["private"]]])
    checks = 0
    def check():
        nonlocal checks
        checks += 1
        if checks > 1:
            raise ApiProblem(403, 'report_access_revoked', 'Report access was removed.')
    services.report_access = NS(check=check)
    events = collect(services, model)
    assert not [event for event in events if event['type'] == 'rows']
    assert next(event for event in events if event['type'] == 'error')['code'] == 'report_access_revoked'
    assert console.cancelled and console.closed == ['result']
