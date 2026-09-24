"""Security regression checks for account sessions and role boundaries."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from schemii.common.api.errors import install_api_error_handlers
from schemii.common.auth.middleware import AuthenticationMiddleware
from schemii.common.auth.routes import router
from schemii.common.auth.service import AuthService, COOKIE
from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID


@pytest.fixture
def app():
    application=FastAPI()
    application.state.auth=AuthService(enabled=True,setup_token='setup-secret')
    application.include_router(router)
    install_api_error_handlers(application)
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


def test_rejected_sign_in_is_actionable_and_does_not_reveal_account_existence(client):
    bootstrap(client)
    wrong=client.post('/api/v1/auth/login',json=dict(username='admin',password='incorrect-password'))
    unknown=client.post('/api/v1/auth/login',json=dict(username='missing',password='incorrect-password'))
    for response in (wrong,unknown):
        assert response.status_code==401
        problem=response.json()['error']
        assert problem['requestId']==response.headers['x-request-id']
        assert {key:value for key,value in problem.items() if key!='requestId'} == {
            'code':'invalid_credentials',
            'message':'Incorrect username or password. Check both fields and try again.',
            'retryable':False,
            'details':{},
        }
        assert 'incorrect-password' not in response.text
    assert client.post('/api/v1/auth/login',json=dict(
        username='admin',password='long-password-123')).status_code==200


def test_login_attempts_are_bounded(client):
    bootstrap(client)
    for _ in range(10):
        assert client.post('/api/v1/auth/login',json=dict(username='admin',password='wrong')).status_code==401
    limited=client.post('/api/v1/auth/login',json=dict(username='admin',password='wrong'))
    assert limited.status_code==429
    assert limited.json()['error']['code']=='sign_in_rate_limited'
    assert limited.json()['error']['message']=='Too many sign-in attempts. Try again in 15 minutes.'


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
    for path in ('/system-map','/api-map','/db-map'):
        assert client.get(path).status_code==403
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


def test_admin_assigns_user_roles_without_changing_role_scopes(client,app):
    from types import SimpleNamespace
    app.state.services=SimpleNamespace()
    bootstrap(client)
    viewer_role=client.post('/api/v1/admin/roles',json=dict(
        name='View reports',capabilities=['schemer:access'])).json()
    author_role=client.post('/api/v1/admin/roles',json=dict(
        name='Author reports',capabilities=['schemer:access','schemer:author'])).json()
    created=client.post('/api/v1/admin/accounts',json=dict(
        username='assigned',display_name='Assigned',password='assigned-password-123',
        role_ids=[viewer_role['id']]))
    assert created.status_code==201
    user=created.json()
    assert app.state.auth.capabilities(user['id'])==['schemer:access']
    assert client.post('/api/v1/admin/accounts',json=dict(
        username='invalid-role',display_name='Invalid',password='invalid-password-123',
        role_ids=['role_application_provisioners'])).status_code==422
    assert client.patch('/api/v1/admin/accounts/'+user['id'],json={
        'role_ids':[author_role['id']]}).status_code==200
    assert app.state.auth.capabilities(user['id'])==['schemer:access','schemer:author']
    roles={role['id']:role for role in client.get('/api/v1/admin/roles').json()}
    assert roles[viewer_role['id']]['user_ids']==[]
    assert roles[author_role['id']]['user_ids']==[user['id']]
    assert roles[author_role['id']]['capabilities']==['schemer:access','schemer:author']
    assert client.patch('/api/v1/admin/accounts/'+user['id'],json={
        'role_ids':['role_missing']}).status_code==422
    assert app.state.auth.capabilities(user['id'])==['schemer:access','schemer:author']


def test_direct_app_access_needs_no_database_and_is_separate_from_roles(client,app):
    from types import SimpleNamespace
    app.state.services=SimpleNamespace()
    bootstrap(client)
    created=client.post('/api/v1/admin/accounts',json=dict(
        username='direct',display_name='Direct',password='direct-password-123',
        direct_access={'capabilities':['schemii:access']}))
    assert created.status_code==201
    user=created.json()
    row=next(item for item in client.get('/api/v1/admin/accounts').json() if item['id']==user['id'])
    assert row['role_ids']==[]
    assert row['direct_access']=={'capabilities':['schemii:access'],'connections':[],'dashboards':[]}
    assert all(role['id']!='role_personal_'+user['id'] for role in client.get('/api/v1/admin/roles').json())
    admin_cookie=client.cookies.get(COOKIE)
    assert client.post('/api/v1/auth/login',json=dict(
        username='direct',password='direct-password-123')).status_code==200
    user_cookie=client.cookies.get(COOKIE)
    assert client.get('/api/v1/schemii/workspaces').status_code==200
    assert client.get('/api/v1/schemoo/models').status_code==403
    client.cookies.set(COOKIE,admin_cookie)
    assert client.patch('/api/v1/admin/accounts/'+user['id'],json={
        'direct_access':{'capabilities':['schemer:access']}}).status_code==200
    assert client.put('/api/v1/admin/roles/role_personal_'+user['id'],json={
        'name':'Forged', 'capabilities':['schemii:access']}).status_code==403
    client.cookies.set(COOKIE,user_cookie)
    assert client.get('/api/v1/schemii/workspaces').status_code==403
    assert client.get('/api/v1/schemer/dashboards').status_code==200
    assert client.post('/api/v1/schemer/dashboards').status_code==403


def test_admin_removes_account_sessions_and_memberships(client,app):
    from types import SimpleNamespace
    app.state.services=SimpleNamespace()
    bootstrap(client)
    assert client.delete('/api/v1/admin/accounts/user_local_prototype').status_code==409
    user=client.post('/api/v1/admin/accounts',json=dict(
        username='temporary',display_name='Temporary',password='temporary-password-123')).json()
    role=client.post('/api/v1/admin/roles',json=dict(
        name='Temporary report access',capabilities=['schemer:access'],user_ids=[user['id']])).json()
    admin_cookie=client.cookies.get(COOKIE)
    assert client.post('/api/v1/auth/login',json=dict(
        username='temporary',password='temporary-password-123')).status_code==200
    temporary_cookie=client.cookies.get(COOKIE)
    client.cookies.set(COOKIE,admin_cookie)
    assert client.delete('/api/v1/admin/accounts/'+user['id']).status_code==204
    assert app.state.auth.resolve(temporary_cookie) is None
    assert all(item['id'] != user['id'] for item in client.get('/api/v1/admin/accounts').json())
    assert user['id'] not in next(item for item in client.get('/api/v1/admin/roles').json()
                                  if item['id']==role['id'])['user_ids']
    assert client.delete('/api/v1/admin/accounts/'+user['id']).status_code==404


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
            user_ids=[user['id']],connections=[dict(connection_id='pg_shared',owner_id=SCHEMII_CONNECTION_OWNER_ID,allow_authoring=True)],dashboards=[])
    access=app.state.auth.connection_access
    assert access(user['id'],'pg_shared','schemoo') is None
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['profile']['capabilities']=['schemoo:access']
    assert access(user['id'],'pg_shared','schemoo')['owner_id']==SCHEMII_CONNECTION_OWNER_ID
    assert access(user['id'],'pg_shared','schemii') is None
    assert access(user['id'],'pg_shared','schemer') is None
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['profile']['capabilities']=['schemer:access']
    assert access(user['id'],'pg_shared','schemer') is None
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['profile']['capabilities']=['schemer:access','schemer:author']
    assert access(user['id'],'pg_shared','schemer')['owner_id']==SCHEMII_CONNECTION_OWNER_ID
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['other']=dict(id='other',name='Legacy person-owned grant',capabilities=['schemer:access','schemer:author'],
            user_ids=[user['id']],connections=[dict(connection_id='pg_shared',owner_id='owner_b',allow_authoring=True)],dashboards=[])
    assert access(user['id'],'pg_shared','schemer')['owner_id']==SCHEMII_CONNECTION_OWNER_ID
    assert all(grant['owner_id']==SCHEMII_CONNECTION_OWNER_ID for grant in app.state.auth.connection_grants(user['id']))


def test_provisioner_can_bind_authoring_profile_to_product_role(client,app):
    from types import SimpleNamespace
    bootstrap(client)
    app.state.services=SimpleNamespace(connections=SimpleNamespace(get=lambda owner, connection: SimpleNamespace(ownership='schemii')))
    user=client.post('/api/v1/admin/accounts',json=dict(username='analyst',display_name='Analyst',password='analyst-password-123')).json()
    role=client.post('/api/v1/admin/roles',json=dict(
        name='Organization modeling',capabilities=['schemoo:access'],user_ids=[user['id']],
        connections=[dict(connection_id='pg_organization',owner_id=SCHEMII_CONNECTION_OWNER_ID,allow_authoring=True)],
    ))
    assert role.status_code==201
    assert app.state.auth.connection_access(user['id'],'pg_organization','schemoo')['role_id']==role.json()['id']


def test_admin_cannot_share_other_users_private_credentials(client,app):
    from types import SimpleNamespace
    app.state.services=SimpleNamespace(connections=SimpleNamespace(get=lambda *args: SimpleNamespace(ownership='user')))
    bootstrap(client)
    private=dict(connection_id='pg_private',owner_id='another_user')
    response=client.post('/api/v1/admin/roles',json=dict(name='Private access',connections=[private]))
    assert response.status_code==422
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['existing']=dict(id='existing',name='Already managed',capabilities=[],user_ids=[],connections=[{**private,'allow_authoring':False}],dashboards=[])
    response=client.post('/api/v1/admin/roles',json=dict(name='Reused access',connections=[private]))
    assert response.status_code==422
    # The reserved owner alone is insufficient if the stored profile is not marked Schemii-owned.
    response=client.post('/api/v1/admin/roles',json=dict(name='Forged pool access',connections=[dict(
        connection_id='pg_private',owner_id=SCHEMII_CONNECTION_OWNER_ID)]))
    assert response.status_code==422


def test_role_connection_dependencies_explain_blocked_deletion(client,app):
    from schemii.common.auth.dependencies import AccountConnectionDependencies
    bootstrap(client)
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['existing']=dict(id='existing',name='Report viewers',capabilities=[],user_ids=[],connections=[dict(connection_id='pg_managed',owner_id=SCHEMII_CONNECTION_OWNER_ID,allow_authoring=False)],dashboards=[])
    provider=AccountConnectionDependencies(app.state.auth)
    assert provider.count_for_connection(SCHEMII_CONNECTION_OWNER_ID,'pg_managed')==1
    dependency=provider.dependencies_for_connection(SCHEMII_CONNECTION_OWNER_ID,'pg_managed')[0]
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
        connections=SimpleNamespace(list=lambda owner: [private] if owner==user['id'] else [],
                                    list_schemii_owned=lambda: [managed]),
        dashboards=SimpleNamespace(list=lambda owner: []),
    )
    with app.state.auth.store.transaction(write=True) as state:
        state['roles']['existing']=dict(id='existing',name='Legacy personal grant',capabilities=[],user_ids=[],connections=[dict(connection_id='pg_private',owner_id=user['id'],allow_authoring=False)],dashboards=[])
    response=client.get('/api/v1/admin/resources')
    assert response.status_code==200
    assert [item['id'] for item in response.json()['connections']]==['pg_managed']
    assert response.json()['connections'][0]['ownership']=='schemii'
    assert 'private_login' not in response.text
