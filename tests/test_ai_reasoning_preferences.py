"""Reasoning is a persisted model preference, separate from permission policy."""
from unittest.mock import Mock
import pytest
from schemii.common.ai.pi import PiError, PiReply
from schemii.schemii.ai.models import AiCapabilities, SchemiiAiPreferencesUpdate
from schemii.schemii.ai.service import AiServiceError
from test_ai_model_preferences import conversation
from test_ai_app_lifecycle import fixture
from test_ai_read_workflow import setup as read_setup, run as read_run
from test_schemoo_ai_conversations import setup as moo_setup


def test_reasoning_roundtrips_and_omitted_update_preserves_existing_choice():
    repo, chat, turn, proposal = conversation()
    repo.finish_turn("owner", chat.id, turn.id, "Ready")
    settings, saved, _ = repo.save_preferences('owner', chat.id, 1, chat.revision, chat.provider_id, chat.model_id, chat.capabilities, 'high')
    assert saved.id == chat.id and saved.reasoning_effort == 'high'
    assert settings.default_reasoning_effort == 'high'
    assert repo.get_proposal('owner', chat.id, proposal.id).status == 'pending'
    settings2, saved2, _ = repo.save_preferences('owner', chat.id, settings.revision, saved.revision, chat.provider_id, chat.model_id, chat.capabilities)
    assert saved2.reasoning_effort == settings2.default_reasoning_effort == 'high'
    defaults = repo.update_settings('owner', settings2.revision, True, chat.provider_id, chat.model_id, chat.capabilities)
    assert defaults.default_reasoning_effort == 'high'


def test_schemii_service_rejects_unadvertised_reasoning_before_saving():
    service, chat, turn, _ = fixture({})
    service.repository.finish_turn("owner-a", chat.id, turn.id, "Ready")
    service.status = Mock(return_value={'providers':[{'id':chat.provider_id,'available':True,'models':[{'id':chat.model_id,'status':'active','reasoningLevels':['default','low','high']}]}]})
    request = SchemiiAiPreferencesUpdate(expected_settings_revision=1, expected_chat_revision=1,
        provider_id=chat.provider_id, model_id=chat.model_id, capabilities=AiCapabilities(), reasoning_effort='max')
    with pytest.raises(AiServiceError) as caught:
        service.save_preferences('owner-a', chat.id, request)
    assert caught.value.code == 'ai_reasoning_unavailable'
    assert service.repository.get_chat('owner-a', chat.id).reasoning_effort == 'default'
    updated = service.save_preferences('owner-a', chat.id, request.model_copy(update={'reasoning_effort':'high'}))
    assert updated.chat.reasoning_effort == updated.settings.default_reasoning_effort == 'high'


def test_schemii_tool_loop_passes_reasoning_on_every_inference():
    service, chat, turn = read_setup()
    chat.reasoning_effort = 'high'
    read_run(service, chat, turn)
    assert len(service.runtime.run.call_args_list) == 2
    assert all(call.kwargs['reasoning_effort'] == 'high' for call in service.runtime.run.call_args_list)


def test_schemoo_reasoning_persists_and_reaches_runtime():
    service, store, runtime, adapter, chat = moo_setup([PiReply('Done', ())])
    runtime.require_reasoning_effort = Mock()
    updated = service.preferences('alice', chat['id'], {'expectedRevision':chat['revision'],'providerId':chat['providerId'],'aiModelId':chat['aiModelId'],'reasoningEffort':'high'})
    assert updated['reasoningEffort'] == 'high'
    unchanged = service.preferences('alice', chat['id'], {'expectedRevision':updated['revision'],'providerId':chat['providerId'],'aiModelId':chat['aiModelId']})
    assert unchanged['reasoningEffort'] == 'high'
    runtime.run = Mock(return_value=PiReply('Done', ()))
    sent = service.send('alice', chat['id'], {'text':'hello','expectedRevision':unchanged['revision']})
    service.run('alice', chat['id'], sent['turnId'])
    assert runtime.run.call_args.kwargs['reasoning_effort'] == 'high'
    service.settings('alice', {'providerId':chat['providerId'],'aiModelId':chat['aiModelId'],'reasoningEffort':'high'})
    assert service.settings('alice')['reasoningEffort'] == 'high'
    assert service.settings('alice', {'modes':{}})['reasoningEffort'] == 'high'


def test_schemoo_unsupported_level_is_rejected_before_update():
    service, store, runtime, adapter, chat = moo_setup([])
    runtime.require_reasoning_effort = Mock(side_effect=PiError('reasoning_unsupported',status=422))
    from schemii.common.api.errors import ApiProblem
    with pytest.raises(ApiProblem):
        service.preferences('alice', chat['id'], {'expectedRevision':chat['revision'],'providerId':chat['providerId'],'aiModelId':chat['aiModelId'],'reasoningEffort':'max'})
    assert store.get('alice',chat['id'])['reasoningEffort'] == 'default'


def test_postgres_reasoning_column_roundtrip_and_old_row_default():
    from contextlib import nullcontext
    from unittest.mock import MagicMock
    from schemii.schemii.ai.repository import PostgresAiRepository
    repo, chat, _, _ = conversation()
    old = chat.model_dump()
    old.pop('reasoning_effort')
    old['status'] = 'idle'
    assert PostgresAiRepository._chat(old).reasoning_effort == 'default'
    updated = {**old,'revision':2,'reasoning_effort':'max'}
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [None,old,updated,{'revision':2,'enabled':True,
        'default_provider_id':chat.provider_id,'default_model_id':chat.model_id,
        'default_reasoning_effort':'max','capabilities':chat.capabilities.model_dump()}]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.transaction.return_value = nullcontext()
    postgres = PostgresAiRepository(lambda:connection)
    postgres._cleanup = lambda cursor:None
    settings, saved, _ = postgres.save_preferences('owner',chat.id,1,1,chat.provider_id,chat.model_id,chat.capabilities,'max')
    assert saved.reasoning_effort == settings.default_reasoning_effort == 'max'
    writes = [(call.args[0],call.args[1]) for call in cursor.execute.call_args_list if call.args[0].startswith(('UPDATE','INSERT'))]
    assert any('reasoning_effort=%s' in sql and values[2]=='max' for sql,values in writes)
    assert any('default_reasoning_effort' in sql and values[-1]=='max' for sql,values in writes)
    # A fresh repository reads the persisted preference, not in-process state.
    cursor.fetchone.side_effect = [updated]
    assert PostgresAiRepository(lambda:connection).get_chat('owner',chat.id).reasoning_effort == 'max'


def test_schemoo_reasoning_reaches_tool_round_and_final_synthesis():
    from test_schemoo_ai_conversations import call
    from schemii.common.admin_config import AiPolicy
    service, store, runtime, adapter, chat = moo_setup([call('inspect'),PiReply('Complete',())],policy=AiPolicy(maximum_tool_rounds=1))
    runtime.require_reasoning_effort = Mock()
    runtime.run = Mock(wraps=runtime.run)
    chat = service.preferences('alice',chat['id'],{'expectedRevision':chat['revision'],'providerId':chat['providerId'],'aiModelId':chat['aiModelId'],'reasoningEffort':'low'})
    sent = service.send('alice',chat['id'],{'text':'inspect','expectedRevision':chat['revision']})
    service.run('alice',chat['id'],sent['turnId'])
    assert len(runtime.run.call_args_list) == 2
    assert all(call.kwargs['reasoning_effort']=='low' for call in runtime.run.call_args_list)


def test_schemii_permission_updates_work_offline_with_unchanged_reasoning():
    from schemii.schemii.ai.models import SchemiiAiSettingsUpdate
    service, chat, turn, _ = fixture({})
    service.repository.finish_turn('owner-a',chat.id,turn.id,'Ready')
    settings, chat, _ = service.repository.save_preferences('owner-a',chat.id,1,chat.revision,chat.provider_id,chat.model_id,chat.capabilities,'high')
    service.status = Mock(side_effect=AssertionError('Permission-only update must not query provider availability'))
    request = SchemiiAiPreferencesUpdate(expected_settings_revision=settings.revision,expected_chat_revision=chat.revision,
        provider_id=chat.provider_id,model_id=chat.model_id,capabilities=AiCapabilities(action_modes={'query.read':'ask'}))
    saved = service.save_preferences('owner-a',chat.id,request)
    assert saved.chat.reasoning_effort == 'high'
    assert saved.chat.capabilities.action_modes['query.read'] == 'ask'
    defaults = service.update_settings('owner-a',SchemiiAiSettingsUpdate(expected_revision=saved.settings.revision,enabled=True,
        default_provider_id=chat.provider_id,default_model_id=chat.model_id,default_capabilities=AiCapabilities()))
    assert defaults.default_reasoning_effort == 'high'
    service.status.assert_not_called()


def test_schemoo_permission_updates_work_offline_with_unchanged_reasoning():
    service, store, runtime, adapter, chat = moo_setup([])
    runtime.require_reasoning_effort = Mock()
    chat = service.preferences('alice',chat['id'],{'expectedRevision':chat['revision'],'providerId':chat['providerId'],'aiModelId':chat['aiModelId'],'reasoningEffort':'high'})
    service.settings('alice',{'providerId':chat['providerId'],'aiModelId':chat['aiModelId'],'reasoningEffort':'high'})
    runtime.require_reasoning_effort = Mock(side_effect=AssertionError('Permission-only update must not query provider availability'))
    saved = service.preferences('alice',chat['id'],{'expectedRevision':chat['revision'],'providerId':chat['providerId'],'aiModelId':chat['aiModelId'],'modes':{'read':'disabled'}})
    assert saved['reasoningEffort'] == 'high'
    assert saved['modes']['read'] == 'disabled'
    assert service.settings('alice',{'modes':{'read':'disabled'}})['reasoningEffort'] == 'high'
    runtime.require_reasoning_effort.assert_not_called()
