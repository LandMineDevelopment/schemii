"""Planned workspace-owned Schemii assistant routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from schemii.common.api.planned import (
    PLANNED_OPENAPI,
    PLANNED_RESPONSES,
    planned_capability,
)
from schemii.common.metadata.models import Principal, get_current_principal

from .models import (
    SchemiiActivityPage,
    SchemiiAiOperation,
    SchemiiAiOperationReconcile,
    SchemiiAiSettings,
    SchemiiAiSettingsUpdate,
    SchemiiChat,
    SchemiiChatCreate,
    SchemiiChatListResponse,
    SchemiiChatPolicy,
    SchemiiChatPolicyUpdate,
    SchemiiChatUpdate,
    SchemiiMessage,
    SchemiiMessageCreate,
    SchemiiMessageListResponse,
    SchemiiProposalExecutionCreate,
)


router = APIRouter(tags=["schemii-assistant-planned"])


@router.get(
    "/ai/settings",
    response_model=SchemiiAiSettings,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_schemii_ai_settings(
    principal: Principal = Depends(get_current_principal),
) -> SchemiiAiSettings:
    """Read Schemii assistant defaults without granting chat or database authority."""

    # TODO(schemii-ai-settings): Persist owner/application defaults separately
    # from per-chat policy snapshots and human Console settings.
    del principal
    planned_capability("schemii.ai.settings.read")


@router.put(
    "/ai/settings",
    response_model=SchemiiAiSettings,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def update_schemii_ai_settings(
    body: SchemiiAiSettingsUpdate,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiAiSettings:
    """Replace bounded assistant defaults after an optimistic revision check."""

    # TODO(schemii-ai-settings): Validate the model against live provider status
    # and persist an auditable settings transition without mutating existing chats.
    del body, principal
    planned_capability("schemii.ai.settings.update")


@router.get(
    "/ai/chats",
    response_model=SchemiiChatListResponse,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def list_schemii_chats(
    workspace_id: str | None = Query(default=None, alias="workspaceId"),
    principal: Principal = Depends(get_current_principal),
) -> SchemiiChatListResponse:
    """List owner-visible chats, optionally restricted to one workspace."""

    # TODO(schemii-ai-chats): Query metadata by owner and optional workspace while
    # excluding redacted payloads and preserving deleted-operation audit evidence.
    del workspace_id, principal
    planned_capability("schemii.ai.chats.list")


@router.post(
    "/workspaces/{workspace_id}/ai/chats",
    response_model=SchemiiChat,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def create_schemii_chat(
    workspace_id: str,
    body: SchemiiChatCreate,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiChat:
    """Create a chat bound to workspace design and optional target context."""

    # TODO(schemii-ai-chats): Snapshot design/target identity and effective policy,
    # then create the provider session only after durable ownership exists.
    del workspace_id, body, principal
    planned_capability("schemii.ai.chats.create")


@router.get(
    "/ai/chats/{chat_id}",
    response_model=SchemiiChat,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_schemii_chat(
    chat_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiChat:
    """Return one owner-scoped conversation and its current context status."""

    # TODO(schemii-ai-chats): Resolve durable chat metadata and report stale design
    # or detached-target context without silently rebinding authority.
    del chat_id, principal
    planned_capability("schemii.ai.chats.read")


@router.patch(
    "/ai/chats/{chat_id}",
    response_model=SchemiiChat,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def update_schemii_chat(
    chat_id: str,
    body: SchemiiChatUpdate,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiChat:
    """Rename one conversation without modifying its authority or provider session."""

    # TODO(schemii-ai-chats): Normalize and persist the title through one optimistic
    # metadata update while leaving message and operation revisions unchanged.
    del chat_id, body, principal
    planned_capability("schemii.ai.chats.update")


@router.delete(
    "/ai/chats/{chat_id}",
    response_model=SchemiiChat,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def delete_schemii_chat(
    chat_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiChat:
    """Tombstone a chat while preserving required operation and policy evidence."""

    # TODO(schemii-ai-chats): Cancel safe active work, revoke replayable authority,
    # redact conversation payloads on schedule, and retain immutable audit records.
    del chat_id, principal
    planned_capability("schemii.ai.chats.delete")


@router.get(
    "/ai/chats/{chat_id}/messages",
    response_model=SchemiiMessageListResponse,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def list_schemii_messages(
    chat_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiMessageListResponse:
    """Return ordered durable messages without executable proposal envelopes."""

    # TODO(schemii-ai-messages): Page owner-bound messages and disclose proposal
    # summaries separately from private, one-use server authority.
    del chat_id, principal
    planned_capability("schemii.ai.messages.list")


@router.post(
    "/ai/chats/{chat_id}/messages",
    response_model=SchemiiMessage,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def create_schemii_message(
    chat_id: str,
    body: SchemiiMessageCreate,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiMessage:
    """Persist a prompt and dispatch bounded, source-derived workspace context."""

    # TODO(schemii-ai-messages): Build context from the current saved design and
    # optional live target, reserve delivery, and persist assistant output exactly once.
    del chat_id, body, principal
    planned_capability("schemii.ai.messages.create")


@router.get(
    "/ai/chats/{chat_id}/activity",
    response_model=SchemiiActivityPage,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_schemii_chat_activity(
    chat_id: str,
    after: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiActivityPage:
    """Resume bounded chat progress events after a durable sequence number."""

    # TODO(schemii-ai-activity): Expose the same durable event source to polling
    # and a later SSE transport without making HTTP connection state authoritative.
    del chat_id, after, principal
    planned_capability("schemii.ai.activity")


@router.get(
    "/ai/chats/{chat_id}/policy",
    response_model=SchemiiChatPolicy,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_schemii_chat_policy(
    chat_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiChatPolicy:
    """Read the explicit authority ceiling bound to one conversation."""

    # TODO(schemii-ai-policy): Reconstruct effective policy from the immutable
    # application policy revision and the chat's owner-approved narrowing.
    del chat_id, principal
    planned_capability("schemii.ai.policy.read")


@router.put(
    "/ai/chats/{chat_id}/policy",
    response_model=SchemiiChatPolicy,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def update_schemii_chat_policy(
    chat_id: str,
    body: SchemiiChatPolicyUpdate,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiChatPolicy:
    """Replace owner-approved chat limits without widening application policy."""

    # TODO(schemii-ai-policy): Reject escalation beyond server policy, persist the
    # new revision, and invalidate unexecuted proposals issued under older authority.
    del chat_id, body, principal
    planned_capability("schemii.ai.policy.update")


@router.post(
    "/ai/chats/{chat_id}/proposals/{proposal_id}/executions",
    response_model=SchemiiAiOperation,
    status_code=status.HTTP_201_CREATED,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def execute_schemii_ai_proposal(
    chat_id: str,
    proposal_id: str,
    body: SchemiiProposalExecutionCreate,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiAiOperation:
    """Consume one server-issued proposal and create its sole durable operation."""

    # TODO(schemii-ai-proposals): Verify digest, context, policy, expiry, and one-use
    # state before reserving an operation; the model never supplies authority fields.
    del chat_id, proposal_id, body, principal
    planned_capability("schemii.ai.proposals.execute")


@router.get(
    "/ai/chats/{chat_id}/operations/{operation_id}",
    response_model=SchemiiAiOperation,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def get_schemii_ai_operation(
    chat_id: str,
    operation_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiAiOperation:
    """Read durable operation status and bounded result delivery state."""

    # TODO(schemii-ai-operations): Resolve exact chat ownership, recover stale
    # leases through maintenance, and redact retained results on policy schedule.
    del chat_id, operation_id, principal
    planned_capability("schemii.ai.operations.read")


@router.delete(
    "/ai/chats/{chat_id}/operations/{operation_id}",
    response_model=SchemiiAiOperation,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def cancel_schemii_ai_operation(
    chat_id: str,
    operation_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiAiOperation:
    """Request cancellation without claiming already committed effects were undone."""

    # TODO(schemii-ai-operations): Signal cancellable work, preserve exact attempts,
    # and leave uncertain writes eligible only for evidence-based reconciliation.
    del chat_id, operation_id, principal
    planned_capability("schemii.ai.operations.cancel")


@router.post(
    "/ai/chats/{chat_id}/operations/{operation_id}/reconciliation",
    response_model=SchemiiAiOperation,
    responses=PLANNED_RESPONSES,
    openapi_extra=PLANNED_OPENAPI,
)
def reconcile_schemii_ai_operation(
    chat_id: str,
    operation_id: str,
    body: SchemiiAiOperationReconcile,
    principal: Principal = Depends(get_current_principal),
) -> SchemiiAiOperation:
    """Resolve uncertain database work from durable evidence without replay."""

    # TODO(schemii-ai-operations): Delegate migration/data reconciliation to the
    # owning service, record evidence, and publish a terminal operation transition.
    del chat_id, operation_id, body, principal
    planned_capability("schemii.ai.operations.reconcile")
