import sys
import time
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.ai_operation_maintenance import AiOperationMaintenance, AiOperationMaintenanceConfig, OperationLeaseLost
from schemii.metadata import MetadataStoreError


class MaintenanceStore:
    def __init__(self):
        self.calls = []
        self.heartbeat_error = None
        self.maintenance_error = None

    def heartbeat_operation(self, attempt_id, token, *, lease_seconds):
        self.calls.append(("heartbeat", attempt_id, token, lease_seconds))
        if self.heartbeat_error:
            raise self.heartbeat_error

    def abandon_operation_attempt(self, attempt_id, token):
        self.calls.append(("abandon_attempt", attempt_id, token))

    def abandon_stale_operations(self, *, stale_before, limit):
        self.calls.append(("abandon_stale", stale_before, limit))
        if self.maintenance_error:
            raise self.maintenance_error
        return ["operation"]

    def recover_stale_results(self, *, reserved_before, delivering_before, limit):
        self.calls.append(("recover", reserved_before, delivering_before, limit))
        return {"released": ["reservation"], "uncertain": ["delivery"]}

    def cleanup(self, *, before, limit):
        self.calls.append(("cleanup", before, limit))
        return {"results": limit, "plans": 0, "chats": 0, "planPayloadsRedacted": 0}


class AiOperationMaintenanceTests(unittest.TestCase):
    def test_config_validates_operator_bounds_and_heartbeat_margin(self):
        config = AiOperationMaintenanceConfig.from_env({
            "SCHEMII_AI_MAINTENANCE_LEASE_SECONDS": "30",
            "SCHEMII_AI_MAINTENANCE_HEARTBEAT_SECONDS": "10",
            "SCHEMII_AI_MAINTENANCE_CLEANUP_BATCH_SIZE": "7",
        })
        self.assertEqual((config.lease_seconds, config.cleanup_batch_size), (30, 7))
        with self.assertRaises(ValueError):
            AiOperationMaintenanceConfig.from_env({"SCHEMII_AI_MAINTENANCE_INTERVAL_SECONDS": "0"})
        with self.assertRaises(ValueError):
            AiOperationMaintenanceConfig(lease_seconds=30, heartbeat_seconds=15)

    def test_run_once_applies_stale_thresholds_and_bounded_batches(self):
        store = MaintenanceStore()
        config = AiOperationMaintenanceConfig(
            operation_stale_seconds=11, reservation_stale_seconds=22, delivery_stale_seconds=33,
            cleanup_retention_seconds=3600, recovery_batch_size=4, cleanup_batch_size=5,
        )
        result = AiOperationMaintenance(store, config).run_once()
        self.assertEqual(result["operationsAbandoned"], 1)
        self.assertEqual(result["reservationsReleased"], 1)
        self.assertEqual(result["deliveriesUncertain"], 1)
        self.assertEqual([call[-1] for call in store.calls], [4, 4, 5])
        self.assertTrue(all(isinstance(call[1], datetime) for call in store.calls))

    def test_exact_attempt_token_is_heartbeated_and_lease_loss_abandons_without_success(self):
        store = MaintenanceStore()
        maintenance = AiOperationMaintenance(store, AiOperationMaintenanceConfig())
        maintenance.track("operation-1", "attempt-1", "claim-secret")
        maintenance.assert_owned("attempt-1")
        self.assertIn(("heartbeat", "attempt-1", "claim-secret", 90), store.calls)
        store.heartbeat_error = MetadataStoreError("operation_lease_expired", "expired", status=409)
        with self.assertRaises(OperationLeaseLost):
            maintenance.assert_owned("attempt-1")
        self.assertIn(("abandon_attempt", "attempt-1", "claim-secret"), store.calls)
        self.assertFalse(any(call[0] == "finish" for call in store.calls))

    def test_loop_reports_failure_recovers_and_stops_cleanly(self):
        store = MaintenanceStore()
        store.maintenance_error = MetadataStoreError("metadata_unavailable", "unavailable")
        config = AiOperationMaintenanceConfig(interval_seconds=1, heartbeat_seconds=1, lease_seconds=3)
        maintenance = AiOperationMaintenance(store, config)
        maintenance.start()
        deadline = time.monotonic() + 2
        while maintenance.health()["consecutiveFailures"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(maintenance.health()["lastErrorCode"], "metadata_unavailable")
        store.maintenance_error = None
        deadline = time.monotonic() + 3
        while maintenance.health()["consecutiveFailures"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(maintenance.health()["status"], "available")
        maintenance.close()
        self.assertFalse(maintenance.health()["running"])


if __name__ == "__main__":
    unittest.main()
