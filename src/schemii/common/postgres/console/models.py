"""Application-neutral SQL Console request and response shapes."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from schemii.common.api.models import ApiModel


ConsoleMode = Literal["managed_read", "managed_write", "explicit", "autocommit"]
ConsoleExecutionStatus = Literal[
    "reserved",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "partial_committed",
    "uncertain",
]


class ConsoleSettings(ApiModel):
    """Application-scoped human SQL Console policy independent from AI authority."""

    revision: Annotated[int, Field(strict=True, ge=1)]
    write_intent: bool
    default_mode: ConsoleMode
    statement_limit: Annotated[int, Field(strict=True, ge=1, le=1000)]
    row_page_size: Annotated[int, Field(strict=True, ge=1, le=1000)]


class ConsoleSettingsUpdate(ApiModel):
    """Optimistic replacement of bounded human Console preferences."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    write_intent: bool
    default_mode: ConsoleMode
    statement_limit: Annotated[int, Field(strict=True, ge=1, le=1000)]
    row_page_size: Annotated[int, Field(strict=True, ge=1, le=1000)]


class ConsoleExecutionCreate(ApiModel):
    """Reviewed SQL script bound to current workspace target and settings."""

    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")
    expected_workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_settings_revision: Annotated[int, Field(strict=True, ge=1)]
    mode: ConsoleMode
    statements: list[Annotated[str, Field(min_length=1, max_length=1024 * 1024)]] = Field(
        min_length=1,
        max_length=1000,
    )


class ConsoleResultSummary(ApiModel):
    """Bounded first-page metadata for one statement result."""

    id: str = Field(pattern=r"^res_[0-9a-f]{32}$")
    statement_index: Annotated[int, Field(strict=True, ge=0)]
    command: Annotated[str, Field(min_length=1, max_length=128)]
    columns: list[str] = Field(max_length=1600)
    row_count: Annotated[int, Field(strict=True, ge=0)] | None = None
    has_more: bool


class ConsoleExecution(ApiModel):
    """Exact execution receipt without silently replaying statements."""

    id: str = Field(pattern=r"^cex_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")
    transaction_id: str | None = Field(default=None, pattern=r"^ctx_[0-9a-f]{32}$")
    status: ConsoleExecutionStatus
    completed_statement_indexes: list[Annotated[int, Field(strict=True, ge=0)]]
    results: list[ConsoleResultSummary] = Field(max_length=1000)
    error_code: str | None = Field(default=None, max_length=128)
    created_at: datetime
    updated_at: datetime


class ConsoleResultPage(ApiModel):
    """Single-advance result page retained from the original execution snapshot."""

    execution_id: str = Field(pattern=r"^cex_[0-9a-f]{32}$")
    result_id: str = Field(pattern=r"^res_[0-9a-f]{32}$")
    columns: list[str] = Field(max_length=1600)
    rows: list[list[Any]] = Field(max_length=1000)
    next_cursor: str | None = Field(default=None, max_length=512)
    truncated: bool
    expires_at: datetime


class ConsoleTransactionCreate(ApiModel):
    """Open one bounded explicit transaction for a workspace SQL Console."""

    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")
    expected_workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_settings_revision: Annotated[int, Field(strict=True, ge=1)]


class ConsoleTransactionExecutionCreate(ApiModel):
    """Reviewed statements to run inside an already owned explicit transaction."""

    statements: list[Annotated[str, Field(min_length=1, max_length=1024 * 1024)]] = Field(
        min_length=1,
        max_length=1000,
    )


class ConsoleTransactionCommand(ApiModel):
    """Optimistic transaction revision required for commit or rollback."""

    expected_revision: Annotated[int, Field(strict=True, ge=1)]


class ConsoleTransaction(ApiModel):
    """Process-bound explicit transaction state and expiry."""

    id: str = Field(pattern=r"^ctx_[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^ws_[0-9a-f]{32}$")
    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")
    revision: Annotated[int, Field(strict=True, ge=1)]
    status: Literal["open", "committed", "rolled_back", "expired", "failed", "uncertain"]
    execution_ids: list[str] = Field(max_length=1000)
    created_at: datetime
    expires_at: datetime
