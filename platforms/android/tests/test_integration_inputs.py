"""Bounded integration admission controls; no fixture establishes Android proof."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ANDROID = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "integration_peer", ANDROID / "runtime/peer_inputs.py"
)
peer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peer)
spec = importlib.util.spec_from_file_location(
    "integration_builder", ANDROID / "build.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

spec = importlib.util.spec_from_file_location(
    "integration_verify", ANDROID / "verify_apk.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class IntegrationInputControls(unittest.TestCase):
    def test_external_handoff_anchor_refuses_rebound_or_stale_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "handoff.json"
            p.write_text(json.dumps({"source_head": "a" * 40, "product": "original"}))
            anchor = peer.digest(p)
            self.assertEqual(peer.anchored_handoff(p, anchor)["product"], "original")
            p.write_text(
                json.dumps(
                    {
                        "source_head": "a" * 40,
                        "product": "rebound",
                        "sha256": hashlib.sha256(b"substitute").hexdigest(),
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "independently selected digest"):
                peer.anchored_handoff(p, anchor)
            with self.assertRaisesRegex(ValueError, "independently selected digest"):
                peer.anchored_handoff(p, "0" * 64)

    def test_new_profile_never_selects_wrapper_guest_or_polling_campaign(self):
        defaults = dict(
            native_donor=None,
            native_donor_receipt_sha256=None,
            build_id="unused",
            wrapper_controls_only=False,
            guest_payload=None,
            network_handoff=Path("handoff"),
            network_checkout=Path("network"),
            process_head=None,
            integration_probes=True,
            subreaper_handoff=Path("subreaper"),
            subreaper_checkout=Path("source"),
        )
        for key, value in [
            ("wrapper_controls_only", True),
            ("guest_payload", [Path("guest")]),
            ("process_head", "a" * 40),
            ("network_handoff", None),
            ("subreaper_handoff", None),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(
                    SimpleNamespace(**{**defaults, key: value}), Path("not-created")
                )

    def test_actual_aapt2_min_sdk_token_and_manifest_policy(self):
        badging = """package: name='development.landmine.schemii.runtimeprobe' compileSdkVersion='36'
minSdkVersion:'29'
targetSdkVersion:'36'
uses-permission: name='android.permission.INTERNET'
uses-permission: name='android.permission.ACCESS_NETWORK_STATE'
application-debuggable
"""
        tree = "E: manifest\n  E: application\n    E: activity\n"
        with patch.object(
            verifier.subprocess, "check_output", side_effect=[badging, tree]
        ):
            result = verifier.integration_manifest(
                Path("fixture.apk"), Path("pinned-aapt2"), True
            )
            self.assertEqual(result["min_sdk"], 29)
        variants = [
            (badging.replace("minSdkVersion:'29'", "minSdkVersion:'28'"), tree),
            (badging.replace("compileSdkVersion='36'", "compileSdkVersion='29'"), tree),
            (badging.replace("targetSdkVersion:'36'", "targetSdkVersion:'29'"), tree),
            (badging.replace("application-debuggable", ""), tree),
            (
                badging
                + "uses-permission: name='android.permission.CHANGE_NETWORK_STATE'\n",
                tree,
            ),
            (badging, tree + "  E: service\n"),
        ]
        for output, xml in variants:
            with (
                self.subTest(output=output, xml=xml),
                patch.object(
                    verifier.subprocess, "check_output", side_effect=[output, xml]
                ),
                self.assertRaises(ValueError),
            ):
                verifier.integration_manifest(
                    Path("fixture.apk"), Path("pinned-aapt2"), True
                )

    def test_fixed_component_identity_cannot_be_downgraded_to_polling(self):
        for profile, components in [
            ("old-polling", {}),
            (peer.INTEGRATION_PROFILE, {"process": {}}),
            (None, {}),
        ]:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                peer.integration_components(
                    {"diagnostic_profile": profile, "peer_components": components}
                )


if __name__ == "__main__":
    unittest.main()
