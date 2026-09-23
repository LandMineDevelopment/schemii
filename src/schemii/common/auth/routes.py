"""Account/session and administrator role management endpoints."""
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID
from schemii.common.metadata.models import get_current_principal
from .service import (COOKIE, SESSION_SECONDS, PROVISIONER_ROLE_ID, PROVISION_CAPABILITY, SCHEMER_AUTHOR,
                      DIRECT_ROLE_PREFIX, direct_role_id, password_matches, public_user)

router = APIRouter(prefix='/api/v1')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AccountCreate(Input):
    username: str = Field(min_length=3,max_length=64,pattern=r'^[a-zA-Z0-9_.@-]+$')
    display_name: str = Field(min_length=1,max_length=128)
    password: str = Field(min_length=12,max_length=256)
    is_admin: bool = False


class Setup(AccountCreate):
    setup_token: str = Field(min_length=1,max_length=512)


class Login(Input):
    username: str = Field(min_length=1,max_length=64)
    password: str = Field(min_length=1,max_length=256)


class PasswordChange(Input):
    current_password: str = Field(min_length=1,max_length=256)
    password: str = Field(min_length=12,max_length=256)


class ConnectionGrant(Input):
    connection_id: str
    owner_id: str
    allow_authoring: bool = False


class DashboardGrant(Input):
    dashboard_id: str
    owner_id: str
    connection_id: str
    connection_owner_id: str
    can_export: bool = False
    can_drill: bool = False


class RoleAccess(Input):
    capabilities: list[Literal['schemii:access','schemoo:access','schemer:access','schemer:author']] = Field(default_factory=list,max_length=4)
    connections: list[ConnectionGrant] = Field(default_factory=list,max_length=1000)
    dashboards: list[DashboardGrant] = Field(default_factory=list,max_length=1000)


class RoleInput(RoleAccess):
    name: str = Field(min_length=1,max_length=128)
    user_ids: list[str] = Field(default_factory=list,max_length=1000)


class AccountProvision(AccountCreate):
    role_ids: list[str] = Field(default_factory=list,max_length=1000)
    direct_access: RoleAccess = Field(default_factory=RoleAccess)


class AccountUpdate(Input):
    display_name: str | None = Field(default=None,min_length=1,max_length=128)
    password: str | None = Field(default=None,min_length=12,max_length=256)
    is_admin: bool | None = None
    disabled: bool | None = None
    role_ids: list[str] | None = Field(default=None,max_length=1000)
    direct_access: RoleAccess | None = None


def auth(request): return request.app.state.auth


def me_document(service,user):
    capabilities=service.capabilities(user['id'])
    return {'user':user,'is_admin':PROVISION_CAPABILITY in capabilities,'capabilities':capabilities}


def set_cookie(response,token):
    response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=True,samesite='lax',path='/')
    response.headers['Cache-Control']='no-store'


def admin(request: Request, principal=Depends(get_current_principal)):
    if not auth(request).enabled or not auth(request).is_admin(principal.user_id): raise HTTPException(403,'Administrator access required')
    return principal.user_id


@router.get('/auth/status')
def status(request: Request):
    service=auth(request)
    return {'enabled':service.enabled,'setup_required':service.enabled and service.setup_required(),'authenticated':bool(service.resolve(request.cookies.get(COOKIE))) if service.enabled else True}


@router.post('/auth/setup')
def setup(data: Setup,request: Request,response: Response):
    service=auth(request)
    if not service.enabled: raise HTTPException(403,'Authentication is disabled')
    service.create_user(data,setup_token=data.setup_token)
    token,user=service.login(data.username,data.password)
    set_cookie(response,token)
    return me_document(service,user)


@router.post('/auth/login')
def login(data: Login,request: Request,response: Response):
    if not auth(request).enabled: raise HTTPException(403,'Authentication is disabled')
    token,user=auth(request).login(data.username,data.password)
    set_cookie(response,token)
    return me_document(auth(request),user)


@router.post('/auth/logout')
def logout(request: Request,response: Response):
    auth(request).logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='lax')
    return {'ok':True}


@router.get('/auth/me')
def me(request: Request,principal=Depends(get_current_principal)):
    user=auth(request).user(principal.user_id)
    if not user and not auth(request).enabled:
        user=dict(id=principal.user_id,username='local',display_name='Local user',is_admin=True,disabled=False)
    if not user: raise HTTPException(401,'Sign in required')
    return me_document(auth(request),user)


@router.post('/auth/change-password')
def change_password(data:PasswordChange,request:Request,principal=Depends(get_current_principal)):
    service=auth(request)
    with service.store.transaction(write=True) as state:
        user=state['users'].get(principal.user_id)
        if not user or not password_matches(data.current_password,user['password_hash']): raise HTTPException(403,'Current password is incorrect')
        # Keep check and mutation in one transaction to prevent concurrent password resets.
        from .service import password_hash
        user['password_hash']=password_hash(data.password)
        state['sessions']={k:s for k,s in state['sessions'].items() if s['user_id'] != principal.user_id}
        state['audit'].append((principal.user_id,'account.password',principal.user_id))
    return {'ok':True}


@router.get('/admin/accounts')
def accounts(request:Request,actor=Depends(admin)):
    with auth(request).store.transaction() as state:
        result=[]
        for user in state['users'].values():
            memberships=[role for role in state['roles'].values() if user['id'] in role['user_ids']]
            direct=state['roles'].get(direct_role_id(user['id']))
            result.append({**public_user(user),
                'role_ids':[role['id'] for role in memberships if role['id'] != PROVISIONER_ROLE_ID
                            and not role['id'].startswith(DIRECT_ROLE_PREFIX)],
                'direct_access':{key:direct[key] if direct else [] for key in ('capabilities','connections','dashboards')},
                'effective_capabilities':sorted({capability for role in memberships for capability in role['capabilities']})})
        return result


@router.post('/admin/accounts',status_code=201)
def create_account(data:AccountProvision,request:Request,actor=Depends(admin)):
    validate_access(request,data.direct_access.model_dump())
    return auth(request).create_user(data,actor=actor)


@router.patch('/admin/accounts/{user_id}')
def update_account(user_id:str,data:AccountUpdate,request:Request,actor=Depends(admin)):
    if any(value is None for value in data.model_dump(exclude_unset=True).values()): raise HTTPException(422,'Account fields cannot be null')
    if data.direct_access is not None: validate_access(request,data.direct_access.model_dump())
    return auth(request).update_user(user_id,data,actor)


@router.delete('/admin/accounts/{user_id}',status_code=204)
def delete_account(user_id:str,request:Request,actor=Depends(admin)):
    auth(request).delete_user(user_id,actor)


@router.get('/admin/roles')
def roles(request:Request,actor=Depends(admin)):
    with auth(request).store.transaction() as state:
        return [role for role in state['roles'].values() if role['id'] != PROVISIONER_ROLE_ID
                and not role['id'].startswith(DIRECT_ROLE_PREFIX)]


def validate_access(request, access):
    if len(set(access['capabilities'])) != len(access['capabilities']): raise HTTPException(422,'Duplicate capabilities')
    if SCHEMER_AUTHOR in access['capabilities'] and 'schemer:access' not in access['capabilities']:
        raise HTTPException(422,'Schemer authoring requires Schemer access in the same role')
    for key,id_key in [('connections','connection_id'),('dashboards','dashboard_id')]:
        if len({g[id_key] for g in access[key]}) != len(access[key]): raise HTTPException(422,'Duplicate grants')
    for grant in access['dashboards']:
        if 'schemer:access' not in access['capabilities']:
            raise HTTPException(422,'Shared dashboards require Schemer access in the same role')
        if not any(c['connection_id']==grant['connection_id'] and c['owner_id']==grant['connection_owner_id'] for c in access['connections']):
            raise HTTPException(422,'Each dashboard requires its database connection in the same role')
    if any(g['allow_authoring'] for g in access['connections']) and not any(
            c in access['capabilities'] for c in ('schemii:access','schemoo:access',SCHEMER_AUTHOR)):
        raise HTTPException(422,'Using a managed connection in product tools requires Schemii, Schemoo, or Schemer editing access in the same role')
    if not access['connections'] and not access['dashboards']:
        return
    services=request.app.state.services
    for grant in access['connections']:
        if grant['owner_id'] != SCHEMII_CONNECTION_OWNER_ID:
            raise HTTPException(422,'Only Schemii-owned connections can be shared through roles')
        profile=services.connections.get(grant['owner_id'],grant['connection_id'])
        if profile.ownership != 'schemii':
            raise HTTPException(422,'Only Schemii-owned connections can be shared through roles')
    for grant in access['dashboards']:
        dashboard=services.dashboards.get(grant['owner_id'],grant['dashboard_id'])
        model=services.models.get(grant['owner_id'],dashboard.model_id)
        source=services.connections.get(model.connection_owner_id or grant['owner_id'],model.connection_id)
        target=services.connections.get(grant['connection_owner_id'],grant['connection_id'])
        if any(getattr(source,key) != getattr(target,key) for key in ('host','port','database')):
            raise HTTPException(422,'Report access must use the same database target as its model')


def save_role(request,data,actor,role_id=None):
    if role_id == PROVISIONER_ROLE_ID or (role_id and role_id.startswith(DIRECT_ROLE_PREFIX)):
        raise HTTPException(403,'Reserved application role cannot be edited')
    role=data.model_dump()
    validate_access(request,role)
    if len(set(role['user_ids'])) != len(role['user_ids']): raise HTTPException(422,'Duplicate role members')
    with auth(request).store.transaction(write=True) as state:
        if role_id is not None and role_id not in state['roles']: raise HTTPException(404,'Role not found')
        if any(u not in state['users'] for u in role['user_ids']): raise HTTPException(422,'Unknown role member')
        if any(r['id'] != role_id and r['name']==role['name'] for r in state['roles'].values()): raise HTTPException(409,'Role name already exists')
        role['id']=role_id or 'role_'+uuid4().hex
        state['roles'][role['id']]=role
        state['audit'].append((actor,'role.save',role['id']))
    return role


@router.post('/admin/roles',status_code=201)
def create_role(data:RoleInput,request:Request,actor=Depends(admin)): return save_role(request,data,actor)


@router.put('/admin/roles/{role_id}')
def update_role(role_id:str,data:RoleInput,request:Request,actor=Depends(admin)): return save_role(request,data,actor,role_id)


@router.delete('/admin/roles/{role_id}',status_code=204)
def delete_role(role_id:str,request:Request,actor=Depends(admin)):
    if role_id == PROVISIONER_ROLE_ID or role_id.startswith(DIRECT_ROLE_PREFIX):
        raise HTTPException(403,'Reserved application role cannot be deleted')
    with auth(request).store.transaction(write=True) as state:
        if state['roles'].pop(role_id,None) is None: raise HTTPException(404,'Role not found')
        state['audit'].append((actor,'role.delete',role_id))
