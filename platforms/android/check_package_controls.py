#!/usr/bin/env python3
"""Counterexamples made from the actual APK; these are packaging, not device checks."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile
from verify_apk import verify_package


def check(apk, root, helper, expected_commit):
    positive = verify_package(apk, root, helper, expected_commit)
    with zipfile.ZipFile(apk) as package:
        entries = {info.filename: package.read(info) for info in package.infolist()}
    chosen = 'assets/shared/src/schemii/main.py'
    outcomes = {}
    mutations = {
        'missing-shared-source': lambda files: files.pop(chosen),
        'stale-shared-source': lambda files: files.__setitem__(chosen, files[chosen] + b'\n# stale bytes\n'),
        'missing-native': lambda files: files.pop(next(n for n in files if n.startswith('lib/'))),
        'stale-native': lambda files: files.__setitem__(next(n for n in files if n.startswith('lib/')),
                                                     b'not an ELF'),
    }
    with tempfile.TemporaryDirectory(prefix='apk-counterexamples-') as temporary:
        for case, mutate in mutations.items():
            changed = entries.copy()
            mutate(changed)
            path = Path(temporary) / (case + '.apk')
            # Mutations invalidate signing by design; exercise byte validation independently.
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as package:
                for name, data in changed.items(): package.writestr(name, data)
            try:
                verify_package(path, root, helper, expected_commit)
            except (ValueError, KeyError) as error:
                outcomes[case] = {'rejected': True, 'reason': str(error)}
            else:
                raise AssertionError('Accepted counterexample: ' + case)
    return {'apk_sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'positive': positive,
            'counterexamples': outcomes, 'runtime_acceptance': 'pending'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--parity-helper', type=Path)
    parser.add_argument('--expected-commit', required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.apk, args.root,
                           args.parity_helper or args.root / 'scripts/check_runtime_parity.py',
                           args.expected_commit), indent=2))


if __name__ == '__main__': main()
