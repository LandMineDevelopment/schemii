"""Workspace assistant, proposal, and operation API contracts."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from schemii.common.api.models import ApiModel
from .action_policy import PERMISSIONS, action_modes, permission_descriptors, policy_descriptors


class AiCapabilities(ApiModel):
    """Independent authority switches; broad access never implies write access."""

    design_changes: bool = False
    live_catalog: bool = False
    structured_data_read: bool = False
    raw_sql_read: bool = False
    raw_sql_write: bool = False
    read_approval_required: bool = True
    design_approval_required: bool = True
    migration_apply: bool = False
    migration_approval_required: bool = True
    design_history: bool = False
    history_approval_required: bool = True
    structured_query: bool = False
    structured_query_approval_required: bool = True
    sql_write_execute: bool = False
    write_approval_required: bool = True
    action_modes: dict[str, Literal["disabled", "ask", "automatic"]] | None = None

    @model_validator(mode="after")
    def normalize_action_modes(self):
        if self.action_modes is not None and set(self.action_modes) - PERMISSIONS.keys():
            raise ValueError("Unknown AI action permission")
        modes = action_modes(self)
        object.__setattr__(self, "action_modes", modes)
        # Legacy fields are a derived projection, never the action-level authority.
        for field in {entry[2] for entry in PERMISSIONS.values()}:
            keys = [key for key, entry in PERMISSIONS.items() if entry[2] == field]
            object.__setattr__(self, field, any(modes[key] != "disabled" for key in keys))
        for field in {entry[3] for entry in PERMISSIONS.values() if entry[3]}:
            keys = [key for key, entry in PERMISSIONS.items() if entry[3] == field]
            object.__setattr__(self, field, any(modes[key] == "ask" for key in keys))
        return self


class SchemiiAiSettings(ApiModel):
    permission_actions: list[dict[str, Any]] = Field(default_factory=permission_descriptors)
    action_policies: list[dict[str, Any]] = Field(default_factory=policy_descriptors)
    revision: Annotated[int, Field(strict=True, ge=1)]
    enabled: bool
    default_provider_id: str | None = Field(default=None, max_length=128)
    default_model_id: str | None = Field(default=None, max_length=256)
    default_capabilities: AiCapabilities = Field(default_factory=AiCapabilities)


class SchemiiAiSettingsUpdate(ApiModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    enabled: bool
    default_provider_id: str | None = Field(default=None, max_length=128)
    default_model_id: str | None = Field(default=None, max_length=256)
    default_capabilities: AiCapabilities


class SchemiiAiPreferencesUpdate(ApiModel):
    expected_settings_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_chat_revision: Annotated[int, Field(strict=True, ge=1)]
    provider_id: Annotated[str, Field(min_length=1, max_length=128)]
    model_id: Annotated[str, Field(min_length=1, max_length=256)]
    capabilities: AiCapabilities


class SchemiiChatCreate(ApiModel):
    provider_id: Annotated[str, Field(min_length=1, max_length=128)]
    model_id: Annotated[str, Field(min_length=1, max_length=256)]
    capabilities: AiCapabilities = Field(default_factory=AiCapabilities)
    title: Annotated[str, Field(min_length=1, max_length=80)] | None = None


class SchemiiChatUpdate(ApiModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=80)]


class SchemiiChat(ApiModel):
    id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=80)]
    provider_id: Annotated[str, Field(min_length=1, max_length=128)]
    model_id: Annotated[str, Field(min_length=1, max_length=256)]
    capabilities: AiCapabilities
    status: Literal["idle", "working", "waiting_approval", "failed", "deleted"]
    created_at: datetime
    updated_at: datetime


class SchemiiAiPreferencesResult(ApiModel):
    settings: SchemiiAiSettings
    chat: SchemiiChat
    started_new_conversation: bool


class SchemiiChatListResponse(ApiModel):
    chats: list[SchemiiChat]


class SchemiiMessageCreate(ApiModel):
    acknowledge_provider_data_policy: bool = Field(default=False, strict=True)
    text: Annotated[str, Field(min_length=1, max_length=65_536)]
    expected_chat_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_design_revision: Annotated[int, Field(strict=True, ge=0)]
    result_context_operation_id: str | None = Field(
        default=None, pattern=r"^aop_[0-9a-f]{32}$"
    )


class SchemiiMessage(ApiModel):
    id: str = Field(pattern=r"^msg_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    turn_id: str | None = Field(default=None, pattern=r"^turn_[0-9a-f]{32}$")
    sequence: Annotated[int, Field(strict=True, ge=1)]
    role: Literal["user", "assistant", "system"]
    text: Annotated[str, Field(max_length=1_000_000)]
    created_at: datetime


class SchemiiMessageListResponse(ApiModel):
    messages: list[SchemiiMessage]


class SchemiiTransientResponse(ApiModel):
    turn_id: str = Field(pattern=r"^turn_[0-9a-f]{32}$")
    text: Annotated[str, Field(max_length=1_000_000)]
    created_at: datetime
    expires_at: datetime


class SchemiiTransientResponseList(ApiModel):
    responses: list[SchemiiTransientResponse]


class SchemiiTurn(ApiModel):
    id: str = Field(pattern=r"^turn_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    status: Literal["queued", "running", "waiting_approval", "succeeded", "failed", "cancelled"]
    result_context_operation_id: str | None = Field(
        default=None, pattern=r"^aop_[0-9a-f]{32}$"
    )
    result_context_rerun: bool = False
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2048)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SchemiiActivityEvent(ApiModel):
    sequence: Annotated[int, Field(strict=True, ge=1)]
    kind: Literal["status", "message", "proposal", "operation", "error", "freshness"]
    payload: dict[str, Any]
    created_at: datetime


class SchemiiActivityPage(ApiModel):
    events: list[SchemiiActivityEvent]
    next_sequence: Annotated[int, Field(strict=True, ge=0)]


class SchemiiChatPolicy(ApiModel):
    revision: Annotated[int, Field(strict=True, ge=1)]
    capabilities: AiCapabilities


class SchemiiChatPolicyUpdate(ApiModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    capabilities: AiCapabilities


class SchemiiProposal(ApiModel):
    id: str = Field(pattern=r"^prop_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    turn_id: str = Field(pattern=r"^turn_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    capability: Annotated[str, Field(min_length=1, max_length=128)]
    action_type: Annotated[str, Field(min_length=1, max_length=128)]
    summary: Annotated[str, Field(min_length=1, max_length=2048)]
    details: dict[str, Any]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    destructive: bool
    expected_workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_design_revision: Annotated[int, Field(strict=True, ge=0)]
    status: Literal["pending", "executing", "succeeded", "failed", "dismissed", "expired"]
    created_at: datetime
    expires_at: datetime


class SchemiiProposalListResponse(ApiModel):
    proposals: list[SchemiiProposal]


class SchemiiProposalExecutionCreate(ApiModel):
    expected_chat_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_proposal_revision: Annotated[int, Field(strict=True, ge=1)]
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool

    @model_validator(mode="after")
    def require_confirmation(self) -> "SchemiiProposalExecutionCreate":
        if not self.confirmed:
            raise ValueError("confirmed must be true")
        return self


class SchemiiAiOperation(ApiModel):
    id: str = Field(pattern=r"^aop_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^prop_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    kind: Literal["design_change", "migration_review", "migration_apply", "migration_resolve", "migration_reconcile", "design_history", "sql_write", "data_read", "console_script", "navigation"]
    status: Literal["running", "succeeded", "failed", "cancelled", "uncertain"]
    resource_kind: str | None = Field(default=None, max_length=128)
    resource_id: str | None = Field(default=None, max_length=256)
    result_summary: dict[str, Any] | None = None
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2048)
    created_at: datetime
    updated_at: datetime


class SchemiiBatchProposalApproval(SchemiiProposalExecutionCreate):
    proposal_id: str = Field(pattern=r"^prop_[0-9a-f]{32}$")


class SchemiiBatchApproval(ApiModel):
    items: list[SchemiiBatchProposalApproval] = Field(min_length=1, max_length=100)


class SchemiiAiOperationListResponse(ApiModel):
    operations: list[SchemiiAiOperation]


class SchemiiAiOperationReconcile(ApiModel):
    expected_operation_revision: Annotated[int, Field(strict=True, ge=1)]


class SchemiiQueryResult(ApiModel):
    operation_id: str = Field(pattern=r"^aop_[0-9a-f]{32}$")
    columns: list[dict[str, str]] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] | None = None
    next_cursor: str | None = None
    rerun: bool = False
    freshness_notice: str | None = None


class SchemiiReadRun(ApiModel):
    """A retained query reference, never retained result values."""

    id: str = Field(pattern=r"^arr_[0-9a-f]{32}$")
    chat_id: str
    turn_id: str
    operation_id: str
    sql: str
    label: str
    execution_id: str
    result_id: str
    row_count: int | None = Field(default=None, ge=0)
    has_more: bool
    executed_at: datetime
    rerun_of: str | None = None
