"""Focused controls for tool input and native package inspection, not runtime acceptance."""
import hashlib
import json
import importlib.util
from pathlib import Path
import stat
import struct
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load('build')
verify = load('verify_apk')


def elf(machine=183, alignment=16384, flags=5):
    result = bytearray(120)
    result[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<HH', result, 16, 3, machine)
    struct.pack_into('<Q', result, 32, 64)
    struct.pack_into('<HH', result, 54, 56, 1)
    struct.pack_into('<IIQQQQQQ', result, 64, 1, flags, 0, 0, 0, 120, 120, alignment)
    return bytes(result)


class BuildControls(unittest.TestCase):
    def test_sdk_unix_link_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'sdk.zip'
            with zipfile.ZipFile(archive, 'w') as source:
                source.writestr('sdk/clang-21', b'real-binary')
                info = zipfile.ZipInfo('sdk/clang')
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                source.writestr(info, b'clang-21')
            build.unzip(archive, root / 'out')
            self.assertTrue((root / 'out/sdk/clang').is_symlink())
            self.assertEqual((root / 'out/sdk/clang').read_bytes(), b'real-binary')
            build.unzip(archive, root / 'out')  # Verified extraction is reusable.

    def test_sdk_escape_refused(self):
        for name, link in [('../escape', None), ('sdk/link', '../../escape')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                with zipfile.ZipFile(root / 'sdk.zip', 'w') as source:
                    info = zipfile.ZipInfo(name)
                    if link:
                        info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    source.writestr(info, (link or 'x').encode())
                with self.assertRaises(ValueError): build.unzip(root / 'sdk.zip', root / 'out')
                self.assertFalse((root / 'escape').exists())

    def test_existing_sdk_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'out/sdk').mkdir(parents=True)
            (root / 'out/sdk/keep').write_text('owned')
            with zipfile.ZipFile(root / 'sdk.zip', 'w') as source: source.writestr('sdk/new', b'x')
            with self.assertRaises(ValueError): build.unzip(root / 'sdk.zip', root / 'out')
            self.assertEqual((root / 'out/sdk/keep').read_text(), 'owned')

    def test_checksum_mismatch_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'cached.zip'
            p.write_bytes(b'wrong')
            with self.assertRaises(ValueError): build.fetch('https://unused.invalid', '0' * 64, p)
            self.assertEqual(p.read_bytes(), b'wrong')

    def test_helper_snapshot_binds_selector(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'scripts').mkdir()
            helper = root / 'scripts/check_runtime_parity.py'
            selector = root / 'scripts/build-android-release.py'
            helper.write_text('# helper')
            selector.write_text('# selector')
            record = {'source_head': 'a' * 40, 'files': {
                'scripts/check_runtime_parity.py': build.digest(helper),
                'scripts/build-android-release.py': build.digest(selector)}}
            (root / 'provenance.json').write_text(json.dumps(record))
            self.assertEqual(build.helper_identity(helper)['source_head'], 'a' * 40)
            selector.write_text('# changed')
            with self.assertRaises(ValueError): build.helper_identity(helper)

    def test_elf_abi_and_page_alignment(self):
        self.assertEqual(verify.elf_identity(elf(), 'arm64-v8a')['load_alignment'], 16384)
        for data in [elf(machine=62), elf(alignment=4096), elf(flags=7), elf()[:80]]:
            with self.subTest(data=hashlib.sha256(data).hexdigest()):
                with self.assertRaises(ValueError): verify.elf_identity(data, 'arm64-v8a')


if __name__ == '__main__': unittest.main()
