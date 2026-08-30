from __future__ import annotations

import hashlib
import json
import re
from typing import Any


TRANSIENT_KEYS = {
    "x", "y", "color", "fingerprint", "importedAt", "importTime", "updatedAt",
    "profileId", "sourceProfileId", "liveOid", "layout", "timeZone",
}
MAX_VERIFIED_RELATION_PROFILE_DATABASES = 4


class PostgresServiceError(Exception):
    """Safe error suitable for direct serialization by an HTTP adapter."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        error = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


class ValidationError(PostgresServiceError):
    def __init__(self, message: str):
        super().__init__(400, "validation_error", message)


class NotFoundError(PostgresServiceError):
    def __init__(self, message: str):
        super().__init__(404, "not_found", message)


class ConflictError(PostgresServiceError):
    def __init__(self, code: str, message: str):
        super().__init__(409, code, message)


def _bounded_diagnostic_text(value: Any, limit: int = 1000) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value[:4000].split())
    # PostgreSQL frequently quotes rejected input values in primary/detail text.
    value = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "[redacted]", value)
    value = re.sub(r"Key \([^)]*\)=\([^)]*\)", "Key ([redacted])=([redacted])", value, flags=re.I)
    value = re.sub(r"Failing row contains \([^)]*\)", "Failing row contains ([redacted])", value, flags=re.I)[:limit]
    return value or None


def _safe_postgres_context(value: Any) -> str | None:
    """Keep location-only PostgreSQL context, never embedded SQL statements or values."""
    text = _bounded_diagnostic_text(value, 500)
    if not text or any(marker in text.lower() for marker in ("sql statement", "query:", "parameters:")):
        return None
    safe_lines = []
    for line in str(value).splitlines()[:8]:
        line = " ".join(line.split())
        if re.fullmatch(r'(?:PL/pgSQL|SQL) function [A-Za-z0-9_."() ,]+ line [0-9]+(?: at (?:assignment|RETURN|PERFORM|RAISE|IF|CALL))?', line):
            safe_lines.append(line[:250])
    return "; ".join(safe_lines) or None


def postgres_error_diagnostic(exc: Exception) -> dict[str, Any]:
    """Return a bounded PostgreSQL diagnostic safe for an HTTP response."""
    diagnostic = getattr(exc, "diag", None)
    result: dict[str, Any] = {}
    sqlstate = getattr(exc, "sqlstate", None)
    if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        result["sqlstate"] = sqlstate
    for source, target in (("message_primary", "message"), ("message_detail", "detail"), ("message_hint", "hint")):
        value = _bounded_diagnostic_text(getattr(diagnostic, source, None))
        if value:
            result[target] = value
    position = getattr(diagnostic, "statement_position", None)
    if isinstance(position, str) and position.isdigit():
        position = int(position)
    if isinstance(position, int) and not isinstance(position, bool) and 1 <= position <= 100_000:
        result["position"] = position
    context = _safe_postgres_context(getattr(diagnostic, "context", None))
    if context:
        result["context"] = context
    return result


def _bounded_evidence(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return max(-1_000_000_000, min(1_000_000_000, value))
    if isinstance(value, str):
        return _bounded_diagnostic_text(value, 200)
    if isinstance(value, dict) and depth < 2:
        result = {}
        for key, item in list(value.items())[:12]:
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,31}", key):
                continue
            if any(secret in key.lower() for secret in ("password", "credential", "secret", "token", "sql", "query", "definition", "value")):
                continue
            bounded = _bounded_evidence(item, depth=depth + 1)
            if bounded is not None:
                result[key] = bounded
        return result
    return None


def postgres_error_details(
    exc: Exception,
    *,
    phase: str | None = None,
    operation: str | None = None,
    rollback: dict[str, Any] | None = None,
    retry: dict[str, Any] | bool | None = None,
) -> dict[str, Any]:
    """Build the one safe HTTP details envelope for a PostgreSQL exception."""
    details: dict[str, Any] = {"postgres": postgres_error_diagnostic(exc)}
    for key, value in (("phase", phase), ("operation", operation), ("rollback", rollback), ("retry", retry)):
        bounded = _bounded_evidence(value)
        if bounded not in (None, {}, ""):
            details[key] = bounded
    return details


def quote_identifier(value: str) -> str:
    """Always quote a PostgreSQL identifier, including ordinary names."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValidationError("SQL identifier must be a non-empty string")
    return '"' + value.replace('"', '""') + '"'


def narrow_statement_timeout(cursor: Any, timeout_ms: int | None, *, local: bool = True) -> None:
    """Install an application timeout only when it narrows PostgreSQL's effective policy."""
    if timeout_ms is None:
        return
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 1:
        raise ValueError("timeout_ms must be a positive integer or None")
    cursor.execute(
        """SELECT pg_catalog.set_config('statement_timeout',
               CASE WHEN pg_catalog.current_setting('statement_timeout') = '0'
                      OR pg_catalog.current_setting('statement_timeout')::interval > (%s || ' milliseconds')::interval
                    THEN %s || 'ms' ELSE pg_catalog.current_setting('statement_timeout') END, %s)""",
        (timeout_ms, timeout_ms, local),
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical_value(item)
            for key, item in sorted(value.items())
            if key not in TRANSIENT_KEYS
        }
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def canonical_fingerprint(schema: dict[str, Any]) -> str:
    """Hash semantic schema content while ignoring canvas/transient fields."""
    canonical = json.dumps(
        _canonical_value(schema), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
