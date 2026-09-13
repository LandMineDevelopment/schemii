"""A terminal failed turn permits a fresh user prompt without losing history."""
from types import SimpleNamespace
import pytest
from schemii.common.ai.pi import PiError, PiReply
from schemii.schemii.ai.repository import AiConflictError
from test_ai_app_lifecycle import fixture
from test_schemoo_ai_conversations import setup, send


@pytest.mark.parametrize('claimed',[False,True])
def test_schemii_failed_turn_allows_followup_and_preserves_prior_failure(claimed):
    service, chat, turn, _ = fixture({})
    if claimed:
        service.repository.claim_turn('owner-a',chat.id,turn.id)
    before = service.repository.list_messages('owner-a',chat.id,100)
    service.repository.fail_turn('owner-a',chat.id,turn.id,'provider_failure','Provider request failed')
    assert service.repository.get_chat('owner-a',chat.id).status == 'failed'
    service.status = lambda owner:{'providers':[{'id':chat.provider_id,'available':True,'models':[{'id':chat.model_id,'status':'active'}]}]}
    followup = service.send('owner-a',chat.id,SimpleNamespace(expected_chat_revision=chat.revision,
        expected_design_revision=1,text='Please try a different approach',result_context_operation_id=None))
    new_turn = followup[0] if isinstance(followup,tuple) else followup
    assert new_turn.id != turn.id and new_turn.status == 'queued'
    assert service.repository.get_chat('owner-a',chat.id).status == 'working'
    old = service.repository.get_turn('owner-a',chat.id,turn.id)
    assert old.status == 'failed' and old.error_code == 'provider_failure'
    history = service.repository.list_messages('owner-a',chat.id,100)
    assert history[:-1] == before and history[-1].text == 'Please try a different approach'
    with pytest.raises(AiConflictError):
        service.repository.create_turn('owner-a',chat.id,'Duplicate',None,10,10,100)


def test_schemoo_failed_turn_allows_distinct_followup_and_preserves_history():
    conversations,store,runtime,adapter,chat = setup([PiError('provider_failure'),PiReply('Recovered successfully',())])
    failed = send(conversations,chat,'First question')
    assert failed['status'] == 'failed'
    assert not conversations.active
    recovered = send(conversations,failed,'A different followup')
    assert recovered['status'] == 'idle' and recovered['error'] is None
    assert [message['text'] for message in recovered['messages'] if message['role']=='user'] == ['First question','A different followup']
    assert recovered['messages'][-1]['text'] == 'Recovered successfully'
