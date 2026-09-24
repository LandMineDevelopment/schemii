"""Schemer dashboard chats on the shared product conversation lifecycle."""
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from schemii.common.metadata.models import Principal, get_current_principal

router = APIRouter(prefix="/api/v1/schemer/ai", tags=["schemer-ai"])
Mode = Literal["disabled", "ask", "automatic"]

class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasoningEffort: Literal["default", "off", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    modes: dict[str, Mode] = Field(default_factory=dict)
    providerId: str | None = Field(default=None, max_length=128)
    aiModelId: str | None = Field(default=None, max_length=256)

class ChatCreate(Preferences):
    dashboardId: str = Field(pattern=r"^dashboard_[0-9a-f]{32}$")
    providerId: str = Field(min_length=1, max_length=128)
    aiModelId: str = Field(min_length=1, max_length=256)

class ChatPreferences(Preferences):
    expectedRevision: int = Field(ge=1)
    providerId: str = Field(min_length=1, max_length=128)
    aiModelId: str = Field(min_length=1, max_length=256)

class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    expectedRevision: int = Field(ge=1)
    acknowledgeProviderDataPolicy: bool = False

class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pendingId: str = Field(pattern=r"^approval_[0-9a-f]{32}$")
    approved: bool
    expectedRevision: int = Field(ge=1)


def service(request):
    return request.app.state.schemer_ai

@router.get("/settings")
def settings(request: Request, principal: Principal = Depends(get_current_principal)):
    return service(request).settings(principal.user_id)

@router.put("/settings")
def put_settings(body: Preferences, request: Request, principal: Principal = Depends(get_current_principal)):
    return service(request).settings(principal.user_id, body.model_dump(exclude_unset=True))

@router.get("/chats")
def chats(request: Request, dashboard_id: str | None = None,
          principal: Principal = Depends(get_current_principal)):
    return {"chats": service(request).list(principal.user_id, dashboard_id, request)}

@router.post("/chats", status_code=201)
def create(body: ChatCreate, request: Request, principal: Principal = Depends(get_current_principal)):
    return service(request).create(principal.user_id, body.model_dump(exclude_unset=True), request)

@router.get("/chats/{chat_id}")
def get(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    return service(request).snapshot(principal.user_id, chat_id, request)

@router.post("/chats/{chat_id}/messages", status_code=202)
def send(chat_id: str, body: Message, request: Request, background_tasks: BackgroundTasks,
         principal: Principal = Depends(get_current_principal)):
    value = service(request).send(principal.user_id, chat_id, body.model_dump(exclude_unset=True), request)
    background_tasks.add_task(service(request).run, principal.user_id, chat_id, value["turnId"])
    return value

@router.put("/chats/{chat_id}/preferences")
def preferences(chat_id: str, body: ChatPreferences, request: Request,
                principal: Principal = Depends(get_current_principal)):
    return service(request).preferences(principal.user_id, chat_id, body.model_dump(exclude_unset=True), request)

@router.post("/chats/{chat_id}/approval", status_code=202)
def approval(chat_id: str, body: Approval, request: Request, background_tasks: BackgroundTasks,
             principal: Principal = Depends(get_current_principal)):
    value, actions, rejected = service(request).approval(principal.user_id, chat_id, body.model_dump(exclude_unset=True), request)
    background_tasks.add_task(service(request).run, principal.user_id, chat_id, value["turnId"], actions, rejected)
    return value

@router.post("/chats/{chat_id}/cancel")
def cancel(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    return service(request).cancel(principal.user_id, chat_id, request)

@router.delete("/chats/{chat_id}", status_code=204)
def delete(chat_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    service(request).delete(principal.user_id, chat_id, request)
    return Response(status_code=204)
