"""Both product orchestrators revoke live database work, not just inference."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from schemii.common.query_executions.cancellation import cancellable_connection
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.service import AiServiceError
from test_ai_read_execution import reads, proposal, approve
from test_schemoo_ai_conversations import setup, call


class BlockingConnection:
    def __init__(self):
        self.started = Event()
        self.cancelled = Event()

    def cancel_safe(self, *, timeout):
        assert timeout <= 1
        self.cancelled.set()

    def query(self):
        with cancellable_connection(self):
            self.started.set()
            assert self.cancelled.wait(3), "Stop never reached the active connection"


@pytest.mark.parametrize("action", ["stop", "permissions", "delete"])
def test_schemii_revokes_approved_read_batch(reads, action):
    connection = BlockingConnection()
    reads.console.run = lambda *_: connection.query()
    item = proposal(reads)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(approve, reads, item)
        assert connection.started.wait(2)
        if action == "stop":
            reads.service.cancel_turn("owner", reads.chat.id, reads.turn.id)
        elif action == "permissions":
            reads.service.update_chat_policy("owner", reads.chat.id, reads.chat.revision, AiCapabilities())
        else:
            reads.service.delete_chat("owner", reads.chat.id)
        with pytest.raises(AiServiceError) as error:
            pending.result(timeout=2)
        assert error.value.code == "ai_request_cancelled"
    assert len(reads.console.requests) == 1
    assert connection.cancelled.is_set()
    if action != "delete":
        assert not reads.repo.list_read_runs("owner", reads.chat.id)


@pytest.mark.parametrize("action", ["stop", "permissions", "delete"])
def test_schemoo_revokes_live_work_and_skips_remaining_actions(action):
    service, store, runtime, adapter, chat = setup([call("inspect", "inspect")])
    connection = BlockingConnection()
    calls = []

    def execute(*args):
        calls.append(args[-1])
        connection.query()
        return {"status": "succeeded"}

    adapter.execute_action = execute
    queued = service.send("alice", chat["id"], {"text": "Inspect twice", "expectedRevision": chat["revision"]})
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.run, "alice", chat["id"], queued["turnId"])
        assert connection.started.wait(2)
        if action == "stop":
            service.cancel("alice", chat["id"])
        elif action == "permissions":
            current = service.snapshot("alice", chat["id"])
            service.preferences("alice", chat["id"], {"expectedRevision": current["revision"],
                "modes": {"inspect": "disabled"}, "providerId": "openai", "aiModelId": "test-model"})
        else:
            service.delete("alice", chat["id"])
        pending.result(timeout=2)
    assert connection.cancelled.is_set()
    assert len(calls) == 1
    assert len(runtime.requests) == 1
    assert ("alice", chat["id"]) not in service.active
