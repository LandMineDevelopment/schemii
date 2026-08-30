from __future__ import annotations

from typing import Any


FULL_SCHEMA_COMPLETENESS_VERSION = 1


def full_schema_completeness_proof(live_fingerprint: str, desired_fingerprint: str) -> dict[str, Any]:
    return {
        "version": FULL_SCHEMA_COMPLETENESS_VERSION,
        "complete": True,
        "liveFingerprint": live_fingerprint,
        "desiredFingerprint": desired_fingerprint,
    }


def has_full_schema_completeness_proof(
    private_payload: Any, review_payload: Any, live_fingerprint: str, desired_fingerprint: str,
) -> bool:
    expected = full_schema_completeness_proof(live_fingerprint, desired_fingerprint)
    return (
        isinstance(private_payload, dict)
        and private_payload.get("completenessProof") == expected
        and isinstance(review_payload, dict)
        and review_payload.get("complete") is True
        and review_payload.get("applyCapable") is True
        and review_payload.get("blockingDifferences") == []
        and review_payload.get("completenessProof") == expected
    )
