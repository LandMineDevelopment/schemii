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


async def _cleanup(console, owner, receipt, pending):
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
        try:
            await asyncio.to_thread(console.close_result, owner, None, receipt.id, result.id)
        except Exception as error:
            first_error = first_error or error
    if first_error:
        raise first_error


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
    total_bytes = 0
    total_rows = 0
    failure = None
    try:
        receipt = await reserve(services, owner, model, tiles)
        yield {"type": "execution", "executionId": receipt.id}
        pending = asyncio.create_task(asyncio.to_thread(console.run, owner, receipt.id))
        await asyncio.shield(pending)
        pending = None
        execution = await asyncio.to_thread(console.get_owned, owner, receipt.id)
        if execution.status != "succeeded":
            raise ApiProblem(422, execution.error_code or "report_query_failed",
                             "The report query did not complete. Refresh to try again.")
        if len(execution.results) != len(tiles):
            raise ApiProblem(502, "report_result_missing", "The report did not return all result sets.")
        for index, (tile, result) in enumerate(zip(tiles, execution.results)):
            tile_id, cursor, count, byte_count, reason = tile["tileId"], None, 0, 0, None
            while True:
                pending = asyncio.create_task(asyncio.to_thread(console.page, owner, None,
                    receipt.id, result.id, cursor))
                page = await asyncio.shield(pending)
                pending = None
                columns = [document(column) for column in page.columns]
                labels = tile["plan"].get("outputLabels", [])
                columns = [{**column, "name": labels[i] if i < len(labels) else column["name"]}
                           for i, column in enumerate(columns)]
                accepted = []
                for row in page.rows:
                    size = len(encode(row))
                    if not export:
                        if count >= LIMITS["rows"]:
                            reason = "row_limit"
                        elif total_rows >= LIMITS["totalRows"]:
                            reason = "dashboard_row_limit"
                        elif byte_count + size > LIMITS["bytes"]:
                            reason = "byte_limit"
                        elif total_bytes + size > LIMITS["totalBytes"]:
                            reason = "dashboard_byte_limit"
                    if reason:
                        break
                    accepted.append(row)
                    count += 1
                    total_rows += 1
                    byte_count += size
                    total_bytes += size
                if accepted or count == 0:
                    yield {"type": "rows", "tileId": tile_id, "columns": columns, "rows": accepted}
                if page.truncated:
                    raise ApiProblem(422, "report_result_truncated", "The database stopped this result at a resource limit.")
                cursor = page.next_cursor
                if reason or cursor is None:
                    break
            await asyncio.to_thread(console.close_result, owner, None, receipt.id, result.id)
            if index == len(tiles) - 1:
                receipt = None  # All source results are closed before final completion.
            yield {"type": "complete", "tileId": tile_id, "rowCount": count,
                   "limitReached": reason is not None, "reason": reason, "snapshotAt": snapshot_at}
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
                cleanup = asyncio.create_task(_cleanup(console, owner, receipt, pending))
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
