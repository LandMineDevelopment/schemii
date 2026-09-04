import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from schemii.common.admin_config import AdminConfig, AiPolicy
from schemii.common.ai.pi import PiReply
from schemii.common.postgres.console.models import (
    ConsoleExecution,
    ConsoleResultColumn,
    ConsoleResultPage,
    ConsoleResultSummary,
)
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import AiCapacityError, InMemoryAiRepository
from schemii.schemii.ai.service import AiService, AiServiceError
from schemii.schemii.ai.tools import (
    authority_manifest,
    normalize_tool_call,
    tools_for_capabilities,
)


class AvailableRuntime:
    def status(self, owner):
        return {"healthy": True, "providers": [{"id": "provider", "available": True,
                "models": [{"id": "model", "status": "active"}]}]}


def test_service_uses_owner_runtime_catalog_without_substituting_models():
    runtime = SimpleNamespace(status=lambda owner: {
        "healthy": True, "providers": [{
            "id": "opencode", "available": True, "models": [{"id": "free", "status": "active"}],
        }],
    })
    service = AiService(InMemoryAiRepository(), runtime, SimpleNamespace(admin_config=AdminConfig()))
    service.model_catalog = SimpleNamespace(snapshot=lambda: {"models": [{"id": "free"}]})
    assert [m["id"] for m in service.status("owner")["providers"][0]["models"]] == ["free"]
    with pytest.raises(AiServiceError) as error:
        service._require_available_model("owner", "opencode", "retired")
    assert error.value.code == "ai_model_unavailable"


def test_send_rejects_unavailable_model_without_saving_message():
    repo = InMemoryAiRepository()
    chat = repo.create_chat("owner", "ws_" + "b" * 32, "Keep", "gone", "gone", AiCapabilities())
    service = AiService(repo, None, SimpleNamespace(
        admin_config=AdminConfig(), designs=SimpleNamespace(get=lambda *_: SimpleNamespace(revision=0)),
    ))
    with pytest.raises(AiServiceError) as error:
        service.send("owner", chat.id, SimpleNamespace(
            expected_chat_revision=chat.revision, expected_design_revision=0,
            text="Keep my draft", result_context_operation_id=None,
        ))
    assert error.value.code == "ai_model_unavailable"
    assert repo.list_messages("owner", chat.id, 100) == []
    assert repo.get_chat("owner", chat.id).status == "idle"


def test_ai_status_passes_current_owner_without_sharing_availability() -> None:
    owners = []
    def status(owner):
        owners.append(owner)
        return {"healthy": True, "providers": [{"id": "provider", "available": owner == "alice"}]}
    runtime = SimpleNamespace(status=status)
    service = AiService(
        InMemoryAiRepository(),
        runtime,
        SimpleNamespace(admin_config=AdminConfig(), ai=SimpleNamespace()),
    )

    assert service.status("alice")["providers"][0]["available"] is True
    assert service.status("bob")["providers"][0]["available"] is False
    assert owners == ["alice", "bob"]


class ReplayRequired(RuntimeError):
    code = "console_result_replay_required"


def test_preferences_save_defaults_and_chat_policy_as_one_change() -> None:
    owner = "owner"
    workspace_id = "ws_" + "9" * 32
    repository = InMemoryAiRepository(
        AiPolicy(maximum_chats_per_workspace=1)
    )
    chat = repository.create_chat(
        owner,
        workspace_id,
        "Current",
        "provider",
        "model",
        AiCapabilities(),
    )

    saved_settings, saved_chat, started_new = repository.save_preferences(
        owner,
        chat.id,
        1,
        1,
        "provider",
        "different-model",
        AiCapabilities(live_catalog=True),
    )

    assert started_new is False
    assert saved_settings.revision == 2
    assert saved_settings.default_capabilities.live_catalog is True
    assert saved_chat.revision == 2
    assert saved_chat.id == chat.id
    assert saved_chat.model_id == "different-model"
    assert saved_chat.capabilities.live_catalog is True


class ReplayConsole:
    def __init__(self) -> None:
        self.reserve_calls = []

    def page(self, owner, workspace_id, execution_id, result_id, cursor):
        if execution_id == "cex_" + "1" * 32:
            raise ReplayRequired("released")
        return ConsoleResultPage(
            execution_id=execution_id,
            result_id=result_id,
            columns=[ConsoleResultColumn(name="total", data_type="bigint")],
            rows=[[2]],
            next_cursor=None,
            truncated=False,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    def reserve(self, owner, workspace_id, request):
        self.reserve_calls.append(request)
        return SimpleNamespace(id="cex_" + "2" * 32)

    def run(self, owner, execution_id):
        return None

    def get(self, owner, workspace_id, execution_id):
        now = datetime.now(timezone.utc)
        return ConsoleExecution(
            id=execution_id,
            revision=1,
            workspace_id=workspace_id,
            console_id="con_" + "3" * 32,
            status="succeeded",
            completed_statement_indexes=[0],
            results=[
                ConsoleResultSummary(
                    id="res_" + "4" * 32,
                    statement_index=0,
                    command="SELECT",
                    columns=[ConsoleResultColumn(name="total", data_type="bigint")],
                    row_count=2,
                    has_more=False,
                    replayable=True,
                )
            ],
            created_at=now,
            updated_at=now,
        )


def test_released_ai_query_result_reruns_saved_sql_without_persisting_rows() -> None:
    owner = "owner"
    workspace_id = "ws_" + "a" * 32
    repository = InMemoryAiRepository()
    chat = repository.create_chat(
        owner,
        workspace_id,
        "Query",
        "provider",
        "model",
        AiCapabilities(raw_sql_read=True),
    )
    turn, _ = repository.create_turn(owner, chat.id, "Count rows", None, 4, 2, 100)
    repository.claim_turn(owner, chat.id, turn.id)
    proposal = repository.create_proposal(
        owner,
        chat.id,
        turn.id,
        "raw_sql_read",
        "data_read",
        "Count rows",
        {"sql": "SELECT count(*) AS total FROM orders"},
        "a" * 64,
        1,
        0,
        False,
        datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    operation = repository.create_operation(owner, chat.id, proposal.id, "data_read")
    repository.finish_operation(
        owner,
        chat.id,
        operation.id,
        status="succeeded",
        resource_kind="consoleResult",
        resource_id="res_" + "0" * 32,
        result_summary={
            "executionId": "cex_" + "1" * 32,
            "resultId": "res_" + "0" * 32,
            "rowCount": 1,
            "hasMore": False,
        },
    )
    console = ReplayConsole()
    workspace = SimpleNamespace(revision=1)
    services = SimpleNamespace(
        admin_config=AdminConfig(),
        console=console,
        workspaces=SimpleNamespace(get=lambda _owner, _workspace: workspace),
    )
    service = AiService(repository, None, services)

    page = service.query_result(owner, chat.id, operation.id, None)

    assert page.rows == [[2]]
    assert page.rerun is True
    assert "data may have changed" in page.freshness_notice
    assert console.reserve_calls[0].statements == [
        "SELECT count(*) AS total FROM orders"
    ]
    saved = repository.get_operation(owner, chat.id, operation.id)
    assert saved.result_summary == {
        "executionId": "cex_" + "2" * 32,
        "resultId": "res_" + "4" * 32,
        "rowCount": 2,
        "hasMore": False,
    }
    assert "rows" not in saved.result_summary
    with pytest.raises(ValueError, match="cannot contain query result rows"):
        repository.finish_operation(
            owner,
            chat.id,
            operation.id,
            status="succeeded",
            result_summary={"rows": [["must not persist"]]},
        )


def test_prompt_byte_limit_is_server_enforced_and_recorded() -> None:
    owner = "owner"
    workspace_id = "ws_" + "b" * 32
    repository = InMemoryAiRepository()
    chat = repository.create_chat(
        owner,
        workspace_id,
        "Prompt",
        "provider",
        "model",
        AiCapabilities(),
    )
    design = SimpleNamespace(revision=0)
    services = SimpleNamespace(
        admin_config=AdminConfig.from_document({"ai": {"prompt_bytes": 1024}}),
        designs=SimpleNamespace(get=lambda _owner, _workspace: design),
    )
    service = AiService(repository, None, services)
    body = SimpleNamespace(
        expected_chat_revision=chat.revision,
        expected_design_revision=0,
        text="x" * 1025,
        result_context_operation_id=None,
    )

    with pytest.raises(AiServiceError, match="prompt limit") as error:
        service.send(owner, chat.id, body)

    assert error.value.code == "ai_prompt_too_large"
    assert repository.activity(owner, chat.id, 0)[0].payload == {
        "code": "ai_prompt_too_large"
    }


def test_design_tool_covers_table_owned_and_top_level_objects() -> None:
    index = normalize_tool_call(
        "schemii_design_change",
        {
            "summary": "Add the lookup index",
            "action": {
                "type": "put_table_member",
                "table_id": "table_" + "1" * 32,
                "collection": "indexes",
                "object": {
                    "name": "orders_customer_idx",
                    "column_ids": ["column_" + "2" * 32],
                },
            },
        },
    )
    view = normalize_tool_call(
        "schemii_design_change",
        {
            "summary": "Add an order summary",
            "action": {
                "type": "put_top_level_object",
                "collection": "views",
                "object": {
                    "name": "order_summary",
                    "kind": "view",
                    "definition": "SELECT 1",
                },
            },
        },
    )

    assert index.action["collection"] == "indexes"
    assert view.action["collection"] == "views"
    assert index.capability == view.capability == "design_changes"

    serialized = normalize_tool_call(
        "schemii_design_change",
        {
            "summary": "Add audit table",
            "action": json.dumps(
                {
                    "type": "add_table",
                    "name": "audit_log",
                    "columns": [{"name": "id", "data_type": "bigint"}],
                }
            ),
        },
    )
    assert serialized.action["name"] == "audit_log"


def test_tool_availability_and_authority_manifest_follow_current_policy() -> None:
    capabilities = AiCapabilities(design_changes=True, live_catalog=True)

    tools = tools_for_capabilities(capabilities)
    authority = authority_manifest(7, capabilities)

    assert tools["schemii_design_change"] is True
    assert tools["schemii_review_migration"] is True
    assert tools["schemii_read_query"] is False
    assert tools["schemii_open_console"] is False
    assert tools["bash"] is False
    assert authority["policyRevision"] == 7
    assert authority["assistantCanApproveOrApply"] is False
    assert authority["capabilities"]["design_changes"] == {
        "enabled": True,
        "permissionLabel": "Propose design changes",
    }
    assert {
        "tool": "schemii_read_query",
        "requiredPermission": "Prepare read queries",
    } in authority["disabledTools"]
    assert authority["contextSources"]["liveCatalog"] == {
        "enabled": True,
        "requiredPermission": "Inspect live catalog",
        "location": "CONTEXT.liveCatalog",
    }


def test_query_result_context_is_bounded_by_rows_and_encoded_bytes() -> None:
    services = SimpleNamespace(
        admin_config=AdminConfig.from_document(
            {"ai": {"result_context_rows": 2, "result_context_bytes": 16 * 1024}}
        )
    )
    service = AiService(InMemoryAiRepository(), None, services)

    assert service._bounded_rows([[1], [2], [3]]) == [[1], [2]]
    assert service._bounded_rows([["x" * (16 * 1024)]]) == []


def test_turn_activity_reports_real_orchestration_stages() -> None:
    owner = "owner"
    workspace_id = "ws_" + "c" * 32
    repository = InMemoryAiRepository()
    chat = repository.create_chat(
        owner,
        workspace_id,
        "Explain",
        "provider",
        "model",
        AiCapabilities(),
    )
    turn, _ = repository.create_turn(
        owner,
        chat.id,
        "Explain this design",
        None,
        4,
        2,
        100,
    )

    class Runtime(AvailableRuntime):
        def create_session(self, title):
            assert title == "Explain"
            return "session"

        def run(self, owner_id, turn_id, provider_id, model_id, system, prompt, tools, **kwargs):
            assert (owner_id, provider_id, model_id) == (
                owner,
                "provider",
                "model",
            )
            assert prompt == "Explain this design"
            context = json.loads(system.split("\nCONTEXT ", 1)[1])
            assert context["authority"]["policyRevision"] == chat.revision
            assert context["authority"]["assistantCanApproveOrApply"] is False
            assert context["authority"]["contextSources"]["liveCatalog"] == {
                "enabled": False,
                "requiredPermission": "Inspect live catalog",
                "location": "CONTEXT.liveCatalog",
            }
            assert "do not substitute 'Prepare read queries'" in system
            assert "schemii_design_change" not in {tool["name"] for tool in tools}
            assert "schemii_read_query" not in {tool["name"] for tool in tools}
            return PiReply("A formatted answer", ())

        def delete_session(self, session_id):
            assert session_id == "session"

    design = SimpleNamespace(
        revision=0,
        model_dump=lambda **_kwargs: {"revision": 0, "tables": []},
    )
    workspace = SimpleNamespace(
        revision=1,
        connection_id=None,
        namespace=None,
        model_dump=lambda **_kwargs: {"id": workspace_id, "revision": 1},
    )
    services = SimpleNamespace(
        admin_config=AdminConfig(),
        designs=SimpleNamespace(get=lambda _owner, _workspace: design),
        workspaces=SimpleNamespace(get=lambda _owner, _workspace: workspace),
    )

    AiService(repository, Runtime(), services).run_turn(owner, chat.id, turn.id)

    status_events = [
        event.payload
        for event in repository.activity(owner, chat.id, 0)
        if event.kind == "status"
    ]
    assert [(event["stage"], event["state"]) for event in status_events] == [
        ("context", "running"),
        ("context", "completed"),
        ("model", "running"),
        ("model", "completed"),
        ("response", "running"),
        ("response", "completed"),
    ]
    assert repository.list_messages(owner, chat.id, 100)[-1].text == "A formatted answer"


def test_permission_changes_take_effect_on_the_next_turn_and_denied_calls_are_truthful() -> None:
    owner = "owner"
    workspace_id = "ws_" + "d" * 32
    repository = InMemoryAiRepository()
    chat = repository.create_chat(
        owner,
        workspace_id,
        "Permission change",
        "provider",
        "model",
        AiCapabilities(),
    )
    first_turn, _ = repository.create_turn(
        owner, chat.id, "Add an audit table", None, 4, 2, 100
    )

    table_call = (
        "schemii_design_change",
        {
            "summary": "Add audit table",
            "action": {
                "type": "add_table",
                "name": "audit_log",
                "columns": [{"name": "id", "data_type": "bigint"}],
            },
        },
    )

    class Runtime(AvailableRuntime):
        def __init__(self):
            self.turns = []

        def create_session(self, title):
            return f"session-{len(self.turns) + 1}"

        def run(self, owner_id, turn_id, provider_id, model_id, system, prompt, tools, **kwargs):
            context = json.loads(system.split("\nCONTEXT ", 1)[1])
            self.turns.append((context["authority"], tools))
            return PiReply("I approved and created the table.", (table_call,))

        def delete_session(self, session_id):
            return None

    design = SimpleNamespace(
        revision=0,
        model_dump=lambda **_kwargs: {"revision": 0, "tables": []},
    )
    workspace = SimpleNamespace(
        revision=1,
        connection_id=None,
        namespace=None,
        model_dump=lambda **_kwargs: {"id": workspace_id, "revision": 1},
    )
    services = SimpleNamespace(
        admin_config=AdminConfig(),
        designs=SimpleNamespace(get=lambda _owner, _workspace: design),
        workspaces=SimpleNamespace(get=lambda _owner, _workspace: workspace),
    )
    runtime = Runtime()
    service = AiService(repository, runtime, services)

    service.run_turn(owner, chat.id, first_turn.id)

    assert runtime.turns[0][0]["policyRevision"] == 1
    assert "schemii_design_change" not in {tool["name"] for tool in runtime.turns[0][1]}
    assert repository.list_proposals(owner, chat.id) == []
    denied_reply = repository.list_messages(owner, chat.id, 100)[-1].text
    assert "No proposal was created" in denied_reply
    assert "Propose design changes" in denied_reply
    assert "approved and created" not in denied_reply

    chat = repository.update_chat_policy(
        owner,
        chat.id,
        chat.revision,
        AiCapabilities(design_changes=True),
    )
    second_turn, _ = repository.create_turn(
        owner, chat.id, "Try again", None, 4, 2, 100
    )
    service.run_turn(owner, chat.id, second_turn.id)

    assert runtime.turns[1][0]["policyRevision"] == 2
    assert runtime.turns[1][0]["capabilities"]["design_changes"]["enabled"] is True
    assert "schemii_design_change" in {tool["name"] for tool in runtime.turns[1][1]}
    proposals = repository.list_proposals(owner, chat.id)
    assert len(proposals) == 1
    assert proposals[0].details["name"] == "audit_log"


def test_permission_change_during_model_work_discards_reply_and_tool_calls() -> None:
    owner = "owner"
    workspace_id = "ws_" + "e" * 32
    repository = InMemoryAiRepository()
    chat = repository.create_chat(
        owner,
        workspace_id,
        "Race",
        "provider",
        "model",
        AiCapabilities(design_changes=True),
    )
    turn, _ = repository.create_turn(owner, chat.id, "Add a table", None, 4, 2, 100)

    class Runtime(AvailableRuntime):
        def create_session(self, title):
            return "session"

        def run(self, *args, **kwargs):
            repository.update_chat_policy(
                owner, chat.id, chat.revision, AiCapabilities()
            )
            return PiReply(
                "I created it.",
                (
                    (
                        "schemii_design_change",
                        {
                            "summary": "Add forbidden table",
                            "action": {
                                "type": "add_table",
                                "name": "forbidden",
                                "columns": [{"name": "id", "data_type": "bigint"}],
                            },
                        },
                    ),
                ),
            )

        def delete_session(self, session_id):
            return None

    design = SimpleNamespace(
        revision=0,
        model_dump=lambda **_kwargs: {"revision": 0, "tables": []},
    )
    workspace = SimpleNamespace(revision=1, connection_id=None, namespace=None)
    services = SimpleNamespace(
        admin_config=AdminConfig(),
        designs=SimpleNamespace(get=lambda *_args: design),
        workspaces=SimpleNamespace(get=lambda *_args: workspace),
    )

    AiService(repository, Runtime(), services).run_turn(owner, chat.id, turn.id)

    assert repository.list_proposals(owner, chat.id) == []
    response = repository.list_messages(owner, chat.id, 100)[-1].text
    assert "permissions changed while I was working" in response
    assert "created it" not in response


def test_row_backed_assistant_answer_is_transient_and_never_saved_as_message() -> None:
    owner = "owner"
    workspace_id = "ws_" + "f" * 32
    repository = InMemoryAiRepository()
    chat = repository.create_chat(
        owner,
        workspace_id,
        "Rows",
        "provider",
        "model",
        AiCapabilities(structured_data_read=True),
    )
    operation_id = "aop_" + "1" * 32
    turn, _ = repository.create_turn(
        owner, chat.id, "Analyze rows", operation_id, 4, 2, 100
    )
    sentinel = "private-row-d116ece0"

    class Runtime(AvailableRuntime):
        def create_session(self, title):
            return "session"

        def run(self, owner_id, turn_id, provider_id, model_id, system, prompt, tools, **kwargs):
            assert sentinel in system
            return PiReply(f"The value is {sentinel}.", ())

        def delete_session(self, session_id):
            return None

    design = SimpleNamespace(
        revision=0,
        model_dump=lambda **_kwargs: {"revision": 0, "tables": []},
    )
    workspace = SimpleNamespace(revision=1, connection_id=None, namespace=None)
    services = SimpleNamespace(
        admin_config=AdminConfig(),
        designs=SimpleNamespace(get=lambda *_args: design),
        workspaces=SimpleNamespace(get=lambda *_args: workspace),
    )

    class Service(AiService):
        def query_result(self, *args):
            return SimpleNamespace(
                columns=[{"name": "secret", "dataType": "text"}],
                rows=[[sentinel]],
                freshness_notice=None,
                rerun=False,
            )

    service = Service(repository, Runtime(), services)
    service.run_turn(owner, chat.id, turn.id)

    stored = repository.list_messages(owner, chat.id, 100)[-1].text
    assert sentinel not in stored
    transient = service.transient_responses(owner, chat.id)
    assert transient[0].turn_id == turn.id
    assert sentinel in transient[0].text


def test_add_table_tool_preserves_primary_and_unique_key_semantics() -> None:
    proposal = normalize_tool_call(
        "schemii_design_change",
        {
            "summary": "Add accounts",
            "action": {
                "type": "add_table",
                "name": "accounts",
                "columns": [
                    {"name": "id", "data_type": "bigint"},
                    {"name": "email", "data_type": "text"},
                ],
                "keys": [
                    {"kind": "primary", "columns": ["id"]},
                    {"kind": "unique", "columns": ["email"]},
                ],
            },
        },
    )

    assert proposal.action["keys"] == [
        {"kind": "primary", "columns": ["id"], "name": None},
        {"kind": "unique", "columns": ["email"], "name": None},
    ]
    with pytest.raises(ValueError, match="unknown column"):
        normalize_tool_call(
            "schemii_design_change",
            {
                "summary": "Broken",
                "action": {
                    "type": "add_table",
                    "name": "broken",
                    "columns": [{"name": "id", "data_type": "bigint"}],
                    "keys": [{"kind": "primary", "columns": ["missing"]}],
                },
            },
        )


def test_chat_delete_is_physical_and_restart_recovery_resolves_active_state() -> None:
    owner = "owner"
    workspace_id = "ws_" + "1" * 32
    repository = InMemoryAiRepository()
    chat = repository.create_chat(
        owner, workspace_id, "Lifecycle", "provider", "model", AiCapabilities()
    )
    turn, _ = repository.create_turn(owner, chat.id, "Wait", None, 4, 2, 100)
    repository.claim_turn(owner, chat.id, turn.id)
    repository.set_chat_runtime(owner, chat.id, external_session_id="session-orphan")

    sessions = repository.recover_interrupted()

    assert sessions == [(owner, chat.id, "session-orphan")]
    assert repository.get_turn(owner, chat.id, turn.id).status == "failed"
    assert repository.get_chat(owner, chat.id).status == "failed"
    deleted = repository.delete_chat(owner, chat.id)
    assert deleted.status == "deleted"
    with pytest.raises(Exception, match="Chat was not found"):
        repository.get_chat(owner, chat.id)


def test_active_turn_cancel_stops_runtime_and_returns_chat_to_idle() -> None:
    owner = "owner"
    workspace_id = "ws_" + "2" * 32
    repository = InMemoryAiRepository()
    chat = repository.create_chat(
        owner, workspace_id, "Cancellation", "provider", "model", AiCapabilities()
    )
    turn, _ = repository.create_turn(owner, chat.id, "Stop", None, 4, 2, 100)
    repository.claim_turn(owner, chat.id, turn.id)
    repository.set_chat_runtime(owner, chat.id, external_session_id="session-active")

    class Runtime(AvailableRuntime):
        deleted_sessions: list[str] = []

        def cancel(self, owner_id: str, turn_id: str) -> None:
            self.deleted_sessions.append((owner_id, turn_id))

    runtime = Runtime()
    service = AiService(
        repository,
        runtime,
        SimpleNamespace(admin_config=AdminConfig(), ai=SimpleNamespace()),
    )

    cancelled = service.cancel_turn(owner, chat.id, turn.id)

    assert cancelled.status == "cancelled"
    assert repository.get_chat(owner, chat.id).status == "idle"
    assert runtime.deleted_sessions == [(owner, turn.id)]
    assert repository.activity(owner, chat.id, 0)[-1].payload == {
        "turnId": turn.id,
        "stage": "response",
        "state": "cancelled",
        "label": "Turn stopped",
    }
