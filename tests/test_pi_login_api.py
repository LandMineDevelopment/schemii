from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemii.common.ai.credential_store import MemoryAiCredentialStore
from schemii.common.ai.prototype import router
from schemii.common.api.errors import ApiProblem, install_api_error_handlers
from schemii.common.metadata.models import Principal, get_current_principal


class Sidecar:
    def __init__(self):
        self.entries = {}

    def call(self, path, body=None):
        if path == '/health':
            return {'status': 'ok'}
        if path == '/logins':
            key = str(len(self.entries))
            self.entries[key] = {**body, 'id': key, 'status': 'succeeded',
                                 'credential': {'type': 'oauth', 'access': 'SECRET', 'refresh': 'REFRESH'}}
            return self.entries[key]
        entry = self.entries.get(body['id'])
        if not entry or entry['owner'] != body['owner']:
            raise ApiProblem(404, 'pi_login_missing', 'Not found')
        if path == '/logins/cancel':
            del self.entries[body['id']]
        return entry


@pytest.fixture
def setup():
    app = FastAPI()
    app.include_router(router)
    install_api_error_handlers(app)
    store = MemoryAiCredentialStore()
    app.state.services = SimpleNamespace(metadata=SimpleNamespace(ai_credentials=store))
    app.state.pi_client = Sidecar()
    app.dependency_overrides[get_current_principal] = lambda: Principal(user_id='alice', authentication_source='local_prototype')
    return app, TestClient(app), store


def test_success_stores_encrypted_credential_without_browser_disclosure(setup):
    app, client, store = setup
    start = client.post('/api/v1/ai/prototype/login').json()
    response = client.get('/api/v1/ai/prototype/logins/' + start['id'])
    assert response.status_code == 200
    assert response.json() == {'id': start['id'], 'status': 'succeeded'}
    assert store.get('alice', 'codex-prototype')['credential']['access'] == 'SECRET'
    assert 'SECRET' not in str(store._rows)
    assert 'REFRESH' not in client.get('/api/v1/ai/prototype/credentials').text


def test_owner_is_from_principal_not_request(setup):
    app, client, store = setup
    start = client.post('/api/v1/ai/prototype/login?owner=bob').json()
    app.dependency_overrides[get_current_principal] = lambda: Principal(user_id='bob', authentication_source='local_prototype')
    assert client.get('/api/v1/ai/prototype/logins/' + start['id']).status_code == 404
    assert client.get('/api/v1/ai/prototype/credentials').json() == {'credentials': []}


def test_disconnect_fences_late_completion(setup):
    _, client, store = setup
    start = client.post('/api/v1/ai/prototype/login').json()
    assert client.delete('/api/v1/ai/prototype/credentials').status_code == 200
    assert client.get('/api/v1/ai/prototype/logins/' + start['id']).status_code == 409
    assert store.get('alice', 'codex-prototype') is None


def test_new_login_supersedes_old_and_repeated_poll_is_safe(setup):
    _, client, _ = setup
    old = client.post('/api/v1/ai/prototype/login').json()
    new = client.post('/api/v1/ai/prototype/login').json()
    assert client.get('/api/v1/ai/prototype/logins/' + old['id']).status_code == 409
    for _ in range(2):
        assert client.get('/api/v1/ai/prototype/logins/' + new['id']).status_code == 200


def test_disabled_page_and_api(setup):
    app, client, _ = setup
    app.state.pi_client = None
    assert client.get('/ai-prototype').status_code == 503
    assert client.post('/api/v1/ai/prototype/login').status_code == 503


def test_cancel_fences_an_already_fetched_completion(setup):
    app, client, store = setup
    start = client.post('/api/v1/ai/prototype/login').json()
    late = dict(app.state.pi_client.entries[start['id']])
    assert client.delete('/api/v1/ai/prototype/logins/' + start['id']).status_code == 200
    assert not store.save('alice', late['credentialId'], 'openai-codex',
                          late['credential'], late['generation'])
