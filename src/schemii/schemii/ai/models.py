"""Schemii chat, policy, proposal, and operation contracts."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from schemii.common.api.models import ApiModel


AiAccessLevel = Literal["metadata", "schema", "data", "write"]


class SchemiiAiSettings(ApiModel):
    """Owner-scoped Schemii assistant defaults independent from Console policy."""

    revision: Annotated[int, Field(strict=True, ge=1)]
    enabled: bool
    default_model: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    default_access_level: AiAccessLevel = "metadata"


class SchemiiAiSettingsUpdate(ApiModel):
    """Optimistic replacement of assistant defaults."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    enabled: bool
    default_model: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    default_access_level: AiAccessLevel


class SchemiiChatCreate(ApiModel):
    """Open a chat whose schema and optional target context derive from its workspace."""

    model: Annotated[str, Field(min_length=1, max_length=256)]
    access_level: AiAccessLevel = "metadata"
    title: Annotated[str, Field(min_length=1, max_length=80)] | None = None


class SchemiiChatUpdate(ApiModel):
    """Rename one chat without changing its workspace or authority."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=80)]


class SchemiiChat(ApiModel):
    """Durable workspace-owned conversation identity and current policy revision."""

    id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=80)]
    model: Annotated[str, Field(min_length=1, max_length=256)]
    access_level: AiAccessLevel
    status: Literal["idle", "working", "blocked", "deleted"]
    created_at: datetime
    updated_at: datetime


class SchemiiChatListResponse(ApiModel):
    """Owner-visible conversations optionally filtered to one workspace."""

    chats: list[SchemiiChat]


class SchemiiMessageCreate(ApiModel):
    """User prompt bound to the latest chat and design revision."""

    text: Annotated[str, Field(min_length=1, max_length=16_384)]
    expected_chat_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_design_revision: Annotated[int, Field(strict=True, ge=0)]
    result_ref: str | None = Field(default=None, max_length=512)


class SchemiiMessage(ApiModel):
    """Persisted user or assistant message with no executable authority."""

    id: str = Field(pattern=r"^msg_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    role: Literal["user", "assistant", "system"]
    text: Annotated[str, Field(max_length=1_000_000)]
    created_at: datetime


class SchemiiMessageListResponse(ApiModel):
    """Ordered durable messages for one owner-scoped chat."""

    messages: list[SchemiiMessage]


class SchemiiActivityEvent(ApiModel):
    """Bounded progress event derived from the durable chat operation stream."""

    sequence: Annotated[int, Field(strict=True, ge=1)]
    kind: Literal["status", "message", "proposal", "operation", "error"]
    payload: dict[str, Any]
    created_at: datetime


class SchemiiActivityPage(ApiModel):
    """Resumable activity events after one acknowledged sequence."""

    events: list[SchemiiActivityEvent]
    next_sequence: Annotated[int, Field(strict=True, ge=1)]


class SchemiiChatPolicy(ApiModel):
    """Versioned assistant disclosure and action limits bound to one chat."""

    revision: Annotated[int, Field(strict=True, ge=1)]
    access_level: AiAccessLevel
    allow_database_contact: bool
    allow_schema_proposals: bool
    allow_data_read_proposals: bool
    allow_write_proposals: bool


class SchemiiChatPolicyUpdate(ApiModel):
    """Optimistic replacement of one chat's explicit authority ceiling."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    access_level: AiAccessLevel
    allow_database_contact: bool
    allow_schema_proposals: bool
    allow_data_read_proposals: bool
    allow_write_proposals: bool


class SchemiiProposalExecutionCreate(ApiModel):
    """One-use confirmation for a server-issued, context-bound proposal."""

    expected_chat_revision: Annotated[int, Field(strict=True, ge=1)]
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool


class SchemiiAiOperation(ApiModel):
    """Durable proposal execution state that cannot be replayed after uncertainty."""

    id: str = Field(pattern=r"^aop_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^prop_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    kind: Literal["design_change", "data_read", "migration", "navigation"]
    status: Literal["reserved", "running", "succeeded", "failed", "cancelled", "uncertain"]
    result: dict[str, Any] | None = None
    error_code: str | None = Field(default=None, max_length=128)
    created_at: datetime
    updated_at: datetime


class SchemiiAiOperationReconcile(ApiModel):
    """Revision guard for evidence-based operation reconciliation."""

    expected_operation_revision: Annotated[int, Field(strict=True, ge=1)]
