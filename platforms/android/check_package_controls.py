#!/usr/bin/env python3
"""Counterexamples made from the actual APK; these are packaging, not device checks."""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile
from verify_apk import verify_package


def check(apk, root, helper, expected_commit, guest_only=False):
    positive = verify_package(apk, root, helper, expected_commit)
    with zipfile.ZipFile(apk) as package:
        entries = {info.filename: package.read(info) for info in package.infolist()}
    chosen = "assets/shared/src/schemii/main.py"
    outcomes = {}
    mutations = {
        "missing-shared-source": lambda files: files.pop(chosen),
        "stale-shared-source": lambda files: files.__setitem__(
            chosen, files[chosen] + b"\n# stale bytes\n"
        ),
        "missing-native": lambda files: files.pop(
            next(n for n in files if n.startswith("lib/"))
        ),
        "stale-native": lambda files: files.__setitem__(
            next(n for n in files if n.startswith("lib/")), b"not an ELF"
        ),
    }
    guests = positive["guest_payloads"]
    if guests:
        abi = next(iter(guests))
        guest_zip = "assets/runtime/guest-" + abi + ".zip"
        mutations.update(
            {
                "missing-guest-archive": lambda files: files.pop(guest_zip),
                "stale-guest-archive": lambda files: files.__setitem__(
                    guest_zip, b"stale guest"
                ),
                "stale-guest-probe": lambda files: files.__setitem__(
                    "assets/runtime/guest_probe.py", b"stale probe"
                ),
                "stale-runtime-interface": lambda files: files.__setitem__(
                    "assets/runtime/runtime-interface.json", b"{}"
                ),
            }
        )
    if guest_only:
        if not guests:
            raise ValueError("Guest controls require an actual packaged guest")
        mutations = {
            name: mutate
            for name, mutate in mutations.items()
            if "guest" in name or name == "stale-runtime-interface"
        }
    with tempfile.TemporaryDirectory(prefix="apk-counterexamples-") as temporary:
        for case, mutate in mutations.items():
            changed = entries.copy()
            mutate(changed)
            path = Path(temporary) / (case + ".apk")
            # Mutations invalidate signing by design; exercise byte validation independently.
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
                for name, data in changed.items():
                    package.writestr(name, data)
            try:
                verify_package(path, root, helper, expected_commit)
            except (ValueError, KeyError) as error:
                outcomes[case] = {"rejected": True, "reason": str(error)}
            else:
                raise AssertionError("Accepted counterexample: " + case)
    return {
        "apk_sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
        "positive": positive,
        "counterexamples": outcomes,
        "runtime_acceptance": "pending",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--parity-helper", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument(
        "--guest-only",
        action="store_true",
        help="Run only the new guest/probe/interface byte controls",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            check(
                args.apk,
                args.root,
                args.parity_helper or args.root / "scripts/check_runtime_parity.py",
                args.expected_commit,
                args.guest_only,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
