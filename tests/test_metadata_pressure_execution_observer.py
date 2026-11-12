"""Offline fixture observer contracts: actual Console reserve and ASGI task context."""

import asyncio
from copy import deepcopy
import json
import secrets
from threading import Event
from types import SimpleNamespace as NS

import pytest

from dev.pressure.app import ObservedApplication
from dev.pressure.execution_observer import (
    MAXIMUM_OPERATIONS,
    declaration,
    validate_snapshot,
)
from schemii.schemii.console.repository import InMemoryConsoleRepository
from schemii.schemii.console.service import ConsoleService
from schemii.schemer.access import ReportConsole
from schemii.schemer import streaming

ACTOR = "user_owned"
RESOURCE = "dashboard_owned"
RUN = "pressure_" + "a" * 32
PATH = f"/api/v1/schemer/dashboards/{RESOURCE}/executions/stream"
MODEL = NS(connection_id="connection", database="owned", namespace="public")
TILES = [{"tileId": "tile", "plan": {"sql": "SELECT 'PRIVATE_SQL_SENTINEL'"}}]


class ForbiddenSource:
    register_retained_connection_reclaimer = None

    def __getattr__(self, name):
        raise AssertionError("observer must not execute or inspect source")


class Access:
    actor = ACTOR
    resource_id = RESOURCE
    auth = NS(audit=lambda *args: None)
    revoked = False

    def check(self):
        if self.revoked:
            raise PermissionError("PRIVATE_REVOCATION_DETAIL")

    def get(self, actor, identifier):
        assert actor == ACTOR and identifier == "connection"
        return NS(id=identifier, revision=1, database="owned", owner_id=ACTOR)


def fixture(tmp_path, request=None):
    tmp_path.chmod(0o700)
    access = Access()
    base = ConsoleService(
        repository=InMemoryConsoleRepository(),
        connection_access=access,
        postgres=ForbiddenSource(),
        workspaces=ForbiddenSource(),
    )
    services = NS(console=ReportConsole(base, access))

    class Application:
        state = NS(services=NS(console=base))

        async def __call__(self, scope, receive, send):
            if request:
                await request(services, scope, send)
            else:
                # A separate ASGI task plus to_thread must preserve context.
                await asyncio.create_task(
                    streaming.reserve(services, ACTOR, MODEL, TILES)
                )
                await send(
                    {"type": "http.response.start", "status": 200, "headers": []}
                )
                await send(
                    {"type": "http.response.body", "body": b"PRIVATE_ROWS_SENTINEL"}
                )

    wrapper = ObservedApplication(Application(), RUN, tmp_path)
    observer = wrapper.executions
    observer.commands = tmp_path / "commands.json"
    observer.install(base)
    return wrapper, observer, base, access


def declare(observer, operations=("1" * 32,), acknowledge=()):
    items = [
        {
            "operation": key,
            "actor": ACTOR,
            "resource": RESOURCE,
            "method": "POST",
            "path": PATH,
            "kind": "dashboard-stream",
        }
        for key in operations
    ]
    observer.commands.write_text(
        json.dumps(
            {
                "version": 1,
                "run": RUN,
                "process": observer.process,
                "command": secrets.token_hex(16),
                "operations": items,
                "acknowledge": list(acknowledge),
            }
        )
    )
    observer.commands.chmod(0o640)
    return items


async def invoke(wrapper, operation="1" * 32, send=None, **changes):
    async def receive():
        return {"type": "http.disconnect"}

    async def discard(message):
        pass

    return await wrapper(
        {
            "type": "http",
            "method": "POST",
            "path": PATH,
            "headers": [(b"x-schemii-fixture-operation", operation.encode())],
            **changes,
        },
        receive,
        send or discard,
    )


def test_short_request_captures_actual_durable_id_before_sampling_without_rows_or_source(
    tmp_path,
):
    wrapper, observer, base, _ = fixture(tmp_path)
    declare(observer)
    asyncio.run(invoke(wrapper))
    receipt = observer.operations["1" * 32]["receipts"][0]
    actual = base.get_owned(ACTOR, receipt["id"])
    assert actual.status == "reserved" and base.is_shared_report_execution(actual.id)
    observer.sample()
    snapshot = json.loads((tmp_path / "execution-observation.json").read_text())
    assert snapshot["operations"][0]["request"] == "complete"
    assert snapshot["operations"][0]["receipts"][0]["status"] == "reserved"
    validate_snapshot(snapshot, RUN, observer.process)
    raw = json.dumps(snapshot)
    assert "PRIVATE_" not in raw and "statements" not in raw and "results" not in raw
    assert snapshot["instrumentation"]["metadataReads"] == 1
    assert snapshot["instrumentation"]["stockCapacityEligible"] is False


def test_simultaneous_same_actor_operations_have_distinct_worker_contexts(tmp_path):
    wrapper, observer, _, _ = fixture(tmp_path)
    declare(observer, ("1" * 32, "2" * 32))

    async def run():
        await asyncio.gather(invoke(wrapper), invoke(wrapper, "2" * 32))

    asyncio.run(run())
    receipts = [entry["receipts"][0]["id"] for entry in observer.operations.values()]
    assert len(set(receipts)) == 2 and observer.losses == 0


def test_async_cancel_captures_worker_success_before_native_cancel_and_preserves_original_return(
    tmp_path,
):
    entered, release = Event(), Event()
    wrapper, observer, base, _ = fixture(tmp_path)
    # Reinstall around the actual bound implementation, with a controlled
    # metadata-reserve delay. No asynchronous wrapper can supply this proof.
    original = observer.console.reserve_read_target
    returned = []

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        result = original(*args, **kwargs)
        returned.append(result)
        return result

    base.reserve_read_target = slow
    cancelled = []
    base.cancel = lambda owner, workspace, identifier: cancelled.append(identifier)
    declare(observer)

    async def run():
        task = asyncio.create_task(invoke(wrapper))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    entry = observer.operations["1" * 32]
    assert entry["receipts"][0]["id"] == returned[0].id == cancelled[0]
    assert entry["request"] == "failed" and len(returned) == 1


@pytest.mark.parametrize("reason", ["reserve", "send", "revocation"])
def test_original_errors_survive_and_request_failure_is_separate(tmp_path, reason):
    wrapper, observer, base, access = fixture(tmp_path)
    error = RuntimeError("PRIVATE_ERROR_SENTINEL")
    if reason == "reserve":
        base._repository.reserve = lambda *args, **kwargs: (_ for _ in ()).throw(error)
    if reason == "revocation":
        access.revoked = True
    declare(observer)

    async def send(message):
        raise error

    with pytest.raises(
        PermissionError if reason == "revocation" else RuntimeError
    ) as caught:
        asyncio.run(invoke(wrapper, send=send if reason == "send" else None))
    if reason != "revocation":
        assert caught.value is error
    entry = observer.operations["1" * 32]
    assert entry["request"] == "failed"
    assert bool(entry["receipts"]) is (reason == "send")
    assert "PRIVATE_" not in json.dumps(observer.snapshot())


@pytest.mark.parametrize("mismatch", ["owner", "resource", "nonce", "path", "restart"])
def test_unmatched_context_or_actual_authority_never_captures_foreign_receipt(
    tmp_path, mismatch
):
    wrapper, observer, base, access = fixture(tmp_path)
    declare(observer)
    if mismatch == "owner":
        access.actor = "other"
    if mismatch == "resource":
        access.resource_id = "other"
    if mismatch == "restart":
        observer.process = {**observer.process, "incarnation": "f" * 32}
    asyncio.run(
        invoke(
            wrapper,
            "3" * 32 if mismatch == "nonce" else "1" * 32,
            **({"path": PATH + "/wrong"} if mismatch == "path" else {}),
        )
    )
    assert observer.losses > 0
    assert not any(entry["receipts"] for entry in observer.operations.values())


def test_budget_and_unreadable_status_are_unknown_not_evicted_or_zero(tmp_path):
    wrapper, observer, base, _ = fixture(tmp_path)
    operations = tuple(f"{i:032x}" for i in range(MAXIMUM_OPERATIONS))
    declare(observer, operations)
    observer.reload()
    declare(observer, ("f" * 32,))
    observer.reload()
    assert len(observer.operations) == MAXIMUM_OPERATIONS and observer.losses == 1
    # An existing explicit context remains capturable; a pruned receipt read is
    # unknown, with its exact ID retained for private cleanup.
    declare(observer, operations)
    asyncio.run(invoke(wrapper, operations[0]))
    base.get_owned = lambda *args: (_ for _ in ()).throw(
        RuntimeError("PRIVATE_DB_ERROR")
    )
    observer.sample()
    entry = observer.operations[operations[0]]
    assert entry["receipts"][0]["status"] == "unknown"
    assert len(observer.operations) == MAXIMUM_OPERATIONS


@pytest.mark.parametrize("keyword_owner", [False, True])
def test_post_reserve_observer_write_failure_never_replaces_identical_receipt(
    tmp_path, keyword_owner
):
    wrapper, observer, base, _ = fixture(tmp_path)
    declare(observer)
    observer.reload()
    operation, token = observer.begin(
        {
            "method": "POST",
            "path": PATH,
            "headers": [(b"x-schemii-fixture-operation", b"1" * 32)],
        }
    )
    original_reserve = base._reserve_read
    returned = []

    def reserve(*args, **kwargs):
        result = original_reserve(*args, **kwargs)
        returned.append(result)
        return result

    base._reserve_read = reserve
    observer.write = lambda: (_ for _ in ()).throw(OSError("PRIVATE_DISK_ERROR"))
    result = base.reserve_read_target(
        *([] if keyword_owner else [ACTOR]),
        **({"owner_id": ACTOR} if keyword_owner else {}),
        connection_id="connection",
        database="owned",
        namespace="public",
        console_id="con_" + "c" * 32,
        statements=["SELECT 1"],
        connection_access=Access(),
    )
    assert result is returned[0] and len(returned) == 1 and observer.losses == 1
    observer.finish(operation, token, False)


def test_acknowledgment_is_explicit_and_closed_wire_rejects_data(tmp_path):
    wrapper, observer, base, _ = fixture(tmp_path)
    declare(observer)
    asyncio.run(invoke(wrapper))
    base.cancel(ACTOR, None, observer.operations["1" * 32]["receipts"][0]["id"])
    observer.sample()
    snapshot = observer.snapshot()
    bad = deepcopy(snapshot)
    bad["operations"][0]["receipts"][0]["sql"] = "PRIVATE_SQL"
    with pytest.raises(ValueError, match="receipt_fields"):
        validate_snapshot(bad, RUN, observer.process)
    declare(observer, (), ("1" * 32,))
    observer.reload()
    assert observer.operations == {} and observer.acknowledged == 1
    assert declaration(
        {
            key: snapshot["operations"][0][key]
            for key in ("operation", "actor", "resource", "method", "path", "kind")
        }
    )


def test_ack_does_not_evict_unknown_or_unfinished_work(tmp_path):
    wrapper, observer, _, _ = fixture(tmp_path)
    declare(observer)
    asyncio.run(invoke(wrapper))
    observer.sample()  # Reserved is an actual receipt, not native-release proof.
    declare(observer, (), ("1" * 32,))
    observer.reload()
    assert "1" * 32 in observer.operations and observer.acknowledged == 0
    assert observer.losses == 1


def test_cleanup_worker_order_is_unchanged_by_observer(tmp_path):
    wrapper, observer, base, _ = fixture(tmp_path)
    declare(observer)
    asyncio.run(invoke(wrapper))
    identifier = observer.operations["1" * 32]["receipts"][0]["id"]
    order = []
    base.cancel = lambda *args: order.append("cancel")
    base.get_owned = lambda *args: NS(results=[NS(id="actual-result")])
    base.close_result = lambda *args: order.append("close")

    async def run():
        async def worker():
            while "cancel" not in order:
                await asyncio.sleep(0)
            order.append("joined")

        await streaming._cleanup(
            base, ACTOR, NS(id=identifier), asyncio.create_task(worker())
        )

    asyncio.run(run())
    assert order == ["cancel", "joined", "close"]
