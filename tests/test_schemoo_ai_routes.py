"""HTTP integration with real product services and a deterministic provider."""
from dataclasses import replace, asdict
from schemii.common.ai.pi import PiReply
from schemii.common.metadata.models import Principal, get_current_principal
from test_schemoo_routes import setup
from test_schemoo_ai_conversations import FakeRuntime, call


def create(api, model):
    response=api.post("/api/v1/schemoo/ai/chats",json={"modelId":model["id"],"providerId":"openai","aiModelId":"fixture","modes":{}})
    assert response.status_code == 201,response.text
    return response.json()


def test_chat_http_tools_context_and_owner_boundary(setup):
    api,model,_,_=setup
    api.app.state.schemoo_ai.runtime=FakeRuntime([call({"operation":"get_model","args":{}}),PiReply("Model has one source.",())])
    chat=create(api,model)
    url=f"/api/v1/schemoo/ai/chats/{chat['id']}"
    sent=api.post(url+"/messages",json={"text":"Inspect my model","expectedRevision":chat["revision"]})
    assert sent.status_code == 202,sent.text
    snapshot=api.get(url).json()
    assert snapshot["status"] == "idle"
    assert snapshot["messages"][-1]["text"] == "Model has one source."
    assert snapshot["activity"][0]["operation"] == "get_model"
    assert api.get("/api/v1/schemoo/ai/chats").json()["chats"][0]["id"] == chat["id"]
    api.app.dependency_overrides[get_current_principal]=lambda:Principal(user_id="other",authentication_source="local_prototype")
    assert api.get(url).status_code == 404
    assert api.post("/api/v1/schemoo/ai/chats",json={"modelId":model["id"],"providerId":"openai","aiModelId":"fixture"}).status_code == 404


def test_reviewed_model_change_uses_real_revision_and_completes_followup(setup):
    api,model,_,_=setup
    action={"operation":"update_model","args":{"expectedRevision":1,"name":"Reviewed model","definition":model["definition"]}}
    api.app.state.schemoo_ai.runtime=FakeRuntime([call(action),PiReply("Renamed the model.",())])
    chat=create(api,model); url=f"/api/v1/schemoo/ai/chats/{chat['id']}"
    api.post(url+"/messages",json={"text":"Rename it","expectedRevision":chat["revision"]})
    pending=api.get(url).json()
    assert pending["status"] == "waiting_approval"
    assert api.get(f"/api/v1/schemoo/models/{model['id']}").json()["revision"] == 1
    body={"pendingId":pending["pending"]["id"],"expectedRevision":pending["revision"],"approved":True}
    assert api.post(url+"/approval",json=body).status_code == 202
    done=api.get(url).json()
    assert done["status"] == "idle",done
    assert done["messages"][-1]["text"] == "Renamed the model."
    assert api.get(f"/api/v1/schemoo/models/{model['id']}").json()["name"] == "Reviewed model"
    assert api.post(url+"/approval",json=body).status_code == 409


def test_preference_change_invalidates_pending_authority(setup):
    api,model,_,_=setup
    api.app.state.schemoo_ai.runtime=FakeRuntime([call({"operation":"delete_model","args":{"expectedRevision":1}})])
    chat=create(api,model); url=f"/api/v1/schemoo/ai/chats/{chat['id']}"
    api.post(url+"/messages",json={"text":"Delete","expectedRevision":chat["revision"]})
    pending=api.get(url).json()
    update=api.put(url+"/preferences",json={"expectedRevision":pending["revision"],"providerId":"openai","aiModelId":"fixture","modes":{"delete_model":"disabled"}})
    assert update.status_code == 200,update.text
    assert update.json()["status"] == "idle"
    assert update.json()["pending"] is None
    assert api.get(f"/api/v1/schemoo/models/{model['id']}").status_code == 200


def test_chat_capacity_and_prompt_rejections_reach_metadata_handler(setup):
    api, model, _, _ = setup
    service = api.app.state.schemoo_ai
    service.runtime = FakeRuntime([])
    service.policy = replace(service.policy, maximum_chats_per_workspace=1)
    service.store.policy = service.policy
    chat = create(api, model)
    rejected = api.post("/api/v1/schemoo/ai/chats", json={"modelId": model["id"],
        "providerId": "openai", "aiModelId": "fixture"})
    assert rejected.status_code == 409
    assert "Delete an old chat" in rejected.json()["error"]["message"]
    prompt = "private-query-value" + "x" * service.policy.prompt_bytes
    rejected = api.post(f"/api/v1/schemoo/ai/chats/{chat['id']}/messages", json={
        "text": prompt, "expectedRevision": chat["revision"]})
    assert rejected.status_code == 413
    events = api.app.state.services.metadata.limit_events.events()
    assert [event.notice.limit_name for event in events] == [
        "ai.maximum_chats_per_workspace", "ai.prompt_bytes"]
    assert "private-query-value" not in repr([asdict(event) for event in events])


def test_turn_capacity_rejection_identifies_owner_limit_in_metadata(setup):
    api, model, _, _ = setup
    service = api.app.state.schemoo_ai
    service.runtime = FakeRuntime([])
    chat = create(api, model)
    owner = model["ownerId"]
    for index in range(service.policy.maximum_concurrent_turns_per_user):
        service.active[owner, "occupied-" + str(index)] = {"turnId": "pending"}
    response = api.post(f"/api/v1/schemoo/ai/chats/{chat['id']}/messages", json={
        "text": "hello", "expectedRevision": chat["revision"]})
    assert response.status_code == 429
    event = api.app.state.services.metadata.limit_events.events()[-1]
    assert event.notice.limit_name == "ai.maximum_concurrent_turns_per_user"
    assert event.notice.observed_value == service.policy.maximum_concurrent_turns_per_user
    assert not service.runtime.requests


def test_first_model_chat_adopts_created_model_and_keeps_history(setup):
    api, existing, _, _ = setup
    source = existing["connectionId"]
    assert api.delete(f"/api/v1/schemoo/models/{existing['id']}",
        params={"expected_revision": existing["revision"]}).status_code == 204
    assert api.get("/api/v1/schemoo/models").json()["models"] == []

    create_action = {"operation": "create_model", "args": {"name": "From chat",
        "connectionId": source, "database": "warehouse", "namespace": "public"}}
    runtime = FakeRuntime([call(create_action), PiReply("Created the model.", ()),
        call({"operation": "get_model", "args": {}}), PiReply("The model is open.", ())])
    api.app.state.schemoo_ai.runtime = runtime
    response = api.post("/api/v1/schemoo/ai/chats", json={"modelId": "",
        "sourceConnectionId": source, "providerId": "openai", "aiModelId": "fixture"})
    assert response.status_code == 201, response.text
    chat = response.json()
    url = f"/api/v1/schemoo/ai/chats/{chat['id']}"
    assert chat["modelId"] == "" and chat["sourceConnectionId"] == source

    queued = api.post(url + "/messages", json={"text": "Create my first model",
        "expectedRevision": chat["revision"]})
    assert queued.status_code == 202, queued.text
    pending = api.get(url).json()
    assert pending["status"] == "waiting_approval"
    assert api.get("/api/v1/schemoo/models").json()["models"] == []
    approved = api.post(url + "/approval", json={"pendingId": pending["pending"]["id"],
        "expectedRevision": pending["revision"], "approved": True})
    assert approved.status_code == 202, approved.text
    adopted = api.get(url).json()
    assert adopted["status"] == "idle" and adopted["modelId"].startswith("model_")
    assert adopted["activity"][0]["operation"] == "create_model"
    assert api.get(f"/api/v1/schemoo/ai/chats?model_id={adopted['modelId']}").json()["chats"][0]["id"] == chat["id"]
    followup = api.post(url + "/messages", json={"text": "Inspect it",
        "expectedRevision": adopted["revision"]})
    assert followup.status_code == 202, followup.text
    latest = api.get(url).json()
    assert latest["status"] == "idle" and latest["modelId"] == adopted["modelId"]
    assert latest["activity"][-1]["operation"] == "get_model"
    assert latest["messages"][-1]["text"] == "The model is open."


def test_bootstrap_preferences_recheck_selected_source_for_shared_codex(setup):
    api, model, _, _ = setup
    source = model["connectionId"]
    runtime = FakeRuntime([])
    scopes = []
    runtime.require_instance_policy = lambda owner, provider, ai_model, effort, scope: scopes.append(scope())
    api.app.state.schemoo_ai.runtime = runtime
    response = api.post("/api/v1/schemoo/ai/chats", json={"modelId": "",
        "sourceConnectionId": source, "providerId": "instance-codex", "aiModelId": "fixture"})
    assert response.status_code == 201, response.text
    chat = response.json()
    update = api.put(f"/api/v1/schemoo/ai/chats/{chat['id']}/preferences", json={
        "expectedRevision": chat["revision"], "providerId": "instance-codex",
        "aiModelId": "fixture", "modes": {"create_model": "automatic"}})
    assert update.status_code == 200, update.text
    assert update.json()["sourceConnectionId"] == source
    assert update.json()["modes"]["create_model"] == "automatic"
    assert scopes == [("schemoo", model["ownerId"], source)] * 2


def test_bootstrap_chat_rejects_other_source_and_missing_source_for_shared_codex(setup):
    api, model, _, _ = setup
    source = model["connectionId"]
    other = api.post("/api/v1/connections", json={"name": "Other", "host": "localhost",
        "database": "warehouse", "username": "reader"}).json()["id"]
    runtime = FakeRuntime([call({"operation": "create_model", "args": {
        "name": "Wrong source", "connectionId": other, "database": "warehouse",
        "namespace": "public"}}), PiReply("The selected source is required.", ())])
    api.app.state.schemoo_ai.runtime = runtime
    chat = api.post("/api/v1/schemoo/ai/chats", json={"modelId": "",
        "sourceConnectionId": source, "providerId": "openai", "aiModelId": "fixture",
        "modes": {"create_model": "automatic"}}).json()
    url = f"/api/v1/schemoo/ai/chats/{chat['id']}"
    sent = api.post(url + "/messages", json={"text": "Create on the other source",
        "expectedRevision": chat["revision"]})
    assert sent.status_code == 202, sent.text
    result = api.get(url).json()
    assert result["activity"][0]["status"] == "failed"
    assert len(api.get("/api/v1/schemoo/models").json()["models"]) == 1
    assert api.post("/api/v1/schemoo/ai/chats", json={"modelId": "",
        "providerId": "instance-codex", "aiModelId": "fixture"}).status_code == 422
