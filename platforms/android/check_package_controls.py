#!/usr/bin/env python3
"""Counterexamples made from the actual APK; these are packaging, not device checks."""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile
from verify_apk import verify_package


def check(
    apk,
    root,
    helper,
    expected_commit,
    guest_only=False,
    source_only=False,
    integration_only=False,
    aapt2=None,
):
    positive = verify_package(apk, root, helper, expected_commit, aapt2=aapt2)
    with zipfile.ZipFile(apk) as package:
        entries = {info.filename: package.read(info) for info in package.infolist()}
    chosen = "assets/shared/src/schemii/main.py"
    outcomes = {}
    mutations = {
        "missing-shared-record": lambda files: files.pop(
            "assets/runtime/shared-source.json"
        ),
        "wrong-source-record": lambda files: files.__setitem__(
            "assets/runtime/shared-source.json",
            json.dumps(
                {
                    **json.loads(files["assets/runtime/shared-source.json"]),
                    "commit": "0" * 40,
                }
            ).encode(),
        ),
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
    if integration_only:
        original = json.loads(entries["assets/runtime/native-provenance.json"])
        if not original.get("diagnostic_profile"):
            raise ValueError("Integration controls require actual integration package")

        def provenance(files, change):
            value = json.loads(files["assets/runtime/native-provenance.json"])
            change(value)
            files["assets/runtime/native-provenance.json"] = json.dumps(value).encode()

        abi = original["abis"][0]

        def wrong_abi(files):
            target = "lib/" + abi + "/libsubreaper_probe.so"
            data = entries["lib/" + original["abis"][1] + "/libsubreaper_probe.so"]
            files[target] = data
            sha = hashlib.sha256(data).hexdigest()

            def rebind(n):
                n["native_files"][target] = sha
                n["peer_components"]["subreaper"]["builds"][abi]["library_sha256"] = sha

            provenance(files, rebind)

        mutations = {
            "missing-subreaper": lambda f: f.pop(
                "lib/" + abi + "/libsubreaper_probe.so"
            ),
            "stale-network-library": lambda f: f.__setitem__(
                "lib/" + abi + "/libschemii_network.so", b"stale"
            ),
            "wrong-network-source": lambda f: provenance(
                f,
                lambda n: n["peer_components"]["network"].update(source_head="0" * 40),
            ),
            "wrong-process-source": lambda f: provenance(
                f,
                lambda n: n["peer_components"]["subreaper"].update(
                    source_head="0" * 40
                ),
            ),
            "missing-case": lambda f: provenance(
                f, lambda n: n["peer_components"]["subreaper"]["cases"].pop()
            ),
            "wrong-profile": lambda f: provenance(
                f, lambda n: n.update(diagnostic_profile="old-polling")
            ),
            "wrong-debug-label": lambda f: provenance(
                f, lambda n: n.update(debuggable=not n["debuggable"])
            ),
            "wrong-native-abi": lambda f: wrong_abi(f),
        }
    if guest_only:
        if not guests:
            raise ValueError("Guest controls require an actual packaged guest")
        mutations = {
            name: mutate
            for name, mutate in mutations.items()
            if "guest" in name or name == "stale-runtime-interface"
        }
    if source_only:
        mutations = {
            name: mutate
            for name, mutate in mutations.items()
            if "shared" in name or name == "wrong-source-record"
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
                verify_package(path, root, helper, expected_commit, aapt2=aapt2)
            except Exception as error:
                outcomes[case] = {
                    "rejected": True,
                    "error_type": type(error).__name__,
                    "reason": str(error),
                }
            else:
                raise AssertionError("Accepted counterexample: " + case)
    if source_only:
        try:
            verify_package(apk, root, helper, "0" * 40, aapt2=aapt2)
        except Exception as error:
            outcomes["wrong-independent-expected-source"] = {
                "rejected": True,
                "error_type": type(error).__name__,
                "reason": str(error),
            }
        else:
            raise AssertionError("Accepted independent wrong-source expectation")
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
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Run shared-record/source controls, without unrelated native/guest mutations",
    )
    parser.add_argument("--parity-helper", type=Path)
    parser.add_argument("--integration-only", action="store_true")
    parser.add_argument("--aapt2", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument(
        "--guest-only",
        action="store_true",
        help="Run only the new guest/probe/interface byte controls",
    )
    args = parser.parse_args()
    if sum([args.source_only, args.guest_only, args.integration_only]) > 1:
        parser.error("Choose one control scope")
    print(
        json.dumps(
            check(
                args.apk,
                args.root,
                args.parity_helper or args.root / "scripts/check_runtime_parity.py",
                args.expected_commit,
                args.guest_only,
                args.source_only,
                args.integration_only,
                args.aapt2,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
