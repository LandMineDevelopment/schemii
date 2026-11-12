"""Bounded, request-owned report streams over the admitted read runtime."""
import asyncio
import anyio
import csv
from datetime import datetime, timezone
import io
import json
import os
import secrets

from fastapi.responses import StreamingResponse
from schemii.common.api.errors import ApiProblem
from schemii.schemoo.service import document


def _limit(name, default):
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


LIMITS = {"rows": _limit("SCHEMER_PREVIEW_ROWS", 10000),
          "bytes": _limit("SCHEMER_PREVIEW_BYTES", 8 * 1024 * 1024),
          "totalRows": _limit("SCHEMER_DASHBOARD_PREVIEW_ROWS", 50000),
          "totalBytes": _limit("SCHEMER_DASHBOARD_PREVIEW_BYTES", 32 * 1024 * 1024)}
STREAM_SECONDS = _limit("SCHEMER_STREAM_SECONDS", 120)
EXPORT_SECONDS = _limit("SCHEMER_EXPORT_SECONDS", 900)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()


async def reserve(services, owner, model, tiles):
    console = services.console
    if console is None:
        raise ApiProblem(503, "console_unavailable", "Report execution is unavailable.")
    task = asyncio.create_task(asyncio.to_thread(console.reserve_read_target, owner,
        connection_id=model.connection_id, database=model.database, namespace=model.namespace,
        console_id=f"con_{secrets.token_hex(16)}", statements=[tile["plan"]["sql"] for tile in tiles],
        protect_result=True))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            try:
                receipt = await asyncio.shield(task)
            except Exception:
                pass
            else:
                await asyncio.to_thread(console.cancel, owner, None, receipt.id)
        raise


async def _cleanup(console, owner, receipt, pending, closed):
    # Interrupt any database call before joining its worker. Never close a cursor
    # concurrently with a worker that is still fetching from it.
    cancel_error = None
    try:
        await asyncio.to_thread(console.cancel, owner, None, receipt.id)
    except Exception as error:
        cancel_error = error
    if pending is not None:
        try:
            await asyncio.shield(pending)
        except Exception:
            pass
    execution = await asyncio.to_thread(console.get_owned, owner, receipt.id)
    first_error = cancel_error
    for result in execution.results:
        if result.id in closed:
            continue
        try:
            await asyncio.to_thread(console.close_result, owner, None, receipt.id, result.id)
            closed.add(result.id)
        except Exception as error:
            first_error = first_error or error
    if first_error:
        raise first_error


class _PreviewShares:
    """Protected unconsumed capacity; consumed rows remain charged until end."""

    def __init__(self, states):
        self.states = states

    def remaining(self, used, limit):
        return max(0, LIMITS[limit] - sum(state[used] for state in self.states))

    def distribute(self):
        for used, local, total, share in [("count", "rows", "totalRows", "rowShare"),
                                          ("bytes", "bytes", "totalBytes", "byteShare")]:
            active = [state for state in self.states if not state["done"] and state[used] < LIMITS[local]]
            remaining = self.remaining(used, total)
            for state in self.states:
                state[share] = 0
            while active and remaining:
                portion, extra = divmod(remaining, len(active))
                distributed = 0
                for index, state in enumerate(active):
                    amount = min(portion + (index < extra), LIMITS[local] - state[used] - state[share])
                    state[share] += amount
                    distributed += amount
                remaining -= distributed
                active = [state for state in active if state[used] + state[share] < LIMITS[local]]

    def fits(self, state):
        if state["offset"] == len(state["rows"]):
            return False
        size = len(encode(state["rows"][state["offset"]]))
        return (self.remaining("count", "totalRows") > 0
                and size <= self.remaining("bytes", "totalBytes"))

    def lend(self, chosen):
        size = len(encode(chosen["rows"][chosen["offset"]]))
        for share, needed in [("rowShare", 1), ("byteShare", size)]:
            for donor in reversed(self.states):
                if donor is chosen or donor["done"]:
                    continue
                amount = min(max(0, needed - chosen[share]), donor[share])
                donor[share] -= amount
                chosen[share] += amount


async def events(services, owner, model, tiles, errors=(), *, export=False):
    """Every yielded batch is bounded; no source lease survives the stream."""
    snapshot_at = datetime.now(timezone.utc).isoformat()
    yield {"type": "start", "snapshotAt": snapshot_at, "tiles": tiles,
           "tileErrors": list(errors), "limits": LIMITS}
    if not tiles:
        yield {"type": "end"}
        return
    console, receipt, pending = services.console, None, None
    tile_id = None
    closed = set()
    failure = None
    try:
        receipt = await reserve(services, owner, model, tiles)
        yield {"type": "execution", "executionId": receipt.id}
        pending = asyncio.create_task(asyncio.to_thread(console.run, owner, receipt.id))
        access = getattr(services, "report_access", None)
        while access is not None and not pending.done():
            await asyncio.wait({pending}, timeout=0.5)
            await asyncio.to_thread(access.check)
        await asyncio.shield(pending)
        pending = None
        execution = await asyncio.to_thread(console.get_owned, owner, receipt.id)
        if execution.status != "succeeded":
            raise ApiProblem(422, execution.error_code or "report_query_failed",
                             "The report query did not complete. Refresh to try again.")
        if len(execution.results) != len(tiles):
            raise ApiProblem(502, "report_result_missing", "The report did not return all result sets.")
        states = [{"tile": tile, "result": result, "cursor": None, "rows": [],
                   "columns": [], "offset": 0, "count": 0, "bytes": 0,
                   "fetched": False, "done": False, "rowShare": 0, "byteShare": 0}
                  for tile, result in zip(tiles, execution.results)]
        order = sorted(states, key=lambda state: not state["tile"].get("scalarKpi", False))
        shares = _PreviewShares(states)
        if not export:
            shares.distribute()
        arbitration = 0
        while any(not state["done"] for state in states):
            progressed = False
            for state in order:
                if state["done"]:
                    continue
                tile = state["tile"]
                tile_id = tile["tileId"]
                if access is not None:
                    await asyncio.to_thread(access.check)
                if state["offset"] == len(state["rows"]):
                    if state["fetched"] and state["cursor"] is None:
                        continue
                    options = {"page_size": 1} if tile.get("scalarKpi") and not state["fetched"] else {}
                    pending = asyncio.create_task(asyncio.to_thread(console.page, owner, None,
                        receipt.id, state["result"].id, state["cursor"], **options))
                    while access is not None and not pending.done():
                        await asyncio.wait({pending}, timeout=0.5)
                        await asyncio.to_thread(access.check)
                    page = await asyncio.shield(pending)
                    pending = None
                    if access is not None:
                        await asyncio.to_thread(access.check)
                    columns = [document(column) for column in page.columns]
                    labels = tile["plan"].get("outputLabels", [])
                    state["columns"] = [{**column, "name": labels[i] if i < len(labels) else column["name"]}
                                        for i, column in enumerate(columns)]
                    if page.truncated:
                        raise ApiProblem(422, "report_result_truncated", "The database stopped this result at a resource limit.")
                    state.update(rows=page.rows, offset=0, cursor=page.next_cursor, fetched=True)
                    progressed = True
                accepted, reason = [], None
                while state["offset"] < len(state["rows"]):
                    row = state["rows"][state["offset"]]
                    size = len(encode(row))
                    if not export:
                        if state["count"] >= LIMITS["rows"]:
                            reason = "row_limit"
                        elif state["bytes"] + size > LIMITS["bytes"]:
                            reason = "byte_limit"
                        elif state["rowShare"] < 1 or state["byteShare"] < size:
                            break  # Keep this page suffix before advancing its one-use cursor.
                        if reason:
                            break
                    accepted.append(row)
                    state["offset"] += 1
                    state["count"] += 1
                    state["bytes"] += size
                    if not export:
                        state["rowShare"] -= 1
                        state["byteShare"] -= size
                    progressed = True
                if accepted or state["count"] == 0:
                    yield {"type": "rows", "tileId": tile_id,
                           "columns": state["columns"], "rows": accepted}
                exhausted = (state["offset"] == len(state["rows"])
                             and (state["cursor"] is None or tile.get("scalarKpi", False)))
                if reason or exhausted:
                    await asyncio.to_thread(console.close_result, owner, None, receipt.id, state["result"].id)
                    closed.add(state["result"].id)
                    state["done"] = True
                    state["rows"] = []
                    if len(closed) == len(states):
                        receipt = None
                    yield {"type": "complete", "tileId": tile_id, "rowCount": state["count"],
                           "limitReached": reason is not None, "reason": reason, "snapshotAt": snapshot_at}
                    if not export:
                        shares.distribute()
                    progressed = True
            if not progressed:
                # Every live result has had a first fetch. Resolve byte/row share
                # fragmentation without dropping a pending head or spinning.
                active = [state for state in states if not state["done"]]
                candidates = active[arbitration:] + active[:arbitration]
                chosen = next((state for state in candidates if shares.fits(state)), None)
                if chosen is not None:
                    shares.lend(chosen)
                    arbitration = (active.index(chosen) + 1) % len(active)
                    continue
                for state in active:
                    tile_id = state["tile"]["tileId"]
                    reason = "dashboard_row_limit" if shares.remaining("count", "totalRows") == 0 else "dashboard_byte_limit"
                    await asyncio.to_thread(console.close_result, owner, None, receipt.id, state["result"].id)
                    closed.add(state["result"].id)
                    state["done"] = True
                    state["rows"] = []
                    if len(closed) == len(states):
                        receipt = None
                    yield {"type": "complete", "tileId": tile_id, "rowCount": state["count"],
                           "limitReached": True, "reason": reason, "snapshotAt": snapshot_at}
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Database diagnostic strings can contain source values. Only explicit
        # report errors are safe to send to browser clients.
        failure = {"type": "error", "tileId": tile_id,
               "code": error.code if isinstance(error, ApiProblem) else "report_stream_failed",
               "message": error.message if isinstance(error, ApiProblem) else
                   "The report stream stopped. Refresh to try again."}
    finally:
        if receipt is not None:
            with anyio.CancelScope(shield=True):
                cleanup = asyncio.create_task(_cleanup(console, owner, receipt, pending, closed))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await asyncio.shield(cleanup)
                    raise
    if failure is not None:
        yield failure
    yield {"type": "end"}


class ReportStreamingResponse(StreamingResponse):
    """Own the iterator and its deadline in the same task that sends bytes.

    Starlette does not close body iterators when ASGI send fails. Closing here
    also covers disconnect-task cancellation on older ASGI implementations.
    """

    def __init__(self, *args, lifetime_seconds, **kwargs):
        super().__init__(*args, **kwargs)
        self.lifetime_seconds = lifetime_seconds

    async def stream_response(self, send):
        try:
            with anyio.fail_after(self.lifetime_seconds):
                await super().stream_response(send)
        finally:
            # A suspended generator may own a database cursor even though no
            # database call is currently running. Never rely on generator GC.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


def response(services, owner, model, tiles, errors=(), *, export=False):
    async def body():
        source = events(services, owner, model, tiles, errors, export=export)
        header = False
        try:
            async for event in source:
                if not export:
                    yield encode(event)
                elif event["type"] == "error":
                    # Fail the HTTP stream rather than producing a seemingly
                    # successful but incomplete CSV file.
                    raise RuntimeError(event["message"])
                elif event["type"] == "rows":
                    buffer = io.StringIO(newline="")
                    writer = csv.writer(buffer)
                    if not header:
                        writer.writerow([column["name"] for column in event["columns"]])
                        header = True
                    for row in event["rows"]:
                        writer.writerow([_csv_value(value) for value in row])
                    yield buffer.getvalue().encode("utf-8")
        finally:
            await source.aclose()
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    if export:
        headers["Content-Disposition"] = 'attachment; filename="schemer-full-results.csv"'
        headers["X-Schemer-Snapshot"] = "fresh"
    return ReportStreamingResponse(body(), lifetime_seconds=EXPORT_SECONDS if export else STREAM_SECONDS, media_type="text/csv" if export else "application/x-ndjson", headers=headers)


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    # Prevent spreadsheet formula execution for textual source values.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
