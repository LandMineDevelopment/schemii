"""Shared reports preserve actor identity and never inherit author credentials."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from schemii.common.api.errors import ApiProblem
from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID
from schemii.schemer.access import available_dashboards, prepare_dashboard, ReportCatalogs
from schemii.schemer.dashboard_models import DashboardCreate
from schemii.schemer.dashboard_store import InMemoryDashboardRepository, DashboardNotFoundError
from schemii.schemoo.models import ModelCreate
from schemii.schemoo.service import load_model
from schemii.schemoo.store import InMemoryModelRepository, ModelNotFoundError


@pytest.fixture
def shared():
    profiles = {
        'pg_'+'a'*32: NS(id='pg_'+'a'*32, owner_id='admin', host='postgres', port=5432, database='sales', revision=1, username='author', ownership='user'),
        'pg_'+'b'*32: NS(id='pg_'+'b'*32, owner_id=SCHEMII_CONNECTION_OWNER_ID, host='postgres', port=5432, database='sales', revision=1, username='east', ownership='schemii'),
    }
    uses = []
    class Connections:
        def get(self, owner, key):
            expected = 'admin' if key == 'pg_'+'a'*32 or profiles[key].ownership == 'user' else SCHEMII_CONNECTION_OWNER_ID
            assert owner == expected
            return profiles[key]
        @contextmanager
        def use(self, owner, key):
            uses.append((owner, key))
            yield self.get(owner, key)
    models, dashboards = InMemoryModelRepository(), InMemoryDashboardRepository()
    model = models.create('admin', ModelCreate(name='Sales', connection_id='pg_'+'a'*32,
        database='sales', namespace='public', definition={'root':'sales','nodes':[{'id':'sales','table':'sales','label':'Sales'}]}))
    dashboard = dashboards.create('admin', DashboardCreate(name='Sales',model_id=model.id,model_revision=1))
    grant = dict(role_id='east',owner_id='admin',dashboard_id=dashboard.id,connection_id='pg_'+'b'*32,
                 connection_owner_id=SCHEMII_CONNECTION_OWNER_ID,can_export=False,can_drill=False)
    db_grant = dict(role_id='east',owner_id=SCHEMII_CONNECTION_OWNER_ID,connection_id=grant['connection_id'])
    auth = NS(enabled=True, is_admin=lambda actor:False, capabilities=lambda actor:['schemer:access'] if actor == 'viewer' else [], audit=lambda *args:None, user=lambda actor: {'disabled':False},
              dashboard_grants=lambda actor: [deepcopy(grant)] if actor=='viewer' else [],
              connection_grants=lambda actor: [deepcopy(db_grant)])
    calls=[]
    console=NS(reserve_read_target=lambda actor, **kwargs: calls.append((actor,kwargs)),
               run=lambda actor, execution_id, **kwargs: calls.append((actor,kwargs)))
    services=NS(models=models,dashboards=dashboards,connections=Connections(),console=console,model_catalogs=None)
    request=NS(app=NS(state=NS(services=services,auth=auth)))
    return NS(request=request,services=services,model=model,dashboard=dashboard,grant=grant,
              db_grant=db_grant,profiles=profiles,auth=auth,uses=uses,calls=calls)


def test_shared_report_keeps_viewer_as_execution_owner_and_uses_only_assigned_profile(shared):
    dashboard, scoped, permissions=prepare_dashboard(shared.request,'viewer',shared.dashboard.id)
    model=load_model(scoped,'viewer',shared.model.id,shared.model.revision)
    assert model.connection_id==shared.grant['connection_id']
    assert model.connection_owner_id==SCHEMII_CONNECTION_OWNER_ID
    assert shared.services.models.get('admin',model.id).connection_id!=model.connection_id
    assert shared.services.models.get('admin',model.id).connection_owner_id=='admin'
    with scoped.connections.use('viewer',model.connection_id) as target:
        assert target.username=='east'
    scoped.console.reserve_read_target('viewer',connection_id=model.connection_id)
    scoped.console.run('viewer','execution')
    assert all(actor=='viewer' and kwargs['connection_access'] is scoped.report_access for actor,kwargs in shared.calls)
    assert permissions=={'edit':False,'export':False,'drill':False}
    assert [d.id for d in available_dashboards(shared.request,'viewer')]==[dashboard.id]
    assert available_dashboards(shared.request,'stranger')==[]
    with pytest.raises(ModelNotFoundError): scoped.models.get('viewer','another-model')
    with pytest.raises(ApiProblem): scoped.connections.get('viewer',shared.model.connection_id)


@pytest.mark.parametrize('action',['export','drill'])
def test_sensitive_actions_require_explicit_grant(shared,action):
    with pytest.raises(ApiProblem) as denied:
        prepare_dashboard(shared.request,'viewer',shared.dashboard.id,**{action:True})
    assert denied.value.status_code==403
    shared.grant['can_'+action]=True
    assert prepare_dashboard(shared.request,'viewer',shared.dashboard.id,**{action:True})[2][action]


def test_database_and_report_grants_must_come_from_same_role(shared):
    shared.db_grant['role_id']='different-role'
    with pytest.raises(ApiProblem,match='no longer'):
        prepare_dashboard(shared.request,'viewer',shared.dashboard.id)


def test_mismatched_database_and_ambiguous_profiles_fail_closed(shared):
    shared.profiles[shared.grant['connection_id']].database='other'
    with pytest.raises(ApiProblem,match='must target'):
        prepare_dashboard(shared.request,'viewer',shared.dashboard.id)
    shared.auth.dashboard_grants=lambda actor:[shared.grant,{**shared.grant,'connection_id':shared.model.connection_id}]
    with pytest.raises(ApiProblem,match='different connections'):
        prepare_dashboard(shared.request,'viewer',shared.dashboard.id)


def test_revocation_disable_and_rotation_invalidate_bound_access(shared):
    _, scoped, _=prepare_dashboard(shared.request,'viewer',shared.dashboard.id)
    # Stored grants are copied; later mutations cannot modify the issued capability.
    shared.grant['can_export']=True
    with pytest.raises(ApiProblem,match='changed'): scoped.report_access.check()
    shared.grant['can_export']=False
    shared.auth.user=lambda actor:None
    with pytest.raises(ApiProblem,match='removed'): scoped.report_access.check()


def test_shared_catalog_hides_forbidden_columns_and_relationships(shared):
    _, scoped, _=prepare_dashboard(shared.request,'viewer',shared.dashboard.id)
    catalog={'tables':[{'name':'sales','columns':[{'name':'id'},{'name':'secret'}],
                       'primaryKey':['id','secret'],'uniqueKeys':[['secret']]}],
             'relationships':[{'sourceTable':'sales','sourceColumn':'secret','targetTable':'sales','targetColumn':'id'}]}
    base=NS(get=lambda *a,**kw:deepcopy(catalog))
    scoped.postgres=NS(readable_columns=lambda *args:{('sales','id')})
    visible=ReportCatalogs(base,scoped.report_access).get(scoped,'viewer',shared.grant['connection_id'],'public')
    assert visible['tables'][0]['columns']==[{'name':'id'}]
    assert visible['relationships']==[]
    assert visible['tables'][0]['uniqueKeys']==[]


def test_stranger_cannot_read_shared_dashboard(shared):
    with pytest.raises(DashboardNotFoundError): prepare_dashboard(shared.request,'stranger',shared.dashboard.id)


def test_legacy_person_owned_dashboard_grant_cannot_resolve(shared):
    shared.grant['connection_owner_id'] = 'admin'
    shared.db_grant['owner_id'] = 'admin'
    shared.profiles[shared.grant['connection_id']].ownership = 'user'
    with pytest.raises(ApiProblem, match='Schemii-owned'):
        prepare_dashboard(shared.request,'viewer',shared.dashboard.id)
