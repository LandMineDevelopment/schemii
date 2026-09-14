"""Short-lived, bounded reports on the shared admitted read runtime.

Rows exist only during the request. The shared cursor is an execution detail:
all results are released before returning, including on failure or disconnect.
"""

import asyncio
import secrets
import threading
import time

from schemii.common.api.errors import ApiProblem
from schemii.schemoo.service import document, load_model, model_catalog, plan_query


def report_plan(services, owner, body, *, fresh=False):
    model = load_model(services, owner, body.model_id, body.expected_revision)
    if body.explore.root and body.explore.root != model.definition.root:
        raise ApiProblem(422, "report_root_changed", "Reports must use the model's starting table.")
    plan = plan_query(model_catalog(services, owner, model, fresh=fresh),
                      model.definition, body.explore, model.catalog_fingerprint)
    return model, plan


def _materialize(console, owner, receipt, plan, limit, stopped):
    started = time.monotonic()
    try:
        if stopped.is_set():
            console.cancel(owner, None, receipt.id)
        console.run(owner, receipt.id)
        execution = console.get_owned(owner, receipt.id)
        if execution.status != "succeeded":
            raise ApiProblem(409 if execution.status == "cancelled" else 422,
                             execution.error_code or "report_query_failed",
                             execution.error_message or "The report query did not complete.")
        if len(execution.results) != 1:
            raise ApiProblem(502, "report_result_missing", "The report did not return one result set.")
        result = execution.results[0]
        rows, columns, cursor = [], [], None
        truncated = False
        while len(rows) < limit:
            if stopped.is_set():
                raise ApiProblem(499, "report_cancelled", "The report request was cancelled.")
            page = console.page(owner, None, receipt.id, result.id, cursor)
            columns = [document(column) for column in page.columns]
            rows.extend(page.rows[:limit - len(rows)])
            truncated = truncated or page.truncated
            cursor = page.next_cursor
            if cursor is None:
                break
        warnings = list(plan.get("warnings", []))
        if truncated:
            warnings.append("The database result reached an execution resource limit.")
        return {"plan": plan, "columns": columns, "rows": rows,
                "elapsedMs": round((time.monotonic() - started) * 1000, 3),
                "rowLimit": limit, "limitReached": len(rows) >= limit,
                "warnings": warnings}
    finally:
        # Fetch final state because even failed runs may have produced results.
        execution = console.get_owned(owner, receipt.id)
        first_error = None
        for result in execution.results:
            try:
                console.close_result(owner, None, receipt.id, result.id)
            except Exception as error:
                first_error = first_error or error
        if first_error:
            raise first_error


async def query_report(services, owner, body, request):
    started = time.monotonic()
    model, plan = await asyncio.to_thread(report_plan, services, owner, body, fresh=True)
    return await execute_prepared(services, owner, model, plan, body.explore.limit, request, started=started)


async def execute_prepared(services, owner, model, plan, row_limit, request, *, started=None):
    """Execute trusted, server-compiled SQL and release its result before returning."""
    started = time.monotonic() if started is None else started
    console = services.console
    if console is None:
        raise ApiProblem(503, "console_unavailable", "Report execution is unavailable.")
    reservation = asyncio.create_task(asyncio.to_thread(
        console.reserve_read_target, owner, connection_id=model.connection_id,
        database=model.database, namespace=model.namespace,
        console_id=f"con_{secrets.token_hex(16)}", statements=[plan["sql"]]))
    try:
        receipt = await asyncio.shield(reservation)
    except asyncio.CancelledError:
        # The thread may already have admitted a lease. Preserve its receipt so
        # cancellation cannot leave a reserved execution consuming capacity.
        try:
            receipt = await asyncio.shield(reservation)
        except Exception:
            pass  # Reservation failed before returning an admitted execution.
        else:
            await asyncio.to_thread(console.cancel, owner, None, receipt.id)
        raise
    stopped = threading.Event()
    work = asyncio.create_task(asyncio.to_thread(
        _materialize, console, owner, receipt, plan, row_limit, stopped))
    try:
        while not work.done():
            if await request.is_disconnected():
                stopped.set()
                await asyncio.to_thread(console.cancel, owner, None, receipt.id)
                break
            await asyncio.wait({work}, timeout=0.1)
        response = await asyncio.shield(work)
        response["elapsedMs"] = round((time.monotonic() - started) * 1000, 3)
        return response
    except asyncio.CancelledError:
        stopped.set()
        try:
            await asyncio.to_thread(console.cancel, owner, None, receipt.id)
        finally:
            # Cancelling the HTTP task must not orphan the worker's result lease.
            try:
                await asyncio.shield(work)
            except Exception:
                pass
        raise
