"""Owner-scoped assistant conversations, proposals, and transient query results."""
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.metadata.limit_events import LimitEventNotice
from .models import (
    SchemiiActivityPage,
    SchemiiAiOperation,
    SchemiiAiOperationListResponse,
    SchemiiAiPreferencesResult,
    SchemiiAiPreferencesUpdate,
    SchemiiChat,
    SchemiiChatCreate,
    SchemiiChatListResponse,
    SchemiiChatPolicy,
    SchemiiChatPolicyUpdate,
    SchemiiChatUpdate,
    SchemiiMessageCreate,
    SchemiiMessageListResponse,
    SchemiiTransientResponseList,
    SchemiiProposal,
    SchemiiProposalExecutionCreate,
    SchemiiProposalListResponse,
    SchemiiQueryResult,
    SchemiiAiSettings,
    SchemiiAiSettingsUpdate,
    SchemiiTurn,
)
from .repository import AiCapacityError, AiConflictError, AiNotFoundError
from .service import AiService, AiServiceError

router = APIRouter(tags=["schemii-assistant"])
def _service(request: Request) -> AiService: return request.app.state.ai_service
def _call(function, *args):
    try: return function(*args)
    except AiNotFoundError as error: raise ApiProblem(404, "ai_not_found", str(error)) from error
    except AiCapacityError as error: raise ApiProblem(429, "ai_capacity_reached", str(error), retryable=True, limit_event=LimitEventNotice(resource=error.resource, limit_name=error.limit_name, configured_limit=error.configured_limit, observed_value=error.observed_value)) from error
    except AiConflictError as error: raise ApiProblem(409, "ai_conflict", str(error)) from error
    except AiServiceError as error: raise ApiProblem(error.status, error.code, str(error), details=error.details) from error

@router.get("/ai/settings", response_model=SchemiiAiSettings)
def settings(request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).repository.settings, principal.user_id)
@router.put("/ai/settings", response_model=SchemiiAiSettings)
def update_settings(body: SchemiiAiSettingsUpdate, request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).repository.update_settings, principal.user_id, body.expected_revision, body.enabled, body.default_provider_id, body.default_model_id, body.default_capabilities)
@router.put("/ai/chats/{chat_id}/preferences", response_model=SchemiiAiPreferencesResult)
def save_preferences(chat_id: str, body: SchemiiAiPreferencesUpdate, request: Request, principal: Principal = Depends(get_current_principal)):
    return _call(_service(request).save_preferences, principal.user_id, chat_id, body)
@router.get("/ai/chats", response_model=SchemiiChatListResponse)
def chats(request: Request, workspace_id: str | None = Query(None, alias="workspaceId"), principal: Principal = Depends(get_current_principal)): return SchemiiChatListResponse(chats=_call(_service(request).repository.list_chats, principal.user_id, workspace_id))
@router.post("/workspaces/{workspace_id}/ai/chats", response_model=SchemiiChat, status_code=201)
def create_chat(workspace_id: str, body: SchemiiChatCreate, request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).create_chat, principal.user_id, workspace_id, body)
@router.get("/ai/chats/{chat_id}", response_model=SchemiiChat)
def chat(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).repository.get_chat, principal.user_id, chat_id)
@router.patch("/ai/chats/{chat_id}", response_model=SchemiiChat)
def update_chat(chat_id: str, body: SchemiiChatUpdate, request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).repository.update_chat, principal.user_id, chat_id, body.expected_revision, body.title)
@router.delete("/ai/chats/{chat_id}", response_model=SchemiiChat)
def delete_chat(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).repository.delete_chat, principal.user_id, chat_id)
@router.get("/ai/chats/{chat_id}/messages", response_model=SchemiiMessageListResponse)
def messages(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)): return SchemiiMessageListResponse(messages=_call(_service(request).repository.list_messages, principal.user_id, chat_id, _service(request).policy.message_history_limit))
@router.post("/ai/chats/{chat_id}/messages", response_model=SchemiiTurn, status_code=202)
def send(chat_id: str, body: SchemiiMessageCreate, tasks: BackgroundTasks, request: Request, principal: Principal = Depends(get_current_principal)):
    turn, _ = _call(_service(request).send, principal.user_id, chat_id, body); tasks.add_task(_service(request).run_turn, principal.user_id, chat_id, turn.id); return turn
@router.get("/ai/chats/{chat_id}/transient-responses", response_model=SchemiiTransientResponseList)
def transient_responses(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    return SchemiiTransientResponseList(responses=_call(_service(request).transient_responses, principal.user_id, chat_id))
@router.post("/ai/chats/{chat_id}/turns/{turn_id}/cancel", response_model=SchemiiTurn)
def cancel_turn(chat_id: str, turn_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    return _call(_service(request).cancel_turn, principal.user_id, chat_id, turn_id)
@router.get("/ai/chats/{chat_id}/activity", response_model=SchemiiActivityPage)
def activity(chat_id: str, request: Request, after: int = Query(0, ge=0), principal: Principal = Depends(get_current_principal)):
    events = _call(_service(request).repository.activity, principal.user_id, chat_id, after); return SchemiiActivityPage(events=events, next_sequence=events[-1].sequence if events else after)
@router.get("/ai/chats/{chat_id}/policy", response_model=SchemiiChatPolicy)
def policy(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    value = _call(_service(request).repository.get_chat, principal.user_id, chat_id); return SchemiiChatPolicy(revision=value.revision, capabilities=value.capabilities)
@router.put("/ai/chats/{chat_id}/policy", response_model=SchemiiChatPolicy)
def update_policy(chat_id: str, body: SchemiiChatPolicyUpdate, request: Request, principal: Principal = Depends(get_current_principal)):
    value = _call(_service(request).repository.update_chat_policy, principal.user_id, chat_id, body.expected_revision, body.capabilities); return SchemiiChatPolicy(revision=value.revision, capabilities=value.capabilities)
@router.get("/ai/chats/{chat_id}/proposals", response_model=SchemiiProposalListResponse)
def proposals(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)): return SchemiiProposalListResponse(proposals=_call(_service(request).repository.list_proposals, principal.user_id, chat_id))
@router.delete("/ai/chats/{chat_id}/proposals/{proposal_id}", response_model=SchemiiProposal)
def dismiss(chat_id: str, proposal_id: str, request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).repository.dismiss_proposal, principal.user_id, chat_id, proposal_id)
@router.post("/ai/chats/{chat_id}/proposals/{proposal_id}/executions", response_model=SchemiiAiOperation, status_code=201)
def execute(chat_id: str, proposal_id: str, body: SchemiiProposalExecutionCreate, request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).execute, principal.user_id, chat_id, proposal_id, body)
@router.get("/ai/chats/{chat_id}/operations/{operation_id}", response_model=SchemiiAiOperation)
def operation(chat_id: str, operation_id: str, request: Request, principal: Principal = Depends(get_current_principal)): return _call(_service(request).repository.get_operation, principal.user_id, chat_id, operation_id)
@router.get("/ai/chats/{chat_id}/operations", response_model=SchemiiAiOperationListResponse)
def operations(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)): return SchemiiAiOperationListResponse(operations=_call(_service(request).repository.list_operations, principal.user_id, chat_id))
@router.get("/ai/chats/{chat_id}/operations/{operation_id}/query-result", response_model=SchemiiQueryResult)
def query_result(chat_id: str, operation_id: str, request: Request, cursor: str | None = None, principal: Principal = Depends(get_current_principal)): return _call(_service(request).query_result, principal.user_id, chat_id, operation_id, cursor)
