"""Application and COPY actions use the real proposal/operation lifecycle."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from schemii.common.admin_config import AdminConfig
from schemii.schemii.ai.action_policy import requires_approval
from schemii.schemii.ai.models import AiCapabilities, SchemiiProposalExecutionCreate
from schemii.schemii.ai.repository import InMemoryAiRepository
from schemii.schemii.ai.service import AiService, AiServiceError
from schemii.common.api.errors import ApiProblem
from test_raw_console import policy_fixture

OWNER = 'owner-a'
WORKSPACE = 'ws_'+'a'*32


def fixture(modes):
    repository = InMemoryAiRepository()
    chat = repository.create_chat(OWNER, WORKSPACE, 'Actions', 'provider', 'model', AiCapabilities(action_modes=modes))
    turn, _ = repository.create_turn(OWNER, chat.id, 'Update the result preferences', None, 10, 10, 100)
    console = Mock()
    console.update_settings.return_value = {'revision': 2, 'rowPageSize': 50}
    services = SimpleNamespace(admin_config=AdminConfig(), console=console,
        workspaces=SimpleNamespace(get=lambda *args:SimpleNamespace(revision=1)),
        designs=SimpleNamespace(get=lambda *args:SimpleNamespace(revision=1)))
    return AiService(repository, None, services), chat, turn, console


def approve(chat, proposal):
    return SchemiiProposalExecutionCreate(expected_chat_revision=chat.revision,
        expected_proposal_revision=proposal.revision,proposal_digest=proposal.digest,confirmed=True)


def save_settings(service, chat, turn):
    return service._save_tool_proposal(OWNER, chat, turn.id, 'schemii_app_action',
        {'operation':'update_console_settings','args':{'body':{'expectedRevision':1,'rowPageSize':50}}})


def test_ask_application_action_is_pending_until_exact_approval_and_receipt_finishes():
    service, chat, turn, console = fixture({'console.settings.update':'ask'})
    proposal = save_settings(service, chat, turn)
    assert proposal.status == 'pending'
    assert requires_approval(chat.capabilities, proposal.action_type, proposal.details)
    console.update_settings.assert_not_called()
    assert service.repository.list_operations(OWNER, chat.id) == []
    operation = service.execute(OWNER, chat.id, proposal.id, approve(chat, proposal))
    console.update_settings.assert_called_once_with(OWNER, 1, 50)
    assert operation.kind == 'app_action'
    assert operation.status == 'succeeded'
    assert operation.result_summary == {'revision':2,'rowPageSize':50}
    assert service.repository.get_proposal(OWNER, chat.id, proposal.id).status == 'succeeded'


def test_permission_revocation_invalidates_pending_application_approval():
    service, chat, turn, console = fixture({'console.settings.update':'ask'})
    proposal = save_settings(service, chat, turn)
    service.repository.update_chat_policy(OWNER, chat.id, chat.revision, AiCapabilities(action_modes={'console.settings.read':'automatic'}))
    with pytest.raises(AiServiceError) as caught:
        service.execute(OWNER, chat.id, proposal.id, approve(chat, proposal))
    assert caught.value.code == 'ai_chat_changed'
    console.update_settings.assert_not_called()
    assert service.repository.list_operations(OWNER, chat.id) == []


def test_related_application_permission_does_not_authorize_settings_write():
    service, chat, turn, console = fixture({'console.settings.read':'automatic'})
    with pytest.raises(AiServiceError) as caught:
        save_settings(service, chat, turn)
    assert caught.value.code == 'ai_permission_required'
    console.update_settings.assert_not_called()
    assert service.repository.list_proposals(OWNER, chat.id) == []


def test_copy_ticket_survives_turn_completion_but_not_policy_revocation():
    service, chat, turn, _ = fixture({'console.copy_download':'ask'})
    manager, session = policy_fixture()
    sid = 'raw_'+'b'*32
    session['id'] = sid
    manager.get = lambda owner, workspace, session_id, **kwargs: session
    service.raw_console = manager
    proposal = service._save_tool_proposal(OWNER, chat, turn.id, 'schemii_raw_console',
        {'operation':'copy_download','sessionId':sid,'expectedRevision':1,'sql':'COPY things TO STDOUT'})
    operation = service.execute(OWNER, chat.id, proposal.id, approve(chat, proposal))
    assert operation.status == 'succeeded'
    assert operation.result_summary['sqlExecuted'] is False
    ticket = session['tickets'][operation.result_summary['ticketId']]
    manager.authorize_ticket(ticket)
    service.repository.finish_turn(OWNER, chat.id, turn.id, 'Download the prepared file')
    manager.authorize_ticket(ticket)
    service.repository.update_chat_policy(OWNER, chat.id, chat.revision, AiCapabilities())
    with pytest.raises(ApiProblem) as caught:
        manager.authorize_ticket(ticket)
    assert caught.value.code == 'ai_permission_changed'
