import asyncio
import importlib.util
import json
from pathlib import Path
import threading
import pytest
from types import SimpleNamespace as NS

from schemii.schemer import streaming
from schemii.common.api.errors import ApiProblem
from schemii.common.postgres.console.models import ConsoleResultColumn
from schemii.common.query_executions.errors import ConsoleServiceError


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


@pytest.fixture
def android_streaming(tmp_path, monkeypatch):
    runtime_path = Path(__file__).resolve().parents[1] / "dev/android/runtime.py"
    spec = importlib.util.spec_from_file_location("android_dashboard_runtime", runtime_path)
    runtime_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime_module)
    runtime = runtime_module.Runtime(tmp_path / "state")
    environment = runtime._environment(
        runtime_module.validate_config("100.64.2.3", "phone.example.ts.net")
    )
    for name in (
        "SCHEMER_PREVIEW_ROWS", "SCHEMER_PREVIEW_BYTES",
        "SCHEMER_DASHBOARD_PREVIEW_ROWS", "SCHEMER_DASHBOARD_PREVIEW_BYTES",
    ):
        monkeypatch.setenv(name, environment[name])
    # Read the actual module's import-time limits from the phone environment,
    # without replacing the shared streaming module used by the other tests.
    spec = importlib.util.spec_from_file_location("android_dashboard_streaming", streaming.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_dashboard(module, row_sets):
    console, services, model = setup([])
    pages = {
        f"tile-{index}": [rows[start:start + 1000] for start in range(0, len(rows), 1000)]
        for index, rows in enumerate(row_sets)
    }
    console.execution.results = [NS(id=tile_id) for tile_id in pages]

    def page(_owner, _connection, _execution, result_id, _cursor):
        remaining = pages[result_id]
        return NS(rows=remaining.pop(0), columns=[ConsoleResultColumn(name="value", data_type="text")],
                  next_cursor="next" if remaining else None, truncated=False)

    console.page = page
    tiles = [{"tileId": tile_id, "plan": {"sql": "select 1"}} for tile_id in pages]

    async def run():
        return [event async for event in module.events(services, "owner", model, tiles)]

    events = asyncio.run(run())
    assert console.closed == list(pages)
    assert not console.cancelled
    assert events[-1]["type"] == "end"
    assert not [event for event in events if event["type"] == "error"]
    return events


def test_android_dashboard_stream_keeps_all_three_detail_tiles(android_streaming):
    # Five summary tiles precede three detail tables. Every detail source exceeds
    # its own row cap; a 10,000-row dashboard cap starves the later two tables.
    row_sets = [[[row] for row in range(count)] for count in (1, 1, 2, 7, 500, 10001, 10001, 10001)]
    events = collect_dashboard(android_streaming, row_sets)
    complete = [event for event in events if event["type"] == "complete"]
    assert [event["rowCount"] for event in complete] == [1, 1, 2, 7, 500, 10000, 10000, 10000]
    assert [event["reason"] for event in complete] == [None] * 5 + ["row_limit"] * 3
    assert sum(len(event["rows"]) for event in events if event["type"] == "rows") == 30511


def test_android_dashboard_stream_retains_tile_and_dashboard_byte_caps(android_streaming):
    row = ["x" * (4 * 1024 * 1024 - 5)]
    assert len(android_streaming.encode(row)) == 4 * 1024 * 1024
    events = collect_dashboard(android_streaming, [[row] * 3 for _ in range(3)])
    complete = [event for event in events if event["type"] == "complete"]
    assert [event["rowCount"] for event in complete] == [2, 2, 0]
    assert [event["reason"] for event in complete] == ["byte_limit", "byte_limit", "dashboard_byte_limit"]
    assert all(event["limitReached"] for event in complete)
    assert events[0]["limits"]["bytes"] == 8 * 1024 * 1024
    assert events[0]["limits"]["totalBytes"] == 16 * 1024 * 1024


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


class StrictMultiResultConsole(Console):
    """Match the repositories' rejection of a result closed more than once."""

    def __init__(self):
        super().__init__([])
        self.execution.results = [NS(id="first"), NS(id="second")]
        self.close_attempts = []
        self.release = threading.Event()

    def page(self, _owner, _workspace, _execution, result_id, _cursor):
        return NS(
            rows=[[result_id]],
            columns=[ConsoleResultColumn(name="value", data_type="text")],
            next_cursor=None,
            truncated=False,
        )

    def close_result(self, *args):
        result_id = args[-1]
        self.close_attempts.append(result_id)
        if result_id in self.closed:
            raise ConsoleServiceError(
                410,
                "console_result_gone",
                "Console result was closed, expired, or already advanced",
            )
        super().close_result(*args)

    def cancel(self, *args):
        super().cancel(*args)
        self.release.set()


def setup_multi_result():
    console = StrictMultiResultConsole()
    services = NS(console=console)
    model = NS(connection_id="c", database="d", namespace="public")
    tiles = [
        {"tileId": result.id, "plan": {"sql": "select 1"}}
        for result in console.execution.results
    ]
    return console, services, model, tiles


async def wait_for_worker(event):
    # Keep fixture failures bounded without leaving a worker blocked forever.
    with streaming.anyio.fail_after(2):
        while not event.is_set():
            await asyncio.sleep(0.001)


def assert_results_released_once(console):
    assert console.close_attempts == ["first", "second"]
    assert console.closed == ["first", "second"]


def test_multi_result_stream_completes_with_strict_closes():
    console, services, model, tiles = setup_multi_result()

    async def run():
        return [
            event async for event in streaming.events(services, "owner", model, tiles)
        ]

    events = asyncio.run(run())
    assert [event["type"] for event in events] == [
        "start",
        "execution",
        "rows",
        "complete",
        "rows",
        "complete",
        "end",
    ]
    assert_results_released_once(console)
    assert not console.cancelled


def test_multi_result_query_error_keeps_completed_result_closed():
    console, services, model, tiles = setup_multi_result()
    page = console.page

    def fail_second(*args):
        if args[-2] == "second":
            raise ApiProblem(
                422, "report_query_failed", "The report query did not complete."
            )
        return page(*args)

    console.page = fail_second

    async def run():
        events = []
        async for event in streaming.events(services, "owner", model, tiles):
            if event["type"] == "error":
                assert_results_released_once(console)
            events.append(event)
        return events

    events = asyncio.run(run())
    assert [event["type"] for event in events] == [
        "start",
        "execution",
        "rows",
        "complete",
        "error",
        "end",
    ]
    assert events[-2] == {
        "type": "error",
        "tileId": "second",
        "code": "report_query_failed",
        "message": "The report query did not complete.",
    }
    assert_results_released_once(console)
    assert console.cancelled


def test_multi_result_response_deadline_preserves_timeout_after_completed_tile():
    console, services, model, tiles = setup_multi_result()
    page = console.page
    entered = threading.Event()

    def block_second(*args):
        if args[-2] == "second":
            entered.set()
            assert console.release.wait(2)
        return page(*args)

    console.page = block_second

    async def run():
        events = []
        report = streaming.response(services, "owner", model, tiles)
        report.lifetime_seconds = 0.05

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                events.extend(json.loads(line) for line in message["body"].splitlines())

        with pytest.raises(TimeoutError):
            await report.stream_response(send)
        assert entered.is_set()
        assert [event["type"] for event in events] == [
            "start",
            "execution",
            "rows",
            "complete",
        ]

    asyncio.run(run())
    assert_results_released_once(console)
    assert console.cancelled


def test_multi_result_disconnect_closes_only_remaining_results():
    console, services, model, tiles = setup_multi_result()

    async def run():
        source = streaming.events(services, "owner", model, tiles)
        async for event in source:
            if event["type"] == "complete":
                break
        await source.aclose()

    asyncio.run(run())
    assert_results_released_once(console)
    assert console.cancelled


def test_multi_result_send_failure_preserves_transport_error():
    console, services, model, tiles = setup_multi_result()
    failure = OSError("browser disconnected")

    async def run():
        report = streaming.response(services, "owner", model, tiles)

        async def send(message):
            if (
                message["type"] == "http.response.body"
                and b'"type":"complete"' in message["body"]
            ):
                raise failure

        with pytest.raises(OSError) as caught:
            await report.stream_response(send)
        assert caught.value is failure

    asyncio.run(run())
    assert_results_released_once(console)
    assert console.cancelled


def test_multi_result_owner_cancellation_preserves_cancel_and_joins_fetch():
    console, services, model, tiles = setup_multi_result()
    page = console.page
    entered = threading.Event()

    def block_second(*args):
        if args[-2] == "second":
            entered.set()
            assert console.release.wait(2)
        return page(*args)

    console.page = block_second

    async def run():
        async def consume():
            return [
                event
                async for event in streaming.events(services, "owner", model, tiles)
            ]

        work = asyncio.create_task(consume())
        try:
            await wait_for_worker(entered)
            work.cancel("owner disconnected")
            with pytest.raises(asyncio.CancelledError) as caught:
                await work
            assert caught.value.args == ("owner disconnected",)
        finally:
            console.release.set()
            if not work.done():
                work.cancel()

    asyncio.run(run())
    assert_results_released_once(console)
    assert console.cancelled


@pytest.mark.parametrize("close_committed", [False, True])
def test_multi_result_cancellation_joins_inflight_close_before_cleanup(close_committed):
    console, services, model, tiles = setup_multi_result()
    close = console.close_result
    entered = threading.Event()

    def block_first_close(*args):
        if args[-1] == "first":
            if close_committed:
                close(*args)
            entered.set()
            assert console.release.wait(2)
            if not close_committed:
                close(*args)
        else:
            close(*args)

    console.close_result = block_first_close

    async def run():
        async def consume():
            return [
                event
                async for event in streaming.events(services, "owner", model, tiles)
            ]

        work = asyncio.create_task(consume())
        try:
            await wait_for_worker(entered)
            work.cancel("owner disconnected during close")
            with pytest.raises(asyncio.CancelledError) as caught:
                await work
            assert caught.value.args == ("owner disconnected during close",)
        finally:
            console.release.set()
            if not work.done():
                work.cancel()

    asyncio.run(run())
    assert_results_released_once(console)
    assert console.cancelled


@pytest.mark.parametrize("operation", ["cancel", "close"])
def test_multi_result_cleanup_propagates_unknown_failures(operation):
    console, services, model, tiles = setup_multi_result()
    failure = RuntimeError("private cleanup failure")
    if operation == "cancel":
        cancel = console.cancel

        def fail_cancel(*args):
            cancel(*args)
            raise failure

        console.cancel = fail_cancel
    else:
        close = console.close_result

        def fail_close(*args):
            if args[-1] == "second":
                console.close_attempts.append("second")
                raise failure
            close(*args)

        console.close_result = fail_close

    async def run():
        source = streaming.events(services, "owner", model, tiles)
        async for event in source:
            if event["type"] == "complete":
                break
        with pytest.raises(RuntimeError) as caught:
            await source.aclose()
        assert caught.value is failure

    asyncio.run(run())
    assert console.close_attempts == ["first", "second"]
    assert console.closed == (
        ["first", "second"] if operation == "cancel" else ["first"]
    )
    assert console.cancelled
