from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from schemii.schemii.ai.app_actions import AppAction, OPERATIONS, execute_action, permission_id
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.tools import normalize_tool_call, tool_enabled

WORKSPACE = 'ws_' + 'a' * 32


def test_unknown_operations_and_owner_injection_are_rejected():
    for value in [
        {'operation': 'approve_ai', 'args': {}},
        {'operation': 'list_workspaces', 'args': {'principal': {'user_id': 'other'}}},
        {'operation': 'get_workspace', 'args': {'workspaceId': '../other'}},
        {'operation': 'update_console_settings', 'args': {'body': {'expectedRevision': 0, 'rowPageSize': 20}}},
    ]:
        with pytest.raises(ValidationError):
            AppAction.model_validate(value)


def test_public_connection_actions_do_not_accept_passwords_or_store_null_updates():
    payload = {'operation': 'update_connection', 'args': {'connectionId': 'pg_' + 'b' * 32,
               'body': {'expectedRevision': 1, 'name': 'New name'}}}
    normalized = normalize_tool_call('schemii_app_action', payload)
    assert normalized.action['args']['body'] == {'expected_revision': 1, 'name': 'New name'}
    payload['args']['body']['password'] = 'never-store-in-proposal'
    with pytest.raises(ValidationError):
        AppAction.model_validate(payload)


def test_new_app_actions_default_disabled_and_check_exact_permission():
    action = {'operation': 'list_saved_queries', 'args': {}}
    assert not tool_enabled('schemii_app_action', AiCapabilities(), action)
    allowed = AiCapabilities(action_modes={'query.saved.read': 'ask'})
    assert tool_enabled('schemii_app_action', allowed, action)
    assert not tool_enabled('schemii_app_action', allowed, {'operation': 'delete_saved_query', 'args': {
        'queryId': 'sq_' + 'c' * 32, 'expectedRevision': 1}})
    assert permission_id(action) == 'query.saved.read'


def test_console_adapter_uses_authenticated_owner_and_chat_workspace():
    console = Mock()
    console.saved_queries.return_value = []
    services = SimpleNamespace(console=console)
    result = execute_action(services, 'owner-a', WORKSPACE, {'operation': 'list_saved_queries', 'args': {}})
    console.saved_queries.assert_called_once_with('owner-a', WORKSPACE)
    assert result == {'queries': []}


def test_console_settings_use_existing_revision_bound_service():
    console = Mock()
    console.update_settings.return_value = {'revision': 3, 'rowPageSize': 200}
    result = execute_action(SimpleNamespace(console=console), 'owner-a', WORKSPACE,
        {'operation': 'update_console_settings', 'args': {'body': {'expectedRevision': 2, 'rowPageSize': 200}}})
    console.update_settings.assert_called_once_with('owner-a', 2, 200)
    assert result['revision'] == 3


def test_all_registered_operations_have_provider_schemas_and_permissions():
    schema = AppAction.model_json_schema()
    assert set(schema['discriminator']['mapping']) == set(OPERATIONS)
    assert len(OPERATIONS) == 37
    assert 'password' not in str(schema)


def test_deleting_chat_workspace_cannot_cascade_its_approval_receipt():
    from schemii.common.api.errors import ApiProblem
    workspaces = Mock()
    with pytest.raises(ApiProblem) as caught:
        execute_action(SimpleNamespace(workspaces=workspaces), 'owner-a', WORKSPACE,
                       {'operation':'delete_workspace','args':{'workspaceId':WORKSPACE,'expectedRevision':1}})
    assert caught.value.code == 'ai_current_workspace_delete'
    workspaces.delete.assert_not_called()


def test_another_workspace_can_be_deleted_with_its_reviewed_revision():
    target = 'ws_'+'b'*32
    workspaces = Mock()
    result = execute_action(SimpleNamespace(workspaces=workspaces), 'owner-a', WORKSPACE,
                           {'operation':'delete_workspace','args':{'workspaceId':target,'expectedRevision':3}})
    workspaces.delete.assert_called_once_with('owner-a', target, 3)
    assert result['httpStatus'] == 204
