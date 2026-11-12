"""The displayed handoff is opt-in; other product conversations keep their contract."""

from types import SimpleNamespace as NS

import pytest

from schemii.common.admin_config import AiPolicy
from schemii.common.ai.conversation_store import ConversationStore
from schemii.common.ai.conversations import Conversations
from schemii.common.api.errors import ApiProblem


def test_products_without_displayed_adapter_reject_private_attachment_before_persistence():
    policy = AiPolicy()
    store = ConversationStore(None, policy, "schemoo")
    services = NS(admin_config=NS(ai=policy))
    service = Conversations(store, None, services, NS())
    with pytest.raises(ApiProblem) as error:
        service.send(
            "alice",
            "absent",
            {
                "text": "private-row-207",
                "expectedRevision": 1,
                "displayedResult": {"rows": [["private-row-207"]]},
            },
        )
    assert error.value.code == "ai_displayed_result_unsupported"
    assert "private-row-207" not in str(error.value)
    assert not store.memory and not service.transient and not service.active


def test_ordinary_live_query_transient_answers_keep_their_existing_snapshot_lifecycle():
    policy = AiPolicy()
    store = ConversationStore(None, policy, "schemoo")
    service = Conversations(
        store,
        None,
        NS(admin_config=NS(ai=policy)),
        NS(ACTIONS={}, context=lambda *args: {}),
    )
    chat = service.create(
        "alice",
        {"modelId": "saved-model", "providerId": "provider", "aiModelId": "model"},
    )
    service.store.update(
        "alice",
        chat["id"],
        lambda value: service._message(
            value, "assistant", "ordinary live row explanation", True, "alice"
        ),
    )
    snapshot = service.snapshot("alice", chat["id"])
    assert snapshot["messages"][-1]["text"] == "ordinary live row explanation"
    assert snapshot["messages"][-1]["transient"]
    assert all(not key[2].startswith("displayed_answer:") for key in service.transient)
