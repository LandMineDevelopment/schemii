from __future__ import annotations

import json
import secrets
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Callable

from .dashboard_store import (
    DASHBOARD_ID_PATTERN,
    MAX_DASHBOARD_ID_LENGTH,
    DashboardStore,
    DashboardStoreError,
)
from .postgres_common import MAX_VERIFIED_RELATION_PROFILE_DATABASES, PostgresServiceError, canonical_fingerprint
from .query_type_capabilities import snapshot_column
from .relation_source import FINGERPRINT_PATTERN, RelationSourceValidationError, normalize_relation_source
from .signed_json import decode_signed_json, encode_signed_json
from .widget_query import QueryValidationError, normalize_query


DEFAULT_LEGACY_SOURCE_UPGRADE_TTL_SECONDS = 300
MAX_LEGACY_SOURCE_UPGRADE_WIDGETS = 100
POST_WRITE_VERIFICATION_BATCH_SIZE = 50
RELATION_TARGET_FIELDS = ("profileId", "database", "namespace", "relation")
# IDs are ASCII and are the only request-sized repeated values in the signed
# envelope. The fixed allowance covers every other token field, including
# evidence and time/revision integers, without making the decoder unbounded.
_MAX_LEGACY_SOURCE_UPGRADE_TOKEN_JSON_BYTES = (
    (MAX_LEGACY_SOURCE_UPGRADE_WIDGETS + 1) * MAX_DASHBOARD_ID_LENGTH
    + MAX_LEGACY_SOURCE_UPGRADE_WIDGETS * 3
    + 8192
)
MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH = 4 * (
    (_MAX_LEGACY_SOURCE_UPGRADE_TOKEN_JSON_BYTES + 32 + 2) // 3
)
MAX_LEGACY_SOURCE_UPGRADE_REQUEST_BODY_BYTES = (
    MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH
    + (MAX_LEGACY_SOURCE_UPGRADE_WIDGETS + 1) * (MAX_DASHBOARD_ID_LENGTH + 3)
    + 8192
)


class LegacySourceUpgrade:
    def __init__(
        self,
        service: Any,
        store: DashboardStore,
        *,
        secret: bytes | None = None,
        ttl_seconds: int = DEFAULT_LEGACY_SOURCE_UPGRADE_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 1:
            raise ValueError("legacy source upgrade TTL must be a positive integer")
        self.service = service
        self.store = store
        self.secret = secret or secrets.token_bytes(32)
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    @staticmethod
    def _request(value: Any, *, apply: bool) -> tuple[str, int, list[str], str | None]:
        expected = {"dashboardId", "expectedRevision", "widgetIds"}
        if apply:
            expected |= {"digest", "confirmed"}
        if not isinstance(value, dict) or set(value) != expected:
            raise DashboardStoreError(400, "validation_error", "Legacy source upgrade fields are invalid")
        dashboard_id = value.get("dashboardId")
        revision = value.get("expectedRevision")
        widget_ids = value.get("widgetIds")
        if (
            not isinstance(dashboard_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(dashboard_id)
            or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1
            or not isinstance(widget_ids, list) or not 1 <= len(widget_ids) <= MAX_LEGACY_SOURCE_UPGRADE_WIDGETS
            or any(not isinstance(item, str) or not DASHBOARD_ID_PATTERN.fullmatch(item) for item in widget_ids)
            or len(widget_ids) != len(set(widget_ids))
        ):
            raise DashboardStoreError(400, "validation_error", "Legacy source upgrade binding is invalid")
        if apply and value.get("confirmed") is not True:
            raise DashboardStoreError(400, "confirmation_required", "Legacy source upgrades require explicit confirmation")
        digest = value.get("digest") if apply else None
        if apply and not isinstance(digest, str):
            raise DashboardStoreError(400, "validation_error", "Legacy source upgrade digest is invalid")
        return dashboard_id, revision, widget_ids, digest

    @staticmethod
    def _legacy_columns(descriptor: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {key: column[key] for key in ("name", "type", "nullable", "ordinal")}
            for column in descriptor.get("columns", [])
        ]

    @staticmethod
    def _incompatible(widget: dict[str, Any] | None, widget_id: str, code: str, message: str) -> dict[str, Any]:
        return {
            "widgetId": widget_id,
            "title": widget.get("title", widget_id) if isinstance(widget, dict) else widget_id,
            "status": "incompatible",
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _target_key(value: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(value.get(field) for field in RELATION_TARGET_FIELDS)

    def _inspect_widget(
        self,
        widget: dict[str, Any] | None,
        widget_id: str,
        snapshots: dict[tuple[Any, ...], dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if widget is None:
            return self._incompatible(widget, widget_id, "widget_not_found", "The requested widget is not saved on this dashboard"), None
        configuration = widget.get("configuration", {})
        source = configuration.get("source")
        if not isinstance(source, dict) or source.get("snapshotVersion", 1) != 1 or "columns" not in source:
            return self._incompatible(widget, widget_id, "source_not_legacy", "The widget does not have a version 1 source snapshot"), None
        try:
            identity = {key: source[key] for key in (*RELATION_TARGET_FIELDS, "kind")}
            if snapshots is None:
                profile_fingerprint = self.service.profile_context_fingerprint(identity["profileId"])
                descriptor = self.service.inspect_relation(
                    identity["profileId"], identity["database"], identity["namespace"], identity["relation"],
                )
                if self.service.profile_context_fingerprint(identity["profileId"]) != profile_fingerprint:
                    raise PostgresServiceError(409, "profile_changed", "The PostgreSQL profile changed during source verification; review it again")
            else:
                snapshot = snapshots.get(self._target_key(identity))
                if snapshot is None:
                    raise PostgresServiceError(
                        409, "legacy_source_changed",
                        "The saved version 1 source was not included in the verified PostgreSQL relation snapshot",
                    )
                profile_fingerprint = snapshot.get("profileFingerprint")
                descriptor = snapshot.get("descriptor")
                if not isinstance(descriptor, dict):
                    raise PostgresServiceError(
                        409, "legacy_source_changed", "The verified PostgreSQL relation snapshot is invalid",
                    )
            if not isinstance(profile_fingerprint, str) or not FINGERPRINT_PATTERN.fullmatch(profile_fingerprint):
                raise PostgresServiceError(409, "profile_changed", "The saved PostgreSQL profile cannot be bound to this review")
            if any(descriptor.get(key) != identity[key] for key in ("profileId", "database", "namespace", "relation")):
                raise PostgresServiceError(
                    409, "legacy_source_changed",
                    "PostgreSQL returned a different relation target than the saved version 1 source; reselect this source",
                )
            legacy_kind = "table" if descriptor.get("kind") == "partitioned_table" else descriptor.get("kind")
            if legacy_kind != identity["kind"]:
                raise PostgresServiceError(
                    409, "legacy_source_changed",
                    "The current PostgreSQL relation kind does not match the saved version 1 source; reselect this source",
                )
            if descriptor.get("legacyFingerprint") != source["fingerprint"]:
                raise PostgresServiceError(
                    409, "legacy_source_changed",
                    "The current PostgreSQL catalog does not match the saved version 1 fingerprint; reselect this source",
                )
            current_legacy_columns = self._legacy_columns(descriptor)
            if current_legacy_columns != source["columns"]:
                raise PostgresServiceError(
                    409, "legacy_columns_changed",
                    "The current PostgreSQL columns do not exactly match the saved version 1 snapshot; reselect this source",
                )
            if descriptor.get("snapshotVersion") != 2 or any("capabilities" not in column for column in descriptor.get("columns", [])):
                raise PostgresServiceError(
                    409, "source_capabilities_unavailable",
                    "The current source cannot produce a complete version 2 capability snapshot",
                )
            columns = [snapshot_column(column) for column in descriptor["columns"]]
            candidate = normalize_relation_source({
                **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind")},
                "fingerprint": descriptor["fingerprint"],
                "snapshotVersion": 2,
                "columns": columns,
            })
            query_status = "not_configured"
            if "query" in configuration:
                normalized_query = normalize_query(configuration["query"], candidate["columns"])
                if normalized_query != configuration["query"]:
                    raise QueryValidationError("the current capability snapshot would change the saved query during normalization")
                query_status = "valid"
        except PostgresServiceError as error:
            detail = error.to_dict()["error"]
            return self._incompatible(widget, widget_id, detail["code"], detail["message"]), None
        except (KeyError, RelationSourceValidationError, QueryValidationError, ValueError) as error:
            return self._incompatible(widget, widget_id, "legacy_query_invalid", str(error)), None
        evidence = {
            "widgetId": widget_id,
            "title": widget["title"],
            "status": "compatible",
            "source": identity,
            "profileFingerprint": profile_fingerprint,
            "savedLegacyFingerprint": source["fingerprint"],
            "currentLegacyFingerprint": descriptor["legacyFingerprint"],
            "currentFingerprint": candidate["fingerprint"],
            "columnCount": len(columns),
            "columns": "exact",
            "query": query_status,
        }
        return evidence, {"expectedSource": json.loads(json.dumps(source)), "source": candidate}

    def _widgets(self, dashboard_id: str, revision: int) -> dict[str, dict[str, Any]]:
        with self.store.guard_revision(dashboard_id, revision) as record:
            return {widget["id"]: widget for widget in record["dashboard"]["widgets"]}

    def _review_widgets(
        self,
        dashboard_id: str,
        revision: int,
        widget_ids: list[str],
        widgets: dict[str, dict[str, Any]],
        snapshots: dict[tuple[Any, ...], dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        results = []
        replacements = {}
        for widget_id in widget_ids:
            result, replacement = self._inspect_widget(widgets.get(widget_id), widget_id, snapshots)
            results.append(result)
            if replacement is not None:
                replacements[widget_id] = replacement
        evidence = {
            "dashboardId": dashboard_id,
            "expectedRevision": revision,
            "widgetIds": widget_ids,
            "results": results,
            "compatibleWidgetIds": [item["widgetId"] for item in results if item["status"] == "compatible"],
            "incompatibleWidgetIds": [item["widgetId"] for item in results if item["status"] == "incompatible"],
        }
        return evidence, replacements

    def _review(self, dashboard_id: str, revision: int, widget_ids: list[str]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        return self._review_widgets(dashboard_id, revision, widget_ids, self._widgets(dashboard_id, revision))

    @staticmethod
    def _relation_targets(
        widgets: dict[str, dict[str, Any]], widget_ids: list[str],
    ) -> list[dict[str, Any]]:
        targets = []
        for widget_id in widget_ids:
            widget = widgets.get(widget_id)
            source = widget.get("configuration", {}).get("source") if isinstance(widget, dict) else None
            if (
                isinstance(source, dict) and source.get("snapshotVersion", 1) == 1
                and "columns" in source and all(field in source for field in RELATION_TARGET_FIELDS)
            ):
                targets.append({field: source[field] for field in RELATION_TARGET_FIELDS})
        return targets

    @staticmethod
    def _preview_batch(
        widgets: dict[str, dict[str, Any]], widget_ids: list[str],
    ) -> tuple[list[str], list[str]]:
        selected_pairs: set[tuple[str, str]] = set()
        selected = []
        deferred = []
        for widget_id in widget_ids:
            widget = widgets.get(widget_id)
            source = widget.get("configuration", {}).get("source") if isinstance(widget, dict) else None
            pair = None
            if (
                isinstance(source, dict) and source.get("snapshotVersion", 1) == 1
                and "columns" in source and all(field in source for field in RELATION_TARGET_FIELDS)
                and isinstance(source.get("profileId"), str) and isinstance(source.get("database"), str)
            ):
                pair = (source["profileId"], source["database"])
            if pair is not None and pair not in selected_pairs and len(selected_pairs) >= MAX_VERIFIED_RELATION_PROFILE_DATABASES:
                deferred.append(widget_id)
                continue
            selected.append(widget_id)
            if pair is not None:
                selected_pairs.add(pair)
        return selected, deferred

    def preview(self, value: Any) -> dict[str, Any]:
        dashboard_id, revision, widget_ids, _ = self._request(value, apply=False)
        widgets = self._widgets(dashboard_id, revision)
        widget_ids, deferred_widget_ids = self._preview_batch(widgets, widget_ids)
        evidence, _ = self._review_widgets(dashboard_id, revision, widget_ids, widgets)
        issued_at = int(self.clock())
        expires_at = issued_at + self.ttl_seconds
        token = encode_signed_json(self.secret, {
            "version": 1,
            "kind": "legacy_source_upgrade",
            "dashboardId": dashboard_id,
            "expectedRevision": revision,
            "widgetIds": widget_ids,
            "evidenceHash": canonical_fingerprint(evidence),
            "issuedAt": issued_at,
            "expiresAt": expires_at,
        })
        return {
            **evidence,
            "deferredWidgetIds": deferred_widget_ids,
            "maximumUniqueProfileDatabases": MAX_VERIFIED_RELATION_PROFILE_DATABASES,
            "maximumDigestLength": MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH,
            "digest": token,
            "expiresAt": datetime.fromtimestamp(expires_at, timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def apply(self, value: Any) -> dict[str, Any]:
        dashboard_id, revision, widget_ids, digest = self._request(value, apply=True)
        try:
            token = decode_signed_json(
                self.secret, digest,
                maximum_length=MAX_LEGACY_SOURCE_UPGRADE_DIGEST_LENGTH,
            )
        except ValueError as error:
            raise DashboardStoreError(400, "legacy_source_digest_invalid", "Legacy source upgrade digest is invalid") from error
        expected = {"version", "kind", "dashboardId", "expectedRevision", "widgetIds", "evidenceHash", "issuedAt", "expiresAt"}
        if (
            not isinstance(token, dict) or set(token) != expected
            or token.get("version") != 1 or token.get("kind") != "legacy_source_upgrade"
            or token.get("dashboardId") != dashboard_id or token.get("expectedRevision") != revision
            or token.get("widgetIds") != widget_ids
            or isinstance(token.get("issuedAt"), bool) or not isinstance(token.get("issuedAt"), int)
            or isinstance(token.get("expiresAt"), bool) or not isinstance(token.get("expiresAt"), int)
            or not isinstance(token.get("evidenceHash"), str)
        ):
            raise DashboardStoreError(400, "legacy_source_digest_invalid", "Legacy source upgrade digest does not match this request")
        current_time = int(self.clock())
        if (
            token["expiresAt"] != token["issuedAt"] + self.ttl_seconds
            or current_time < token["issuedAt"]
            or current_time >= token["expiresAt"]
        ):
            raise DashboardStoreError(409, "legacy_source_digest_expired", "Legacy source upgrade review expired; preview it again")
        widgets = self._widgets(dashboard_id, revision)
        targets = self._relation_targets(widgets, widget_ids)
        guarded_snapshots = (
            self.service.verified_relation_catalog_snapshots(targets)
            if targets else nullcontext([])
        )
        with guarded_snapshots as snapshots:
            snapshots_by_target = {
                self._target_key(snapshot): snapshot
                for snapshot in snapshots
            }
            evidence, replacements = self._review_widgets(
                dashboard_id, revision, widget_ids, widgets, snapshots_by_target,
            )
            if canonical_fingerprint(evidence) != token["evidenceHash"]:
                raise DashboardStoreError(409, "legacy_source_upgrade_changed", "Legacy source verification changed; preview it again")
            if not replacements:
                raise DashboardStoreError(409, "legacy_source_upgrade_incompatible", "None of the reviewed legacy sources can be upgraded")
            record = self.store.upgrade_legacy_sources(dashboard_id, revision, replacements)
        post_write_verification = self._post_write_verification(replacements)
        return {
            "dashboardId": dashboard_id,
            "previousRevision": revision,
            "revision": record["revision"],
            "upgradedWidgetIds": list(replacements),
            "incompatibleWidgetIds": evidence["incompatibleWidgetIds"],
            "postWriteVerification": post_write_verification,
        }

    def _post_write_verification(
        self, replacements: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        changed_widget_ids = []
        unavailable_widget_ids = []
        by_profile: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for widget_id, replacement in replacements.items():
            source = replacement["source"]
            by_profile.setdefault(source["profileId"], []).append((widget_id, source))
        for profile_id, items in by_profile.items():
            for offset in range(0, len(items), POST_WRITE_VERIFICATION_BATCH_SIZE):
                batch = items[offset:offset + POST_WRITE_VERIFICATION_BATCH_SIZE]
                try:
                    payload = self.service.verify_relation_sources(
                        profile_id, [source for _, source in batch],
                    )
                    results = payload.get("results") if isinstance(payload, dict) else None
                    if not isinstance(results, list) or len(results) != len(batch):
                        raise ValueError("post-write source verification returned an incomplete batch")
                    for (widget_id, _), verification in zip(batch, results):
                        matches = verification.get("matches") if isinstance(verification, dict) else None
                        if matches is False:
                            changed_widget_ids.append(widget_id)
                        elif matches is not True:
                            unavailable_widget_ids.append(widget_id)
                except Exception:
                    # The dashboard write is already durable. This check is reporting only;
                    # it must never turn a successful write into an uncertain retry window.
                    unavailable_widget_ids.extend(widget_id for widget_id, _ in batch)
        status = (
            "changed" if changed_widget_ids
            else "unavailable" if unavailable_widget_ids
            else "current"
        )
        return {
            "status": status,
            "changedWidgetIds": changed_widget_ids,
            "unavailableWidgetIds": unavailable_widget_ids,
        }
