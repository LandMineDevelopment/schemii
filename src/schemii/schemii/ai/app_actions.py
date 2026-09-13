"""Typed, owner-bound adapters to existing public application operations.

The registry is explicit: it exposes no arbitrary URL, Python name, credential
reader, assistant policy setter, or approval endpoint. Route input models remain
responsible for validation, revisions and product-level ownership checks.
"""
from copy import deepcopy
from dataclasses import dataclass
import inspect
from types import SimpleNamespace
from typing import Annotated, Literal, Union, get_type_hints

from fastapi import Response
from fastapi.params import Param
from pydantic import ConfigDict, Field, RootModel, create_model
from schemii.common.api.models import ApiModel
from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal
from schemii.common.connections import routes as connections
from schemii.common.connections.models import PostgresConnectionCreate, PostgresConnectionUpdate
from schemii.schemii.workspaces import routes as workspaces
from schemii.schemii.designs import routes as designs
from schemii.schemii.console import routes as console


@dataclass(frozen=True)
class Operation:
    handler: object
    permission: str
    label: str
    group: str
    mutates: bool = False


OPERATIONS = {
    'list_workspaces': Operation(workspaces.list_workspaces, 'workspace.list', 'List workspaces', 'Workspaces'),
    'get_workspace': Operation(workspaces.get_workspace, 'workspace.read', 'Inspect workspace', 'Workspaces'),
    'create_workspace': Operation(workspaces.create_workspace, 'workspace.create', 'Create workspace', 'Workspaces', True),
    'open_postgres_workspace': Operation(workspaces.open_postgres_workspace, 'workspace.open', 'Open database workspace', 'Workspaces', True),
    'update_workspace': Operation(workspaces.update_workspace_metadata, 'workspace.update', 'Rename workspace', 'Workspaces', True),
    'delete_workspace': Operation(workspaces.delete_workspace, 'workspace.delete', 'Delete workspace', 'Workspaces', True),
    'get_catalog': Operation(workspaces.get_workspace_catalog, 'workspace.catalog', 'Refresh workspace catalog', 'Workspaces'),
    'get_layout': Operation(designs.get_workspace_design_layout, 'layout.read', 'Inspect design layout', 'Layout'),
    'update_layout': Operation(designs.replace_workspace_design_layout, 'layout.update', 'Save design layout', 'Layout', True),
    'update_browser_layout': Operation(workspaces.update_workspace_layout, 'layout.update', 'Save browser layout and column order', 'Layout', True),
    'get_design': Operation(designs.get_workspace_design, 'design.inspect', 'Inspect saved design', 'Design inspection'),
    'get_snapshot': Operation(designs.get_workspace_design_snapshot, 'design.inspect', 'Inspect design snapshot', 'Design inspection'),
    'get_history': Operation(designs.get_design_history, 'history.inspect', 'Inspect design history', 'History'),
    'deletion_impact': Operation(designs.get_design_deletion_impact, 'design.impact', 'Inspect deletion dependencies', 'Design inspection'),
    'export_design': Operation(designs.export_workspace_design, 'design.export', 'Export design SQL or JSON', 'Design inspection'),
    'analyze_column_type': Operation(designs.analyze_column_type, 'design.analyze', 'Analyze type and SQL definitions', 'Design inspection'),
    'analyze_type': Operation(designs.analyze_workspace_type, 'design.analyze', 'Analyze type and SQL definitions', 'Design inspection'),
    'analyze_view': Operation(designs.analyze_workspace_view, 'design.analyze', 'Analyze type and SQL definitions', 'Design inspection'),
    'analyze_routine': Operation(designs.analyze_workspace_routine, 'design.analyze', 'Analyze type and SQL definitions', 'Design inspection'),
    'analyze_trigger': Operation(designs.analyze_workspace_trigger, 'design.analyze', 'Analyze type and SQL definitions', 'Design inspection'),
    'list_connections': Operation(connections.list_connections, 'connection.inspect', 'Inspect connection profiles', 'Connections'),
    'get_connection': Operation(connections.get_connection, 'connection.inspect', 'Inspect connection profiles', 'Connections'),
    'create_connection': Operation(connections.create_connection, 'connection.create', 'Create connection profile', 'Connections', True),
    'update_connection': Operation(connections.update_connection, 'connection.update', 'Update connection profile', 'Connections', True),
    'test_connection': Operation(connections.test_connection, 'connection.test', 'Test database connection', 'Connections'),
    'list_namespaces': Operation(connections.list_connection_namespaces, 'connection.catalog', 'List database namespaces', 'Connections'),
    'connection_deletion_impact': Operation(connections.get_connection_deletion_impact, 'connection.inspect', 'Inspect connection profiles', 'Connections'),
    'delete_connection': Operation(connections.delete_connection, 'connection.delete', 'Delete connection profile', 'Connections', True),
    'get_console_settings': Operation(console.get_console_settings, 'console.settings.read', 'Inspect result preferences', 'Console library'),
    'update_console_settings': Operation(console.update_console_settings, 'console.settings.update', 'Set result page size', 'Console library', True),
    'query_history': Operation(console.list_console_history, 'query.history', 'Inspect SQL history', 'Console library'),
    'list_saved_queries': Operation(console.list_console_saved_queries, 'query.saved.read', 'Read saved queries', 'Console library'),
    'create_saved_query': Operation(console.create_console_saved_query, 'query.saved.create', 'Save a query', 'Console library', True),
    'update_saved_query': Operation(console.update_console_saved_query, 'query.saved.update', 'Edit saved query', 'Console library', True),
    'delete_saved_query': Operation(console.delete_console_saved_query, 'query.saved.delete', 'Delete saved query', 'Console library', True),
    'cancel_query': Operation(console.cancel_console_execution, 'query.cancel', 'Stop console query', 'Queries', True),
    'close_result': Operation(console.close_console_result, 'query.release', 'Release console result', 'Queries', True),
}

APP_PERMISSIONS = {item.permission: (item.label, item.group, 'app_actions', 'app_approval_required') for item in OPERATIONS.values()}


def _public_connection_body(model):
    # Passwords must use the dedicated credential form, never proposal/history
    # storage. Missing password on update preserves the existing encrypted value.
    fields = {name: (field.annotation, deepcopy(field)) for name, field in model.model_fields.items() if name != 'password'}
    return create_model('AiPublic' + model.__name__, __base__=ApiModel, **fields)


_PUBLIC_CONNECTIONS = {model: _public_connection_body(model) for model in (PostgresConnectionCreate, PostgresConnectionUpdate)}
_INPUTS = {}
_VARIANTS = []
for name, operation in OPERATIONS.items():
    hints = get_type_hints(operation.handler, include_extras=True)
    fields = {}
    for key, parameter in inspect.signature(operation.handler).parameters.items():
        if key in {'request', 'principal', 'response'}:
            continue
        annotation = hints[key]
        default = parameter.default
        if isinstance(default, Param):
            default = deepcopy(default)
        elif default is inspect.Parameter.empty:
            default = ...
        if key == 'body' and annotation in _PUBLIC_CONNECTIONS:
            annotation = _PUBLIC_CONNECTIONS[annotation]
        if key == 'workspace_id':
            annotation = Annotated[str | None, Field(pattern=r'^ws_[0-9a-f]{32}$')]
            default = None
        fields[key] = (annotation, default)
    arguments = create_model('AiApp' + ''.join(part.title() for part in name.split('_')), __base__=ApiModel, **fields)
    _INPUTS[name] = arguments
    _VARIANTS.append(create_model('AiAppAction' + ''.join(part.title() for part in name.split('_')), __base__=ApiModel,
        operation=(Literal[name], ...), args=(arguments, ...)))


class AppAction(RootModel[Annotated[Union[tuple(_VARIANTS)], Field(discriminator='operation')]]):
    model_config = ConfigDict(json_schema_extra={'type': 'object'})


def permission_id(action):
    return OPERATIONS[AppAction.model_validate(action).root.operation].permission


def execute_action(services, owner, workspace_id, action):
    parsed = AppAction.model_validate(action).root
    operation = OPERATIONS[parsed.operation]
    kwargs = {key: getattr(parsed.args, key) for key in type(parsed.args).model_fields}
    if 'workspace_id' in kwargs:
        kwargs['workspace_id'] = kwargs['workspace_id'] or workspace_id
    if parsed.operation == 'delete_workspace' and kwargs['workspace_id'] == workspace_id:
        # PostgreSQL cascades workspace deletion through this conversation's
        # proposals and operation receipts. Keep the approved action auditable;
        # another workspace's assistant can delete this workspace normally.
        raise ApiProblem(409, 'ai_current_workspace_delete',
                         'Delete this workspace from the workspace manager or from another workspace assistant. Deleting it here would also delete this conversation and its approval receipt.')
    if 'body' in kwargs:
        expected = get_type_hints(operation.handler)['body']
        # Preserve omitted update fields; explicit null is never synthesized.
        kwargs['body'] = expected.model_validate(kwargs['body'].model_dump(exclude_unset=True))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services)))
    if 'response' in inspect.signature(operation.handler).parameters:
        kwargs['response'] = Response()
    value = operation.handler(request=request, principal=Principal(user_id=owner, authentication_source='local_prototype'), **kwargs)
    if isinstance(value, Response):
        return {'status': 'succeeded', 'operation': parsed.operation, 'httpStatus': value.status_code}
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json', by_alias=True)
    return value
