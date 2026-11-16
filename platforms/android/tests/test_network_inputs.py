"""Build-consumer controls only; fixture libraries do not prove Android execution."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ANDROID = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "network_peer_inputs", ANDROID / "runtime/peer_inputs.py"
)
peer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peer)


class NetworkInputControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "source"
        subprocess.run(["git", "init", "-q", str(self.checkout)], check=True)
        self.sources = {
            "dev/android/tailnet-proxy/go.mod": b"module fixture\n",
            "dev/android/tailnet-proxy/go.sum": b"fixture checksum\n",
            "platforms/android/native/network/bridge.c": b"bridge source\n",
            "platforms/android/native/network/bridge.h": b"bridge header\n",
        }
        for name, data in self.sources.items():
            path = self.checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        subprocess.run(["git", "-C", str(self.checkout), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.checkout),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        head = subprocess.check_output(
            ["git", "-C", str(self.checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        hashes = {
            name: hashlib.sha256(data).hexdigest()
            for name, data in self.sources.items()
        }
        self.bridge = hashlib.sha256(
            self.sources["platforms/android/native/network/bridge.c"]
            + self.sources["platforms/android/native/network/bridge.h"]
        ).hexdigest()
        self.library = self.root / "fixture.so"
        self.library.write_bytes(b"non-executable input-control fixture")
        self.receipt = {
            "source_head": head,
            "source_files": hashes,
            "abi": "x86_64",
            "bridge_sha256": self.bridge,
            "library_sha256": peer.digest(self.library),
            "exit": 0,
            "source_unchanged": True,
            "dirty_inputs": [],
        }
        self.receipt_path = self.root / "network-build.json"
        self.handoff = {
            "source_head": head,
            "source_files": hashes,
            "elf": {
                "x86_64": {
                    "library": str(self.library),
                    "sha256": peer.digest(self.library),
                    "build_receipt": str(self.receipt_path),
                    "bridge_sha256": self.bridge,
                }
            },
        }
        self.write_receipt()
        self.contract = {
            "dependency_inputs": {
                name: hashes[name]
                for name in hashes
                if name.endswith(("go.mod", "go.sum"))
            }
        }
        self.out = self.root / "out"
        (self.out / "lib/x86_64").mkdir(parents=True)

    def write_receipt(self):
        self.receipt_path.write_text(json.dumps(self.receipt))
        self.handoff["elf"]["x86_64"]["build_receipt_sha256"] = peer.digest(
            self.receipt_path
        )

    def consume(self):
        path = self.root / "handoff.json"
        path.write_text(json.dumps(self.handoff))
        return peer.network(path, self.checkout, self.out, ["x86_64"], self.contract)

    def test_valid_identity_is_carried_into_apk_provenance(self):
        component, _, libraries = self.consume()
        self.assertEqual(component["bridge_sha256"], self.bridge)
        self.assertEqual(
            component["build_receipts_sha256"]["x86_64"], peer.digest(self.receipt_path)
        )
        self.assertEqual(
            libraries["lib/x86_64/libschemii_network.so"], peer.digest(self.library)
        )

    def test_build_receipt_byte_drift_is_refused(self):
        self.receipt_path.write_text(self.receipt_path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "receipt byte drift"):
            self.consume()

    def test_rebound_receipt_cannot_claim_a_stale_bridge(self):
        self.receipt["bridge_sha256"] = "0" * 64
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "bridge/source identity"):
            self.consume()

    def test_handoff_bridge_disagreement_is_refused(self):
        self.handoff["elf"]["x86_64"]["bridge_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "bridge/source identity"):
            self.consume()

    def test_mislabeled_receipt_abi_is_refused(self):
        self.receipt["abi"] = "arm64-v8a"
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "artifact/source receipt"):
            self.consume()

    def test_missing_bridge_identity_fails_closed(self):
        del self.receipt["bridge_sha256"]
        self.write_receipt()
        with self.assertRaises(KeyError):
            self.consume()


if __name__ == "__main__":
    unittest.main()
