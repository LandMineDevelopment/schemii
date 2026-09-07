import io
import json
from types import SimpleNamespace

import pytest

from schemii.common.ai.credential_store import MemoryAiCredentialStore
from schemii.common.ai.pi import PiError, PiRuntime


class Client:
    url = "http://private"
    _secret = "service-secret"

    def __init__(self):
        self.calls = []

    def call(self, path, body=None):
        self.calls.append((path, body))
        return {"models": [
            {"providerId": "openai-codex", "id": "model", "name": "Model"},
            {"providerId": "opencode", "id": "free", "name": "Free"},
            {"providerId": "opencode", "id": "not-verified", "name": "Not free"},
        ]}


def setup(events, before=None):
    store = MemoryAiCredentialStore()
    store.begin_login("alice", "codex-prototype", "openai-codex")
    store.save("alice", "codex-prototype", "openai-codex", {"type": "oauth", "refresh": "old"}, 1)
    requests = []

    def opener(request, timeout):
        requests.append(json.loads(request.data))
        if before:
            before(store)
        return io.BytesIO(b"".join(json.dumps(event).encode() + b"\n" for event in events))

    runtime = PiRuntime(Client(), store, SimpleNamespace(snapshot=lambda: {"models": [{"id": "free"}]}), opener=opener)
    return runtime, store, requests


def run(runtime, **kwargs):
    return runtime.run("alice", "turn1", "openai-codex", "model", "system", "hello", [], **kwargs)


def test_owner_specific_availability_and_verified_free_intersection():
    runtime, _, _ = setup([])
    alice = runtime.status("alice")["providers"]
    bob = runtime.status("bob")["providers"]
    assert alice[0]["available"]
    assert not bob[0]["available"]
    assert [m["id"] for m in bob[2]["models"]] == ["free"]
    assert "personal" in bob[2]["privacy"]


def test_stream_response_and_refresh_are_saved_without_touching_activity():
    runtime, store, requests = setup([
        {"type": "text", "text": "Hello"},
        {"type": "result", "text": "Hello", "toolCalls": [], "generation": 1,
         "credential": {"type": "oauth", "refresh": "new"}},
    ])
    activity = store._activity.copy()
    text = []
    assert run(runtime, on_text=text.append).text == "Hello"
    assert text == ["Hello"]
    assert store.get("alice", "codex-prototype")["credential"]["refresh"] == "new"
    assert store._activity == activity
    assert requests[0]["credential"]["refresh"] == "old"


@pytest.mark.parametrize("change", ["logout", "replace"])
def test_late_refresh_cannot_restore_disconnected_or_replaced_credentials(change):
    def change_store(store):
        if change == "logout":
            store.delete("alice", "codex-prototype")
        else:
            store.begin_login("alice", "codex-prototype", "openai-codex")
            store.save("alice", "codex-prototype", "openai-codex", {"refresh": "replacement"}, 2)

    runtime, store, _ = setup([
        {"type": "result", "text": "secret response", "generation": 1,
         "credential": {"type": "oauth", "refresh": "late"}},
    ], change_store)
    with pytest.raises(PiError, match="disconnected"):
        run(runtime)
    current = store.get("alice", "codex-prototype")
    assert current is None if change == "logout" else current["credential"]["refresh"] == "replacement"


def test_error_refresh_is_retained_but_provider_error_text_is_not_exposed():
    runtime, store, _ = setup([
        {"type": "error", "code": "unknown", "message": "secret-provider-token",
         "generation": 1, "credential": {"refresh": "rotated"}},
    ])
    with pytest.raises(PiError) as caught:
        run(runtime)
    assert "secret-provider-token" not in str(caught.value)
    assert store.get("alice", "codex-prototype")["credential"]["refresh"] == "rotated"


def test_permission_change_suppresses_terminal_response():
    runtime, _, _ = setup([{"type": "result", "text": "hidden"}])
    calls = iter([True, False])
    text = []
    with pytest.raises(PiError) as caught:
        run(runtime, is_authorized=lambda: next(calls), on_text=text.append)
    assert caught.value.code == "permission_changed"
    assert text == []


def test_unadvertised_tool_is_returned_as_inert_data_for_server_denial():
    call = {"id": "denied1", "name": "bash", "arguments": {"command": "do not execute"}}
    message = {"role": "assistant", "content": [{"type": "toolCall", **call}]}
    runtime, _, requests = setup([{"type": "result", "text": "", "toolCalls": [call],
                                   "assistantMessage": message}])
    reply = run(runtime)
    assert reply.tool_calls == (("bash", call["arguments"]),)
    assert reply.tool_call_ids == ("denied1",)
    assert reply.assistant_message == message
    assert requests[0]["context"]["tools"] == []
    assert len(requests) == 1


@pytest.mark.parametrize("name", [None, "", "  ", 42, {}])
def test_malformed_tool_names_are_rejected(name):
    runtime, _, _ = setup([{"type": "result", "text": "", "toolCalls": [{"name": name, "arguments": {}}]}])
    with pytest.raises(PiError) as caught:
        run(runtime)
    assert caught.value.code == "invalid_response"


def test_same_identity_reentrant_turn_is_rejected_until_save_finishes():
    runtime, _, _ = setup([{"type": "text", "text": "Hi"}, {"type": "result", "text": "Hi"}])
    def on_text(text):
        with pytest.raises(PiError) as caught:
            run(runtime)
        assert caught.value.code == "busy"
    assert run(runtime, on_text=on_text).text == "Hi"
    assert run(runtime).text == "Hi"


def test_free_turn_uses_explicit_public_auth_and_never_an_owners_paid_key():
    runtime, _, requests = setup([{"type": "result", "text": "Free reply"}])
    reply = runtime.run("bob", "free-turn", "opencode", "free", "system", "hello", [])
    assert reply.text == "Free reply"
    assert requests[0]["credential"] == {"type": "api_key", "key": "public"}
    assert requests[0]["credentialId"] == "zen-public"


def test_unknown_free_model_cannot_reach_transport():
    runtime, _, requests = setup([])
    with pytest.raises(PiError) as caught:
        runtime.run("alice", "turn", "opencode", "not-verified", "system", "hello", [])
    assert caught.value.code == "model_unavailable"
    assert requests == []


def test_disconnect_fences_database_even_if_sidecar_is_unavailable():
    runtime, store, _ = setup([])
    def failed_call(*args):
        raise RuntimeError("private error")
    runtime.client.call = failed_call
    runtime.disconnect("alice")
    assert store.get("alice", "codex-prototype") is None
    assert not store.save("alice", "codex-prototype", "openai-codex", {"refresh": "late"}, 1)


def test_tool_continuation_preserves_provider_message_and_result_identifiers():
    call = {"id": "call_one|provider_id", "name": "read", "arguments": {"queries": ["select 1"]}}
    message = {"role": "assistant", "content": [{"type": "toolCall", **call}],
               "provider": "openai-codex", "api": "openai-codex-responses", "model": "model",
               "stopReason": "toolUse", "timestamp": 10, "responseId": "resp_private"}
    runtime, _, requests = setup([{"type": "result", "text": "", "toolCalls": [call],
                                   "assistantMessage": message}])
    tools = [{"name": "read"}]
    reply = runtime.run("alice", "turn1", "openai-codex", "model", "system", "hello", tools)
    assert reply.assistant_message == message
    assert reply.tool_call_ids == (call["id"],)
    assert reply.tool_calls == (("read", call["arguments"]),)
    messages = [{"role": "user", "content": "hello", "timestamp": 0}, reply.assistant_message,
                {"role": "toolResult", "toolCallId": reply.tool_call_ids[0], "toolName": "read",
                 "content": [{"type": "text", "text": '{"rows":[[1]]}'}],
                 "isError": False, "timestamp": 11}]
    runtime.run("alice", "turn1", "openai-codex", "model", "system", "ignored", tools, messages=messages)
    assert requests[-1]["context"]["messages"] == messages


def test_replay_message_cannot_smuggle_a_different_tool_call():
    call = {"id": "call1", "name": "read", "arguments": {}}
    runtime, _, _ = setup([{"type": "result", "text": "", "toolCalls": [call],
                           "assistantMessage": {"role": "assistant", "content": [
                               {"type": "toolCall", **call, "name": "write"}]}}])
    with pytest.raises(PiError) as caught:
        runtime.run("alice", "turn", "openai-codex", "model", "system", "hello", [{"name": "read"}])
    assert caught.value.code == "invalid_response"
