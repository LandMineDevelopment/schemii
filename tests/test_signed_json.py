import base64
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.signed_json import decode_signed_json, encode_signed_json


class SignedJsonTests(unittest.TestCase):
    def test_framing_matches_existing_catalog_and_dashboard_tokens(self):
        secret = bytes(range(32))
        payload = {"v": 1, "context": {"kind": "catalog", "pageSize": 25}, "after": ["z"]}
        encoded = json.dumps(
            payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")
        legacy = base64.urlsafe_b64encode(
            encoded + hmac.new(secret, encoded, hashlib.sha256).digest(),
        ).decode("ascii").rstrip("=")

        token = encode_signed_json(secret, payload)
        self.assertEqual(token, legacy)
        self.assertEqual(decode_signed_json(secret, token), payload)

    def test_tampering_wrong_secret_and_bounds_are_rejected(self):
        token = encode_signed_json(b"a" * 32, {"v": 1})
        tampered = ("A" if token[0] != "A" else "B") + token[1:]
        for candidate, secret in ((tampered, b"a" * 32), (token, b"b" * 32), ("x" * 4097, b"a" * 32)):
            with self.subTest(candidate=candidate[:10]), self.assertRaises(ValueError):
                decode_signed_json(secret, candidate)


if __name__ == "__main__":
    unittest.main()
