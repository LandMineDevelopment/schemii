import io
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from schemii.common.ai.credential_store import MemoryAiCredentialStore
from schemii.common.ai.instance_provider_store import MemoryInstanceAiProviderStore
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


def test_owner_specific_availability_requires_a_zen_key_and_verified_free_model():
    runtime, _, _ = setup([])
    alice = runtime.status("alice")["providers"]
    bob = runtime.status("bob")["providers"]
    assert alice[0]["available"]
    assert not bob[0]["available"]
    assert [provider["id"] for provider in bob] == ["openai-codex", "openai", "opencode", "instance-codex"]
    assert not bob[2]["available"]
    assert [model["id"] for model in bob[2]["models"]] == ["free"]
    assert "personal" in bob[2]["privacy"]


def test_confirmed_model_denial_is_owner_scoped_and_expires(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("schemii.common.ai.pi.time.monotonic", lambda: clock[0])
    runtime, store, requests = setup([{"type": "error", "code": "model_unavailable", "generation": 1}])
    store.begin_login("bob", "codex-prototype", "openai-codex")
    store.save("bob", "codex-prototype", "openai-codex", {"refresh": "bob"}, 1)
    with pytest.raises(PiError) as caught:
        run(runtime)
    assert caught.value.code == "model_unavailable"
    assert "conversation is kept" in str(caught.value)
    alice = runtime.status("alice")["providers"][0]
    assert not alice["available"] and alice["models"][0]["status"] == "unavailable"
    assert runtime.status("bob")["providers"][0]["models"][0]["status"] == "active"
    with pytest.raises(PiError):
        run(runtime)
    assert len(requests) == 1
    clock[0] += runtime.policy.catalog_refresh_seconds + 1
    assert runtime.status("alice")["providers"][0]["models"][0]["status"] == "active"
    assert runtime._model_denials == {}


def test_replacing_credentials_clears_model_denial():
    runtime, store, _ = setup([{"type": "error", "code": "model_unavailable", "generation": 1}])
    with pytest.raises(PiError):
        run(runtime)
    store.begin_login("alice", "codex-prototype", "openai-codex")
    store.save("alice", "codex-prototype", "openai-codex", {"refresh": "replacement"}, 2)
    assert runtime.status("alice")["providers"][0]["available"]
    assert runtime._model_denials == {}


@pytest.mark.parametrize("code", ["provider_request_rejected", "rate_limited", "billing_required"])
def test_other_provider_failures_do_not_deny_model_access(code):
    runtime, _, _ = setup([{"type": "error", "code": code, "generation": 1, "message": "private provider text"}])
    with pytest.raises(PiError) as caught:
        run(runtime)
    assert caught.value.code == code
    assert "private provider text" not in str(caught.value)
    assert runtime.status("alice")["providers"][0]["available"]
    assert runtime._model_denials == {}


def discovery_runtime(reply, before=None):
    runtime, store, requests = setup([])
    runtime.catalog = None
    original = runtime.client.call

    def call(path, body=None):
        if path != "/models/refresh":
            return original(path, body)
        runtime.client.calls.append((path, body))
        if before:
            before(store)
        return reply

    runtime.client.call = call
    return runtime, store, requests


def test_opening_refresh_discovers_account_models_without_an_inference_request():
    runtime, store, requests = discovery_runtime({"type": "result", "models": [], "generation": 1,
                                                "credential": {"refresh": "rotated"}})
    store.begin_login("bob", "codex-prototype", "openai-codex")
    store.save("bob", "codex-prototype", "openai-codex", {"refresh": "bob"}, 1)
    status = runtime.status("alice", refresh=True)
    assert status["healthy"] and not status["providers"][0]["available"]
    assert status["providers"][0]["models"][0]["status"] == "unavailable"
    assert status["providers"][0]["catalogCheckedAt"]
    assert status["message"] is None
    assert requests == []
    assert store.get("alice", "codex-prototype")["credential"]["refresh"] == "rotated"
    assert "rotated" not in json.dumps(status)
    assert runtime.status("bob")["providers"][0]["available"]
    assert runtime.client.calls[-1][0] == "/models/refresh"
    assert runtime.client.calls[-1][1]["owner"] == "alice"
    assert runtime.client.calls[-1][1]["generation"] == 1


def test_refresh_intersects_static_sdk_support_and_is_not_called_by_passive_status():
    runtime, _, _ = discovery_runtime({"type": "result", "generation": 1, "models": [
        {"providerId": "openai-codex", "id": "model"},
        {"providerId": "openai-codex", "id": "not-implemented-by-sdk"},
    ]})
    runtime.status("alice")
    assert all(path != "/models/refresh" for path, _ in runtime.client.calls)
    assert runtime.status("alice", refresh=True)["providers"][0]["models"] == [
        {"id": "model", "name": "Model", "status": "active", "reasoningLevels": ["default"]}]
    assert runtime._identities == set()


def test_failed_refresh_keeps_bounded_snapshot_and_exposes_only_safe_error():
    clock = [100.0]
    from unittest.mock import patch
    with patch("schemii.common.ai.pi.time.monotonic", side_effect=lambda: clock[0]):
        reply = {"type": "result", "generation": 1, "models": []}
        runtime, store, _ = discovery_runtime(reply)
        runtime.status("alice", refresh=True)
        reply.update(type="error", code="provider_failed", message="private-provider-payload",
                     credential={"refresh": "rotated-on-error"})
        failed = runtime.status("alice", refresh=True)
        assert not failed["providers"][0]["available"]
        assert "Could not refresh" in failed["message"]
        assert "private-provider-payload" not in json.dumps(failed)
        assert "rotated-on-error" not in json.dumps(failed)
        assert store.get("alice", "codex-prototype")["credential"]["refresh"] == "rotated-on-error"
        clock[0] += runtime.policy.catalog_max_stale_seconds + 1
        runtime.status("alice")
        assert runtime._account_catalogs == {}


@pytest.mark.parametrize("change", ["disconnect", "replace"])
def test_discovery_cannot_restore_credentials_or_models_after_generation_change(change):
    def change_store(store):
        if change == "disconnect":
            store.delete("alice", "codex-prototype")
        else:
            store.begin_login("alice", "codex-prototype", "openai-codex")
            store.save("alice", "codex-prototype", "openai-codex", {"refresh": "replacement"}, 2)
    runtime, store, _ = discovery_runtime({"type": "result", "generation": 1, "models": [],
                                         "credential": {"refresh": "late"}}, change_store)
    runtime.status("alice", refresh=True)
    record = store.get("alice", "codex-prototype")
    assert record is None if change == "disconnect" else record["credential"]["refresh"] == "replacement"
    assert runtime._account_catalogs == {}


def test_refresh_does_not_race_oauth_rotation_during_a_chat():
    runtime, _, _ = discovery_runtime({"type": "result", "models": []})
    runtime._identities.add(("alice", "codex-prototype"))
    status = runtime.status("alice", refresh=True)
    assert "current request finishes" in status["message"]
    assert all(path != "/models/refresh" for path, _ in runtime.client.calls)
    assert runtime._identities == {("alice", "codex-prototype")}
    assert runtime._account_catalogs == {}


def test_transport_compacts_completed_calls_before_enforcing_context_limit():
    from dataclasses import replace

    runtime, _, requests = setup([
        {"type": "result", "text": "The saved change needs repair.", "toolCalls": [], "generation": 1},
    ])
    runtime.policy = replace(runtime.policy, context_bytes=16384, result_context_bytes=16384)
    messages = [{"role": "user", "content": "Update the model then check it."},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinkingSignature": "private" * 10000},
                    {"type": "toolCall", "id": "call1", "name": "actions", "arguments": {"modelId": "model1"}}]},
                {"role": "toolResult", "toolCallId": "call1", "isError": False,
                 "content": [{"type": "text", "text": json.dumps({"status": "succeeded", "revision": 2})}]}]
    reply = run(runtime, messages=messages)
    assert reply.text == "The saved change needs repair."
    sent = requests[-1]["context"]["messages"]
    assert sent[0] == messages[0]
    assert "succeeded" in sent[1]["content"]
    assert "private" not in sent[1]["content"]
    assert "do not repeat completed mutations" in sent[1]["content"]


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


@pytest.mark.parametrize("name,configured", [
    ("maximum_concurrent_turns", 4), ("maximum_concurrent_turns_per_user", 2),
    ("credential_overlap_guard", 1),
])
def test_capacity_events_preserve_safe_actual_limit(name, configured):
    runtime, _, _ = setup([{"type": "error", "code": "busy", "message": "private-diagnostic",
                           "limit": {"name": name, "configured": configured, "observed": configured}}])
    with pytest.raises(PiError) as caught:
        run(runtime)
    notice = caught.value.limit_event
    assert notice.configured_limit == configured
    assert notice.observed_value == configured
    assert notice.limit_name.endswith(name)
    assert "private-diagnostic" not in str(caught.value)


@pytest.mark.parametrize("descriptor", [
    {"name": "private-provider-data", "configured": 1, "observed": 1},
    {"name": "maximum_concurrent_turns", "configured": 999, "observed": 1},
    {"name": "maximum_concurrent_turns", "configured": 4, "observed": "private-row"},
])
def test_runtime_rejects_untrusted_limit_descriptors(descriptor):
    runtime, _, _ = setup([{"type": "error", "code": "busy", "limit": descriptor}])
    with pytest.raises(PiError) as caught:
        run(runtime)
    assert caught.value.limit_event is None


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


@pytest.mark.parametrize("model_id", ["free", "not-verified"])
def test_zen_models_cannot_reach_transport_without_an_owner_key(model_id):
    runtime, _, requests = setup([])
    with pytest.raises(PiError) as caught:
        runtime.run("alice", "turn", "opencode", model_id, "system", "hello", [])
    assert caught.value.code == "model_unavailable"
    assert requests == []


def test_zen_turn_uses_only_instance_key_after_exact_grant_for_a_verified_free_model():
    runtime, store, requests = setup([{"type": "result", "text": "Zen reply", "generation": 1}])
    instance = MemoryInstanceAiProviderStore()
    runtime.instance_store = instance
    instance.set_key("instance-zen-key")
    instance.upsert_grant("alice", "schemii")
    status = runtime.status("alice")["providers"][2]
    assert status["authenticated"] and status["available"]
    assert runtime.status("alice", zen_scope=lambda: ("schemoo", "alice", "pg_" + "a" * 32))["providers"][2]["available"] is False
    assert runtime.status("alice", zen_scope=lambda: ("schemii", None, None))["providers"][2]["available"] is True
    assert [model["id"] for model in status["models"]] == ["free"]
    scope = lambda: ("schemii", None, None)
    assert runtime.run("alice", "zen-turn", "opencode", "free", "system", "hello", [], zen_scope=scope).text == "Zen reply"
    assert requests[0]["credentialId"] == "instance-opencode"
    assert requests[0]["credential"] == {"type": "api_key", "key": "instance-zen-key"}
    assert requests[0]["generation"] == 1
    assert all(path != "/models/refresh" for path, _ in runtime.client.calls)
    with pytest.raises(PiError, match="not available"):
        runtime.run("alice", "zen-turn-2", "opencode", "not-verified", "system", "hello", [], zen_scope=scope)
    assert len(requests) == 1


def test_zen_exact_grant_is_checked_before_transport_and_after_revocation():
    instance = MemoryInstanceAiProviderStore()
    runtime, _, requests = setup([{"type": "result", "text": "reply", "generation": 1}])
    runtime.instance_store = instance
    instance.set_key("instance-zen-key")
    instance.upsert_grant("alice", "schemoo", "alice", "pg_" + "a" * 32)
    scope = lambda: ("schemoo", "alice", "pg_" + "b" * 32)
    with pytest.raises(PiError) as caught:
        runtime.run("alice", "turn1", "opencode", "free", "system", "hello", [], zen_scope=scope)
    assert caught.value.code == "instance_access_denied"
    assert not requests
    instance.delete_grant("alice", "schemoo", "alice", "pg_" + "a" * 32)
    assert not runtime.status("alice")["providers"][2]["available"]


def test_zen_private_terminal_credential_is_not_saved_to_owner_store():
    credential = {"type": "api_key", "key": "instance-zen-key"}
    runtime, owner_store, _ = setup([{
        "type": "result", "text": "reply", "generation": 1, "credential": credential,
    }])
    instance = MemoryInstanceAiProviderStore()
    runtime.instance_store = instance
    instance.set_key(credential["key"])
    instance.upsert_grant("alice", "schemii")
    assert runtime.run("alice", "turn", "opencode", "free", "system", "hello", [],
                       zen_scope=lambda: ("schemii", None, None)).text == "reply"
    assert owner_store.get("alice", "instance-opencode") is None


def test_zen_revoked_grant_stops_a_running_turn_before_accepting_reply():
    instance = MemoryInstanceAiProviderStore()
    instance.set_key("instance-zen-key")
    instance.upsert_grant("alice", "schemii")
    runtime, _, _ = setup([{"type": "result", "text": "late", "generation": 1,
                            "credential": {"type": "api_key", "key": "instance-zen-key"}}],
                           before=lambda _: instance.delete_grant("alice", "schemii"))
    runtime.instance_store = instance
    with pytest.raises(PiError) as caught:
        runtime.run("alice", "turn", "opencode", "free", "system", "hello", [],
                    zen_scope=lambda: ("schemii", None, None))
    assert caught.value.code == "permission_changed"


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


def test_native_replay_signature_does_not_consume_visible_response_budget():
    from dataclasses import replace
    message = {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "", "thinkingSignature": "s" * 4096},
        {"type": "text", "text": "Done"}]}
    runtime, _, _ = setup([{"type": "result", "text": "Done", "toolCalls": [],
                            "assistantMessage": message}])
    runtime.policy = replace(runtime.policy, response_bytes=1024)
    assert run(runtime).assistant_message == message


def test_native_replay_stays_bounded_and_reports_its_actual_limit():
    from dataclasses import replace
    message = {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "", "thinkingSignature": "s" * 20000},
        {"type": "text", "text": "Done"}]}
    runtime, _, _ = setup([{"type": "result", "text": "Done", "toolCalls": [],
                            "assistantMessage": message}])
    runtime.policy = replace(runtime.policy, response_bytes=1024, context_bytes=16384,
                             result_context_bytes=16384)
    with pytest.raises(PiError) as caught:
        run(runtime)
    assert caught.value.code == "context_too_large"
    notice = caught.value.limit_event
    assert notice.resource == "ai_native_response"
    assert notice.limit_name == "ai.context_bytes"
    assert notice.observed_value == runtime._json_bytes(message)


def test_sidecar_size_limit_keeps_only_known_source_and_numeric_diagnostics():
    runtime, _, _ = setup([{"type": "error", "code": "response_too_large", "limit": {
        "name": "response_bytes", "source": "response_stream", "configured": 262144,
        "observed": 262145, "private": "must-not-be-recorded"}}])
    with pytest.raises(PiError) as caught:
        run(runtime)
    assert caught.value.limit_event.resource == "ai_response_stream"
    assert caught.value.limit_event.observed_value == 262145
    assert "must-not-be-recorded" not in str(caught.value.limit_event)


def test_fine_grained_text_stream_does_not_exhaust_answer_budget_on_envelopes():
    from dataclasses import replace
    text = "x" * 50000
    runtime, _, _ = setup([*({"type": "text", "text": character} for character in text),
                            {"type": "result", "text": text, "toolCalls": []}])
    runtime.policy = replace(runtime.policy, response_bytes=65536)
    assert run(runtime).text == text


def test_reasoning_effort_catalog_validation_and_transport():
    runtime, _, requests = setup([{"type": "result", "text": "ok", "toolCalls": [], "generation": 1}])
    runtime._supported = [{"providerId": "openai-codex", "id": "model", "reasoningLevels": ["default", "off", "high"]}]
    runtime._supported_until = float("inf")
    assert runtime.status("alice")["providers"][0]["models"][0]["reasoningLevels"] == ["default", "off", "high"]
    run(runtime, reasoning_effort="high")
    assert requests[-1]["reasoningEffort"] == "high"
    run(runtime)
    assert "reasoningEffort" not in requests[-1]
    with pytest.raises(PiError) as error:
        run(runtime, reasoning_effort="max")
    assert error.value.code == "reasoning_unsupported"
    assert len(requests) == 2


def test_shared_codex_uses_admin_policy_and_refreshes_only_instance_credential():
    rotated = {"type": "oauth", "refresh": "rotated"}
    runtime, owner_store, requests = setup([{"type": "result", "text": "shared reply",
        "generation": 1, "credential": rotated}])
    runtime._supported = [{"providerId": "openai-codex", "id": "gpt-6-luna",
                           "reasoningLevels": ["default", "low", "high"]}]
    runtime._supported_until = float("inf")
    instance = MemoryInstanceAiProviderStore()
    instance.set_credential("openai-codex", {"type": "oauth", "refresh": "original"})
    instance.upsert_grant("alice", "schemii", provider_id="openai-codex",
                          model_id="gpt-6-luna", reasoning_effort="high")
    runtime.instance_store = instance
    scope = lambda: ("schemii", None, None)
    provider = runtime.status("alice", zen_scope=scope)["providers"][-1]
    assert provider["adminManaged"] is True and provider["available"] is True
    assert provider["selectedModelId"] == "gpt-6-luna"
    assert provider["models"][0]["reasoningLevels"] == ["high"]
    with pytest.raises(PiError) as caught:
        runtime.run("alice", "bad", "instance-codex", "gpt-6-luna", "system", "hello", [],
                    zen_scope=scope, reasoning_effort="low")
    assert caught.value.code == "instance_policy_changed" and requests == []
    reply = runtime.run("alice", "shared", "instance-codex", "gpt-6-luna", "system", "hello", [],
                        zen_scope=scope, reasoning_effort="high")
    assert reply.text == "shared reply"
    assert requests[0]["providerId"] == "openai-codex"
    assert requests[0]["credentialId"] == "instance-codex"
    assert requests[0]["reasoningEffort"] == "high"
    assert instance.credential("openai-codex")["credential"] == rotated
    assert owner_store.get("alice", "codex-prototype")["credential"]["refresh"] == "old"


def test_shared_codex_grant_revision_fences_running_turn():
    instance = MemoryInstanceAiProviderStore()
    instance.set_credential("openai-codex", {"type": "oauth", "refresh": "original"})
    instance.upsert_grant("alice", "schemii", provider_id="openai-codex")
    runtime, _, requests = setup([{"type": "result", "text": "late", "generation": 1}],
        before=lambda _: instance.upsert_grant("alice", "schemii", provider_id="openai-codex"))
    runtime._supported = [{"providerId": "openai-codex", "id": "gpt-6-luna",
                           "reasoningLevels": ["default"]}]
    runtime._supported_until = float("inf")
    runtime.instance_store = instance
    with pytest.raises(PiError) as caught:
        runtime.run("alice", "shared", "instance-codex", "gpt-6-luna", "system", "hello", [],
                    zen_scope=lambda: ("schemii", None, None))
    assert caught.value.code == "permission_changed"
    assert len(requests) == 1


def test_role_policy_requires_active_same_role_app_and_database_authority():
    from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID

    instance = MemoryInstanceAiProviderStore()
    instance.set_credential("openai-codex", {"type": "oauth", "refresh": "instance"})
    source = "pg_" + "a" * 32
    scope = lambda: ("schemii", SCHEMII_CONNECTION_OWNER_ID, source)
    role = {"user_ids": ["alice"], "capabilities": ["schemii:access"],
            "connections": [{"owner_id": SCHEMII_CONNECTION_OWNER_ID,
                             "connection_id": source, "allow_authoring": True}],
            "dashboards": []}
    roles = {"role_shared": role}

    class Auth:
        enabled = True
        store = None

        def __init__(self):
            self.store = self

        def user(self, user_id):
            return {"id": user_id} if user_id == "alice" else None

        def capabilities(self, user_id):
            return ["schemii:access"] if user_id == "alice" else []

        @contextmanager
        def transaction(self):
            yield {"roles": roles}

    instance.upsert_role_grant("role_shared", "schemii", SCHEMII_CONNECTION_OWNER_ID,
                               source, provider_id="openai-codex", model_id="gpt-6-luna",
                               reasoning_effort="high")
    runtime, _, requests = setup([{"type": "result", "text": "late", "generation": 1}],
        before=lambda _: role["user_ids"].clear())
    runtime.instance_store = instance
    runtime.auth = Auth()
    runtime._supported = [{"providerId": "openai-codex", "id": "gpt-6-luna",
                           "reasoningLevels": ["default", "high"]}]
    runtime._supported_until = float("inf")
    provider = runtime.status("alice", zen_scope=scope)["providers"][-1]
    assert provider["available"] and provider["models"][0]["reasoningLevels"] == ["high"]
    with pytest.raises(PiError) as caught:
        runtime.run("alice", "shared", "instance-codex", "gpt-6-luna", "system", "hello", [],
                    zen_scope=scope, reasoning_effort="high")
    assert caught.value.code == "permission_changed" and len(requests) == 1
    assert not runtime.status("alice", zen_scope=scope)["providers"][-1]["available"]


def test_direct_and_role_codex_policies_are_a_union_per_exact_scope():
    instance = MemoryInstanceAiProviderStore()
    instance.set_credential("openai-codex", {"type": "oauth", "refresh": "instance"})
    instance.upsert_grant("alice", "schemii", provider_id="openai-codex",
                          model_id="gpt-6-luna", reasoning_effort="default")
    instance.add_grant("alice", "schemii", model_id="gpt-6-sol", reasoning_effort="high")
    instance.upsert_role_grant("role_ai", "schemii", provider_id="openai-codex",
                               model_id="gpt-6-luna", reasoning_effort="high")
    runtime, _, _ = setup([])
    runtime.instance_store = instance
    @contextmanager
    def read_roles():
        yield {"roles": {"role_ai": {"user_ids": ["alice"],
                                    "capabilities": ["schemii:access"],
                                    "connections": [], "dashboards": []}}}
    runtime.auth = SimpleNamespace(enabled=True, user=lambda owner: {"id": owner},
                                   capabilities=lambda owner: ["schemii:access"],
                                   store=SimpleNamespace(transaction=read_roles))
    runtime._supported = [
        {"providerId": "openai-codex", "id": "gpt-6-luna", "reasoningLevels": ["default", "high"]},
        {"providerId": "openai-codex", "id": "gpt-6-sol", "reasoningLevels": ["default", "high"]}]
    runtime._supported_until = float("inf")
    scope = lambda: ("schemii", None, None)
    provider = runtime.status("alice", zen_scope=scope)["providers"][-1]
    assert [(model["id"], model["reasoningLevels"]) for model in provider["models"]] == [
        ("gpt-6-luna", ["default", "high"]), ("gpt-6-sol", ["high"])]
    assert "selectedModelId" not in provider
    assert runtime.require_instance_policy("alice", "instance-codex", "gpt-6-sol", "high", scope)
    assert runtime.require_instance_policy("alice", "instance-codex", "gpt-6-luna", "high", scope)
    with pytest.raises(PiError) as caught:
        runtime.require_instance_policy("alice", "instance-codex", "gpt-6-sol", "default", scope)
    assert caught.value.code == "instance_policy_changed"


def test_zen_role_grant_is_enforced_and_revocation_removes_access():
    instance = MemoryInstanceAiProviderStore()
    instance.set_key("fixture-zen-key")
    instance.upsert_role_grant("role_ai", "schemii")
    roles = {"role_ai": {"user_ids": ["alice"], "capabilities": ["schemii:access"],
                         "connections": [], "dashboards": []}}

    @contextmanager
    def read_roles():
        yield {"roles": roles}

    runtime, _, _ = setup([])
    runtime.instance_store = instance
    runtime.auth = SimpleNamespace(enabled=True, user=lambda owner: {"id": owner},
                                   capabilities=lambda owner: ["schemii:access"],
                                   store=SimpleNamespace(transaction=read_roles))
    scope = lambda: ("schemii", None, None)
    assert runtime.require_instance_access("alice", scope) == scope()
    instance.delete_role_grant("role_ai", "schemii")
    with pytest.raises(PiError) as caught:
        runtime.require_instance_access("alice", scope)
    assert caught.value.code == "instance_access_denied"


def test_shared_codex_admin_connection_test_uses_live_catalog_without_prompt():
    runtime, _, requests = setup([])
    instance = MemoryInstanceAiProviderStore()
    instance.set_credential("openai-codex", {"type": "oauth", "refresh": "old"})
    runtime.instance_store = instance
    runtime._supported = [{"providerId": "openai-codex", "id": "gpt-6-luna"}]
    runtime._supported_until = float("inf")
    def call(path, body=None):
        assert path == "/models/refresh"
        assert body["providerId"] == "openai-codex"
        return {"type": "result", "generation": 1,
                "credential": {"type": "oauth", "refresh": "new"},
                "models": [{"providerId": "openai-codex", "id": "gpt-6-luna"}]}
    runtime.client.call = call
    result = runtime.test_instance_codex()
    assert [model["id"] for model in result["models"]] == ["gpt-6-luna"]
    assert [model["id"] for model in runtime.instance_codex_catalog()["verifiedModels"]] == ["gpt-6-luna"]
    assert instance.credential("openai-codex")["credential"]["refresh"] == "new"
    assert requests == []
