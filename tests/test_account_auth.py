"""Security regression checks for account sessions and role boundaries."""
from fastapi import FastAPI, HTTPException
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
    @application.get('/api/v1/schemii/workspaces')
    def workspaces(): return []
    @application.get('/api/v1/schemoo/models')
    def models(): return []
    @application.get('/api/v1/schemer/dashboards')
    def dashboards(): return []
    @application.post('/api/v1/schemer/dashboards')
    def create_dashboard(): return {'ok':True}
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
    assert client.get('/api/v1/private').status_code==403
    assert client.get('/api/v1/schemii/workspaces').status_code==200
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
    assert client.get('/api/v1/schemer/dashboards').status_code==403
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
    role=client.post('/api/v1/admin/roles',json=dict(name='Schema authors',capabilities=['schemii:access'],user_ids=[user['id']])).json()
    admin_cookie=client.cookies.get(COOKIE)
    assert client.post('/api/v1/auth/login',json=dict(username='author',password='author-password-123')).status_code==200
    author_cookie=client.cookies.get(COOKIE)
    assert client.get('/api/v1/schemii/workspaces').status_code==200
    assert client.get('/api/v1/schemoo/models').status_code==403
    assert client.get('/api/v1/schemer/dashboards').status_code==403
    assert client.get('/_developer/inspection').status_code==403
    client.cookies.clear()
    client.cookies.set(COOKIE,admin_cookie)
    assert client.delete('/api/v1/admin/roles/'+role['id']).status_code==204
    client.cookies.clear()
    client.cookies.set(COOKIE,author_cookie)
    assert client.get('/api/v1/schemii/workspaces').status_code==403


def test_role_rejects_unbound_dashboard_and_authoring_without_product(client):
    bootstrap(client)
    grant=dict(dashboard_id='dashboard_x',owner_id='user_x',connection_id='pg_x',connection_owner_id='user_x')
    assert client.post('/api/v1/admin/roles',json=dict(name='Invalid',dashboards=[grant])).status_code==422
    assert client.post('/api/v1/admin/roles',json=dict(name='Invalid',connections=[dict(connection_id='pg_x',owner_id='user_x',allow_authoring=True)])).status_code==422
    assert client.post('/api/v1/admin/roles',json=dict(name='Invalid',capabilities=['schemer:author'])).status_code==422


def test_provisioner_has_no_implicit_product_access(client):
    bootstrap(client)
    provisioner=client.post('/api/v1/admin/accounts',json=dict(username='provisioner',display_name='Provisioner',password='provision-password-123',is_admin=True)).json()
    assert provisioner['is_admin']
    roles=client.get('/api/v1/admin/roles').json()
    assert all(role['id'] != 'role_application_provisioners' for role in roles)
    assert client.delete('/api/v1/admin/roles/role_application_provisioners').status_code==403
    assert client.post('/api/v1/admin/roles',json=dict(name='Escalation',capabilities=['accounts:provision'])).status_code==422
    assert client.post('/api/v1/auth/login',json=dict(username='provisioner',password='provision-password-123')).status_code==200
    assert client.get('/api/v1/auth/me').json()['capabilities']==['accounts:provision']
    assert client.get('/api/v1/admin/accounts').status_code==200
    assert client.get('/api/v1/schemii/workspaces').status_code==403
    assert client.get('/api/v1/schemoo/models').status_code==403
    assert client.get('/api/v1/schemer/dashboards').status_code==403


def test_provisioner_revocation_applies_to_existing_session(client):
    bootstrap(client)
    provisioner=client.post('/api/v1/admin/accounts',json=dict(username='operator',display_name='Operator',password='operator-password-123',is_admin=True)).json()
    bootstrap_cookie=client.cookies.get(COOKIE)
    client.post('/api/v1/auth/login',json=dict(username='operator',password='operator-password-123'))
    operator_cookie=client.cookies.get(COOKIE)
    assert client.get('/api/v1/admin/accounts').status_code==200
    client.cookies.set(COOKIE,bootstrap_cookie)
    assert client.patch('/api/v1/admin/accounts/'+provisioner['id'],json={'is_admin':False}).status_code==200
    client.cookies.set(COOKIE,operator_cookie)
    assert client.get('/api/v1/admin/accounts').status_code==403
    assert client.get('/api/v1/auth/me').json()['capabilities']==[]


def test_schemer_viewer_cannot_edit_and_author_can(client,app):
    app.state.services=type('Services',(),{})()
    bootstrap(client)
    user=client.post('/api/v1/admin/accounts',json=dict(username='reporter',display_name='Reporter',password='report-password-123')).json()
    role=client.post('/api/v1/admin/roles',json=dict(name='Report access',capabilities=['schemer:access'],user_ids=[user['id']])).json()
    admin_cookie=client.cookies.get(COOKIE)
    client.post('/api/v1/auth/login',json=dict(username='reporter',password='report-password-123'))
    viewer_cookie=client.cookies.get(COOKIE)
    assert client.get('/api/v1/schemer/dashboards').status_code==200
    assert client.post('/api/v1/schemer/dashboards').status_code==403
    client.cookies.set(COOKIE,admin_cookie)
    assert client.put('/api/v1/admin/roles/'+role['id'],json=dict(name='Report access',capabilities=['schemer:access','schemer:author'],user_ids=[user['id']])).status_code==200
    client.cookies.set(COOKIE,viewer_cookie)
    assert client.post('/api/v1/schemer/dashboards').status_code==200


def test_managed_connection_requires_same_role_product_and_authoring_grant(client,app):
    bootstrap(client)
    user=client.post('/api/v1/admin/accounts',json=dict(username='modeler',display_name='Modeler',password='modeler-password-123')).json()
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['product']=dict(id='product',name='Model access',capabilities=['schemoo:access'],
            user_ids=[user['id']],connections=[],dashboards=[])
        state['roles']['profile']=dict(id='profile',name='Managed profile',capabilities=[],
            user_ids=[user['id']],connections=[dict(connection_id='pg_shared',owner_id='owner_a',allow_authoring=True)],dashboards=[])
    access=app.state.auth.connection_access
    assert access(user['id'],'pg_shared','schemoo') is None
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['profile']['capabilities']=['schemoo:access']
    assert access(user['id'],'pg_shared','schemoo')['owner_id']=='owner_a'
    assert access(user['id'],'pg_shared','schemii') is None
    assert access(user['id'],'pg_shared','schemer') is None
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['profile']['capabilities']=['schemer:access']
    assert access(user['id'],'pg_shared','schemer') is None
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['profile']['capabilities']=['schemer:access','schemer:author']
    assert access(user['id'],'pg_shared','schemer')['owner_id']=='owner_a'
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['other']=dict(id='other',name='Other identity',capabilities=['schemer:access','schemer:author'],
            user_ids=[user['id']],connections=[dict(connection_id='pg_shared',owner_id='owner_b',allow_authoring=True)],dashboards=[])
    with pytest.raises(HTTPException) as failure:
        access(user['id'],'pg_shared','schemer')
    assert failure.value.status_code==409


def test_provisioner_can_bind_authoring_profile_to_product_role(client,app):
    from types import SimpleNamespace
    bootstrap(client)
    app.state.services=SimpleNamespace(connections=SimpleNamespace(get=lambda owner, connection: object()))
    user=client.post('/api/v1/admin/accounts',json=dict(username='analyst',display_name='Analyst',password='analyst-password-123')).json()
    role=client.post('/api/v1/admin/roles',json=dict(
        name='Organization modeling',capabilities=['schemoo:access'],user_ids=[user['id']],
        connections=[dict(connection_id='pg_organization',owner_id='user_local_prototype',allow_authoring=True)],
    ))
    assert role.status_code==201
    assert app.state.auth.connection_access(user['id'],'pg_organization','schemoo')['role_id']==role.json()['id']


def test_admin_cannot_share_other_users_private_credentials(client,app):
    from types import SimpleNamespace
    app.state.services=SimpleNamespace(connections=SimpleNamespace(get=lambda *args: object()))
    bootstrap(client)
    private=dict(connection_id='pg_private',owner_id='another_user')
    response=client.post('/api/v1/admin/roles',json=dict(name='Private access',connections=[private]))
    assert response.status_code==403
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['existing']=dict(id='existing',name='Already managed',capabilities=[],user_ids=[],connections=[{**private,'allow_authoring':False}],dashboards=[])
    response=client.post('/api/v1/admin/roles',json=dict(name='Reused access',connections=[private]))
    assert response.status_code==201


def test_role_connection_dependencies_explain_blocked_deletion(client,app):
    from schemii.common.auth.dependencies import AccountConnectionDependencies
    bootstrap(client)
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['existing']=dict(id='existing',name='Report viewers',capabilities=[],user_ids=[],connections=[dict(connection_id='pg_managed',owner_id='user_local_prototype',allow_authoring=False)],dashboards=[])
    provider=AccountConnectionDependencies(app.state.auth)
    assert provider.count_for_connection('user_local_prototype','pg_managed')==1
    dependency=provider.dependencies_for_connection('user_local_prototype','pg_managed')[0]
    assert dependency.name=='Report viewers'
    assert dependency.deletion_blocked
    assert provider.count_for_connection('another_user','pg_managed')==0


def test_admin_inventory_excludes_private_profiles(client,app):
    from types import SimpleNamespace
    from schemii.common.auth.resources import router as resources_router
    app.include_router(resources_router)
    bootstrap(client)
    user=client.post('/api/v1/admin/accounts',json=dict(username='private_owner',display_name='Private owner',password='private-password-123')).json()
    private=SimpleNamespace(id='pg_private',name='Private',database='private_db',username='private_login')
    managed=SimpleNamespace(id='pg_managed',name='Managed',database='report_db',username='report_login')
    app.state.services=SimpleNamespace(
        connections=SimpleNamespace(list=lambda owner: [private,managed] if owner==user['id'] else []),
        dashboards=SimpleNamespace(list=lambda owner: []),
    )
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['existing']=dict(id='existing',name='Managed',capabilities=[],user_ids=[],connections=[dict(connection_id='pg_managed',owner_id=user['id'],allow_authoring=False)],dashboards=[])
    response=client.get('/api/v1/admin/resources')
    assert response.status_code==200
    assert [item['id'] for item in response.json()['connections']]==['pg_managed']
    assert 'private_login' not in response.text
