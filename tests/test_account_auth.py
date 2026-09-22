"""Security regression checks for account sessions and role boundaries."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from schemii.common.auth.middleware import AuthenticationMiddleware
from schemii.common.auth.routes import router
from schemii.common.auth.service import AuthService, COOKIE


@pytest.fixture
def app():
    application=FastAPI()
    application.state.auth=AuthService(enabled=True,setup_token='setup-secret')
    application.include_router(router)
    application.add_middleware(AuthenticationMiddleware)
    @application.get('/api/v1/private')
    def private(): return {'secret':True}
    @application.get('/api/v1/schemer/dashboards')
    def dashboards(): return []
    return application


@pytest.fixture
def client(app):
    return TestClient(app,base_url='https://localhost:8001',headers={'Origin':'https://localhost:8001'})


def bootstrap(client):
    response=client.post('/api/v1/auth/setup',json=dict(setup_token='setup-secret',username='admin',display_name='Admin',password='long-password-123'))
    assert response.status_code==200
    return response


def test_bootstrap_claims_owner_once_and_secure_cookie(client):
    assert client.get('/api/v1/private').status_code==401
    response=bootstrap(client)
    assert response.json()['user']['id']=='user_local_prototype'
    cookie=response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
    assert client.get('/api/v1/private').status_code==200
    assert client.post('/api/v1/auth/setup',json=dict(setup_token='setup-secret',username='other',display_name='Other',password='long-password-123')).status_code==403


def test_setup_and_login_require_same_origin(client):
    payload=dict(setup_token='setup-secret',username='admin',display_name='Admin',password='long-password-123')
    assert client.post('/api/v1/auth/setup',json=payload,headers={'Origin':'https://evil.invalid'}).status_code==403
    assert client.post('/api/v1/auth/setup',json=payload,headers={'Origin':''}).status_code==403


def test_viewer_default_deny_disable_and_last_admin(client,app):
    bootstrap(client)
    user=client.post('/api/v1/admin/accounts',json=dict(username='viewer',display_name='Viewer',password='viewer-password-123')).json()
    admin_cookie=client.cookies.get(COOKIE)
    assert client.post('/api/v1/auth/login',json=dict(username='viewer',password='viewer-password-123')).status_code==200
    viewer_cookie=client.cookies.get(COOKIE)
    assert client.get('/api/v1/private').status_code==403
    assert client.get('/api/v1/admin/accounts').status_code==403
    assert client.get('/api/v1/schemer/dashboards').status_code==200
    client.cookies.clear()
    client.cookies.set(COOKIE,admin_cookie)
    assert client.patch('/api/v1/admin/accounts/user_local_prototype',json={'disabled':True}).status_code==409
    assert client.patch('/api/v1/admin/accounts/'+user['id'],json={'disabled':True}).status_code==200
    assert app.state.auth.resolve(viewer_cookie) is None


def test_password_change_revokes_sessions_and_logout(client,app):
    bootstrap(client)
    cookie=client.cookies.get(COOKIE)
    assert client.post('/api/v1/auth/change-password',json=dict(current_password='wrong-password',password='changed-password-123')).status_code==403
    assert client.post('/api/v1/auth/change-password',json=dict(current_password='long-password-123',password='changed-password-123')).status_code==200
    assert app.state.auth.resolve(cookie) is None
    assert client.post('/api/v1/auth/login',json=dict(username='admin',password='long-password-123')).status_code==401
    assert client.post('/api/v1/auth/login',json=dict(username='admin',password='changed-password-123')).status_code==200
    assert client.post('/api/v1/auth/logout').status_code==200
    assert client.get('/api/v1/auth/me').status_code==401


def test_login_attempts_are_bounded(client):
    bootstrap(client)
    for _ in range(10):
        assert client.post('/api/v1/auth/login',json=dict(username='admin',password='wrong')).status_code==401
    assert client.post('/api/v1/auth/login',json=dict(username='admin',password='wrong')).status_code==429


def test_role_changes_take_effect_without_new_session(client,app):
    from types import SimpleNamespace
    app.state.services=SimpleNamespace()
    bootstrap(client)
    user=client.post('/api/v1/admin/accounts',json=dict(username='author',display_name='Author',password='author-password-123')).json()
    role=client.post('/api/v1/admin/roles',json=dict(name='Authors',capabilities=['author'],user_ids=[user['id']])).json()
    admin_cookie=client.cookies.get(COOKIE)
    assert client.post('/api/v1/auth/login',json=dict(username='author',password='author-password-123')).status_code==200
    author_cookie=client.cookies.get(COOKIE)
    assert client.get('/api/v1/private').status_code==200
    assert client.get('/_developer/inspection').status_code==403
    client.cookies.clear()
    client.cookies.set(COOKIE,admin_cookie)
    assert client.delete('/api/v1/admin/roles/'+role['id']).status_code==204
    client.cookies.clear()
    client.cookies.set(COOKIE,author_cookie)
    assert client.get('/api/v1/private').status_code==403


def test_role_rejects_unbound_dashboard_and_managed_authoring(client):
    bootstrap(client)
    grant=dict(dashboard_id='dashboard_x',owner_id='user_x',connection_id='pg_x',connection_owner_id='user_x')
    assert client.post('/api/v1/admin/roles',json=dict(name='Invalid',dashboards=[grant])).status_code==422
    assert client.post('/api/v1/admin/roles',json=dict(name='Invalid',connections=[dict(connection_id='pg_x',owner_id='user_x',allow_authoring=True)])).status_code==422
