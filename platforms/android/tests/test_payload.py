"""Input/ABI-adapter controls; Android/SQL execution evidence is separate."""

import hashlib
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
ANDROID = ROOT / "platforms/android"


class PayloadControls(unittest.TestCase):
    def test_payload_pins_consume_canonical_contract(self):
        contract_path = ROOT / "dev/runtime-contract.json"
        contract = json.loads(contract_path.read_text())
        lock = json.loads((ANDROID / "runtime/payload.lock.json").read_text())
        self.assertEqual(
            lock["contract_sha256"],
            hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(set(lock["abi"]), {"arm64-v8a", "x86_64"})
        for abi, record in lock["abi"].items():
            self.assertEqual(
                {x["package"]: x["version"] for x in record["wheels"]},
                contract["dependencies"]["python"],
            )
            self.assertEqual(
                record["node"]["version"], contract["dependencies"]["node"]
            )
            pg = next(x for x in record["packages"] if x["package"] == "postgresql-17")
            self.assertEqual(
                pg["version"].split(".")[0],
                contract["dependencies"]["postgresql_major"],
            )
            if abi == "arm64-v8a":
                self.assertEqual(
                    record["node"]["sha256"], contract["node_arm64_sha256"]
                )
            for item in record["packages"] + record["wheels"] + [record["node"]]:
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(item["url"].startswith("https://"))

    def test_peer_source_uses_committed_bytes_without_reverting_peer(self):
        spec = importlib.util.spec_from_file_location(
            "peer_inputs", ANDROID / "runtime/peer_inputs.py"
        )
        peer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(peer)
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            path = checkout / "probe.c"
            path.write_text("committed")
            subprocess.run(["git", "-C", str(checkout), "add", "probe.c"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout),
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
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
            ).strip()
            path.write_text("peer edits")
            self.assertEqual(
                peer.committed(checkout, head, ["probe.c"]), {"probe.c": b"committed"}
            )
            self.assertEqual(path.read_text(), "peer edits")
            with self.assertRaises(ValueError):
                peer.committed(checkout, head, ["../escape"])

    def test_guest_rename_adapter_preserves_posix_semantics(self):
        if not shutil.which("clang"):
            self.fail("Host clang is required for the explicit glibc adapter control")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "compat.so"
            subprocess.run(
                [
                    "clang",
                    "-shared",
                    "-fPIC",
                    "-O2",
                    "-Wl,-z,max-page-size=16384",
                    str(ANDROID / "native/runtime/guest_compat.c"),
                    "-o",
                    str(library),
                ],
                check=True,
            )
            script = r"""
import errno, os
from pathlib import Path
p=Path('from'); q=Path('to')
p.write_bytes(b'new'); q.write_bytes(b'old'); os.replace(p,q)
assert q.read_bytes()==b'new' and not p.exists()
Path('link').symlink_to('to'); os.rename('link','moved-link')
assert os.readlink('moved-link')=='to' and q.read_bytes()==b'new'
Path('dir').mkdir(); os.chdir('dir'); os.rename('../dir','../renamed')
assert Path.cwd().name=='renamed'
try: os.rename('absent','target')
except FileNotFoundError as e: assert e.errno==errno.ENOENT
else: raise AssertionError('Missing source must fail')
"""
            env = os.environ.copy()
            env["LD_PRELOAD"] = str(library)
            subprocess.run(["python3", "-c", script], cwd=root, env=env, check=True)


if __name__ == "__main__":
    unittest.main()
