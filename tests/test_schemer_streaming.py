import asyncio
from datetime import datetime, timezone
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


def test_request_start_time_precedes_admission_and_survives_completion(monkeypatch):
    started = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(streaming, "datetime", NS(now=lambda zone: started))
    console, services, model = setup([[["changed-after-request-start"]]])
    reservations = []
    original_reserve = console.reserve_read_target

    def reserve(*args, **kwargs):
        reservations.append(True)
        return original_reserve(*args, **kwargs)

    console.reserve_read_target = reserve

    async def run():
        source = streaming.events(services, "owner", model,
            [{"tileId": "tile", "plan": {"sql": "select 1"}}])
        first = await anext(source)
        assert first["type"] == "start"
        assert first["snapshotAt"] == started.isoformat()
        assert reservations == []  # This is a request time, not DB snapshot acquisition.
        return [first, *[event async for event in source]]

    events = asyncio.run(run())
    assert next(event for event in events if event["type"] == "complete")["snapshotAt"] == started.isoformat()
    assert next(event for event in events if event["type"] == "rows")["rows"] == [["changed-after-request-start"]]
    assert reservations == [True]
    assert console.closed == ["result"]


def test_fresh_drill_and_full_export_read_changed_source_per_request(monkeypatch):
    times = iter(datetime(2026, 10, 13, 12, minute, tzinfo=timezone.utc) for minute in range(3))
    monkeypatch.setattr(streaming, "datetime", NS(now=lambda zone: next(times)))
    console, services, model = setup([])
    source_rows = [["original"]]
    reservations = []

    def reserve(*args, **kwargs):
        reservations.append(kwargs["console_id"])
        console.pages = [[row[:] for row in source_rows]]
        return console.execution

    console.reserve_read_target = reserve
    report = collect(services, model)
    source_rows.append(["arrived-after-report"])
    drill = collect(services, model)
    assert next(event for event in report if event["type"] == "rows")["rows"] == [["original"]]
    assert next(event for event in drill if event["type"] == "rows")["rows"] == source_rows
    assert report[0]["snapshotAt"] == "2026-10-13T12:00:00+00:00"
    assert drill[0]["snapshotAt"] == "2026-10-13T12:01:00+00:00"

    source_rows.append(["arrived-before-full-export"])
    response = streaming.response(services, "owner", model,
        [{"tileId": "tile", "plan": {"sql": "select 1"}}], export=True)

    async def download():
        return b"".join([chunk async for chunk in response.body_iterator])

    exported = asyncio.run(download()).decode()
    assert "arrived-before-full-export" in exported
    assert "arrived-before-full-export" not in str(drill)
    assert response.headers["X-Schemer-Snapshot"] == "fresh"
    assert len(set(reservations)) == 3
    assert console.closed == ["result", "result", "result"]


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


def test_cancellation_during_result_close_joins_its_worker_and_never_closes_twice():
    import threading
    console, services, model = setup([[['row']]])
    entered, cancelled, release, exited = [threading.Event() for _ in range(4)]

    def close(*args):
        assert args[-1] not in console.closed, "result retirement is not idempotent"
        console.closed.append(args[-1])
        entered.set()
        assert release.wait(3)
        exited.set()

    def cancel(*args):
        console.cancelled = True
        cancelled.set()

    console.close_result, console.cancel = close, cancel

    async def consume():
        async for _ in streaming.events(services, "owner", model,
                [{"tileId": "tile", "plan": {"sql": "select 1"}}]):
            pass

    async def run():
        task = asyncio.create_task(consume())
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        assert await asyncio.to_thread(cancelled.wait, 2)
        assert not task.done(), "consumer must join the close worker before finishing"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()

    try:
        asyncio.run(run())
    finally:
        release.set()
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


class MultiConsole(Console):
    def __init__(self, pages):
        super().__init__([])
        self.by_result = {key: list(value) for key, value in pages.items()}
        self.execution.results = [NS(id=key) for key in pages]
        self.fetches = []
        self.reservations = []
        self.runs = 0

    def reserve_read_target(self, *args, **kwargs):
        self.reservations.append(kwargs)
        return self.execution

    def run(self, *args):
        self.runs += 1

    def page(self, owner, workspace, execution, result, cursor, **kwargs):
        self.fetches.append((result, cursor, kwargs))
        pages = self.by_result[result]
        rows = pages.pop(0)
        return NS(rows=rows, columns=[ConsoleResultColumn(name="value", data_type="text")],
                  next_cursor=f"next-{len(pages)}" if pages else None, truncated=False)

    def close_result(self, *args):
        assert args[-1] not in self.closed, "already closed result must never be retired twice"
        super().close_result(*args)


def multi_collect(console, *, priority=()):
    services = NS(console=console)
    model = NS(connection_id="c", database="d", namespace="public")
    tiles = [{"tileId": key, "plan": {"sql": f"select '{key}'"}, "scalarKpi": key in priority}
             for key in console.by_result]

    async def run():
        return [event async for event in streaming.events(services, "owner", model, tiles)]
    return asyncio.run(run())


def test_pages_interleave_without_changing_one_execution_or_statement_mapping():
    console = MultiConsole({"detail": [[[1]], [[2]]], "other": [[[9]], [[10]]]})
    events = multi_collect(console)
    assert [(item["tileId"], item["rows"]) for item in events if item["type"] == "rows"] == [
        ("detail", [[1]]), ("other", [[9]]), ("detail", [[2]]), ("other", [[10]])]
    assert console.runs == len(console.reservations) == 1
    assert console.reservations[0]["statements"] == ["select 'detail'", "select 'other'"]
    assert console.closed == ["detail", "other"]


def test_scalar_kpi_first_fetch_precedes_detail_and_uses_one_row_batch():
    console = MultiConsole({"detail": [[[1]], [[2]]], "kpi": [[[9]], []]})
    events = multi_collect(console, priority={"kpi"})
    assert [item["tileId"] for item in events[0]["tiles"]] == ["detail", "kpi"]
    assert console.fetches[0] == ("kpi", None, {"page_size": 1})
    assert [item["tileId"] for item in events if item["type"] == "rows"] == ["kpi", "detail", "detail"]
    assert console.closed == ["kpi", "detail"]


def test_dashboard_row_shares_protect_later_tiles_and_keep_pending_page_suffix(monkeypatch):
    monkeypatch.setitem(streaming.LIMITS, "totalRows", 5)
    console = MultiConsole({"first": [[[1], [2], [3], [4]]], "later": [[[10], [11], [12], [13]]]})
    events = multi_collect(console)
    rows = {key: [row for event in events if event["type"] == "rows" and event["tileId"] == key
                  for row in event["rows"]] for key in console.by_result}
    assert rows == {"first": [[1], [2], [3]], "later": [[10], [11]]}
    assert len(console.fetches) == 2
    assert {item["reason"] for item in events if item["type"] == "complete"} == {"dashboard_row_limit"}


def test_empty_tile_returns_row_and_byte_share_to_unfinished_tile(monkeypatch):
    monkeypatch.setitem(streaming.LIMITS, "totalRows", 4)
    monkeypatch.setitem(streaming.LIMITS, "totalBytes", 32)
    console = MultiConsole({"first": [[[1], [2], [3]], [[4]]], "empty": [[]]})
    events = multi_collect(console)
    assert [row for item in events if item["type"] == "rows" for row in item["rows"]] == [[1], [2], [3], [4]]
    assert next(item for item in events if item["type"] == "complete" and item["tileId"] == "first")["rowCount"] == 4
    assert all(not item["limitReached"] for item in events if item["type"] == "complete")


def test_fragmented_byte_shares_lend_one_head_without_skipping_or_looping(monkeypatch):
    row = ["12345"]
    size = len(streaming.encode(row))
    monkeypatch.setitem(streaming.LIMITS, "totalBytes", size * 2 - 1)
    console = MultiConsole({"first": [[row, ["later"]]], "second": [[row]]})
    events = multi_collect(console)
    assert [row for item in events if item["type"] == "rows" for row in item["rows"]] == [row]
    assert len(console.fetches) == 2
    assert [item["reason"] for item in events if item["type"] == "complete"] == ["dashboard_byte_limit"] * 2


def test_budget_smaller_than_participant_count_has_stable_original_order_remainder(monkeypatch):
    monkeypatch.setitem(streaming.LIMITS, "totalRows", 1)
    console = MultiConsole({key: [[[key]]] for key in ["first", "second", "third"]})
    events = multi_collect(console, priority={"third"})
    assert [row for item in events if item["type"] == "rows" for row in item["rows"]] == [["first"]]
    assert len(console.fetches) == 3  # Every participant receives its initial opportunity.
    assert set(console.closed) == set(console.by_result)


def test_failure_after_earlier_result_completion_closes_only_outstanding_and_delivers_error():
    console = MultiConsole({"first": [[[1]]], "second": [[[2]]]})
    page = console.page

    def fail(*args, **kwargs):
        if args[3] == "second":
            raise RuntimeError("private source value")
        return page(*args, **kwargs)

    console.page = fail
    events = multi_collect(console)
    assert console.closed == ["first", "second"]
    assert console.cancelled
    error = next(item for item in events if item["type"] == "error")
    assert error["message"] == "The report stream stopped. Refresh to try again."
    assert events[-1] == {"type": "end"}


def test_cancel_after_completed_peer_joins_fetch_before_closing_remaining_result():
    import threading
    console = MultiConsole({"first": [[[1]]], "second": [[[2]]]})
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    page, close = console.page, console.close_result

    def blocked(*args, **kwargs):
        if args[3] == "second":
            entered.set()
            assert release.wait(2)
            exited.set()
        return page(*args, **kwargs)

    def cancel(*args):
        console.cancelled = True
        release.set()

    def close_after_join(*args):
        if args[-1] == "second":
            assert exited.is_set()
        close(*args)

    console.page, console.cancel, console.close_result = blocked, cancel, close_after_join
    model = NS(connection_id="c", database="d", namespace="public")
    tiles = [{"tileId": key, "plan": {"sql": "select 1"}} for key in console.by_result]

    async def run():
        async def consume():
            async for _ in streaming.events(NS(console=console), "owner", model, tiles):
                pass
        task = asyncio.create_task(consume())
        while not entered.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert console.cancelled
    assert console.closed == ["first", "second"]


def test_revocation_blocks_parked_rows_after_a_completed_peer_returns_its_share(monkeypatch):
    from schemii.common.api.errors import ApiProblem
    monkeypatch.setitem(streaming.LIMITS, "totalRows", 2)
    console = MultiConsole({"detail": [[[1], [2]]], "empty": [[]]})
    revoked = False

    def check():
        if revoked:
            raise ApiProblem(403, "report_access_revoked", "Report access was removed.")

    services = NS(console=console, report_access=NS(check=check))
    model = NS(connection_id="c", database="d", namespace="public")
    tiles = [{"tileId": key, "plan": {"sql": "select 1"}} for key in console.by_result]

    async def run():
        nonlocal revoked
        seen = []
        async for event in streaming.events(services, "owner", model, tiles):
            seen.append(event)
            if event["type"] == "complete" and event["tileId"] == "empty":
                revoked = True
        return seen

    events = asyncio.run(run())
    assert [row for event in events if event["type"] == "rows" for row in event["rows"]] == [[1]]
    assert next(event for event in events if event["type"] == "error")["code"] == "report_access_revoked"
    assert console.closed == ["empty", "detail"]


@pytest.mark.parametrize('value', ['-9007199254740993','-12345678901234567890.123456789','-1e-100','+0','0','-0'])
def test_raw_csv_exact_numeric_text_uses_only_numeric_column_metadata(value):
    assert streaming._csv_value(value, 'numeric') == value
    assert streaming._csv_value(value, 'bigint') == value
    for kind in ['text', 'json', 'jsonb', 'numeric[]', 'unknown']:
        assert streaming._csv_value(value, kind) == ("'"+value if value[0] in '+-' else value)


@pytest.mark.parametrize('value', ['-1;=HYPERLINK("x")','-1\n=2',' -1','\t+1','-1e99999999999999999999','-Infinity','+NaN','@SUM(1)','=1+1'])
def test_raw_csv_hostile_or_nonfinite_numeric_lookalikes_remain_escaped(value):
    assert streaming._csv_value(value, 'numeric') == "'" + value
    assert streaming._csv_value(value, 'text') == "'" + value
    assert streaming._csv_value(value, 'jsonb') == "'" + value
